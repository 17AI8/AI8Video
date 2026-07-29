    const SIDEBAR_COLLAPSED_STORAGE_KEY = `${BRAND_SLUG}-sidebar-collapsed`;

    const SIDEBAR_NAV_ICON_NAMES = new Set([
      'progress',
      'image',
      'script',
      'recycle',
      'smartImage',
      'hotRadar',
      'viral',
    ]);

    function buildSidebarNavItemMarkup({
      icon,
      title,
      meta,
      actionLabel,
      attrs = '',
      extraClass = '',
    }) {
      const safeTitle = escapeHtml(title || '');
      const safeMeta = escapeHtml(meta || '');
      const safeAction = escapeHtml(actionLabel || '');
      const iconName = SIDEBAR_NAV_ICON_NAMES.has(icon) ? icon : 'progress';
      return `
        <button type="button" class="sidebar-nav-item material-card ${extraClass}" ${attrs} title="${safeTitle}">
          <span class="sidebar-nav-icon" data-icon="${iconName}" aria-hidden="true"><span class="sidebar-nav-icon-glyph"></span></span>
          <span class="sidebar-nav-copy">
            <span class="sidebar-nav-title">${safeTitle}</span>
            <span class="sidebar-nav-meta">${safeMeta}</span>
          </span>
          <span class="sidebar-nav-action" aria-hidden="true">${safeAction}</span>
        </button>
      `;
    }

    function isSidebarCollapsed() {
      try {
        return localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '1';
      } catch (_error) {
        return false;
      }
    }

    function applySidebarCollapsed(collapsed) {
      const shell = els.shell || document.querySelector('.shell');
      const button = els.sidebarCollapseButton || document.getElementById('sidebarCollapseButton');
      const brandButton = document.getElementById('sidebarBrandToggle');
      if (shell) {
        shell.classList.toggle('is-sidebar-collapsed', !!collapsed);
      }
      if (button) {
        button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        button.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
        button.title = collapsed ? '展开侧边栏' : '折叠侧边栏';
      }
      if (brandButton) {
        brandButton.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
        brandButton.title = collapsed ? '展开侧边栏' : '折叠侧边栏';
      }
    }

    function setSidebarCollapsed(collapsed) {
      const next = !!collapsed;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, next ? '1' : '0');
      } catch (_error) {
        /* ignore quota / private mode */
      }
      applySidebarCollapsed(next);
    }

    function toggleSidebarCollapsed() {
      setSidebarCollapsed(!document.querySelector('.shell')?.classList.contains('is-sidebar-collapsed'));
    }

    function bindSidebarCollapse() {
      applySidebarCollapsed(isSidebarCollapsed());
      const button = els.sidebarCollapseButton || document.getElementById('sidebarCollapseButton');
      const brandButton = document.getElementById('sidebarBrandToggle');
      [button, brandButton].filter(Boolean).forEach((control) => {
        if (control.dataset.boundCollapse === '1') return;
        control.dataset.boundCollapse = '1';
        control.addEventListener('click', toggleSidebarCollapsed);
      });
    }

    const MAIN_BACKGROUND_STORAGE_KEY = `${BRAND_SLUG}-main-background`;
    const MAIN_BACKGROUND_MODES = ['grid', 'dots', 'blank'];
    const MAIN_BACKGROUND_LABELS = { grid: '网格背景', dots: '点阵背景', blank: '纯色背景' };

    function readMainBackgroundMode() {
      try {
        const stored = localStorage.getItem(MAIN_BACKGROUND_STORAGE_KEY);
        return MAIN_BACKGROUND_MODES.includes(stored) ? stored : 'grid';
      } catch (_error) {
        return 'grid';
      }
    }

    function applyMainBackgroundMode(mode) {
      const next = MAIN_BACKGROUND_MODES.includes(mode) ? mode : 'grid';
      const main = document.querySelector('.main');
      const button = document.getElementById('mainBackgroundButton');
      main?.classList.remove('is-grid-background', 'is-dots-background', 'is-blank-background');
      main?.classList.add(`is-${next}-background`);
      if (button) {
        const label = button.querySelector('[data-main-background-label]');
        if (label) label.textContent = '背景';
        button.dataset.backgroundMode = next;
        button.title = `当前为${MAIN_BACKGROUND_LABELS[next]}，点击切换背景`;
        button.setAttribute('aria-label', button.title);
      }
      return next;
    }

    function cycleMainBackgroundMode() {
      const current = applyMainBackgroundMode(readMainBackgroundMode());
      const next = MAIN_BACKGROUND_MODES[(MAIN_BACKGROUND_MODES.indexOf(current) + 1) % MAIN_BACKGROUND_MODES.length];
      try {
        localStorage.setItem(MAIN_BACKGROUND_STORAGE_KEY, next);
      } catch (_error) {
        /* ignore quota / private mode */
      }
      applyMainBackgroundMode(next);
    }

    function bindMainBackgroundSwitcher() {
      applyMainBackgroundMode(readMainBackgroundMode());
      const button = document.getElementById('mainBackgroundButton');
      if (!button || button.dataset.boundBackground === '1') return;
      button.dataset.boundBackground = '1';
      button.addEventListener('click', cycleMainBackgroundMode);
    }
