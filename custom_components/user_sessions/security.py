"""Failed sign-in capture, IP/location/device enrichment, and the register + history store.

Home Assistant fires no event for a failed sign-in. Every failure (wrong password on the login
page, or an API / WebSocket request with a bad token) goes through
`homeassistant.components.http.ban.process_wrong_login`, which logs one WARNING line holding the
remote host, IP, requested URL and user agent. We attach a logging handler to that logger, parse
the line, enrich it and keep it in our own store.

Store layout (.storage/user_sessions.security):
  active    : uncleared failed sign-ins, one entry per IP (repeat attempts are folded in)
  history   : cleared / auto-archived entries, pruned by age and count
  ip_book   : every IP ever seen on a *successful* sign-in (first/last seen, users, clients)
  trusted   : IPs the user marked as theirs (trust_ip service)
  geo_cache : geolocation results per IP
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AUTO_ARCHIVE_DAYS,
    CONF_GEO_PROVIDER,
    CONF_HISTORY_DAYS,
    CONF_HISTORY_MAX,
    CONF_KNOWN_NETWORKS,
    CONF_KNOWN_RADIUS_KM,
    EVENT_FAILED_LOGIN,
    GEO_CACHE_TTL,
    GEO_IP_API,
    GEO_IPWHO,
    MAX_URLS_PER_ENTRY,
    SECURITY_STORAGE_KEY,
    SECURITY_STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

BAN_LOGGER = "homeassistant.components.http.ban"

# "Login attempt or request with invalid authentication from HOST (IP). Requested URL: 'URL'. (UA)"
_FAIL_RE = re.compile(
    r"invalid authentication from (?P<host>.+?) \((?P<ip>[0-9a-fA-F:.]+)\)\."
    r"(?: Requested URL: '(?P<url>[^']*)'\.)?(?: \((?P<ua>.*)\))?\s*$",
    re.S,
)
_BAN_RE = re.compile(r"Banned IP (?P<ip>[0-9a-fA-F:.]+)")

_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_NET6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


# --------------------------------------------------------------------------- pure helpers


def parse_failed_line(message: str) -> dict[str, str | None] | None:
    m = _FAIL_RE.search(message)
    if not m:
        return None
    host, ip = m.group("host"), m.group("ip")
    ua = m.group("ua")
    return {
        "ip": ip,
        "hostname": None if host == ip else host,
        "url": m.group("url"),
        "user_agent": None if ua in (None, "", "None") else ua,
    }


def parse_ban_line(message: str) -> str | None:
    m = _BAN_RE.search(message)
    return m.group("ip") if m else None


def classify_network(ip: str, trusted: list[ipaddress._BaseNetwork]) -> str:
    """lan | tailscale | loopback | trusted | public | invalid."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "invalid"
    if any(addr in net for net in trusted if addr.version == net.version):
        return "trusted"
    if addr.is_loopback:
        return "loopback"
    if (addr.version == 4 and addr in _TAILSCALE_NET) or (addr.version == 6 and addr in _TAILSCALE_NET6):
        return "tailscale"
    if addr.is_private or addr.is_link_local:
        return "lan"
    return "public"


def parse_trusted_networks(text: str) -> list[ipaddress._BaseNetwork]:
    nets = []
    for part in re.split(r"[,\s]+", text or ""):
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            _LOGGER.warning("Ignoring invalid trusted network %r", part)
    return nets


def parse_labeled(entry: str) -> tuple[str, str | None]:
    """'192.168.1.0/24 Home LAN' -> ('192.168.1.0/24', 'Home LAN'). The label is optional."""
    parts = str(entry or "").strip().split(None, 1)
    if not parts:
        return "", None
    label = parts[1].strip(" \t-\u2013\u2014\u00b7:#") if len(parts) > 1 else ""
    return parts[0], label or None


def parse_known_networks(entries: list[str] | None) -> list[tuple[ipaddress._BaseNetwork, str | None]]:
    out = []
    for item in entries or []:
        cidr, label = parse_labeled(item)
        if not cidr:
            continue
        try:
            out.append((ipaddress.ip_network(cidr, strict=False), label))
        except ValueError:
            _LOGGER.warning("Ignoring invalid known network %r", item)
    return out


