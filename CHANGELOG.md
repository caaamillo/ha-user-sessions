# Changelog

## 0.6.1
- First release validated by the full HACS action now the repository is public; no functional changes

## 0.6.0
- MAC address, device name and maker for failed sign-ins from your own network (ARP table, any router integration's device trackers, device registry; vendor via the IEEE OUI list)
- Learns which MACs have signed in successfully, so a known device is flagged
- When Home Assistant's IP ban fires: a notification with steps to block the device on your own router/firewall, and a new `user_sessions_ip_banned` event. Nothing is blocked by the integration itself
- Card: MAC / network device rows, a BANNED badge and the block steps
- Shows Home Assistant's IP-ban status in the Configure screen with a link to the HTTP server settings, and raises a Repairs notice while banning is off or unlimited
- `ha_ip_ban_enabled` / `ha_ban_threshold` attributes on the Failed sign-ins sensor
- Fix: "unlimited login attempts" was reported as banning being on
- Fix: rapid repeat attempts from one IP could create duplicate entries

## 0.5.0
- Optional, theme-aware dashboard card (`custom:user-sessions-card`) bundled with the integration; turn it on in options (off by default). Views: failed (Active / History), sessions, summary
- Integration icon (`brand/`, Home Assistant 2026.3+)
- Install instructions for the built-in Marketplace (HA 2026.11+)
- CI: full HACS validation including the brands check (the icon ships in `brand/`)
- Notification example no longer shows "on ?" when the attacker's OS is unknown

## 0.4.0
- Options form with sections; editable **Known networks** and **Known IPs** lists (pre-filled, labelled)
- Known networks replace the LAN / Tailscale toggles (migrated automatically)
- Immediate store flush on unload so reloads never lose a change

## 0.3.0
- Failed sign-in capture with geolocation, device parsing, known IP / location / device and risk score
- Active register + history with retention and auto-archive
- New services: clear_failed_sign_ins, clear_history, trust_ip, forget_ip
- Options flow; new-session events enriched with location and first-time-IP flag
- Back-fill of failed sign-ins logged before start-up

## 0.2.0
- Long-lived tokens tracked and announced

## 0.1.0
- Per-user sign-in sensors, new sign-in event, sign-out actions
