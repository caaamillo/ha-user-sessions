"""User Sessions: see every Home Assistant user's sign-ins and long-lived tokens, get told about new
ones and about failed sign-ins, sign users out.

Home Assistant's own API only shows each user their *own* sessions (Profile > Security).
Code running inside HA can read all users' refresh tokens, so this integration:
  * exposes one sensor per user (state = active sign-ins, attributes = each session's
    client, how it signed in, first seen, last used and last IP, plus the user's long-lived tokens),
  * fires `user_sessions_new_session` whenever a new sign-in OR a new long-lived token appears,
    enriched with network type, location and whether the IP / location were seen before,
  * captures failed sign-ins (bad password or bad token) into a register, enriched the same way,
    fires `user_sessions_failed_login`, and moves cleared entries to a history with retention,
  * adds admin-only actions to sign out a whole user or revoke a single session / token,
    clear failed sign-ins, purge history, and trust / forget an IP.
"""
from __future__ import annotations

import inspect
import logging
from pathlib import Path
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.auth.models import (
    TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
    TOKEN_TYPE_NORMAL,
    RefreshToken,
)
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    ATTR_ENTRY_ID,
    ATTR_INCLUDE_LONG_LIVED,
    ATTR_IP,
    ATTR_LABEL,
    ATTR_OLDER_THAN_DAYS,
    ATTR_SESSION_ID,
    ATTR_USER_ID,
    ATTR_USER_NAME,
    CARD_URL,
    CONF_ENABLE_PANEL,
    CONF_PANEL_THEME,
    PANEL_PATH,
    THEME_DEFAULT,
    PANEL_URL,
    CARD_VERSION,
    CONF_DISMISS_HA_NOTIFICATION,
    CONF_ENABLE_CARD,
    CONF_KNOWN_NETWORKS,
    CONF_LAN_IS_KNOWN,
    CONF_SCAN_INTERVAL,
    CONF_TAILSCALE_IS_KNOWN,
    CONF_TRUSTED_NETWORKS,
    TAILSCALE_RANGE,
    CONF_TRACK_FAILED,
    DEFAULTS,
    DOMAIN,
    EVENT_NEW_SESSION,
    HA_LOGIN_NOTIFICATION_ID,
    HTTP_SETTINGS_URL,
    ISSUE_IP_BAN_OFF,
    MAINTENANCE_INTERVAL,
    PLATFORMS,
    SERVICE_CLEAR_FAILED,
    SERVICE_CLEAR_HISTORY,
    SERVICE_FORGET_IP,
    SERVICE_SIGN_OUT_SESSION,
    SERVICE_SIGN_OUT_USER,
    SERVICE_TRUST_IP,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .security import SecurityManager, classify_network, ha_ban_config

_LOGGER = logging.getLogger(__name__)

ALL_SERVICES = (
    SERVICE_SIGN_OUT_USER,
    SERVICE_SIGN_OUT_SESSION,
    SERVICE_CLEAR_FAILED,
    SERVICE_CLEAR_HISTORY,
    SERVICE_TRUST_IP,
    SERVICE_FORGET_IP,
)


def get_options(entry: ConfigEntry) -> dict[str, Any]:
    return {**DEFAULTS, **(entry.options or {})}


async def _async_static(hass: HomeAssistant) -> None:
    """Serve the card and panel files. Static paths can't be unregistered, so once per HA run."""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get("static"):
        return
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    base = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths([
        StaticPathConfig(CARD_URL, str(base / "user-sessions-card.js"), False),
        StaticPathConfig(PANEL_URL, str(base / "user-sessions-panel.js"), False),
    ])
    store["static"] = True


async def _async_card(hass: HomeAssistant, enable: bool) -> None:
    """Load the bundled dashboard card into every open frontend (opt-in).

    What switches the card on and off is the frontend's extra-module list, which open browsers follow live.
    """
    from homeassistant.components import frontend  # noqa: PLC0415

    store = hass.data.setdefault(DOMAIN, {})
    url = f"{CARD_URL}?v={CARD_VERSION}"
    if enable:
        await _async_static(hass)
        if store.get("card_url") != url:
            try:
                frontend.add_extra_js_url(hass, url)
            except Exception as err:  # noqa: BLE001 - frontend not loaded (e.g. headless install)
                _LOGGER.warning("Could not load the User Sessions card into the frontend: %s", err)
                return
            store["card_url"] = url
    elif store.get("card_url"):
        try:
            frontend.remove_extra_js_url(hass, store.pop("card_url"))
        except Exception:  # noqa: BLE001
            pass


