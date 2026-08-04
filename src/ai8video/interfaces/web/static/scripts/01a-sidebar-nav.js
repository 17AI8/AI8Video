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
      count = null,
    }) {
      const safeTitle = escapeHtml(title || '');
      const safeMeta = escapeHtml(meta || '');
      const safeAction = escapeHtml(actionLabel || '');
      const iconName = SIDEBAR_NAV_ICON_NAMES.has(icon) ? icon : 'progress';
      const numericCount = count === null || count === '' ? null : Number(count);
      const normalizedCount = Number.isFinite(numericCount) ? Math.max(0, Math.trunc(numericCount)) : null;
      const safeCount = normalizedCount === null ? '' : escapeHtml(String(normalizedCount));
      const countedClass = normalizedCount === null ? '' : ' sidebar-nav-item--counted';
      const tooltip = safeMeta ? `${safeTitle}，${safeMeta}` : safeTitle;
      const countMarkup = normalizedCount === null
        ? ''
        : `<span class="sidebar-nav-count" aria-hidden="true">${safeCount}</span>`;
      return `
        <button type="button" class="sidebar-nav-item material-card${countedClass} ${extraClass}" ${attrs} title="${tooltip}">
          <span class="sidebar-nav-icon" data-icon="${iconName}" aria-hidden="true"><span class="sidebar-nav-icon-glyph"></span></span>
          <span class="sidebar-nav-copy">
            <span class="sidebar-nav-title-row">
              <span class="sidebar-nav-title">${safeTitle}</span>
              ${countMarkup}
            </span>
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
