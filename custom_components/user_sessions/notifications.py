"""Built-in phone notifications (opt-in: Configure → Notifications).

Sends to the Companion-app devices picked in the options, for new sign-ins, failed sign-ins and IP
bans, with action buttons. Nothing is sent until at least one device is picked, so people who prefer
their own automations on the user_sessions_* events are unaffected.

Action buttons come back as `mobile_app_notification_action` events. They are only acted on when the
person who tapped is an admin, because trusting an IP or signing someone out are admin actions.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import slugify

from .const import (
    CONF_NOTIFY_BAN,
    CONF_NOTIFY_CLICK_URL,
    CONF_NOTIFY_FAILED,
    CONF_NOTIFY_FAILED_MIN_RISK,
    CONF_NOTIFY_NEW_SESSION,
    CONF_NOTIFY_TARGETS,
    EVENT_FAILED_LOGIN,
    EVENT_IP_BANNED,
    EVENT_NEW_SESSION,
)
from .security import SecurityManager, mac_label

_LOGGER = logging.getLogger(__name__)

ACTION_PREFIX = "USER_SESSIONS|"
_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def mobile_app_targets(hass: HomeAssistant) -> list[dict[str, str]]:
    """[{value: notify service, label: device name}] for every Companion app that can be notified."""
    out = []
    for entry in hass.config_entries.async_entries("mobile_app"):
        name = entry.data.get("device_name") or entry.title
        service = f"mobile_app_{slugify(name)}"
        if hass.services.has_service("notify", service):
            out.append({"value": service, "label": entry.title or name})
    return sorted(out, key=lambda o: o["label"].lower())


def _client(d: dict[str, Any] | None) -> str | None:
    d = d or {}
    if d.get("is_bot"):
        return "automated client"
    return " on ".join(x for x in (d.get("browser"), d.get("os")) if x) or None


class Notifier:
    def __init__(self, hass: HomeAssistant, options: dict[str, Any], security: SecurityManager) -> None:
        self.hass = hass
        self.options = options
        self.security = security

    @property
    def targets(self) -> list[str]:
        return [t for t in (self.options.get(CONF_NOTIFY_TARGETS) or []) if t]

    @callback
    def async_start(self) -> list:
        bus = self.hass.bus
        return [
            bus.async_listen(EVENT_FAILED_LOGIN, self._on_failed),
            bus.async_listen(EVENT_IP_BANNED, self._on_ban),
            bus.async_listen(EVENT_NEW_SESSION, self._on_new_session),
            bus.async_listen("mobile_app_notification_action", self._on_action),
        ]

    # ---- sending ------------------------------------------------------------
    async def _send(self, title: str, message: str, *, actions: list[dict] | None = None,
                    urgent: bool = False, tag: str | None = None) -> None:
        url = (self.options.get(CONF_NOTIFY_CLICK_URL) or "").strip()
        for service in self.targets:
            payload: dict[str, Any] = {"title": title, "message": message}
            if service.startswith("mobile_app_"):
                data: dict[str, Any] = {"group": "user-sessions", "channel": "Security"}
                if actions:
                    data["actions"] = actions
                if tag:
                    data["tag"] = tag
                if urgent:
                    data["push"] = {"interruption-level": "time-sensitive"}
                    data["priority"] = "high"
                if url:
                    data["url"] = url
                    data["clickAction"] = url
                payload["data"] = data
            try:
                await self.hass.services.async_call("notify", service, payload, blocking=False)
            except Exception as err:  # noqa: BLE001 - a missing phone must not break the others
                _LOGGER.warning("Couldn't notify %s: %s", service, err)

    async def _on_failed(self, event: Event) -> None:
        d = event.data
        if not self.targets or not self.options.get(CONF_NOTIFY_FAILED, True) or not d.get("is_new"):
            return
        floor = _RISK_RANK.get(self.options.get(CONF_NOTIFY_FAILED_MIN_RISK) or "low", 0)
        if _RISK_RANK.get(d.get("risk") or "low", 0) < floor:
            return
        ip = d.get("ip")
        bits = ["Known IP" if d.get("known_ip") else "Never-seen IP", d.get("isp"), _client(d.get("device")),
                mac_label(d.get("lan_device"))]
        where = f" ({d['location']})" if d.get("location") else ""
        await self._send(
            f"🛡️ Failed sign-in · {(d.get('risk') or 'unknown').title()} risk",
            f"{d.get('description') or 'Failed sign-in'} from {ip}{where}. " + " · ".join(b for b in bits if b) + ".",
            actions=[
                {"action": f"{ACTION_PREFIX}TRUST|{ip}", "title": "It was me (trust IP)", "authenticationRequired": True},
                {"action": f"{ACTION_PREFIX}CLEAR|{ip}", "title": "Clear"},
            ],
            urgent=d.get("risk") in ("medium", "high"),
            tag=f"user-sessions-{ip}",
        )

    async def _on_ban(self, event: Event) -> None:
        d = event.data
        if not self.targets or not self.options.get(CONF_NOTIFY_BAN, True):
            return
        advice = [a for a in (d.get("block_advice") or []) if not a.startswith("Home Assistant has already banned")]
        n = d.get("attempts", 1)
        where = f" from {d['location']}" if d.get("location") else ""
        await self._send(
            f"⛔ Sign-in ban · {d.get('ip')}",
            f"Banned after {n} failed sign-in{'s' if n != 1 else ''}{where}. " + " ".join(advice[:2]),
            urgent=True,
            tag=f"user-sessions-{d.get('ip')}",
        )

    async def _on_new_session(self, event: Event) -> None:
        d = event.data
        if not self.targets or not self.options.get(CONF_NOTIFY_NEW_SESSION, True):
            return
        token = d.get("kind") == "long_lived"
        client = d.get("client_name") or d.get("via") or "Unknown client"
        where = d.get("location") or d.get("last_ip") or "an unknown place"
        first = "" if d.get("known_ip") or not d.get("last_ip") else " · first time from this IP"
        await self._send(
            f"🔑 New {'long-lived token' if token else 'sign-in'} · {d.get('user_name')}",
            f"{client} from {where}{first}.",
            actions=[{"action": f"{ACTION_PREFIX}SIGNOUT|{d.get('session_id')}", "title": "Sign it out",
                      "authenticationRequired": True, "destructive": True}],
            urgent=d.get("risk") in ("medium", "high"),
        )

    # ---- action buttons -------------------------------------------------------
    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if not action.startswith(ACTION_PREFIX):
            return
        verb, _, arg = action[len(ACTION_PREFIX):].partition("|")
        user = await self.hass.auth.async_get_user(event.context.user_id) if event.context.user_id else None
        if user is None or not user.is_admin:
            _LOGGER.warning("Ignored %s from a non-admin user", verb)
            return
        if verb == "TRUST":
            try:
                self.security.trust_ip(arg, f"Trusted from a notification by {user.name}")
            except ValueError:
                return
            self.security.clear(None, arg, user.name)
        elif verb == "CLEAR":
            self.security.clear(None, arg, user.name)
        elif verb == "SIGNOUT":
            token = self.hass.auth.async_get_refresh_token(arg)
            if hasattr(token, "__await__"):
                token = await token
            if token is not None:
                removed = self.hass.auth.async_remove_refresh_token(token)
                if hasattr(removed, "__await__"):
                    await removed
                _LOGGER.warning("Signed out a session of %s from a notification (by %s)", token.user.name, user.name)
