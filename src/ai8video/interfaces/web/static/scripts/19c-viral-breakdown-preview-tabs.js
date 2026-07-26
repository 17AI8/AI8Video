    function buildViralBreakdownVideoInfoMarkup(item) {
      if (!item) {
        return '<div class="viral-breakdown-empty">请先选择一个视频。</div>';
      }
      const media = item.media && typeof item.media === 'object' ? item.media : {};
      const hasMedia = Number(media.width || 0) > 0 || Number(media.durationSeconds || 0) > 0;
      if (!hasMedia) {
        return '<div class="viral-breakdown-empty">未能读取该视频的媒体元数据（时长、分辨率、编码等）。</div>';
      }
      const audioBits = [
        media.audioCodec || '',
        media.audioChannels ? `${media.audioChannels} 声道` : '',
        media.sampleRate ? `${Math.round(Number(media.sampleRate) / 1000)} kHz` : '',
      ].filter(Boolean).join(' · ');
      const rows = [
        ['时长', media.durationLabel || '—'],
        ['分辨率', media.resolution || '—'],
        ['画幅', media.aspectRatio || '—'],
        ['帧率', media.fpsLabel || '—'],
        ['视频编码', media.videoCodec || '—'],
        ['音频', audioBits || '—'],
        ['码率', media.bitrateLabel || '—'],
        ['容器', media.container || '—'],
        ['像素格式', media.pixelFormat || '—'],
        ['文件大小', item.sizeLabel || humanizeByteSize(item.sizeBytes || 0)],
      ];
      return `
        <dl class="viral-breakdown-info-list">
          ${rows.map(([label, value]) => `
            <div class="viral-breakdown-info-row">
              <dt>${escapeHtml(label)}</dt>
              <dd title="${escapeHtml(String(value || ''))}">${escapeHtml(String(value || '—'))}</dd>
            </div>
          `).join('')}
        </dl>
      `;
    }

    function viralBreakdownMediaSummary(item) {
      const media = item?.media && typeof item.media === 'object' ? item.media : {};
      const parts = [media.resolution, media.durationLabel, media.fpsLabel].filter(Boolean);
      return parts.join(' · ') || (item?.sizeLabel || '');
    }

    function buildViralBreakdownShotLanguageMarkup(analysis) {
      if (!analysis || typeof analysis !== 'object') {
        return '<div class="viral-breakdown-empty">先完成“拆解画面”和“分析台词”，再分析镜头语言。</div>';
      }
      const rows = [
        ['整体策略', analysis.overall],
        ['开场钩子', analysis.hook],
        ['节奏', analysis.rhythm],
        ['视觉风格', analysis.visualStyle],
        ['镜头与构图', analysis.camera],
        ['光线与色彩', analysis.lighting],
        ['可复用方法', analysis.reusable],
        ['避免照搬', analysis.avoid],
      ].filter(([, value]) => String(value || '').trim());
      const beats = Array.isArray(analysis.beats) ? analysis.beats : [];
      const stale = analysis.stale === true
        ? '<div class="viral-breakdown-empty">截图或台词已变化，请重新分析后再用于猜剧本和生成。</div>'
        : '';
      return `
        ${stale}
        <dl class="viral-breakdown-info-list">
          ${rows.map(([label, value]) => `
            <div class="viral-breakdown-info-row">
              <dt>${escapeHtml(label)}</dt>
              <dd>${escapeHtml(String(value || '—'))}</dd>
            </div>
          `).join('')}
          ${beats.map((beat) => {
            const detail = [beat?.visual, beat?.technique, beat?.purpose].filter(Boolean).join(' · ');
            return `
              <div class="viral-breakdown-info-row">
                <dt>${escapeHtml(String(beat?.time || '关键节拍'))}</dt>
                <dd>${escapeHtml(detail || '—')}</dd>
              </div>
            `;
          }).join('')}
        </dl>
      `;
    }

    function getViralBreakdownPreviewTab() {
      const tab = String(state.viralBreakdown.previewTab || 'preview').trim();
      return ['preview', 'info'].includes(tab) ? tab : 'preview';
    }

    function activateViralBreakdownPreviewTab(tabName) {
      const tab = ['preview', 'info'].includes(String(tabName || '')) ? String(tabName) : 'preview';
      state.viralBreakdown.previewTab = tab;
      syncViralBreakdownPreviewTab();
    }

    function syncViralBreakdownPreviewTab() {
      const tab = getViralBreakdownPreviewTab();
      const root = document.querySelector('#viralBreakdownModal .viral-breakdown-preview-column');
      if (!root) return;
      root.querySelectorAll('[data-viral-breakdown-preview-tab]').forEach((button) => {
        const active = button.getAttribute('data-viral-breakdown-preview-tab') === tab;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      root.querySelectorAll('[data-viral-breakdown-preview-panel]').forEach((panel) => {
        panel.classList.toggle('is-active', panel.getAttribute('data-viral-breakdown-preview-panel') === tab);
      });
    }

    function getViralBreakdownActiveTab() {
      const tab = String(state.viralBreakdown.activeTab || 'grid').trim();
      return ['grid', 'shot-language', 'transcript', 'script', 'generated'].includes(tab) ? tab : 'grid';
    }

    function activateViralBreakdownTab(tabName) {
      const tab = ['grid', 'shot-language', 'transcript', 'script', 'generated'].includes(String(tabName || ''))
        ? String(tabName)
        : 'grid';
      state.viralBreakdown.activeTab = tab;
      syncViralBreakdownActiveTab();
    }

    function syncViralBreakdownActiveTab() {
      const tab = getViralBreakdownActiveTab();
      const root = document.querySelector('#viralBreakdownModal .viral-breakdown-detail-column');
      if (!root) return;
      root.querySelectorAll('[data-viral-breakdown-tab]').forEach((button) => {
        const active = button.getAttribute('data-viral-breakdown-tab') === tab;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      root.querySelectorAll('[data-viral-breakdown-panel]').forEach((panel) => {
        panel.classList.toggle('is-active', panel.getAttribute('data-viral-breakdown-panel') === tab);
      });
      syncViralBreakdownScriptDrawer();
      syncViralBreakdownScriptSubTab();
    }

    function syncViralBreakdownScriptDrawer() {
      const drawer = document.querySelector('#viralBreakdownModal [data-viral-breakdown-script-drawer]');
      if (!drawer) return;
      const open = getViralBreakdownActiveTab() === 'script';
      drawer.classList.toggle('is-open', open);
      drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
      const toolbar = drawer.querySelector('.viral-breakdown-script-toolbar');
      if (toolbar) {
        toolbar.style.paddingLeft = '';
        toolbar.style.marginLeft = '';
      }
      const subtabs = drawer.querySelector('.viral-breakdown-subtabs');
      if (subtabs) {
        subtabs.style.paddingLeft = '';
        subtabs.style.marginLeft = '';
      }
    }

    function getViralBreakdownScriptSubTab() {
      const tab = String(state.viralBreakdown.scriptSubTab || 'skeleton').trim();
      return ['skeleton', 'tree'].includes(tab) ? tab : 'skeleton';
    }

    function activateViralBreakdownScriptSubTab(tabName) {
      const tab = ['skeleton', 'tree'].includes(String(tabName || '')) ? String(tabName) : 'skeleton';
      state.viralBreakdown.scriptSubTab = tab;
      syncViralBreakdownScriptSubTab();
    }

    function syncViralBreakdownScriptSubTab() {
      const tab = getViralBreakdownScriptSubTab();
      const root = document.querySelector('#viralBreakdownModal');
      if (!root) return;
      root.querySelectorAll('[data-viral-breakdown-script-tab]').forEach((button) => {
        const active = button.getAttribute('data-viral-breakdown-script-tab') === tab;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
      });
    }
