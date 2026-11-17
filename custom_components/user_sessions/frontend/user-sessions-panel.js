// User Sessions sidebar panel — ships with the User Sessions integration
// (opt-in: Configure → Dashboard → Show User Sessions in the sidebar; admins only).
// One page with the summary, failed sign-ins (Active / History) and sign-ins by person.
// It reuses the bundled card, so both always look and behave the same.
(() => {
  if (customElements.get('user-sessions-panel')) return;
  const ALL_TILES = ['accounts', 'signins', 'tokens', 'failed', 'internet', 'tailscale', 'newest', 'last_activity', 'known_ips', 'ip_ban'];

  class UserSessionsPanel extends HTMLElement {
    set hass(h) {
      this._h = h;
      this._theme();
      this._build();
      (this._cards || []).forEach(c => { c.hass = h; });
      if (this._menu) this._menu.hass = h;
    }
    set narrow(n) { this._narrow = n; if (this._menu) { this._menu.narrow = n; } this._sync(); }
    set panel(p) { this._panel = p; if (this._h) this._theme(); }
    set route(_r) {}

    async _build() {
      if (this._building || !this._h) return;
      this._building = true;
      if (!customElements.get('user-sessions-card')) {
        const url = (this._panel && this._panel.config && this._panel.config.card_url) || '/user_sessions/user-sessions-card.js';
        try { await import(url); } catch (e) { console.error('User Sessions: could not load the card', e); }
      }
      const root = this.attachShadow({ mode: 'open' });
      root.innerHTML = `<style>${CSS}</style>
        <div class="bar"><span id="menu"></span><div class="title">User Sessions</div><span class="grow"></span>
          <a class="link" href="/config/integrations/integration/user_sessions" title="Integration settings">
            <ha-icon icon="mdi:cog-outline"></ha-icon><span>Settings</span></a></div>
        <div class="content">
          <div id="sum"></div>
          <div class="cols"><div id="failed"></div><div id="sessions"></div></div>
          <div class="foot">Admins only · <a href="https://github.com/caaamillo/ha-user-sessions" target="_blank" rel="noreferrer">User Sessions on GitHub</a></div>
        </div>`;
      // the sidebar toggle Home Assistant shows on phones
      const slot = root.getElementById('menu');
      if (customElements.get('ha-menu-button')) {
        this._menu = document.createElement('ha-menu-button');
        this._menu.hass = this._h; this._menu.narrow = this._narrow;
      } else {
        this._menu = document.createElement('button');
        this._menu.className = 'menu';
        this._menu.innerHTML = '<ha-icon icon="mdi:menu"></ha-icon>';
        this._menu.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-toggle-menu', { bubbles: true, composed: true })));
      }
      slot.appendChild(this._menu);
      const mk = (id, cfg) => {
        const el = document.createElement('user-sessions-card');
        el.setConfig(cfg); el.hass = this._h; root.getElementById(id).appendChild(el); return el;
      };
      this._cards = [
        mk('sum', { view: 'summary', title: 'Overview', tiles: ALL_TILES }),
        mk('failed', { view: 'failed', max: 10 }),
        mk('sessions', { view: 'sessions', title: 'Sign-ins by person', filters: true, show_tokens: true }),
      ];
      this._sync();
    }

    // Apply the theme picked in Configure (if any) to this page only, like a dashboard view's theme.
    _theme() {
      const name = this._panel && this._panel.config && this._panel.config.theme, t = this._h.themes || {};
      const dark = !!t.darkMode, key = `${name}|${dark}|${t.themes && name && t.themes[name] ? Object.keys(t.themes[name]).length : 0}`;
      if (key === this._themeKey) return;
      this._themeKey = key;
      (this._themeVars || []).forEach(k => this.style.removeProperty(k));
      this._themeVars = [];
      const th = name && t.themes && t.themes[name];
      if (!th) return;
      const vars = { ...th };
      delete vars.modes;
      if (th.modes) Object.assign(vars, th.modes[dark ? 'dark' : 'light'] || {});
      Object.entries(vars).forEach(([k, v]) => {
        if (typeof v !== 'string' && typeof v !== 'number') return;
        this.style.setProperty('--' + k, String(v)); this._themeVars.push('--' + k);
      });
    }

    _sync() {
      const slot = this.shadowRoot && this.shadowRoot.getElementById('menu');
      if (slot) slot.style.display = this._narrow ? '' : 'none';
    }
  }

  const CSS = `
    :host{display:block;min-height:100vh;background:var(--lovelace-background,var(--primary-background-color));background-attachment:fixed;color:var(--primary-text-color);font-family:var(--ha-font-family-body, var(--paper-font-body1_-_font-family, Roboto, sans-serif))}
    .bar{position:sticky;top:0;z-index:2;display:flex;align-items:center;gap:4px;height:var(--header-height,56px);padding:0 12px;
      box-sizing:border-box;background:var(--app-header-background-color,var(--primary-color));color:var(--app-header-text-color,#fff);
      border-bottom:var(--app-header-border-bottom,none)}
    .title{font-size:20px;font-weight:400;margin-left:8px}
    .grow{flex:1}
    .menu{background:none;border:0;color:inherit;padding:8px;cursor:pointer;display:grid;place-items:center}
    .link{display:flex;align-items:center;gap:6px;color:inherit;text-decoration:none;padding:8px 10px;border-radius:18px;font-size:14px;--mdc-icon-size:20px}
    .link:hover{background:rgba(255,255,255,.12)}
    .content{max-width:1280px;margin:0 auto;padding:16px;box-sizing:border-box;display:flex;flex-direction:column;gap:16px}
    .cols{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;align-items:start}
    .cols>div,#sum{min-width:0}
    @media (min-width: 900px){ .cols{grid-template-columns:minmax(0,1.15fr) minmax(0,1fr)} }
    .foot{text-align:center;font-size:12.5px;color:var(--secondary-text-color);padding:4px 0 24px}
    .foot a{color:var(--primary-color)}
    @media (max-width: 600px){ .content{padding:8px;gap:8px} .link span{display:none} }`;

  customElements.define('user-sessions-panel', UserSessionsPanel);
})();