async def _async_panel(hass: HomeAssistant, enable: bool, theme: str | None = None) -> None:
    """Add or remove the admin-only "User Sessions" sidebar panel (opt-in)."""
    from homeassistant.components import frontend  # noqa: PLC0415

    try:
        frontend.async_remove_panel(hass, PANEL_PATH, warn_if_unknown=False)
    except Exception:  # noqa: BLE001 - frontend not loaded
        return
    if not enable:
        return
    await _async_static(hass)
    try:
        from homeassistant.components import panel_custom  # noqa: PLC0415

        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_PATH,
            webcomponent_name="user-sessions-panel",
            sidebar_title="User Sessions",
            sidebar_icon="mdi:shield-account-outline",
            module_url=f"{PANEL_URL}?v={CARD_VERSION}",
            config={
                "card_url": f"{CARD_URL}?v={CARD_VERSION}",
                "theme": theme if theme and theme != THEME_DEFAULT else None,
            },
            require_admin=True,
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not add the User Sessions panel: %s", err)


async def async_default_networks(hass: HomeAssistant, options: dict[str, Any] | None = None) -> list[str]:
    """Known networks for a fresh install: the LAN(s) HA itself is on, plus Tailscale.

    Honours the v0.3 options (lan_is_known / tailscale_is_known / trusted_networks) when migrating.
    """
    import ipaddress  # noqa: PLC0415

    options = options or {}
    nets: list[str] = []
    if options.get(CONF_LAN_IS_KNOWN, True):
        try:
            from homeassistant.components import network  # noqa: PLC0415

            for adapter in await network.async_get_adapters(hass):
                if not adapter.get("enabled"):
                    continue
                for a in adapter.get("ipv4", []):
                    net = ipaddress.ip_network(f"{a['address']}/{a['network_prefix']}", strict=False)
                    if net.is_private and not net.is_loopback and not str(net).startswith("172.30.32."):
                        entry = f"{net} Home network"
                        if entry not in nets:
                            nets.append(entry)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not read network adapters: %s", err)
        if not nets:
            nets += ["192.168.0.0/16 Home network", "10.0.0.0/8 Private network", "172.16.0.0/12 Private network"]
    if options.get(CONF_TAILSCALE_IS_KNOWN, True):
        nets.append(f"{TAILSCALE_RANGE} Tailscale")
    for part in str(options.get(CONF_TRUSTED_NETWORKS) or "").replace(",", " ").split():
        nets.append(f"{part} Trusted network")
    return nets


async def _maybe_await(value: Any) -> Any:
    """Some auth manager methods changed from coroutines to callbacks between HA versions."""
    if inspect.isawaitable(value):
        return await value
    return value


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _via(client_id: str | None) -> str:
    """Describe how a session signed in, from its OAuth client id."""
    if not client_id:
        return "Unknown"
    low = client_id.lower()
    if "home-assistant.io/ios" in low:
        return "Companion app (iOS)"
    if "home-assistant.io/android" in low:
        return "Companion app (Android)"
    host = urlparse(client_id).netloc or client_id
    return f"Browser via {host}"


def _ha_hosts(hass: HomeAssistant) -> set[str]:
    """Host names Home Assistant itself is reached on (its own browser sign-ins use these)."""
    hosts = {"homeassistant.local", "homeassistant", "localhost"}
    for url in (hass.config.internal_url, hass.config.external_url):
        if url and (h := urlparse(url).hostname):
            hosts.add(h.lower())
    return hosts


