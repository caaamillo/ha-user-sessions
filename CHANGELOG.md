# Changelog

## 0.9.0
- Sidebar panel: pick any installed theme for it (Configure → Sidebar panel theme)
- Card / panel: sign-ins can show search, a person picker and *where* / *type* filter chips with counts (`filters: true`), list long-lived tokens (`show_tokens: true`) and start pre-filtered (`where:`, `type:`). Where filters come from your own labelled known networks. All in the visual editor
- Sign-ins now carry `client_type` (browser, app, connector, token)
- Card: fills follow any theme (tiles were invisible on themes with translucent cards)

## 0.8.0
- Optional **sidebar panel** (admins only): Configure → Sidebar panel & dashboard card → Show User Sessions in the sidebar. Overview with every tile, failed sign-ins and sign-ins by person on one page; works on phones
- Card: badges wrap instead of being cut off on narrow screens; tile titles wrap

## 0.7.0
- Built-in phone notifications (opt-in): pick Companion-app phones in Configure → Phone notifications. New sign-ins (Sign it out), failed sign-ins with a minimum risk (It was me / Clear) and IP bans. Buttons only act for admins. Nothing is sent until a phone is picked
- Card: summary tiles are now opt-in checkboxes in the visual card editor (or `tiles:` in YAML). New tiles: from the internet, over Tailscale, newest sign-in, last activity, IP-ban status; existing tiles show more detail (of N accounts, unused for 30+ days, held by N accounts, last failed attempt)
- Card: visual editor for view, title, rows and tiles
- Sign-ins now carry their network type and known-network label (`network`, `network_label`)

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