def describe_request(url: str | None) -> tuple[str, str]:
    """(attempt_type, human description) from the requested path."""
    path = (url or "").split("?")[0]
    if path.startswith("/auth/login_flow"):
        return "password", "Wrong username or password on the sign-in page"
    if path.startswith("/auth/token"):
        return "token_exchange", "Invalid or expired refresh token / auth code"
    if path.startswith("/api/websocket"):
        return "websocket", "WebSocket connection with an invalid token"
    if path.startswith("/api/webhook"):
        return "webhook", "Webhook request with invalid authentication"
    if path.startswith("/api"):
        return "api", "API request with an invalid or missing token"
    return "other", "Request with invalid authentication"


_BOT_HINTS = (
    "bot", "spider", "crawl", "curl", "wget", "python", "go-http", "httpx", "aiohttp", "okhttp",
    "java/", "libwww", "zgrab", "masscan", "nmap", "nuclei", "censys", "scanner", "headless",
)


def parse_user_agent(ua: str | None) -> dict[str, Any]:
    """Light, dependency-free UA parse: browser, version, OS, device type, bot flag."""
    if not ua:
        return {"browser": None, "browser_version": None, "os": None, "device_type": "unknown", "is_bot": True}
    low = ua.lower()
    is_bot = any(h in low for h in _BOT_HINTS)

    browser = version = None
    if "home assistant" in low or "homeassistant" in low:
        browser = "HA Companion app"
        m = re.search(r"home ?assistant/([\d.]+)", ua, re.I)
        version = m and m.group(1)
    else:
        for name, pat in (
            ("Edge", r"Edg(?:e|A|iOS)?/([\d.]+)"),
            ("Opera", r"OPR/([\d.]+)"),
            ("Samsung Internet", r"SamsungBrowser/([\d.]+)"),
            ("Firefox", r"(?:Firefox|FxiOS)/([\d.]+)"),
            ("Chrome", r"(?:Chrome|CriOS)/([\d.]+)"),
            ("Safari", r"Version/([\d.]+).*Safari/"),
        ):
            m = re.search(pat, ua)
            if m:
                browser, version = name, m.group(1)
                break
        if browser is None and "safari" in low:
            browser = "Safari"
        if browser is None:
            browser = ua.split("/")[0][:40] or None

    os_name = None
    for name, pat in (
        ("iOS", r"iPhone|iPad|iPod|\biOS\b"),
        ("Android", r"Android"),
        ("Windows", r"Windows"),
        ("macOS", r"Mac OS X|Macintosh"),
        ("ChromeOS", r"CrOS"),
        ("Linux", r"Linux"),
    ):
        if re.search(pat, ua):
            os_name = name
            break

    if is_bot:
        device = "bot"
    elif re.search(r"iPad|Tablet", ua):
        device = "tablet"
    elif re.search(r"Mobile|iPhone|Android", ua) or os_name == "iOS":
        device = "mobile"
    elif os_name in ("Windows", "macOS", "Linux", "ChromeOS"):
        device = "desktop"
    else:
        device = "unknown"

    return {"browser": browser, "browser_version": version, "os": os_name, "device_type": device, "is_bot": is_bot}


def device_fingerprint(ua_info: dict[str, Any]) -> str | None:
    if not ua_info.get("browser"):
        return None
    major = (ua_info.get("browser_version") or "").split(".")[0]
    return f"{ua_info['browser']} {major}".strip() + f" / {ua_info.get('os') or '?'} / {ua_info.get('device_type')}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


def location_label(geo: dict[str, Any] | None) -> str | None:
    if not geo:
        return None
    parts = [p for p in (geo.get("city"), geo.get("region"), geo.get("country")) if p]
    return ", ".join(dict.fromkeys(parts)) or None


# --------------------------------------------------------------------------- log handler