def client_type(client_id: str | None, long_lived: bool, ha_hosts: set[str]) -> str:
    """token | app | browser | connector (another service signed in through OAuth, e.g. an MCP client)."""
    if long_lived:
        return "token"
    low = (client_id or "").lower()
    if "home-assistant.io/ios" in low or "home-assistant.io/android" in low:
        return "app"
    host = (urlparse(client_id or "").hostname or "").lower()
    if not host:
        return "browser"
    try:
        import ipaddress  # noqa: PLC0415

        ipaddress.ip_address(host)
        return "browser"
    except ValueError:
        pass
    if host in ha_hosts or host.endswith((".local", ".ts.net", ".ui.nabu.casa", ".duckdns.org")):
        return "browser"
    return "connector"


def _token_info(token: RefreshToken) -> dict[str, Any]:
    long_lived = token.token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
    return {
        "session_id": token.id,
        "kind": "long_lived" if long_lived else "session",
        "client_name": token.client_name or None,
        "client_id": token.client_id,
        "via": "Long-lived token" if long_lived else _via(token.client_id),
        "created": _iso(token.created_at),
        "last_used": _iso(getattr(token, "last_used_at", None)),
        "last_ip": getattr(token, "last_used_ip", None),
    }


class SessionsCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls the auth manager and announces new sign-ins and long-lived tokens."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, security: SecurityManager) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=int(get_options(entry)[CONF_SCAN_INTERVAL])),
            config_entry=entry,
        )
        self.security = security
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        # None = nothing stored yet for that kind: record what exists silently, announce only later ones
        self._seen: set[str] | None = None
        self._seen_ll: set[str] | None = None

    async def async_load_seen(self) -> None:
        stored = await self._store.async_load()
        if stored is not None:
            self._seen = set(stored.get("seen", []))
            if "seen_ll" in stored:  # absent when upgrading from v0.1.0
                self._seen_ll = set(stored["seen_ll"])

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        users = await self.hass.auth.async_get_users()
        ha_hosts = _ha_hosts(self.hass)
        data: dict[str, dict[str, Any]] = {}
        current: set[str] = set()
        current_ll: set[str] = set()
        new_items: list[dict[str, Any]] = []
        session_ips: list[tuple[str, str, str, str | None]] = []

        for user in users:
            if user.system_generated:
                continue
            sessions: list[dict[str, Any]] = []
            long_lived: list[dict[str, Any]] = []
            for token in list(user.refresh_tokens.values()):
                info = _token_info(token)
                info["client_type"] = client_type(
                    token.client_id, token.token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN, ha_hosts
                )
                if info["last_ip"]:
                    info["network"] = classify_network(info["last_ip"], [])
                    info["network_label"] = self.security.known_network_label(info["last_ip"])
                if token.token_type == TOKEN_TYPE_NORMAL:
                    sessions.append(info)
                    current.add(token.id)
                    seen = self._seen
                elif token.token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN:
                    long_lived.append(info)
                    current_ll.add(token.id)
                    seen = self._seen_ll
                else:
                    continue
                if info["last_ip"]:
                    session_ips.append((info["last_ip"], user.name, info["client_name"] or info["via"], info["last_used"]))
                if seen is not None and token.id not in seen:
                    new_items.append({ATTR_USER_ID: user.id, ATTR_USER_NAME: user.name, **info})
            for lst in (sessions, long_lived):
                lst.sort(key=lambda s: s["last_used"] or s["created"] or "", reverse=True)
            data[user.id] = {
                "name": user.name,
                "is_admin": user.is_admin,
                "is_active": user.is_active,
                "local_only": getattr(user, "local_only", False),
                "sessions": sessions,
                "long_lived_tokens": long_lived,
            }

        # Assess new sign-ins BEFORE their IPs go into the IP book, so "known_ip" means "seen before".
        for item in new_items:
            a = await self.security.async_assess(item.get("last_ip"))
            level, reasons = self.security.risk(a)
            item.update(
                network=a.get("network"),
                location=a.get("location"),
                distance_km=a.get("distance_km"),
                known_ip=a.get("known_ip"),
                known_location=a.get("known_location"),
                ip_first_seen=a.get("ip_first_seen"),
                isp=(a.get("geo") or {}).get("isp"),
                risk=level,
                risk_reasons=reasons,
            )

        for ip, uname, client, when in session_ips:
            if self.security.record_success_ip(ip, uname, client, when):
                # geolocate new public IPs once, so later "same city" checks have something to compare
                self.hass.async_create_task(self.security.async_geolocate(ip))

        if self._seen != current or self._seen_ll != current_ll:
            self._seen, self._seen_ll = current, current_ll
            await self._store.async_save({"seen": sorted(current), "seen_ll": sorted(current_ll)})

        for event_data in new_items:
            _LOGGER.info(
                "New %s for %s (%s) from %s",
                "long-lived token" if event_data["kind"] == "long_lived" else "sign-in",
                event_data[ATTR_USER_NAME],
                event_data["client_name"] or event_data["via"],
                event_data.get("location") or event_data.get("last_ip") or "unknown",
            )
            self.hass.bus.async_fire(EVENT_NEW_SESSION, event_data)

        return data


