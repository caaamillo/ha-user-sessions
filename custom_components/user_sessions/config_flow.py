"""Config flow for User Sessions: one click to add, then Configure for options (three sections)."""
from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_AUTO_ARCHIVE_DAYS,
    CONF_DISMISS_HA_NOTIFICATION,
    CONF_LOOKUP_MAC,
    CONF_NOTIFY_ON_BAN,
    CONF_ENABLE_CARD,
    CONF_GEO_PROVIDER,
    CONF_HISTORY_DAYS,
    CONF_HISTORY_MAX,
    CONF_KNOWN_IPS,
    CONF_KNOWN_NETWORKS,
    CONF_KNOWN_RADIUS_KM,
    CONF_SCAN_INTERVAL,
    CONF_TRACK_FAILED,
    DEFAULTS,
    DOMAIN,
    GEO_PROVIDERS,
)
from .security import parse_labeled

SEC_NETWORKS = "networks"
SEC_DETECTION = "detection"
SEC_HISTORY = "history"
SEC_DASHBOARD = "dashboard"
SECTIONS = {
    SEC_NETWORKS: (CONF_KNOWN_NETWORKS, CONF_KNOWN_IPS, CONF_KNOWN_RADIUS_KM),
    SEC_DETECTION: (
        CONF_TRACK_FAILED, CONF_GEO_PROVIDER, CONF_LOOKUP_MAC, CONF_NOTIFY_ON_BAN, CONF_SCAN_INTERVAL,
        CONF_DISMISS_HA_NOTIFICATION,
    ),
    SEC_HISTORY: (CONF_HISTORY_DAYS, CONF_HISTORY_MAX, CONF_AUTO_ARCHIVE_DAYS),
    SEC_DASHBOARD: (CONF_ENABLE_CARD,),
}
INT_KEYS = (CONF_SCAN_INTERVAL, CONF_KNOWN_RADIUS_KM, CONF_HISTORY_DAYS, CONF_HISTORY_MAX, CONF_AUTO_ARCHIVE_DAYS)


