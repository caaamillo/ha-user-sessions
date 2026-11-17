"""Sensors for User Sessions: one per user, totals, failed sign-ins, failed sign-in history, known IPs."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SessionsCoordinator, get_options
from .const import CONF_HISTORY_DAYS, CONF_HISTORY_MAX, CONF_TRACK_FAILED, DOMAIN
from .security import SecurityManager, ha_ban_config

_TOKEN_KEYS = ("session_id", "client_name", "created", "last_used", "last_ip", "network", "network_label", "client_type")
HISTORY_ATTR_LIMIT = 50  # keep state attributes small; the full history lives in .storage


def _device(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="User Sessions",
        manufacturer="Home Assistant",
        entry_type=DeviceEntryType.SERVICE,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SessionsCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_users() -> None:
        new = [
            UserSessionsSensor(coordinator, entry, uid)
            for uid in (coordinator.data or {})
            if uid not in known
        ]
        if new:
            known.update(s.user_id for s in new)
            async_add_entities(new)

    async_add_entities([
        TotalSessionsSensor(coordinator, entry),
        LongLivedTokensSensor(coordinator, entry),
        FailedSignInsSensor(coordinator.security, entry),
        FailedSignInHistorySensor(coordinator.security, entry),
        KnownIPsSensor(coordinator.security, entry),
    ])
    _add_new_users()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_users))


class UserSessionsSensor(CoordinatorEntity[SessionsCoordinator], SensorEntity):
    """Active sign-ins for one user (long-lived tokens listed in attributes)."""

    _attr_has_entity_name = True

    _attr_icon = "mdi:account-key"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "sessions"
    # session lists change constantly and would bloat the database
    _unrecorded_attributes = frozenset({"sessions", "long_lived_tokens"})

    def __init__(self, coordinator: SessionsCoordinator, entry: ConfigEntry, user_id: str) -> None:
        super().__init__(coordinator)
        self.user_id = user_id
        self._attr_unique_id = f"{user_id}_sessions"
        self._attr_device_info = _device(entry)
        name = (coordinator.data or {}).get(user_id, {}).get("name") or user_id
        self._attr_name = f"{name} sessions"

    @property
    def _user(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self.user_id)

    @property
    def available(self) -> bool:
        return super().available and self._user is not None

    @property
    def native_value(self) -> int | None:
        user = self._user
        return len(user["sessions"]) if user else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        user = self._user
        if not user:
            return {}
        latest = user["sessions"][0] if user["sessions"] else {}
        return {
            "user_id": self.user_id,
            "user_name": user["name"],
            "is_admin": user["is_admin"],
            "is_active": user["is_active"],
            "local_only": user["local_only"],
            "last_used": latest.get("last_used"),
            "last_ip": latest.get("last_ip"),
            "last_via": latest.get("via"),
            "long_lived_count": len(user["long_lived_tokens"]),
            "sessions": user["sessions"],
            "long_lived_tokens": [{k: t.get(k) for k in _TOKEN_KEYS} for t in user["long_lived_tokens"]],
        }


class TotalSessionsSensor(CoordinatorEntity[SessionsCoordinator], SensorEntity):
    """Active sign-ins across all users."""

    _attr_has_entity_name = True

    _attr_icon = "mdi:account-group"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "sessions"
    _attr_name = "Total user sessions"

    def __init__(self, coordinator: SessionsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_total_sessions"
        self._attr_device_info = _device(entry)

    @property
    def native_value(self) -> int:
        return sum(len(u["sessions"]) for u in (self.coordinator.data or {}).values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "users": len(data),
            "users_signed_in": sum(1 for u in data.values() if u["sessions"]),
        }


class LongLivedTokensSensor(CoordinatorEntity[SessionsCoordinator], SensorEntity):
    """Long-lived access tokens across all users."""

    _attr_has_entity_name = True

    _attr_icon = "mdi:key-chain-variant"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "tokens"
    _attr_name = "Long-lived tokens"

    def __init__(self, coordinator: SessionsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_long_lived_tokens"
        self._attr_device_info = _device(entry)

    @property
    def native_value(self) -> int:
        return sum(len(u["long_lived_tokens"]) for u in (self.coordinator.data or {}).values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "by_user": {u["name"]: len(u["long_lived_tokens"]) for u in data.values() if u["long_lived_tokens"]},
        }


# --------------------------------------------------------------------------- security sensors


class _SecurityEntity(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, security: SecurityManager, entry: ConfigEntry, key: str) -> None:
        self.security = security
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _device(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.security.async_add_listener(self.async_write_ha_state))


class FailedSignInsSensor(_SecurityEntity):
    """Uncleared failed sign-ins (one entry per IP; repeat attempts folded in)."""

    _attr_icon = "mdi:account-alert"
    _attr_native_unit_of_measurement = "attempts"
    _attr_name = "Failed sign-ins"
    _unrecorded_attributes = frozenset({"entries"})

    def __init__(self, security: SecurityManager, entry: ConfigEntry) -> None:
        super().__init__(security, entry, "failed_sign_ins")

    @property
    def native_value(self) -> int:
        return len(self.security.active)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = self.security.active
        latest = active[0] if active else {}
        return {
            "tracking_enabled": get_options(self.entry)[CONF_TRACK_FAILED],
            "total_attempts": sum(e.get("attempts", 1) for e in active),
            "banned": sum(1 for e in active if e.get("banned")),
            "ha_ip_ban_enabled": (ban := ha_ban_config(self.hass))["enabled"],
            "ha_ban_threshold": ban["threshold"],
            "highest_risk": next((lvl for lvl in ("high", "medium", "low") if any(e.get("risk") == lvl for e in active)), None),
            "last_ip": latest.get("ip"),
            "last_location": latest.get("location"),
            "last_attempt": latest.get("last_attempt"),
            "last_description": latest.get("description"),
            "last_risk": latest.get("risk"),
            "entries": [SecurityManager.public_entry(e) for e in active],
        }


class FailedSignInHistorySensor(_SecurityEntity):
    """Cleared / auto-archived failed sign-ins, kept for the retention period."""

    _attr_icon = "mdi:history"
    _attr_native_unit_of_measurement = "entries"
    _attr_name = "Failed sign-in history"
    _unrecorded_attributes = frozenset({"entries"})

    def __init__(self, security: SecurityManager, entry: ConfigEntry) -> None:
        super().__init__(security, entry, "failed_sign_in_history")

    @property
    def native_value(self) -> int:
        return len(self.security.history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        opts = get_options(self.entry)
        hist = self.security.history
        return {
            "retention_days": opts[CONF_HISTORY_DAYS],
            "max_entries": opts[CONF_HISTORY_MAX],
            "oldest": hist[-1].get("cleared_at") if hist else None,
            "unique_ips": len({e.get("ip") for e in hist}),
            "entries_shown": min(len(hist), HISTORY_ATTR_LIMIT),
            "entries": [SecurityManager.public_entry(e) for e in hist[:HISTORY_ATTR_LIMIT]],
        }


class KnownIPsSensor(_SecurityEntity):
    """IPs seen on successful sign-ins, plus IPs you marked as trusted."""

    _attr_icon = "mdi:ip-network"
    _attr_native_unit_of_measurement = "IPs"
    _attr_name = "Known IPs"
    _unrecorded_attributes = frozenset({"ips", "trusted"})

    def __init__(self, security: SecurityManager, entry: ConfigEntry) -> None:
        super().__init__(security, entry, "known_ips")

    @property
    def native_value(self) -> int:
        return len(set(self.security.ip_book) | set(self.security.trusted))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sec = self.security
        return {
            "ips": {
                ip: {**rec, "location": (sec.geo_cache.get(ip) or {}).get("city")}
                for ip, rec in sorted(sec.ip_book.items(), key=lambda kv: kv[1].get("last_seen") or "", reverse=True)
            },
            "trusted": sec.trusted,
            "known_networks": get_options(self.entry).get("known_networks") or [],
        }
