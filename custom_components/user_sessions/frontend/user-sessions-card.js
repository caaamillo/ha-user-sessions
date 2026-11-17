// User Sessions card — ships with the User Sessions integration (opt-in: Configure → Dashboard card).
//   type: custom:user-sessions-card
//   view: failed | sessions | summary      (default: failed)
//   title: optional heading
//   max: rows before "Show all" (default 6)
// Theme-aware: uses Home Assistant's own CSS variables, so it follows whatever theme you run.
(() => {
  if (customElements.get('user-sessions-card')) return;
  const VERSION = '0.6.1';
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
  const dev = d => d && d.browser ? (d.is_bot ? 'Bot / script' : d.browser + (d.os ? ' on ' + d.os : '')) : 'No browser info';

  class UserSessionsCard extends HTMLElement {
    static getStubConfig() { return { view: 'failed' }; }
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

    // ---------- sessions by person ----------
    _sessions() {
      const users = this._users().filter(u => u.sessions.length || u.tokens.length);
      const where = s => s.last_ip ? (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(s.last_ip) ? 'Private' : /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(s.last_ip) ? 'Tailscale' : 'Internet') : 'Not used yet';
      const groups = users.map(u => `<div class="grp"><div class="gname">${esc(u.name)}${u.admin ? '<span class="pill">Admin</span>' : ''}<span class="grow"></span><span class="sub">${u.sessions.length} sign-in${u.sessions.length === 1 ? '' : 's'}</span></div>`
        + u.sessions.map(s => `<div class="row flat"><div class="ic"><ha-icon icon="${s.via && s.via.startsWith('Companion') ? 'mdi:cellphone' : 'mdi:monitor'}"></ha-icon></div>
          <div class="txt"><div class="t">${esc(s.client_name || s.via)}</div><div class="sub">${esc(where(s))}${s.last_ip ? ' · ' + esc(s.last_ip) : ''} · ${ago(s.last_used)}</div></div>
          <button class="btn warn" data-out="${esc(s.session_id)}">${this._armed === 'out:' + s.session_id ? 'Confirm' : 'Sign out'}</button></div>`).join('') + '</div>').join('');
      return `<div class="head"><div class="title"><ha-icon icon="mdi:account-key-outline"></ha-icon>${esc(this._c.title || 'Sign-ins')}</div></div>${groups || '<div class="empty ok"><ha-icon icon="mdi:account-off-outline"></ha-icon><div><b>Nobody is signed in</b></div></div>'}`;
    }

    // ---------- summary tiles ----------
    _summary() {
      const users = this._users(), all = users.flatMap(u => u.sessions), toks = users.flatMap(u => u.tokens);
      const act = this._a(FS, 'entries') || [], hi = act.filter(e => e.risk === 'high').length;
      const known = this._h.states['sensor.user_sessions_known_ips'];
      const tile = (ic, big, lbl, warn) => `<div class="tile ${warn ? 'bad' : ''}"><ha-icon icon="${ic}"></ha-icon><div class="big">${esc(big)}</div><div class="sub">${esc(lbl)}</div></div>`;
      return `<div class="head"><div class="title"><ha-icon icon="mdi:shield-account-outline"></ha-icon>${esc(this._c.title || 'User sessions')}</div></div><div class="tiles">
        ${tile('mdi:account-group-outline', users.filter(u => u.sessions.length).length, 'accounts signed in')}
        ${tile('mdi:login-variant', all.length, 'sign-ins')}
        ${tile('mdi:key-chain-variant', toks.length, 'long-lived tokens')}
        ${tile('mdi:shield-alert-outline', act.length, hi ? hi + ' high risk' : 'failed sign-ins', act.length > 0)}
        ${tile('mdi:ip-network-outline', known ? known.state : '–', 'known IPs')}
      </div>`;
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
      on('[data-out]', el => { const id = el.dataset.out; if (this._arm('out:' + id)) this._call('sign_out_session', { session_id: id }); });
    }
  }

  const CSS = `
    ha-card{overflow:hidden;padding-bottom:4px}
    .head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:14px 16px 6px}
    .title{display:flex;align-items:center;gap:8px;font-size:16px;font-weight:500;flex:1;min-width:0;color:var(--primary-text-color)}
    .title ha-icon{color:var(--state-icon-color, var(--primary-color))}
    .seg{display:flex;gap:2px;padding:3px;border-radius:10px;background:var(--secondary-background-color)}
    .seg button{all:unset;cursor:pointer;padding:4px 10px;border-radius:8px;font-size:12px;font-weight:500;color:var(--secondary-text-color)}
    .seg button.on{background:var(--card-background-color);color:var(--primary-text-color);box-shadow:0 1px 2px rgba(0,0,0,.2)}
    .summary{padding:0 16px 8px;font-size:12.5px;color:var(--secondary-text-color)}
    .row{display:flex;align-items:center;gap:12px;padding:8px 16px;cursor:pointer;min-height:44px}
    .row.flat{cursor:default}
    .row:hover:not(.flat){background:var(--secondary-background-color)}
    .row:focus-visible{outline:2px solid var(--primary-color);outline-offset:-2px}
    .row.open{background:var(--secondary-background-color)}
    .ic{flex:none;width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:var(--state-icon-color);background:var(--secondary-background-color);--mdc-icon-size:19px}
    .txt{flex:1;min-width:0}
    .t{font-size:14px;font-weight:500;color:var(--primary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .sub{font-size:12px;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .when{flex:none;font-size:12px;color:var(--secondary-text-color)}
    .pill{display:inline-block;margin-left:6px;padding:1px 7px;border-radius:8px;font-size:10.5px;font-weight:600;background:var(--secondary-background-color);color:var(--secondary-text-color);vertical-align:1px}
    .det{padding:4px 16px 12px 62px;background:var(--secondary-background-color);font-size:12.5px}
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
    .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;padding:6px 16px 14px}
    .tile{padding:10px 12px;border-radius:12px;background:var(--secondary-background-color);--mdc-icon-size:20px;color:var(--state-icon-color)}
    .tile .big{font-size:22px;font-weight:600;color:var(--primary-text-color);margin-top:4px}
    .tile.bad{color:var(--error-color,#db4437)}.tile.bad .big{color:var(--error-color,#db4437)}
    @media (max-width:500px){.det{padding-left:16px}.grid{grid-template-columns:minmax(0,1fr)}.grid>span:nth-child(even),.grid code{margin-bottom:5px}}
  `;
  customElements.define('user-sessions-card', UserSessionsCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: 'user-sessions-card', name: 'User Sessions', description: 'Failed sign-ins (with history), sign-ins by person, or a summary — from the User Sessions integration.', preview: true });
  console.info(`%c USER-SESSIONS-CARD %c ${VERSION} `, 'background:#1565c0;color:#fff;border-radius:3px 0 0 3px', 'background:#444;color:#fff;border-radius:0 3px 3px 0');
})();
