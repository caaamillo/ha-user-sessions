// User Sessions card — ships with the User Sessions integration (opt-in: Configure → Dashboard card).
//   type: custom:user-sessions-card
//   view: failed | sessions | summary      (default: failed)
//   title: optional heading
//   max: rows before "Show all" (default 6)
//   tiles: summary tiles to show, in order (summary view; default accounts, signins, tokens, failed, known_ips)
//          optional extras: internet, tailscale, newest, last_activity, ip_ban
//   filters: true — sessions view: search, person picker, where / type chips   show_tokens: true — list long-lived tokens too
//   where / type: filters to start with (where = a network label such as "Home network"; type = browser|app|connector|token)
// Everything can also be set in the visual card editor.
// Theme-aware: uses Home Assistant's own CSS variables, so it follows whatever theme you run.
(() => {
  if (customElements.get('user-sessions-card')) return;
  const VERSION = '0.9.0';
  const FS = 'sensor.user_sessions_failed_sign_ins', FH = 'sensor.user_sessions_failed_sign_in_history';
  const RISK = { high: 'var(--error-color, #db4437)', medium: 'var(--warning-color, #ffa600)', low: 'var(--success-color, #43a047)' };
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ago = iso => {
    const t = Date.parse(iso); if (!iso || isNaN(t)) return '–';
    const s = (Date.now() - t) / 1000;
    if (s < 60) return 'just now'; if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return Math.round(s / 3600) + ' h ago'; const d = Math.round(s / 86400);
    return d === 1 ? 'yesterday' : d < 60 ? d + ' days ago' : Math.round(d / 30) + ' months ago';
  };
  const netLabel = n => ({ public: 'Internet', lan: 'Private', tailscale: 'Tailscale', loopback: 'Local' }[n] || n || '?');
  const TILES = {
    accounts: 'Accounts signed in', signins: 'Sign-ins in use', tokens: 'Long-lived tokens', failed: 'Failed sign-ins',
    known_ips: 'Known IPs', internet: 'From the internet', tailscale: 'Over Tailscale', newest: 'Newest sign-in',
    last_activity: 'Last activity', ip_ban: 'IP ban status',
  };
  const DEFAULT_TILES = ['accounts', 'signins', 'tokens', 'failed', 'known_ips'];
  const SURL = v => (v || '').replace(/^Browser via /, '');
  const TYPES = { browser: 'Browser', app: 'App', connector: 'Connector', token: 'Token' };
  const NETOF = x => x.network || (x.last_ip ? (/^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(x.last_ip) ? 'tailscale' : /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(x.last_ip) ? 'lan' : 'public') : null);
  const typeOf = s => s.client_type || (s.kind === 'long_lived' ? 'token' : (s.via || '').startsWith('Companion') ? 'app' : 'browser');
  const whereOf = s => !s.last_ip ? (s.kind === 'long_lived' && s.last_used ? 'Token' : 'Not used yet') : (s.network_label || ({ public: 'Internet', lan: 'Local network', tailscale: 'Tailscale', loopback: 'This machine' }[NETOF(s)] || 'Unknown'));
  const dev = d => d && d.browser ? (d.is_bot ? 'Bot / script' : d.browser + (d.os ? ' on ' + d.os : '')) : 'No browser info';

  class UserSessionsCard extends HTMLElement {
    static getStubConfig() { return { view: 'failed' }; }
    static getConfigElement() { return document.createElement('user-sessions-card-editor'); }
    setConfig(c) { this._c = Object.assign({ view: 'failed', max: 6 }, c || {}); this._sig = null; if (this._h) this._render(); }
    getCardSize() { return this._c && this._c.view === 'summary' ? 3 : 5; }
    getGridOptions() { return { columns: 12, min_columns: 6, rows: 'auto' }; }
    connectedCallback() { this._tick = setInterval(() => { this._sig = null; this._render(); }, 60000); }
    disconnectedCallback() { clearInterval(this._tick); clearTimeout(this._armT); }
    set hass(h) {
      this._h = h;
      const keys = Object.keys(h.states).filter(k => k.startsWith('sensor.user_sessions_'));
      const sig = keys.map(k => h.states[k].last_updated).join('|');
      if (sig === this._sig) return;
      this._sig = sig; this._render();
    }
    _a(e, a) { const s = this._h.states[e]; return s ? s.attributes[a] : undefined; }
    _arm(key) {
      if (this._armed === key) { this._armed = null; clearTimeout(this._armT); return true; }
      this._armed = key; this._render(true); clearTimeout(this._armT);
      this._armT = setTimeout(() => { this._armed = null; this._render(true); }, 4000);
      return false;
    }
    _call(svc, data) { return this._h.callService('user_sessions', svc, data).catch(e => alert(`That didn't work: ${e.message || e}`)); }
    _users() {
      return Object.values(this._h.states).filter(s => s.entity_id.startsWith('sensor.user_sessions_') && s.attributes.user_id)
        .map(s => ({ id: s.attributes.user_id, name: s.attributes.user_name, admin: s.attributes.is_admin, sessions: s.attributes.sessions || [], tokens: s.attributes.long_lived_tokens || [] }))
        .sort((a, b) => (b.admin - a.admin) || (b.sessions.length - a.sessions.length) || String(a.name).localeCompare(String(b.name)));
    }

    _render(force) {
      if (!this._h || !this._c) return;
      if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
      const v = this._c.view, missing = !this._h.states[FS];
      const body = missing ? `<div class="empty"><ha-icon icon="mdi:alert-circle-outline"></ha-icon><div><b>User Sessions isn't loaded</b><div class="sub">Add the integration under Settings → Devices &amp; services.</div></div></div>`
        : v === 'sessions' ? this._sessions() : v === 'summary' ? this._summary() : this._failed();
      this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card>${body}</ha-card>`;
      this._bind();
    }

    // ---------- failed sign-ins ----------
    _failed() {
      const c = this._c, tab = this._tab === 'history' ? 'history' : 'active';
      const act = this._a(FS, 'entries') || [], hist = this._a(FH, 'entries') || [];
      const histN = parseInt(this._h.states[FH]?.state) || hist.length;
      const list = tab === 'history' ? hist : act;
      const head = `<div class="head"><div class="title"><ha-icon icon="mdi:shield-alert-outline"></ha-icon>${esc(c.title || 'Failed sign-ins')}</div>
        <div class="seg"><button class="${tab === 'active' ? 'on' : ''}" data-tab="active">Active${act.length ? ' · ' + act.length : ''}</button><button class="${tab === 'history' ? 'on' : ''}" data-tab="history">History${histN ? ' · ' + histN : ''}</button></div></div>`;
      if (!list.length) {
        const [ic, t, s] = tab === 'history' ? ['mdi:history', 'No history yet', `Cleared entries are kept for ${this._a(FH, 'retention_days') || 90} days`] : ['mdi:shield-check-outline', 'No failed sign-ins', 'Wrong passwords and rejected tokens show up here'];
        return head + `<div class="empty ok"><ha-icon icon="${ic}"></ha-icon><div><b>${t}</b><div class="sub">${s}</div></div></div>`;
      }
      const tot = act.reduce((a, e) => a + (e.attempts || 1), 0);
      const sum = tab === 'active'
        ? `${tot} attempt${tot === 1 ? '' : 's'} from ${act.length} IP${act.length === 1 ? '' : 's'} · ${act.filter(e => !e.known_ip).length} never seen before`
        : `${histN} cleared · kept ${this._a(FH, 'retention_days') || 90} days`;
      const max = c.max || 6, shown = this._open ? list : list.slice(0, max);
      const yn = (v, y, n) => v === true ? `<span class="yes">${y}</span>` : v === false ? `<span class="no">${n}</span>` : '<span>Unknown</span>';
      const rows = shown.map(e => {
        const exp = this._exp === e.id, rc = RISK[e.risk] || 'var(--secondary-text-color)';
        const when = tab === 'history' ? e.cleared_at : e.last_attempt;
        const det = !exp ? '' : `<div class="det"><div class="grid">
            <span>Seen this IP before</span>${yn(e.known_ip, 'Yes', 'Never')}
            <span>Known location</span>${yn(e.known_location, 'Yes', 'No')}
            <span>Known device</span>${yn(e.known_device, 'Yes', 'No')}
            ${e.mac ? `<span>MAC address</span><span><code>${esc(e.mac)}</code>${e.lan_device?.randomized ? ' <span class="pill">private</span>' : ''}${e.known_mac ? ' <span class="yes">seen before</span>' : ''}</span>` : ''}
            ${e.lan_device && (e.lan_device.name || e.lan_device.vendor) ? `<span>Network device</span><span>${esc([e.lan_device.name, e.lan_device.vendor].filter(Boolean).join(' · '))}</span>` : ''}
            ${e.isp ? `<span>Provider</span><span>${esc(e.isp)}</span>` : ''}
            ${e.distance_km != null ? `<span>From home</span><span>${Math.round(e.distance_km).toLocaleString()} km</span>` : ''}
            ${(e.urls || []).length ? `<span>Requested</span><code>${esc(e.urls.join(', '))}</code>` : ''}
            ${(e.user_agents || []).length ? `<span>User agent</span><code>${esc(e.user_agents[0])}</code>` : ''}
            <span>HA IP ban</span><span>${e.banned ? 'Banned' : e.ha_ip_ban_enabled && e.ha_ban_threshold ? 'Bans after ' + esc(e.ha_ban_threshold) + ' failures' : 'Off · <a href="/config/network">turn on</a>'}</span>
            ${tab === 'history' ? `<span>Cleared</span><span>${e.cleared_reason === 'auto_archived' ? 'Auto-archived' : 'By ' + esc(e.cleared_by || '?')} · ${ago(e.cleared_at)}</span>` : ''}
          </div>${e.banned && (e.block_advice || []).length ? `<div class="advice"><b>Block it on your network</b><ul>${e.block_advice.map(a => `<li>${esc(a)}</li>`).join('')}</ul></div>` : ''}${(e.risk_reasons || []).length ? `<div class="chips">${e.risk_reasons.map(r => `<span>${esc(r)}</span>`).join('')}</div>` : ''}
          <div class="btns">${e.known_ip ? '' : `<button class="btn" data-trust="${esc(e.ip)}">${this._armed === 'trust:' + e.ip ? 'Tap again to trust' : 'It was me · trust IP'}</button>`}${tab === 'active' ? `<button class="btn" data-clear1="${esc(e.id)}">Move to history</button>` : ''}</div></div>`;
        return `<div class="row ${exp ? 'open' : ''}" data-exp="${esc(e.id)}" tabindex="0" role="button" aria-expanded="${exp}">
            <div class="ic" style="color:${rc};background:color-mix(in srgb, ${rc} 16%, transparent)"><ha-icon icon="mdi:${e.attempt_type === 'password' ? 'form-textbox-password' : e.network === 'public' ? 'earth' : 'shield-alert-outline'}"></ha-icon></div>
            <div class="txt"><div class="t">${esc(e.ip)} <span class="pill">${esc(netLabel(e.network))}</span><span class="pill" style="color:${rc};background:color-mix(in srgb, ${rc} 16%, transparent)">${esc((e.risk || '?').toUpperCase())}</span>${e.banned ? '<span class="pill ban">BANNED</span>' : ''}</div>
              <div class="sub">${esc([e.location, e.lan_device?.name, e.description, dev(e.device), e.attempts > 1 ? e.attempts + ' attempts' : ''].filter(Boolean).join(' · '))}</div></div>
            <div class="when">${ago(when)}</div></div>${det}`;
      }).join('');
      const more = list.length > max ? `<button class="btn" data-more>${this._open ? 'Show fewer' : 'Show all ' + list.length}</button>` : '';
      const clr = tab === 'active'
        ? `<button class="btn warn" data-clearall>${this._armed === 'all' ? 'Tap again to confirm' : 'Move all to history'}</button>`
        : `<button class="btn warn" data-clearhist>${this._armed === 'hist' ? 'Tap again to delete' : 'Clear history'}</button>`;
      return head + `<div class="summary">${sum}</div><div class="rows">${rows}</div><div class="foot">${more}<span class="grow"></span>${clr}</div>`;
    }

    // ---------- sign-ins by person (optional search & filters) ----------
    _rows() {
      const tok = this._c.show_tokens;
      return this._users().flatMap(u => [
        ...u.sessions.map(s => ({ ...s, uid: u.id, user: u.name })),
        ...(tok ? u.tokens.map(s => ({ ...s, uid: u.id, user: u.name, kind: 'long_lived' })) : []),
      ]);
    }
    _sessions() {
      const c = this._c, users = this._users(), all = this._rows();
      const F = this._f || (this._f = { q: '', where: c.where || 'all', type: c.type || 'all', person: 'all' });
      if (F.person !== 'all' && !users.some(u => u.id === F.person)) F.person = 'all';
      const q = c.filters ? F.q.trim().toLowerCase() : '';
      const ok = (s, o) => {
        const f = o ? { ...F, ...o } : F;
        return (f.where === 'all' || whereOf(s) === f.where) && (f.type === 'all' || typeOf(s) === f.type) && (f.person === 'all' || s.uid === f.person)
          && (!q || [s.user, s.client_name, s.via, s.last_ip, whereOf(s), TYPES[typeOf(s)]].some(v => String(v || '').toLowerCase().includes(q)));
      };
      const shown = all.filter(s => ok(s)), filtered = !!q || F.where !== 'all' || F.type !== 'all' || F.person !== 'all';
      const chips = (grp, opts) => opts.map(([v, l]) => {
        const n = v === 'all' ? null : all.filter(s => ok(s, { [grp]: v })).length;
        if (n === 0 && F[grp] !== v) return '';
        return `<button class="fc ${F[grp] === v ? 'on' : ''}" data-fc="${grp}" data-fv="${esc(v)}" aria-pressed="${F[grp] === v}">${esc(l)}${n != null ? `<span class="n">${n}</span>` : ''}</button>`;
      }).join('');
      const wheres = [...new Set(all.map(whereOf))].sort((a, b) => all.filter(s => whereOf(s) === b).length - all.filter(s => whereOf(s) === a).length);
      const ppl = users.filter(u => u.sessions.length || (c.show_tokens && u.tokens.length));
      const bar = !c.filters ? '' : `<div class="tb"><label class="sq"><ha-icon icon="mdi:magnify"></ha-icon><input data-q type="search" placeholder="Search device, app, IP or person" value="${esc(F.q)}" aria-label="Search sign-ins"></label>`
        + `<select class="psel" data-person aria-label="Person"><option value="all">Everyone</option>${ppl.map(u => `<option value="${esc(u.id)}" ${F.person === u.id ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select></div>`
        + `<div class="tb">${chips('where', [['all', 'Anywhere'], ...wheres.map(w => [w, w])])}</div>`
        + `<div class="tb">${chips('type', [['all', 'Any type'], ...Object.entries(TYPES)])}${filtered ? '<button class="lnk" data-fx>Clear filters</button>' : ''}</div>`;
      const icon = s => ({ token: 'mdi:key-variant', app: 'mdi:cellphone', connector: 'mdi:robot-outline' }[typeOf(s)] || 'mdi:monitor');
      const row = s => { const t = typeOf(s) === 'token';
        return `<div class="row flat"><div class="ic"><ha-icon icon="${icon(s)}"></ha-icon></div>
          <div class="txt"><div class="t">${esc(s.client_name || (t ? 'Unnamed token' : SURL(s.via)))}</div><div class="sub">${esc([whereOf(s), s.last_ip, s.last_used ? ago(s.last_used) : 'never used'].filter(Boolean).join(' · '))}</div></div>
          <button class="btn warn" data-out="${esc(s.session_id)}">${this._armed === 'out:' + s.session_id ? 'Confirm' : t ? 'Revoke' : 'Sign out'}</button></div>`; };
      const groups = users.map(u => {
        const rows = shown.filter(s => s.uid === u.id);
        if (!rows.length) return '';
        const n = rows.filter(s => typeOf(s) !== 'token').length, k = rows.length - n;
        return `<div class="grp"><div class="gname">${esc(u.name)}${u.admin ? '<span class="pill">Admin</span>' : ''}<span class="grow"></span><span class="sub">${[n ? n + ' sign-in' + (n === 1 ? '' : 's') : '', k ? k + ' token' + (k === 1 ? '' : 's') : ''].filter(Boolean).join(' · ')}</span></div>${rows.map(row).join('')}</div>`;
      }).join('');
      const tot = filtered ? `${shown.length} of ${all.length} shown` : `${all.length} ${c.show_tokens ? 'sign-ins and tokens' : 'sign-ins'}`;
      const none = `<div class="empty ok"><ha-icon icon="mdi:account-off-outline"></ha-icon><div><b>${filtered ? 'No sign-ins match these filters' : 'Nobody is signed in'}</b></div></div>`;
      return `<div class="head"><div class="title"><ha-icon icon="mdi:account-key-outline"></ha-icon>${esc(c.title || 'Sign-ins')}</div><span class="sub">${esc(tot)}</span></div>${bar}${groups || none}`;
    }

    // ---------- summary tiles ----------
    _summary() {
      const users = this._users(), all = users.flatMap(u => u.sessions.map(x => ({ ...x, user: u.name }))), toks = users.flatMap(u => u.tokens);
      const act = this._a(FS, 'entries') || [], hi = act.filter(e => e.risk === 'high').length;
      const known = this._h.states['sensor.user_sessions_known_ips'];
      const total = this._a('sensor.user_sessions_total_user_sessions', 'users');
      const net = x => x.network || (x.last_ip ? (/^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(x.last_ip) ? 'tailscale' : /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(x.last_ip) ? 'lan' : 'public') : null);
      const outside = all.filter(x => net(x) === 'public' && !x.network_label), ts = all.filter(x => net(x) === 'tailscale');
      const stale = all.filter(x => !x.last_used || Date.now() - Date.parse(x.last_used) > 30 * 864e5).length;
      const by = (k) => all.filter(x => x[k]).sort((a, b) => Date.parse(b[k]) - Date.parse(a[k]))[0];
      const newest = by('created'), last = by('last_used');
      const where = x => x ? [SURL(x.client_name || x.via), x.network_label || netLabel(net(x)), ago(x.created)].filter(Boolean).join(' · ') : '';
      const ban = act[0] || {}, banOn = ban.ha_ip_ban_enabled ?? this._a(FS, 'ha_ip_ban_enabled'), thr = ban.ha_ban_threshold ?? this._a(FS, 'ha_ban_threshold');
      const T = {
        accounts: () => ['mdi:account-group-outline', users.filter(u => u.sessions.length).length, total ? `of ${total} accounts` : 'accounts signed in'],
        signins: () => ['mdi:login-variant', all.length, `${stale} unused for 30+ days`],
        tokens: () => ['mdi:key-chain-variant', toks.length, `held by ${users.filter(u => u.tokens.length).length} account${users.filter(u => u.tokens.length).length === 1 ? '' : 's'}`],
        failed: () => ['mdi:shield-alert-outline', act.length, act.length ? (hi ? `${hi} high risk · ` : '') + `last ${ago(act[0].last_attempt)} · ${act[0].ip}` : 'none to review', act.length > 0],
        known_ips: () => ['mdi:ip-network-outline', known ? known.state : '–', 'trusted or seen signing in'],
        internet: () => ['mdi:earth', outside.length, outside.length ? [...new Set(outside.map(x => x.user))].join(', ') : 'none outside your known networks', outside.length > 0],
        tailscale: () => ['mdi:vpn', ts.length, ts.length ? 'remote access via your tailnet' : 'none right now'],
        newest: () => ['mdi:account-plus-outline', newest ? newest.user : '–', where(newest)],
        last_activity: () => ['mdi:account-clock-outline', last ? last.user : '–', last ? [SURL(last.client_name || last.via), ago(last.last_used)].join(' · ') : ''],
        ip_ban: () => ['mdi:shield-lock-outline', banOn ? `On · ${thr}` : 'Off', banOn ? `bans after ${thr} failed sign-ins` : 'turn on in Network → HTTP server', !banOn],
      };
      const keys = (Array.isArray(this._c.tiles) && this._c.tiles.length ? this._c.tiles : DEFAULT_TILES).filter(k => T[k]);
      const tile = k => { const [ic, big, sub, warn] = T[k]();
        return `<div class="tile ${warn ? 'bad' : ''}"><div class="tl"><ha-icon icon="${ic}"></ha-icon>${esc(TILES[k])}</div><div class="big">${esc(big)}</div><div class="sub">${esc(sub)}</div></div>`; };
      return `<div class="head"><div class="title"><ha-icon icon="mdi:shield-account-outline"></ha-icon>${esc(this._c.title || 'User sessions')}</div></div><div class="tiles">${keys.map(tile).join('')}</div>`;
    }

    _bind() {
      const r = this.shadowRoot, on = (sel, fn) => r.querySelectorAll(sel).forEach(el => el.addEventListener('click', ev => { ev.stopPropagation(); fn(el); }));
      on('[data-tab]', el => { this._tab = el.dataset.tab; this._exp = null; this._open = false; this._armed = null; this._render(); });
      r.querySelectorAll('[data-exp]').forEach(el => {
        const t = () => { this._exp = this._exp === el.dataset.exp ? null : el.dataset.exp; this._render(); };
        el.addEventListener('click', t); el.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); t(); } });
      });
      on('[data-more]', () => { this._open = !this._open; this._render(); });
      on('[data-trust]', el => { const ip = el.dataset.trust; if (this._arm('trust:' + ip)) this._call('trust_ip', { ip, label: 'Trusted from dashboard' }).then(() => this._call('clear_failed_sign_ins', { ip })); });
      on('[data-clear1]', el => this._call('clear_failed_sign_ins', { entry_id: el.dataset.clear1 }));
      on('[data-clearall]', () => { if (this._arm('all')) this._call('clear_failed_sign_ins', {}); });
      on('[data-clearhist]', () => { if (this._arm('hist')) this._call('clear_history', {}); });
      on('[data-fc]', el => { this._f[el.dataset.fc] = this._f[el.dataset.fc] === el.dataset.fv ? 'all' : el.dataset.fv; this._render(); });
      on('[data-fx]', () => { Object.assign(this._f, { q: '', where: 'all', type: 'all', person: 'all' }); this._render(); });
      r.querySelectorAll('[data-person]').forEach(el => el.addEventListener('change', () => { this._f.person = el.value; this._render(); }));
      r.querySelectorAll('[data-q]').forEach(el => {
        el.addEventListener('input', () => { this._f.q = el.value; this._qPos = el.selectionStart; this._render(); });
        if (this._qPos != null) { el.focus(); try { el.setSelectionRange(this._qPos, this._qPos); } catch (e) { /* not a text input */ } }
        el.addEventListener('blur', () => { this._qPos = null; });
      });
      on('[data-out]', el => { const id = el.dataset.out; if (this._arm('out:' + id)) this._call('sign_out_session', { session_id: id }); });
    }
  }

  const CSS = `
    :host{display:block;min-width:0;--us-fill:color-mix(in srgb, var(--primary-text-color) 7%, transparent);--us-fill-2:color-mix(in srgb, var(--primary-text-color) 12%, transparent)}
    ha-card{overflow:hidden;padding-bottom:4px}
    .head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:14px 16px 6px}
    .title{display:flex;align-items:center;gap:8px;font-size:16px;font-weight:500;flex:1;min-width:0;color:var(--primary-text-color)}
    .title ha-icon{color:var(--state-icon-color, var(--primary-color))}
    .seg{display:flex;gap:2px;padding:3px;border-radius:10px;background:var(--us-fill)}
    .seg button{all:unset;cursor:pointer;padding:4px 10px;border-radius:8px;font-size:12px;font-weight:500;color:var(--secondary-text-color)}
    .seg button.on{background:var(--card-background-color);color:var(--primary-text-color);box-shadow:0 1px 2px rgba(0,0,0,.2)}
    .summary{padding:0 16px 8px;font-size:12.5px;color:var(--secondary-text-color)}
    .row{display:flex;align-items:center;gap:12px;padding:8px 16px;cursor:pointer;min-height:44px}
    .row.flat{cursor:default}
    .row:hover:not(.flat){background:var(--us-fill)}
    .row:focus-visible{outline:2px solid var(--primary-color);outline-offset:-2px}
    .row.open{background:var(--us-fill)}
    .ic{flex:none;width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:var(--state-icon-color);background:var(--us-fill);--mdc-icon-size:19px}
    .txt{flex:1;min-width:0}
    .t{font-size:14px;font-weight:500;color:var(--primary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .row .t{white-space:normal;line-height:1.6}.row.flat .t{white-space:nowrap}
    .sub{font-size:12px;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .when{flex:none;font-size:12px;color:var(--secondary-text-color)}
    .pill{display:inline-block;margin-left:6px;padding:1px 7px;border-radius:8px;font-size:10.5px;font-weight:600;background:var(--us-fill);color:var(--secondary-text-color);vertical-align:1px}
    .det{padding:4px 16px 12px 62px;background:var(--us-fill);font-size:12.5px}
    .grid{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 14px;color:var(--secondary-text-color)}
    .grid>span:nth-child(even),.grid code{color:var(--primary-text-color);overflow-wrap:anywhere}
    .grid code{font-size:11px}
    .yes{color:var(--success-color,#43a047)!important}.no{color:var(--error-color,#db4437)!important}
    .pill.ban{background:color-mix(in srgb, var(--error-color, #db4437) 18%, transparent);color:var(--error-color, #db4437)}
    .advice{margin-top:9px;padding:8px 10px;border-radius:10px;background:color-mix(in srgb, var(--error-color, #db4437) 9%, transparent);color:var(--primary-text-color);font-size:12.5px}
    .advice ul{margin:4px 0 0;padding-left:18px}.advice li{margin:2px 0;color:var(--secondary-text-color)}
    .chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}
    .chips span{font-size:11px;padding:2px 8px;border-radius:8px;background:color-mix(in srgb, var(--error-color,#db4437) 14%, transparent);color:var(--error-color,#db4437)}
    .btns{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:4px;margin-top:6px}
    .foot{display:flex;align-items:center;padding:4px 8px}
    .grow{flex:1}
    .btn{all:unset;cursor:pointer;padding:6px 12px;border-radius:8px;font-size:13px;font-weight:500;color:var(--primary-color);white-space:nowrap}
    .btn:hover{background:color-mix(in srgb, var(--primary-color) 10%, transparent)}
    .btn:focus-visible{outline:2px solid var(--primary-color)}
    .btn.warn{color:var(--error-color,#db4437)}
    .btn.warn:hover{background:color-mix(in srgb, var(--error-color,#db4437) 10%, transparent)}
    .empty{display:flex;align-items:center;gap:12px;padding:8px 16px 16px;color:var(--secondary-text-color);--mdc-icon-size:28px}
    .empty b{color:var(--primary-text-color);font-weight:500}
    .empty.ok ha-icon{color:var(--success-color,#43a047)}
    .grp+.grp{border-top:1px solid var(--divider-color)}
    .gname{display:flex;align-items:center;gap:6px;padding:10px 16px 2px;font-weight:500;color:var(--primary-text-color)}
    .tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;padding:6px 16px 14px}
    .tile{min-width:0;padding:10px 12px;border-radius:12px;background:var(--us-fill);--mdc-icon-size:18px;color:var(--state-icon-color)}
    .tile .tl{display:flex;align-items:flex-start;gap:6px;font-size:12.5px;line-height:1.3;color:var(--secondary-text-color)}
    .tile .tl ha-icon{color:var(--state-icon-color);flex:none}
    .tile .big{font-size:22px;font-weight:600;color:var(--primary-text-color);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .tile .sub{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .tb{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:2px 16px 8px}
    .sq{flex:1 1 200px;display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:10px;background:var(--us-fill);color:var(--secondary-text-color);--mdc-icon-size:18px}
    .sq input{flex:1;min-width:0;border:0;outline:0;background:none;font:inherit;font-size:14px;color:var(--primary-text-color)}
    .psel{padding:7px 10px;border-radius:10px;border:0;background:var(--us-fill);color:var(--primary-text-color);font:inherit;font-size:14px}
    .psel option{color:#000}
    .fc{border:0;border-radius:14px;padding:5px 11px;font:inherit;font-size:12.5px;font-weight:500;background:var(--us-fill);color:var(--primary-text-color);cursor:pointer}
    .fc.on{background:var(--primary-color);color:var(--text-primary-color,#fff)} .fc .n{margin-left:5px;opacity:.65}
    .vr{width:1px;height:18px;background:var(--divider-color)}
    .lnk{border:0;background:none;color:var(--primary-color);font:inherit;font-size:13px;cursor:pointer;padding:4px 6px}
    .head>.sub{margin-left:auto}
    .tile.bad{color:var(--error-color,#db4437)}.tile.bad .big{color:var(--error-color,#db4437)}
    @media (max-width:500px){.det{padding-left:16px}.grid{grid-template-columns:minmax(0,1fr)}.grid>span:nth-child(even),.grid code{margin-bottom:5px}}
  `;
  customElements.define('user-sessions-card', UserSessionsCard);

  // ---------- visual editor (uses Home Assistant's own ha-form) ----------
  class UserSessionsCardEditor extends HTMLElement {
    setConfig(c) { this._c = { ...c }; this._render(); }
    set hass(h) { this._h = h; if (this._f) this._f.hass = h; else this._render(); }
    _schema() {
      const s = [
        { name: 'view', selector: { select: { mode: 'dropdown', options: [
          { value: 'failed', label: 'Failed sign-ins (Active / History)' }, { value: 'sessions', label: 'Sign-ins by person' }, { value: 'summary', label: 'Summary tiles' }] } } },
        { name: 'title', selector: { text: {} } },
      ];
      if ((this._c.view || 'failed') === 'failed') s.push({ name: 'max', selector: { number: { min: 1, max: 50, mode: 'box' } } });
      if (this._c.view === 'sessions') {
        const wh = new Set();
        Object.values(this._h.states).filter(x => x.entity_id.startsWith('sensor.user_sessions_') && x.attributes.user_id)
          .forEach(x => [...(x.attributes.sessions || []), ...(x.attributes.long_lived_tokens || []).map(t => ({ ...t, kind: 'long_lived' }))].forEach(r => wh.add(whereOf(r))));
        s.push(
          { name: 'filters', selector: { boolean: {} } },
          { name: 'show_tokens', selector: { boolean: {} } },
          { name: 'where', selector: { select: { mode: 'dropdown', options: [{ value: 'all', label: 'Anywhere' }, ...[...wh].sort().map(w => ({ value: w, label: w }))] } } },
          { name: 'type', selector: { select: { mode: 'dropdown', options: [{ value: 'all', label: 'Any type' }, ...Object.entries(TYPES).map(([value, label]) => ({ value, label }))] } } },
        );
      }
      if (this._c.view === 'summary') s.push({ name: 'tiles', selector: { select: { multiple: true, mode: 'list',
        options: Object.entries(TILES).map(([value, label]) => ({ value, label })) } } });
      return s;
    }
    _render() {
      if (!this._c || !this._h) return;
      if (!this._f) {
        this._f = document.createElement('ha-form');
        this._f.computeLabel = s => ({ view: 'Show', title: 'Title (optional)', max: 'Rows before "Show all"', tiles: 'Tiles to show',
          filters: 'Show search & filters', show_tokens: 'Include long-lived tokens', where: 'Start with: where', type: 'Start with: type' }[s.name] || s.name);
        this._f.computeHelper = s => ({ tiles: 'Tick the tiles you want, in any combination.',
          filters: 'Search box, person picker, and where / type chips with counts.',
          where: 'Your labelled known networks appear here (Configure → Known networks).',
          type: 'Connector = another service signed in to Home Assistant, e.g. an AI assistant.' }[s.name]);
        this._f.addEventListener('value-changed', ev => {
          const c = { ...ev.detail.value };
          Object.keys(c).forEach(k => (c[k] === '' || c[k] === undefined || c[k] === 'all' || c[k] === false) && delete c[k]);
          this._c = c; this._f.schema = this._schema();
          this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: c }, bubbles: true, composed: true }));
        });
        this.appendChild(this._f);
      }
      this._f.hass = this._h;
      this._f.data = { view: 'failed', ...(this._c.view === 'sessions' ? { where: 'all', type: 'all' } : {}), ...(this._c.view === 'summary' && !this._c.tiles ? { tiles: DEFAULT_TILES } : {}), ...this._c };
      this._f.schema = this._schema();
    }
  }
  if (!customElements.get('user-sessions-card-editor')) customElements.define('user-sessions-card-editor', UserSessionsCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: 'user-sessions-card', name: 'User Sessions', description: 'Failed sign-ins (with history), sign-ins by person, or a summary — from the User Sessions integration.', preview: true });
  console.info(`%c USER-SESSIONS-CARD %c ${VERSION} `, 'background:#1565c0;color:#fff;border-radius:3px 0 0 3px', 'background:#444;color:#fff;border-radius:0 3px 3px 0');
})();
