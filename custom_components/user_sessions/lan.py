"""Find the hardware (MAC) address behind a local IP address.

A MAC address never crosses a router, so this only works for requests from the same local
network segment as Home Assistant. Internet, Tailscale and reverse-proxy traffic shows the
router's or proxy's address, never the sender's device, so we don't try.

Sources, in order (nothing here is tied to one brand of network gear):
  1. The kernel neighbour (ARP) table. Home Assistant has just answered the request, so a device
     on the same segment is almost always in it.
  2. Any device_tracker entity exposing matching `ip` and `mac` attributes (UniFi, FRITZ!Box,
     OpenWrt, ASUSWRT, Nmap and most other router integrations do).
  3. The device registry, to name a device Home Assistant already knows by its MAC.
The vendor comes from the IEEE OUI list when the optional `aiooui` package is available.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

_LOGGER = logging.getLogger(__name__)

ARP_PATH = "/proc/net/arp"
_MAC_RE = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.I)
_NULL_MAC = "00:00:00:00:00:00"


def normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    mac = mac.strip().lower().replace("-", ":")
    if not _MAC_RE.match(mac) or mac == _NULL_MAC:
        return None
    return mac


def is_randomized(mac: str) -> bool:
    """Locally administered bit set: a private / randomised address (phones, laptops)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except ValueError:
        return False


def parse_arp_table(text: str) -> dict[str, str]:
    """{ip: mac} from /proc/net/arp, complete entries only (flag 0x2)."""
    out: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        ip, _hw, flags, mac = parts[:4]
        try:
            complete = int(flags, 16) & 0x2
        except ValueError:
            continue
        if complete and (mac := normalize_mac(mac)):
            out[ip] = mac
    return out


def _read_arp() -> dict[str, str]:
    try:
        with open(ARP_PATH, encoding="ascii") as f:
            return parse_arp_table(f.read())
    except OSError:
        return {}


async def _vendor(mac: str) -> str | None:
    if is_randomized(mac):
        return None  # random addresses have no registered vendor
    try:
        import aiooui  # noqa: PLC0415 - optional

        if not aiooui.is_loaded():
            await aiooui.async_load()
        return aiooui.get_vendor(mac)
    except Exception:  # noqa: BLE001 - optional enrichment
        return None


async def async_lookup(hass: HomeAssistant, ip: str) -> dict[str, Any] | None:
    """Everything we can find about the device using `ip` on the local network."""
    mac: str | None = None
    source: str | None = None
    name: str | None = None
    tracker: str | None = None

    arp = await hass.async_add_executor_job(_read_arp)
    if ip in arp:
        mac, source = arp[ip], "arp"

    for st in hass.states.async_all("device_tracker"):
        a = st.attributes
        t_mac = normalize_mac(a.get("mac"))
        if not t_mac:
            continue
        if (mac and t_mac == mac) or (not mac and a.get("ip") == ip):
            mac = mac or t_mac
            source = source or "device_tracker"
            tracker = st.entity_id
            name = a.get("friendly_name") or a.get("host_name") or a.get("hostname")
            break

    if not mac:
        return None

    ha_device = None
    try:
        dev = dr.async_get(hass).async_get_device(connections={(dr.CONNECTION_NETWORK_MAC, mac)})
        if dev is not None:
            ha_device = dev.name_by_user or dev.name
            name = name or ha_device
    except Exception:  # noqa: BLE001
        pass

    return {
        "mac": mac,
        "vendor": await _vendor(mac),
        "name": name,
        "randomized": is_randomized(mac),
        "source": source,
        "tracker": tracker,
        "ha_device": ha_device,
    }
