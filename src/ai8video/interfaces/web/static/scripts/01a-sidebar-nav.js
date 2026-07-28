    const SIDEBAR_COLLAPSED_STORAGE_KEY = `${BRAND_SLUG}-sidebar-collapsed`;

    const SIDEBAR_NAV_ICONS = {
      progress: '<svg class="sidebar-nav-icon-svg" viewBox="0 0 24 24" focusable="false"><path d="M4 19V5"/><path d="M4 19h16"/><path d="m8 15 3-4 3 2 4-6"/></svg>',
      image: '<svg class="sidebar-nav-icon-svg" viewBox="0 0 24 24" focusable="false"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m21 15-4.5-4.5L7 20"/></svg>',
      script: '<svg class="sidebar-nav-icon-svg" viewBox="0 0 24 24" focusable="false"><path d="M5 4h9l5 5v11H5z"/><path d="M14 4v5h5"/><path d="M8 13h8"/><path d="M8 17h6"/></svg>',
      recycle: '<svg class="sidebar-nav-icon-svg" viewBox="0 0 24 24" focusable="false"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="m19 6-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
      smartImage: '<svg class="sidebar-nav-icon-svg" viewBox="0 0 24 24" focusable="false"><path d="M12 3a9 9 0 100 18h1.5a2.5 2.5 0 001.2-4.7l-.8-.4a1.5 1.5 0 01.7-2.9H17a4 4 0 004-4c0-3.3-4-6-9-6z"/><circle cx="7.5" cy="10" r="1"/><circle cx="10" cy="6.8" r="1"/><circle cx="14" cy="6.8" r="1"/></svg>',
      hotRadar: '<svg class="sidebar-nav-icon-svg" viewBox="0 0 24 24" focusable="false"><circle cx="12" cy="12" r="2"/><path d="M7.5 7.5a6.4 6.4 0 0 1 9 0"/><path d="M5 5a10 10 0 0 1 14 0"/><path d="M12 12v8"/></svg>',
      viral: '<svg class="sidebar-nav-icon-svg" viewBox="0 0 24 24" focusable="false"><path d="M13 2 4 14h7l-1 8 10-13h-7z"/></svg>',
    };

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
      const iconSvg = SIDEBAR_NAV_ICONS[icon] || SIDEBAR_NAV_ICONS.progress;
      return `
        <button type="button" class="sidebar-nav-item material-card ${extraClass}" ${attrs} title="${safeTitle}">
          <span class="sidebar-nav-icon" data-icon="${escapeHtml(icon || '')}" aria-hidden="true">${iconSvg}</span>
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
      if (shell) {
        shell.classList.toggle('is-sidebar-collapsed', !!collapsed);
      }
      if (button) {
        button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        button.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
        button.title = collapsed ? '展开侧边栏' : '折叠侧边栏';
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
      if (!button || button.dataset.boundCollapse === '1') return;
      button.dataset.boundCollapse = '1';
      button.addEventListener('click', () => {
        toggleSidebarCollapsed();
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
