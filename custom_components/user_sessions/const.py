"""Constants for the User Sessions integration."""
from datetime import timedelta

DOMAIN = "user_sessions"
PLATFORMS = ["sensor"]

STORAGE_KEY = "user_sessions.seen"
STORAGE_VERSION = 1
SECURITY_STORAGE_KEY = "user_sessions.security"
SECURITY_STORAGE_VERSION = 1

EVENT_NEW_SESSION = "user_sessions_new_session"
EVENT_FAILED_LOGIN = "user_sessions_failed_login"
EVENT_IP_BANNED = "user_sessions_ip_banned"

SERVICE_SIGN_OUT_USER = "sign_out_user"
SERVICE_SIGN_OUT_SESSION = "sign_out_session"
SERVICE_CLEAR_FAILED = "clear_failed_sign_ins"
SERVICE_CLEAR_HISTORY = "clear_history"
SERVICE_TRUST_IP = "trust_ip"
SERVICE_FORGET_IP = "forget_ip"

ATTR_USER_ID = "user_id"
ATTR_USER_NAME = "user_name"
ATTR_SESSION_ID = "session_id"
ATTR_INCLUDE_LONG_LIVED = "include_long_lived"
ATTR_ENTRY_ID = "entry_id"
ATTR_IP = "ip"
ATTR_LABEL = "label"
ATTR_OLDER_THAN_DAYS = "older_than_days"

# ---- options -------------------------------------------------------------
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TRACK_FAILED = "track_failed"
CONF_GEO_PROVIDER = "geo_provider"
CONF_KNOWN_RADIUS_KM = "known_radius_km"
CONF_LAN_IS_KNOWN = "lan_is_known"
CONF_TAILSCALE_IS_KNOWN = "tailscale_is_known"
CONF_TRUSTED_NETWORKS = "trusted_networks"
CONF_HISTORY_DAYS = "history_retention_days"
CONF_HISTORY_MAX = "history_max_entries"
CONF_AUTO_ARCHIVE_DAYS = "auto_archive_days"
CONF_DISMISS_HA_NOTIFICATION = "dismiss_ha_notification"
# v0.4: editable lists, one entry per line: "<CIDR or IP> <optional label>"
CONF_KNOWN_NETWORKS = "known_networks"
CONF_KNOWN_IPS = "known_ips"  # form-only: saved into the security store as trusted IPs
TAILSCALE_RANGE = "100.64.0.0/10"
# v0.5: optional dashboard card served by the integration itself
CONF_ENABLE_CARD = "enable_card"
CARD_VERSION = "0.6.0"
CARD_URL = "/user_sessions/user-sessions-card.js"
# v0.6: local device (MAC) lookup and a block prompt when HA bans an IP
CONF_LOOKUP_MAC = "lookup_mac"
CONF_NOTIFY_ON_BAN = "notify_on_ban"
BAN_NOTIFICATION_PREFIX = "user_sessions_ban_"
HA_BAN_NOTIFICATION_ID = "ip-ban"
ISSUE_IP_BAN_OFF = "ip_ban_off"
HTTP_SETTINGS_URL = "homeassistant://config/network"

GEO_NONE = "none"
GEO_IP_API = "ip_api"
GEO_IPWHO = "ipwho_is"
GEO_PROVIDERS = [GEO_IP_API, GEO_IPWHO, GEO_NONE]

DEFAULTS = {
    CONF_SCAN_INTERVAL: 30,
    CONF_TRACK_FAILED: True,
    CONF_GEO_PROVIDER: GEO_IP_API,
    CONF_KNOWN_RADIUS_KM: 50,
    CONF_LAN_IS_KNOWN: True,
    CONF_TAILSCALE_IS_KNOWN: True,
    CONF_TRUSTED_NETWORKS: "",
    CONF_HISTORY_DAYS: 90,
    CONF_HISTORY_MAX: 500,
    CONF_AUTO_ARCHIVE_DAYS: 0,
    CONF_DISMISS_HA_NOTIFICATION: True,
    CONF_ENABLE_CARD: False,
    CONF_LOOKUP_MAC: True,
    CONF_NOTIFY_ON_BAN: True,
    CONF_KNOWN_NETWORKS: None,  # None = not set yet: built from the LAN adapters + Tailscale on first start
}

GEO_CACHE_TTL = timedelta(days=7)
MAINTENANCE_INTERVAL = timedelta(hours=1)
MAX_URLS_PER_ENTRY = 10
HA_LOGIN_NOTIFICATION_ID = "http-login"
