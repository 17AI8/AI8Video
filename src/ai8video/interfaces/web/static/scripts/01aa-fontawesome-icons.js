    const FONT_AWESOME_ICON_NAMES = new Set([
      'plus',
      'xmark',
      'magnifying-glass',
      'comment-alt',
      'up-down-left-right',
      'eye',
      'eye-slash',
      'microphone',
      'gear',
      'pen-to-square',
      'crop-simple',
      'scissors',
      'chevron-down',
      'wand-magic-sparkles',
      'check',
      'rotate-left',
      'rotate-right',
      'trash-can',
      'arrows-rotate',
      'arrow-right-long',
      'download',
      'images',
      'file-lines',
      'left-right',
      'floppy-disk',
      'music',
      'hourglass-half',
      'grip-vertical',
      'play',
      'chevron-right',
      'triangle-exclamation',
    ]);

    function fontAwesomeIconMarkup(iconName, className = '') {
      const normalizedName = String(iconName || '').trim();
      const safeName = FONT_AWESOME_ICON_NAMES.has(normalizedName)
        ? normalizedName
        : 'triangle-exclamation';
      const safeClassName = String(className || '').trim();
      return `<span class="fa-icon${safeClassName ? ` ${escapeHtml(safeClassName)}` : ''}" data-fa-icon="${safeName}" aria-hidden="true"></span>`;
    }