@callback
def async_check_ip_ban(hass: HomeAssistant) -> None:
    """Raise a Repairs notice while HA's IP ban can't ban anyone (off, or unlimited attempts)."""
    cfg = ha_ban_config(hass)
    if not cfg["known"] or cfg["enabled"]:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_IP_BAN_OFF)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_IP_BAN_OFF,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_IP_BAN_OFF,
        learn_more_url=HTTP_SETTINGS_URL,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.options.get(CONF_KNOWN_NETWORKS) is None:
        # first start (or upgrade from v0.3): seed the editable list, dropping the old toggles
        migrated = {k: v for k, v in entry.options.items() if k not in (CONF_LAN_IS_KNOWN, CONF_TAILSCALE_IS_KNOWN, CONF_TRUSTED_NETWORKS)}
        migrated[CONF_KNOWN_NETWORKS] = await async_default_networks(hass, dict(entry.options))
        hass.config_entries.async_update_entry(entry, options=migrated)
    options = get_options(entry)
    security = SecurityManager(hass, options)
    await security.async_load()

    coordinator = SessionsCoordinator(hass, entry, security)
    await coordinator.async_load_seen()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    if options[CONF_TRACK_FAILED]:
        security.start_capture()
        entry.async_on_unload(security.stop_capture)

    await _async_card(hass, bool(options[CONF_ENABLE_CARD]))
    await _async_panel(hass, bool(options[CONF_ENABLE_PANEL]), options.get(CONF_PANEL_THEME))

    from .notifications import Notifier  # noqa: PLC0415

    for unsub in Notifier(hass, options, security).async_start():
        entry.async_on_unload(unsub)

    security.maintenance()
    async_check_ip_ban(hass)

    @callback
    def _hourly(_now) -> None:
        security.maintenance()
        async_check_ip_ban(hass)

    entry.async_on_unload(async_track_time_interval(hass, _hourly, MAINTENANCE_INTERVAL))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # ---------------------------------------------------------------- services
    async def _find_user(call: ServiceCall):
        user_id = call.data.get(ATTR_USER_ID)
        user_name = call.data.get(ATTR_USER_NAME)
        if user_id:
            user = await hass.auth.async_get_user(user_id)
        elif user_name:
            matches = [
                u for u in await hass.auth.async_get_users()
                if not u.system_generated and (u.name or "").lower() == user_name.lower()
            ]
            if len(matches) > 1:
                raise ServiceValidationError(f"More than one user is named '{user_name}'; use user_id")
            user = matches[0] if matches else None
        else:
            raise ServiceValidationError("Give either user_id or user_name")
        if user is None or user.system_generated:
            raise ServiceValidationError("No such user")
        return user

    async def _caller_name(call: ServiceCall) -> str | None:
        if call.context.user_id:
            u = await hass.auth.async_get_user(call.context.user_id)
            return u.name if u else None
        return "automation"

    async def sign_out_user(call: ServiceCall) -> None:
        user = await _find_user(call)
        types = {TOKEN_TYPE_NORMAL}
        if call.data.get(ATTR_INCLUDE_LONG_LIVED, False):
            types.add(TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN)
        tokens = [t for t in list(user.refresh_tokens.values()) if t.token_type in types]
        for token in tokens:
            await _maybe_await(hass.auth.async_remove_refresh_token(token))
        _LOGGER.warning("Signed out user %s (%d token(s) removed)", user.name, len(tokens))
        await coordinator.async_request_refresh()

    async def sign_out_session(call: ServiceCall) -> None:
        """Revoke one sign-in or one long-lived token."""
        session_id = call.data[ATTR_SESSION_ID]
        token = await _maybe_await(hass.auth.async_get_refresh_token(session_id))
        if token is None:
            raise ServiceValidationError("No such session or token (it may already be revoked)")
        what = "long-lived token" if token.token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN else "session"
        user_name = token.user.name
        await _maybe_await(hass.auth.async_remove_refresh_token(token))
        _LOGGER.warning("Revoked one %s of user %s", what, user_name)
        await coordinator.async_request_refresh()

    async def clear_failed(call: ServiceCall) -> ServiceResponse:
        moved = security.clear(call.data.get(ATTR_ENTRY_ID), call.data.get(ATTR_IP), await _caller_name(call))
        if moved and not security.active and get_options(entry)[CONF_DISMISS_HA_NOTIFICATION]:
            persistent_notification.async_dismiss(hass, HA_LOGIN_NOTIFICATION_ID)
        return {"moved_to_history": moved}

    async def clear_history(call: ServiceCall) -> ServiceResponse:
        return {"removed": security.clear_history(call.data.get(ATTR_OLDER_THAN_DAYS))}

    async def trust_ip(call: ServiceCall) -> None:
        try:
            security.trust_ip(call.data[ATTR_IP], call.data.get(ATTR_LABEL))
        except ValueError as err:
            raise ServiceValidationError(f"Not a valid IP address: {call.data[ATTR_IP]}") from err

    async def forget_ip(call: ServiceCall) -> ServiceResponse:
        return {"forgotten": security.forget_ip(call.data[ATTR_IP])}

    reg = [
        (SERVICE_SIGN_OUT_USER, sign_out_user, vol.Schema({
            vol.Optional(ATTR_USER_ID): cv.string,
            vol.Optional(ATTR_USER_NAME): cv.string,
            vol.Optional(ATTR_INCLUDE_LONG_LIVED, default=False): cv.boolean,
        }), SupportsResponse.NONE),
        (SERVICE_SIGN_OUT_SESSION, sign_out_session,
         vol.Schema({vol.Required(ATTR_SESSION_ID): cv.string}), SupportsResponse.NONE),
        (SERVICE_CLEAR_FAILED, clear_failed, vol.Schema({
            vol.Optional(ATTR_ENTRY_ID): cv.string,
            vol.Optional(ATTR_IP): cv.string,
        }), SupportsResponse.OPTIONAL),
        (SERVICE_CLEAR_HISTORY, clear_history, vol.Schema({
            vol.Optional(ATTR_OLDER_THAN_DAYS): vol.All(vol.Coerce(int), vol.Range(min=0)),
        }), SupportsResponse.OPTIONAL),
        (SERVICE_TRUST_IP, trust_ip, vol.Schema({
            vol.Required(ATTR_IP): cv.string,
            vol.Optional(ATTR_LABEL): cv.string,
        }), SupportsResponse.NONE),
        (SERVICE_FORGET_IP, forget_ip,
         vol.Schema({vol.Required(ATTR_IP): cv.string}), SupportsResponse.OPTIONAL),
    ]
    with_response = _supports_response_kw()
    for name, handler, schema, supports in reg:
        if with_response:
            async_register_admin_service(hass, DOMAIN, name, handler, schema, supports_response=supports)
        else:
            async_register_admin_service(hass, DOMAIN, name, handler, schema)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _supports_response_kw() -> bool:
    return "supports_response" in inspect.signature(async_register_admin_service).parameters


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ir.async_delete_issue(hass, DOMAIN, ISSUE_IP_BAN_OFF)
    for service in ALL_SERVICES:
        hass.services.async_remove(DOMAIN, service)
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None:
        await coordinator.security.async_flush()
    await _async_card(hass, False)
    await _async_panel(hass, False)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