def _num(min_: int, max_: int, unit: str | None = None, step: int = 1) -> selector.NumberSelector:
    cfg: dict[str, Any] = {"min": min_, "max": max_, "step": step, "mode": selector.NumberSelectorMode.BOX}
    if unit:
        cfg["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**cfg))


def _list(current: list[str]) -> selector.SelectSelector:
    """Editable list: existing entries shown as chips, type to add new ones."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(current), multiple=True, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN
        )
    )


def _schema(cur: dict[str, Any]) -> vol.Schema:
    nets, ips = list(cur.get(CONF_KNOWN_NETWORKS) or []), list(cur.get(CONF_KNOWN_IPS) or [])
    return vol.Schema({
        vol.Required(SEC_NETWORKS): section(vol.Schema({
            vol.Optional(CONF_KNOWN_NETWORKS, default=nets): _list(nets),
            vol.Optional(CONF_KNOWN_IPS, default=ips): _list(ips),
            vol.Required(CONF_KNOWN_RADIUS_KM, default=cur[CONF_KNOWN_RADIUS_KM]): _num(0, 20000, "km"),
        }), {"collapsed": False}),
        vol.Required(SEC_DETECTION): section(vol.Schema({
            vol.Required(CONF_TRACK_FAILED, default=cur[CONF_TRACK_FAILED]): selector.BooleanSelector(),
            vol.Required(CONF_GEO_PROVIDER, default=cur[CONF_GEO_PROVIDER]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=GEO_PROVIDERS, translation_key=CONF_GEO_PROVIDER, mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(CONF_LOOKUP_MAC, default=cur[CONF_LOOKUP_MAC]): selector.BooleanSelector(),
            vol.Required(CONF_NOTIFY_ON_BAN, default=cur[CONF_NOTIFY_ON_BAN]): selector.BooleanSelector(),
            vol.Required(CONF_SCAN_INTERVAL, default=cur[CONF_SCAN_INTERVAL]): _num(10, 3600, "s"),
            vol.Required(CONF_DISMISS_HA_NOTIFICATION, default=cur[CONF_DISMISS_HA_NOTIFICATION]): selector.BooleanSelector(),
        }), {"collapsed": True}),
        vol.Required(SEC_HISTORY): section(vol.Schema({
            vol.Required(CONF_HISTORY_DAYS, default=cur[CONF_HISTORY_DAYS]): _num(1, 3650, "days"),
            vol.Required(CONF_HISTORY_MAX, default=cur[CONF_HISTORY_MAX]): _num(10, 5000),
            vol.Required(CONF_AUTO_ARCHIVE_DAYS, default=cur[CONF_AUTO_ARCHIVE_DAYS]): _num(0, 365, "days"),
        }), {"collapsed": True}),
        vol.Required(SEC_DASHBOARD): section(vol.Schema({
            vol.Required(CONF_ENABLE_CARD, default=cur[CONF_ENABLE_CARD]): selector.BooleanSelector(),
        }), {"collapsed": not cur[CONF_ENABLE_CARD]}),
    })


def _bad_entries(entries: list[str], kind: str) -> list[str]:
    bad = []
    for item in entries:
        value, _ = parse_labeled(item)
        try:
            ipaddress.ip_network(value, strict=False) if kind == "net" else ipaddress.ip_address(value)
        except ValueError:
            bad.append(item)
    return bad


def _clean(entries: list[str] | None) -> list[str]:
    """Trim, normalise spacing, drop blanks and duplicates (by address), keep order."""
    out, seen = [], set()
    for item in entries or []:
        value, label = parse_labeled(item)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(f"{value} {label}" if label else value)
    return out


class UserSessionsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="User Sessions", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return UserSessionsOptionsFlow()


class UserSessionsOptionsFlow(OptionsFlow):
    def _security(self):
        coordinator = getattr(self.config_entry, "runtime_data", None)
        return getattr(coordinator, "security", None)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"bad": ""}
        if user_input is not None:
            flat: dict[str, Any] = {}
            for sec in SECTIONS:
                flat.update(user_input.get(sec) or {})
            nets, ips = _clean(flat.get(CONF_KNOWN_NETWORKS)), _clean(flat.get(CONF_KNOWN_IPS))
            bad_n, bad_i = _bad_entries(nets, "net"), _bad_entries(ips, "ip")
            if bad_n or bad_i:
                errors["base"] = "invalid_network" if bad_n else "invalid_ip"
                placeholders["bad"] = ", ".join(bad_n or bad_i)
            else:
                for k in INT_KEYS:
                    flat[k] = int(flat[k])
                flat[CONF_KNOWN_NETWORKS] = nets
                flat.pop(CONF_KNOWN_IPS, None)  # lives in the security store, not in options
                sec = self._security()
                if sec is not None:
                    await sec.async_set_known_ips(ips)
                return self.async_create_entry(data={**self.config_entry.options, **flat})
            cur = {**DEFAULTS, **self.config_entry.options, **flat}
        else:
            cur = {**DEFAULTS, **self.config_entry.options}
            sec = self._security()
            cur[CONF_KNOWN_IPS] = sec.known_ip_entries() if sec is not None else []
        if cur.get(CONF_KNOWN_NETWORKS) is None:
            from . import async_default_networks  # noqa: PLC0415

            cur[CONF_KNOWN_NETWORKS] = await async_default_networks(self.hass, cur)
        from .security import ha_ban_config  # noqa: PLC0415

        ban = ha_ban_config(self.hass)
        placeholders["ban_status"] = (
            f"✅ on, after {ban['threshold']} failed sign-ins" if ban["enabled"]
            else "⚠️ off: failed sign-ins are recorded but never blocked" if ban["known"]
            else "unknown"
        )
        return self.async_show_form(
            step_id="init", data_schema=_schema(cur), errors=errors, description_placeholders=placeholders
        )