class _BanLogHandler(logging.Handler):
    """Forwards http.ban log lines to the event loop."""

    def __init__(self, hass: HomeAssistant, on_failed: Callable, on_ban: Callable) -> None:
        super().__init__(level=logging.WARNING)
        self._hass = hass
        self._on_failed = on_failed
        self._on_ban = on_ban

    def emit(self, record: logging.LogRecord) -> None:  # any thread
        try:
            msg = record.getMessage()
            ts = record.created
            if (parsed := parse_failed_line(msg)) is not None:
                self._hass.loop.call_soon_threadsafe(self._on_failed, parsed, ts)
            elif (ip := parse_ban_line(msg)) is not None:
                self._hass.loop.call_soon_threadsafe(self._on_ban, ip, ts)
        except Exception:  # noqa: BLE001 - a logging handler must never raise
            pass


# --------------------------------------------------------------------------- manager


class SecurityManager:
    """Owns the failed sign-in register, history, IP book and geolocation cache."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._store: Store = Store(hass, SECURITY_STORAGE_VERSION, SECURITY_STORAGE_KEY)
        self.active: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.ip_book: dict[str, dict[str, Any]] = {}
        self.trusted: dict[str, dict[str, Any]] = {}
        self.geo_cache: dict[str, dict[str, Any]] = {}
        self._handler: _BanLogHandler | None = None
        self._listeners: list[Callable[[], None]] = []
        self._known_nets = parse_known_networks(options.get(CONF_KNOWN_NETWORKS))
        self._geo_locks: dict[str, asyncio.Lock] = {}

    # ---- lifecycle ------------------------------------------------------
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.active = data.get("active", [])
        self.history = data.get("history", [])
        self.ip_book = data.get("ip_book", {})
        self.trusted = data.get("trusted", {})
        self.geo_cache = data.get("geo_cache", {})

    def start_capture(self) -> None:
        self._handler = _BanLogHandler(self.hass, self._handle_failed, self._handle_ban)
        logging.getLogger(BAN_LOGGER).addHandler(self._handler)
        self._backfill_from_system_log()

    @callback
    def _backfill_from_system_log(self) -> None:
        """Import failed sign-ins HA logged before we were listening (kept in memory by system_log)."""
        try:
            handler = self.hass.data.get("system_log")
            entries = list(handler.records.values()) if handler is not None else []
        except Exception:  # noqa: BLE001
            return
        latest: dict[str, str] = {}
        for e in self.active + self.history:
            latest[e["ip"]] = max(latest.get(e["ip"], ""), e.get("last_attempt") or "")
        for entry in entries:
            if getattr(entry, "name", None) != BAN_LOGGER:
                continue
            for msg in list(getattr(entry, "message", [])):
                parsed = parse_failed_line(msg)
                if not parsed:
                    continue
                first = dt_util.utc_from_timestamp(entry.first_occurred).isoformat()
                # skip if we already hold an attempt from this IP at or after this time
                if latest.get(parsed["ip"], "") >= first:
                    continue
                latest[parsed["ip"]] = first
                self.hass.async_create_task(
                    self._async_record_failed(parsed, entry.first_occurred, backfilled=True)
                )

    def stop_capture(self) -> None:
        if self._handler:
            logging.getLogger(BAN_LOGGER).removeHandler(self._handler)
            self._handler = None

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def _data(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "history": self.history,
            "ip_book": self.ip_book,
            "trusted": self.trusted,
            "geo_cache": self.geo_cache,
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._data, 2)

    async def async_flush(self) -> None:
        """Write now (cancels a pending delayed write), so a reload never reads stale data."""
        await self._store.async_save(self._data())

    # ---- known networks / IPs ---------------------------------------------
    def known_network_label(self, ip: str) -> str | None:
        """Label of the first known network containing ip ('' if it has none); None if not in any."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        for net, label in self._known_nets:
            if addr.version == net.version and addr in net:
                return label or str(net)
        return None

    def known_ip_entries(self) -> list[str]:
        """Form defaults: trusted IPs plus IPs from successful sign-ins that no known network covers."""
        out: dict[str, str] = {}
        for ip, rec in self.trusted.items():
            out[ip] = rec.get("label") or ""
        for ip, rec in sorted(self.ip_book.items(), key=lambda kv: kv[1].get("last_seen") or "", reverse=True):
            if ip in out or self.known_network_label(ip) is not None or classify_network(ip, []) == "loopback":
                continue
            who = ", ".join(rec.get("users", [])[:2])
            what = ", ".join(c.replace("Browser via ", "") for c in rec.get("clients", [])[:1])
            out[ip] = " \u00b7 ".join(x for x in (who, what) if x)
        return [f"{ip} {label}".strip() for ip, label in out.items()]

    async def async_set_known_ips(self, entries: list[str]) -> None:
        """Make the trusted list exactly match the form. IPs removed from it are forgotten everywhere."""
        wanted: dict[str, str | None] = {}
        for item in entries:
            ip, label = parse_labeled(item)
            if ip:
                wanted[str(ipaddress.ip_address(ip))] = label
        before = set(self.known_ip_entries_ips())
        for ip in list(self.trusted):
            if ip not in wanted:
                self.trusted.pop(ip)
        for ip in before - set(wanted):
            self.ip_book.pop(ip, None)
        for ip, label in wanted.items():
            rec = self.trusted.setdefault(ip, {"first_seen": (self.ip_book.get(ip) or {}).get("first_seen") or dt_util.utcnow().isoformat(), "devices": []})
            rec["label"] = label
        await self.async_flush()
        self._notify()

    def known_ip_entries_ips(self) -> list[str]:
        return [parse_labeled(x)[0] for x in self.known_ip_entries()]

    # ---- geolocation ----------------------------------------------------
    async def async_geolocate(self, ip: str) -> dict[str, Any] | None:
        provider = self.options.get(CONF_GEO_PROVIDER)
        if provider not in (GEO_IP_API, GEO_IPWHO):
            return None
        if classify_network(ip, []) != "public":
            return None
        lock = self._geo_locks.setdefault(ip, asyncio.Lock())
        async with lock:
            cached = self.geo_cache.get(ip)
            if cached:
                fetched = dt_util.parse_datetime(cached.get("fetched", ""))
                if fetched and dt_util.utcnow() - fetched < GEO_CACHE_TTL:
                    return cached
            try:
                geo = await self._fetch_geo(provider, ip)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Geolocation of %s failed: %s", ip, err)
                return cached
            if geo:
                geo["fetched"] = dt_util.utcnow().isoformat()
                self.geo_cache[ip] = geo
                if len(self.geo_cache) > 2000:  # keep it bounded
                    for old in sorted(self.geo_cache, key=lambda k: self.geo_cache[k].get("fetched", ""))[:500]:
                        self.geo_cache.pop(old, None)
                self._save()
            return geo

    async def _fetch_geo(self, provider: str, ip: str) -> dict[str, Any] | None:
        session = async_get_clientsession(self.hass)
        if provider == GEO_IP_API:
            url = (
                f"http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,regionName,"
                "city,lat,lon,timezone,isp,org,as,reverse,mobile,proxy,hosting"
            )
            async with asyncio.timeout(6):
                resp = await session.get(url)
                j = await resp.json(content_type=None)
            if j.get("status") != "success":
                return None
            return {
                "provider": "ip-api.com",
                "country": j.get("country"),
                "country_code": j.get("countryCode"),
                "region": j.get("regionName"),
                "city": j.get("city"),
                "latitude": j.get("lat"),
                "longitude": j.get("lon"),
                "timezone": j.get("timezone"),
                "isp": j.get("isp"),
                "org": j.get("org"),
                "asn": j.get("as"),
                "reverse_dns": j.get("reverse") or None,
                "is_mobile": j.get("mobile"),
                "is_proxy_or_vpn": j.get("proxy"),
                "is_hosting": j.get("hosting"),
            }
        url = f"https://ipwho.is/{ip}"
        async with asyncio.timeout(6):
            resp = await session.get(url)
            j = await resp.json(content_type=None)
        if not j.get("success"):
            return None
        conn = j.get("connection") or {}
        return {
            "provider": "ipwho.is",
            "country": j.get("country"),
            "country_code": j.get("country_code"),
            "region": j.get("region"),
            "city": j.get("city"),
            "latitude": j.get("latitude"),
            "longitude": j.get("longitude"),
            "timezone": (j.get("timezone") or {}).get("id"),
            "isp": conn.get("isp"),
            "org": conn.get("org"),
            "asn": f"AS{conn['asn']} {conn.get('org') or ''}".strip() if conn.get("asn") else None,
            "reverse_dns": None,
            "is_mobile": None,
            "is_proxy_or_vpn": None,
            "is_hosting": None,
        }

    # ---- successful sign-ins -> IP book ----------------------------------
    @callback
    def record_success_ip(self, ip: str | None, user_name: str, client: str, when: str | None) -> bool:
        """Remember an IP used by a successful sign-in. Returns True if it was new."""
        if not ip:
            return False
        rec = self.ip_book.get(ip)
        new = rec is None
        if new:
            rec = self.ip_book[ip] = {"first_seen": when or dt_util.utcnow().isoformat(), "users": [], "clients": []}
        rec["last_seen"] = max(filter(None, (rec.get("last_seen"), when)), default=when)
        if user_name and user_name not in rec["users"]:
            rec["users"].append(user_name)
        if client and client not in rec["clients"]:
            rec["clients"] = (rec["clients"] + [client])[-10:]
        if new:
            self._save()
        return new

    def ip_is_known(self, ip: str) -> bool:
        return ip in self.ip_book or ip in self.trusted

    # ---- assessment shared by failed and new sign-ins ----------------------
    async def async_assess(self, ip: str | None, user_agent: str | None = None) -> dict[str, Any]:
        """Everything we can say about where a request came from."""
        out: dict[str, Any] = {"ip": ip}
        if not ip:
            out.update(network="unknown", known_ip=False, known_location=None, location=None, geo=None)
            return out
        network = classify_network(ip, [])
        net_label = self.known_network_label(ip)
        geo = await self.async_geolocate(ip) if network == "public" else None
        book = self.ip_book.get(ip)
        trusted = self.trusted.get(ip)

        known_ip = bool(book or trusted) or net_label is not None or network == "loopback"

        distance = None
        known_location: bool | None = None
        if net_label is not None or network == "loopback":
            known_location = True
        elif network in ("lan", "tailscale"):
            known_location = None  # private address outside every known network: no way to place it
        elif geo and geo.get("latitude") is not None:
            distance = haversine_km(
                self.hass.config.latitude, self.hass.config.longitude, geo["latitude"], geo["longitude"]
            )
            near_home = distance <= float(self.options.get(CONF_KNOWN_RADIUS_KM, 50))
            same_city_as_known = any(
                (g := self.geo_cache.get(k)) and g.get("city") == geo.get("city") and g.get("country") == geo.get("country")
                for k in list(self.ip_book) + list(self.trusted)
                if k != ip
            )
            known_location = near_home or same_city_as_known

        ua_info = parse_user_agent(user_agent) if user_agent is not None else None
        fp = device_fingerprint(ua_info) if ua_info else None
        known_device = None
        if fp:
            known_device = any(fp in (t.get("devices") or []) for t in self.trusted.values())

        prior_failures = sum(e.get("attempts", 1) for e in self.active + self.history if e.get("ip") == ip)

        out.update(
            network=network,
            hostname=(geo or {}).get("reverse_dns"),
            location=net_label or location_label(geo) or {"lan": "Private network", "tailscale": "Tailscale", "loopback": "This machine"}.get(network),
            network_label=net_label,
            geo=geo,
            distance_km=distance,
            known_ip=known_ip,
            ip_first_seen=(book or trusted or {}).get("first_seen"),
            ip_seen_with_users=(book or {}).get("users", []),
            ip_trusted_label=(trusted or {}).get("label"),
            known_location=known_location,
            device=ua_info,
            device_fingerprint=fp,
            known_device=known_device,
            prior_failures_from_ip=prior_failures,
        )
        return out

    @staticmethod
    def risk(a: dict[str, Any], attempt_type: str | None = None) -> tuple[str, list[str]]:
        reasons: list[str] = []
        score = 0
        if a.get("network") == "public":
            score += 1
            reasons.append("From the internet")
        if not a.get("known_ip"):
            score += 1
            reasons.append("IP never seen on a successful sign-in")
        if a.get("known_location") is False:
            score += 1
            reasons.append(f"Unfamiliar location ({a.get('location') or 'unknown'})")
        geo = a.get("geo") or {}
        if geo.get("is_hosting"):
            score += 1
            reasons.append("Data-centre / hosting IP (typical of bots)")
        if geo.get("is_proxy_or_vpn"):
            score += 1
            reasons.append("Proxy / VPN / Tor")
        if (a.get("device") or {}).get("is_bot"):
            score += 1
            reasons.append("Automated client / no browser")
        if attempt_type == "password":
            score += 1
            reasons.append("Password was tried")
        if (a.get("prior_failures_from_ip") or 0) >= 3:
            score += 1
            reasons.append("Repeated failures from this IP")
        level = "high" if score >= 4 else "medium" if score >= 2 else "low"
        return level, reasons

    # ---- failed sign-ins --------------------------------------------------
    @callback
    def _handle_failed(self, parsed: dict[str, Any], ts: float) -> None:
        self.hass.async_create_task(self._async_record_failed(parsed, ts))

    async def _async_record_failed(self, parsed: dict[str, Any], ts: float, backfilled: bool = False) -> None:
        when = dt_util.utc_from_timestamp(ts).isoformat()
        ip = parsed["ip"]
        attempt_type, description = describe_request(parsed.get("url"))
        ban_info = self._ban_status(ip)

        existing = next((e for e in self.active if e["ip"] == ip), None)
        if existing:
            existing["attempts"] += 1
            existing["last_attempt"] = when
            if parsed.get("url") and parsed["url"] not in existing["urls"]:
                existing["urls"] = (existing["urls"] + [parsed["url"]])[-MAX_URLS_PER_ENTRY:]
            if attempt_type == "password":
                existing["attempt_type"] = "password"
                existing["description"] = description
            if parsed.get("user_agent") and parsed["user_agent"] not in existing["user_agents"]:
                existing["user_agents"] = (existing["user_agents"] + [parsed["user_agent"]])[-5:]
            existing.update(ban_info)
            existing["risk"], existing["risk_reasons"] = self.risk(existing["assessment"], existing["attempt_type"])
            entry, is_new = existing, False
        else:
            assessment = await self.async_assess(ip, parsed.get("user_agent") or "")
            level, reasons = self.risk(assessment, attempt_type)
            entry = {
                "id": uuid.uuid4().hex[:12],
                "ip": ip,
                "hostname": parsed.get("hostname") or assessment.get("hostname"),
                "first_attempt": when,
                "last_attempt": when,
                "attempts": 1,
                "attempt_type": attempt_type,
                "description": description,
                "urls": [parsed["url"]] if parsed.get("url") else [],
                "user_agents": [parsed["user_agent"]] if parsed.get("user_agent") else [],
                "location": assessment.get("location"),
                "network": assessment.get("network"),
                "known_ip": assessment.get("known_ip"),
                "known_location": assessment.get("known_location"),
                "known_device": assessment.get("known_device"),
                "device": assessment.get("device"),
                "distance_km": assessment.get("distance_km"),
                "isp": (assessment.get("geo") or {}).get("isp"),
                "asn": (assessment.get("geo") or {}).get("asn"),
                "risk": level,
                "risk_reasons": reasons,
                "assessment": assessment,
                "backfilled": backfilled,
                **ban_info,
            }
            self.active.insert(0, entry)
            is_new = True

        self._save()
        self._notify()
        self.hass.bus.async_fire(
            EVENT_FAILED_LOGIN, {**self.public_entry(entry), "is_new": is_new and not backfilled}
        )

    @callback
    def _handle_ban(self, ip: str, ts: float) -> None:
        for e in self.active:
            if e["ip"] == ip:
                e["banned"] = True
                e["banned_at"] = dt_util.utc_from_timestamp(ts).isoformat()
        self._save()
        self._notify()

    def _ban_status(self, ip: str) -> dict[str, Any]:
        """Is HA's own ip_ban on, and how close is this IP to being banned?"""
        info: dict[str, Any] = {"banned": False}
        try:
            from homeassistant.components.http import ban as ban_mod  # noqa: PLC0415

            app = self.hass.http.app
            key_mgr = getattr(ban_mod, "KEY_BAN_MANAGER", None)
            mgr = app.get(key_mgr) if key_mgr is not None else None
            info["ha_ip_ban_enabled"] = mgr is not None
            if mgr is not None:
                addr = ipaddress.ip_address(ip)
                info["banned"] = addr in getattr(mgr, "ip_bans_lookup", {})
                if (k := getattr(ban_mod, "KEY_FAILED_LOGIN_ATTEMPTS", None)) is not None:
                    info["ha_failed_count"] = app.get(k, {}).get(addr, 0)
                if (k := getattr(ban_mod, "KEY_LOGIN_THRESHOLD", None)) is not None:
                    info["ha_ban_threshold"] = app.get(k)
        except Exception:  # noqa: BLE001 - internals may move between versions
            pass
        return info

    @staticmethod
    def public_entry(e: dict[str, Any]) -> dict[str, Any]:
        """Entry without the bulky raw assessment (for events and attributes)."""
        return {k: v for k, v in e.items() if k != "assessment"}

    # ---- clearing / history ---------------------------------------------
    @callback
    def clear(self, entry_id: str | None, ip: str | None, user_name: str | None, reason: str = "cleared") -> int:
        keep, moved = [], []
        for e in self.active:
            if (entry_id is None and ip is None) or e["id"] == entry_id or e["ip"] == ip:
                moved.append({**e, "cleared_at": dt_util.utcnow().isoformat(), "cleared_by": user_name, "cleared_reason": reason})
            else:
                keep.append(e)
        if moved:
            self.active = keep
            self.history = moved + self.history
            self._prune_history()
            self._save()
            self._notify()
        return len(moved)

    @callback
    def clear_history(self, older_than_days: int | None) -> int:
        before = len(self.history)
        if older_than_days is None:
            self.history = []
        else:
            cutoff = dt_util.utcnow() - timedelta(days=older_than_days)
            self.history = [e for e in self.history if (_dt(e.get("cleared_at")) or cutoff) >= cutoff]
        removed = before - len(self.history)
        if removed:
            self._save()
            self._notify()
        return removed

    def _prune_history(self) -> None:
        days = int(self.options.get(CONF_HISTORY_DAYS, 90))
        cutoff = dt_util.utcnow() - timedelta(days=days)
        self.history = [e for e in self.history if (_dt(e.get("cleared_at") or e.get("last_attempt")) or cutoff) >= cutoff]
        self.history = self.history[: int(self.options.get(CONF_HISTORY_MAX, 500))]

    @callback
    def maintenance(self) -> None:
        """Hourly: auto-archive stale active entries, enforce history retention."""
        archive_days = int(self.options.get(CONF_AUTO_ARCHIVE_DAYS, 0))
        if archive_days > 0:
            cutoff = dt_util.utcnow() - timedelta(days=archive_days)
            stale = [e["id"] for e in self.active if (_dt(e.get("last_attempt")) or cutoff) < cutoff]
            for eid in stale:
                self.clear(eid, None, None, reason="auto_archived")
        before = len(self.history)
        self._prune_history()
        if len(self.history) != before:
            self._save()
            self._notify()

    # ---- trust ------------------------------------------------------------
    @callback
    def trust_ip(self, ip: str, label: str | None) -> None:
        ipaddress.ip_address(ip)  # validate
        devices = sorted({
            e["assessment"].get("device_fingerprint")
            for e in self.active + self.history
            if e.get("ip") == ip and e.get("assessment", {}).get("device_fingerprint")
        })
        rec = self.trusted.setdefault(ip, {"first_seen": dt_util.utcnow().isoformat()})
        rec["label"] = label or rec.get("label")
        rec["devices"] = sorted(set(rec.get("devices", [])) | set(devices))
        for e in self.active + self.history:
            if e.get("ip") == ip:
                e["known_ip"] = True
                e["known_device"] = True
        self._save()
        self._notify()

    @callback
    def forget_ip(self, ip: str) -> bool:
        found = self.trusted.pop(ip, None) is not None
        found = self.ip_book.pop(ip, None) is not None or found
        if found:
            self._save()
            self._notify()
        return found


def _dt(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None
