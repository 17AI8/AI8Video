    function renderFlowerTextColorControl(key, color, saving) {
      const safeKey = key === 'strokeColor' ? 'strokeColor' : 'textColor';
      const safeColor = normalizeFlowerTextColor(color, safeKey === 'strokeColor' ? '#121826' : '#ffee43');
      const hsv = flowerTextColorToHsv(safeColor);
      const hueColor = flowerTextHsvToHex({ h: hsv.h, s: 100, v: 100 });
      const open = state.flowerText?.activeColorPicker === safeKey;
      const disabled = saving ? 'disabled' : '';
      return `
        <div class="flower-text-color-field" data-flower-text-color-field="${safeKey}">
          <button type="button" class="flower-text-color-button" data-flower-text-color-toggle="${safeKey}" style="--flower-text-color: ${escapeHtml(safeColor)};" aria-expanded="${open ? 'true' : 'false'}" ${disabled}>
            <span></span>
          </button>
          ${open ? `
            <div class="flower-text-color-popover" data-flower-text-color-popover="${safeKey}" style="--flower-text-color: ${escapeHtml(safeColor)}; --flower-text-hue-color: ${escapeHtml(hueColor)};">
              <div class="flower-text-color-preview-group">
                <div class="flower-text-color-preview"></div>
                <button type="button" class="flower-text-color-preview-button" data-flower-text-color-preview="${safeKey}" ${disabled}>预览</button>
              </div>
              <label class="flower-text-color-row" data-flower-color-row="hue">
                <span>色相</span>
                <span class="flower-text-color-hue-track">
                  <input type="range" min="0" max="360" step="1" value="${Math.round(hsv.h)}" data-flower-text-color-channel="h" data-flower-text-color-key="${safeKey}">
                </span>
              </label>
              <label class="flower-text-color-row" data-flower-color-row="saturation">
                <span>饱和</span>
                <span class="flower-text-color-saturation-track">
                  <input type="range" min="0" max="100" step="1" value="${Math.round(hsv.s)}" data-flower-text-color-channel="s" data-flower-text-color-key="${safeKey}">
                </span>
              </label>
              <label class="flower-text-color-row" data-flower-color-row="value">
                <span>明暗</span>
                <span class="flower-text-color-value-track">
                  <input type="range" min="0" max="100" step="1" value="${Math.round(hsv.v)}" data-flower-text-color-channel="v" data-flower-text-color-key="${safeKey}">
                </span>
              </label>
            </div>
          ` : ''}
        </div>
      `;
    }

    function renderGenerationModeDrawer() {
      if (!els.generationModeDrawer || !els.generationModeDrawerBody) return;
      const visible = !!state.generationModeDrawer?.visible;
      els.generationModeDrawer.classList.toggle('open', visible);
      els.generationModeDrawer.setAttribute('aria-hidden', visible ? 'false' : 'true');
      els.generationModeButton?.classList.toggle('is-open', visible);
      els.generationModeButton?.setAttribute('aria-expanded', visible ? 'true' : 'false');
      if (!visible) return;
      const mode = state.generationMode || {};
      const enabled = !!mode.concurrentGeneration;
      const saving = !!mode.saving;
      const error = String(mode.error || '').trim();
      const statusText = saving ? '正在保存...' : error ? `提示：${error}` : (enabled ? '已开启' : '已关闭');
      els.generationModeDrawerBody.innerHTML = `
        <div class="generation-mode-panel">
          <label class="generation-mode-toggle">
            <span>并发提交</span>
            <input type="checkbox" data-generation-mode-toggle ${enabled ? 'checked' : ''} ${saving ? 'disabled' : ''}>
          </label>
          <div class="generation-mode-note">
            开启后，多条视频会一次性提交，整体更快。关闭后，一条完成再生成下一条，更稳。
            ${statusText ? `<br>${escapeHtml(statusText)}` : ''}
          </div>
        </div>
      `;
    }

    function renderHtmlMotionOverlayButton() {
      const button = els.htmlMotionOverlayButton;
      if (!button) return;
      const overlay = state.htmlMotionOverlay || {};
      const enabled = !!overlay.enabled;
      const saving = !!overlay.saving;
      button.classList.toggle('is-ready', enabled);
      button.classList.toggle('is-open', !!state.htmlMotionOverlayDrawer?.visible);
      button.disabled = saving;
      button.textContent = saving ? '保存中' : 'HTML 动效';
      button.setAttribute('aria-expanded', state.htmlMotionOverlayDrawer?.visible ? 'true' : 'false');
      button.title = saving
        ? '正在保存 HTML 动效设置'
        : enabled
          ? 'HTML 动效已开启。会在基础视频完成后生成透明动效并叠加。'
          : (overlay.error ? `HTML 动效保存失败：${overlay.error}` : '点击展开 HTML 动效设置。');
    }

    function renderHtmlMotionOverlayDrawer() {
      if (!els.htmlMotionOverlayDrawer || !els.htmlMotionOverlayDrawerBody) return;
      const visible = !!state.htmlMotionOverlayDrawer?.visible;
      els.htmlMotionOverlayDrawer.classList.toggle('open', visible);
      els.htmlMotionOverlayDrawer.setAttribute('aria-hidden', visible ? 'false' : 'true');
      els.htmlMotionOverlayButton?.classList.toggle('is-open', visible);
      els.htmlMotionOverlayButton?.setAttribute('aria-expanded', visible ? 'true' : 'false');
      if (!visible) return;
      const overlay = state.htmlMotionOverlay || {};
      const ready = overlay.runtime?.ready !== false;
      const note = overlay.saving
        ? '正在保存...'
        : overlay.error
          ? `提示：${overlay.error}`
          : (overlay.enabled ? '已开启' : '已关闭');
      const runtimeNote = ready ? '' : `<br>运行环境：${escapeHtml(overlay.runtime?.reason || '未就绪。开启后会自动保留基础视频并标记降级。')}`;
      els.htmlMotionOverlayDrawerBody.innerHTML = `
        <div class="generation-mode-panel">
          <label class="generation-mode-toggle">
            <span>HTML 动效</span>
            <input type="checkbox" data-html-motion-overlay-toggle ${overlay.enabled ? 'checked' : ''} ${overlay.saving ? 'disabled' : ''}>
          </label>
          <div class="generation-mode-note">
            开启后会按每条视频的最终提示词生成透明动态图形，再叠加到基础视频。渲染或叠加失败时会保留基础视频，并在结果里标记原因。
            ${note ? `<br>${escapeHtml(note)}` : ''}${runtimeNote}
          </div>
        </div>
      `;
    }

    function renderProgress() {
      const session = getActiveSession();
      const model = buildProgressModel(session);
      if (!model) {
        els.progressPanel.innerHTML = '';
        return;
      }
      const overview = buildProgressOverview(model);
      const resultCount = getPlayableResultItems(buildResultFolderGalleryModel(session)).length;
      els.progressPanel.innerHTML = `
        <div class="progress-card material-card">
          <div class="material-heading">
            <div class="material-title">${escapeHtml(model.title)}</div>
            <div class="material-meta">${escapeHtml(`${resultCount} 个结果`)}</div>
          </div>
          <div class="material-actions">
            ${renderProgressActionButton(overview)}
            <button type="button" class="material-add-button" data-show-result-modal="1">查看结果</button>
          </div>
        </div>
      `;
    }

    function renderProgressActionButton(overview) {
      const percent = Math.max(0, Math.min(100, Number(overview?.percent || 0)));
      const pending = overview?.pending ? ' pending' : '';
      const label = overview?.label ? `查看进度 · ${overview.label}` : '查看进度';
      return `
        <button
          type="button"
          class="material-library-button progress-action-button${pending}"
          style="--progress: ${percent}%"
          title="${escapeHtml(label)}"
          data-show-progress-modal="1"
        >查看进度</button>
      `;
    }

    function renderProgressOverview(overview) {
      if (!overview) return '';
      const pendingClass = overview.pending ? ' pending' : '';
      const terminalClass = overview.terminal ? ' terminal' : '';
      const percent = Math.max(0, Math.min(100, Number(overview.percent || 0)));
      return `
        <div class="progress-overview">
          <div class="progress-overview-row">
            <strong>总体进度</strong>
            <div class="progress-overview-track${pendingClass}${terminalClass}" aria-label="总体进度">
              <div class="progress-overview-fill" style="--progress: ${percent}%"></div>
            </div>
            <span class="progress-overview-value">${escapeHtml(overview.label || `${percent}%`)}</span>
          </div>
        </div>
      `;
    }

    function buildProgressOverview(model) {
      const cards = Array.isArray(model?.cards) ? model.cards : [];
      if (cards.length) {
        const percents = cards.map((item) => {
          if (item?.__progressStatus) {
            return Math.max(0, Math.min(100, Number(item?.percent || 0)));
          }
          return 100;
        });
        const percent = Math.round(percents.reduce((sum, value) => sum + value, 0) / Math.max(1, percents.length));
        const runningCount = cards.filter((item) => item?.pending).length;
        const pending = runningCount > 0 && model?.isActive !== false;
        return { percent, label: `${percent}%`, pending, terminal: !pending && model?.isActive === false };
      }

      const videos = Array.isArray(model?.videos) ? model.videos : [];
      if (videos.length) {
        const percents = videos.map((item) => Math.max(0, Math.min(100, Number(item?.percent || 0))));
        const percent = Math.round(percents.reduce((sum, value) => sum + value, 0) / Math.max(1, percents.length));
        const runningCount = videos.filter((item) => item?.pending).length;
        const label = `${percent}%`;
        const pending = runningCount > 0 && model?.isActive !== false;
        return { percent, label, pending, terminal: !pending && model?.isActive === false };
      }

      const metricMap = new Map((Array.isArray(model?.metrics) ? model.metrics : []).map((item) => [
        String(item?.label || '').trim(),
        item?.value,
      ]));
      const total = parseProgressNumber(metricMap.get('视频数') ?? metricMap.get('目标'));
      const done = parseProgressNumber(
        metricMap.get('已生成') ?? metricMap.get('通过') ?? metricMap.get('归档')
      );
      if (total > 0 && done >= 0) {
        const percent = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
        return {
          percent,
          label: `${percent}%`,
          pending: false,
          terminal: model?.isActive === false,
        };
      }

      if (String(model?.summary || '').trim()) {
        const pending = Boolean(model?.isActive);
        return { percent: 0, label: '等待真实进度', pending, terminal: !pending };
      }
      return null;
    }

    function parseProgressNumber(value) {
      if (typeof value === 'number' && Number.isFinite(value)) return Math.max(0, value);
      const match = String(value ?? '').match(/\d+/);
      return match ? Number(match[0]) : -1;
    }
    function renderProgressModal() {
      if (!els.progressModal) return;
      const visible = !!state.progressModal.visible;
      const model = buildProgressModel(getActiveSession());
      if (visible && !model) {
        state.progressModal.visible = false;
        els.progressModal.classList.add('hidden');
        return;
      }
      els.progressModal.classList.toggle('hidden', !visible);
      els.progressModalTitle.textContent = model?.title || '当前进度';
      els.progressModalSub.textContent = model?.summary || '当前没有进度。';
      if (els.progressModalCancelSlot) {
        els.progressModalCancelSlot.innerHTML = model?.cancelable
          ? renderForceCancelButton(model.sessionId, { modal: true })
          : '';
      }
      if (!visible) return;
      if (!model) {
        els.progressModalBody.innerHTML = '<div class="empty">当前没有进度。</div>';
        return;
      }
      els.progressModalBody.innerHTML = buildProgressModalMarkup(model);
    }

    function buildProgressModalMarkup(model) {
      const metricMarkup = model.metrics?.length
        ? `<div class="progress-metrics">${model.metrics.map((item) => `
            <div class="progress-metric">
              <strong>${escapeHtml(String(item.value))}</strong>
              <span>${escapeHtml(item.label)}</span>
            </div>
          `).join('')}</div>`
        : '';
      const detailMarkup = model.details?.length
        ? `<div class="progress-details">${model.details.map((item) => `
            <div class="progress-detail-card">
              <strong>${escapeHtml(item.title)}</strong>
              <div>${escapeHtml(item.body).replaceAll('\n', '<br>')}</div>
            </div>
          `).join('')}</div>`
        : '';
      const hasResultCards = !!(model.cards?.length || model.pendingCount);
      const videoMarkup = hasResultCards
        ? renderProgressResultStrip(model.cards || [], Number(model.pendingCount || 0) || 0)
        : (model.videos?.length ? renderProgressVideoGrid(model.videos) : '');
      return `${metricMarkup}${videoMarkup}${detailMarkup}`;
    }

    function renderForceCancelButton(sessionId, options = {}) {
      const targetSessionId = String(sessionId || state.activeId || '').trim();
      if (!targetSessionId) return '';
      const busy = pendingCancelInflight.has(targetSessionId);
      const label = busy ? '终止中' : '强行终止';
      const title = options.modal
        ? '停止等待当前后台任务回填'
        : '停止等待当前后台任务回填';
      const messageIndex = Number(options.messageIndex);
      const indexMarkup = Number.isInteger(messageIndex)
        ? `data-force-cancel-index="${messageIndex}"`
        : '';
      return `
        <button
          type="button"
          class="force-cancel-button"
          data-force-cancel-session="${escapeHtml(targetSessionId)}"
          ${indexMarkup}
          title="${escapeHtml(title)}"
          ${busy ? 'disabled' : ''}
        >${escapeHtml(label)}</button>
      `;
    }

    function renderProgressVideoGrid(videos, options = {}) {
      const compact = !!options.compact;
      return `
        <div class="progress-video-grid${compact ? ' compact' : ''}">
          ${videos.map((item) => `
            <div class="progress-video-card">
              <div class="progress-video-title">${escapeHtml(item.title)}</div>
              <div class="progress-video-stage">${escapeHtml(item.stage)}</div>
              <div class="progress-video-bar${item.pending ? ' pending' : ''}" aria-label="${escapeHtml(item.title)} ${escapeHtml(item.stage)}">
                <span style="--progress-width: ${item.pending ? 100 : Math.max(0, Math.min(100, Number(item.percent) || 0))}%"></span>
              </div>
            </div>
          `).join('')}
        </div>
      `;
    }

    function openProgressModal() {
      state.progressModal.visible = true;
      renderProgressModal();
    }

    function closeProgressModal() {
      state.progressModal.visible = false;
      renderProgressModal();
    }

    function ensureResultModalState() {
      if (!state.resultModal || typeof state.resultModal !== 'object') {
        state.resultModal = { visible: false };
      }
      return state.resultModal;
    }



























    function renderResultModal({ preserveScroll = false } = {}) {
      if (!els.resultModal) return;
      const modalState = ensureResultModalState();
      const visible = !!state.resultModal.visible;
      els.resultModal.classList.toggle('hidden', !visible);
      const gallery = buildResultFolderGalleryModel(getActiveSession());
      const completedCount = getPlayableResultItems(gallery).length;
      els.resultModalTitle.textContent = '生成结果';
      els.resultModalSub.textContent = completedCount
        ? `${completedCount} 个结果`
        : '当前没有生成结果。';
      els.resultModalOpenFolderButton.disabled = false;
      els.resultModalOpenFolderButton.dataset.archiveKey = '';
      els.resultModalOpenFolderButton.dataset.localPath = '';
      if (!visible) return;
      const previousScrollTop = preserveScroll ? els.resultModalBody.scrollTop : 0;
      els.resultModalBody.innerHTML = renderResultMediaWall(gallery, { wall: true });
      if (preserveScroll) {
        els.resultModalBody.scrollTop = previousScrollTop;
      }
    }

    function openResultModal() {
      state.resultModal.visible = true;
      renderResultModal();
    }

    function closeResultModal() {
      state.resultModal.visible = false;
      renderResultModal();
    }

    const VIDEO_PREVIEW_ICONS = {
      play: '<path d="M8 5.2v13.6L19 12 8 5.2Z"/>',
      pause: '<path d="M7 5h3.5v14H7zm6.5 0H17v14h-3.5z"/>',
      replay: '<path d="M3.5 12a8.5 8.5 0 1 0 2.4-6"/><path d="M3.5 4.5v4.2H7.7"/><path d="M12 8v4.2l2.8 1.7"/>',
      volume: '<path d="M11 5 6.2 9H3v6h3.2L11 19V5z"/><path d="M15.4 8.6a4.8 4.8 0 0 1 0 6.8"/><path d="M17.8 6a8 8 0 0 1 0 12"/>',
      muted: '<path d="M11 5 6.2 9H3v6h3.2L11 19V5z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/>',
      mic: '<path d="M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><path d="M12 18v3"/><path d="M8.5 21h7"/>',
      edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>',
      chevron: '<path d="m6 9 6 6 6-6"/>',
      extend: '<g transform="rotate(-45 12 12)"><path d="M12 12c-2-2.67-4-4-6-4a4 4 0 1 0 0 8c2 0 4-1.33 6-4Zm0 0c2 2.67 4 4 6 4a4 4 0 0 0 0-8c-2 0-4 1.33-6 4Z"/></g>',
      sparkles: '<path d="M12 3v3"/><path d="M12 18v3"/><path d="M3 12h3"/><path d="M18 12h3"/><path d="m5.6 5.6 2.1 2.1"/><path d="m16.3 16.3 2.1 2.1"/><path d="m16.3 5.6-2.1 2.1"/><path d="m5.6 16.3 2.1-2.1"/><circle cx="12" cy="12" r="2.2"/>',
      check: '<path d="M20 7 9.5 17.5 4 12"/>',
      trash: '<path d="M3 6h18"/><path d="M8 6V4.8A1.8 1.8 0 0 1 9.8 3h4.4A1.8 1.8 0 0 1 16 4.8V6"/><path d="M19 6v13.2A1.8 1.8 0 0 1 17.2 21H6.8A1.8 1.8 0 0 1 5 19.2V6"/><path d="M10 10.5v6"/><path d="M14 10.5v6"/>',
    };

    function videoPreviewIconSvg(iconKey) {
      const solid = iconKey === 'play' || iconKey === 'pause';
      const paths = VIDEO_PREVIEW_ICONS[iconKey] || '';
      return `<svg class="video-preview-button-icon${solid ? ' is-solid' : ''}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${paths}</svg>`;
    }

    function videoPreviewButtonInnerHtml(iconKey, label) {
      return `${videoPreviewIconSvg(iconKey)}<span class="video-preview-button-label">${escapeHtml(label)}</span>`;
    }

    function getVideoPreviewButtonLabel(button) {
      const label = button?.querySelector?.('.video-preview-button-label');
      return String(label?.textContent || button?.textContent || '').trim();
    }

    function setVideoPreviewButtonLabel(button, text) {
      if (!button) return;
      const label = button.querySelector('.video-preview-button-label');
      if (label) {
        label.textContent = text;
        return;
      }
      button.textContent = text;
    }

    function setVideoPreviewButtonState(button, iconKey, label) {
      if (!button) return;
      if (button.dataset.icon === iconKey) {
        const labelEl = button.querySelector('.video-preview-button-label');
        if (labelEl && labelEl.textContent !== label) labelEl.textContent = label;
        return;
      }
      button.dataset.icon = iconKey;
      button.innerHTML = videoPreviewButtonInnerHtml(iconKey, label);
    }

    function bindVideoPreviewControls(video) {
      if (!video || !els.videoPreviewBody) return;
      const playPauseButton = els.videoPreviewBody.querySelector('[data-video-preview-action="toggle-play"]');
      const restartButton = els.videoPreviewBody.querySelector('[data-video-preview-action="restart"]');
      const muteButton = els.videoPreviewBody.querySelector('[data-video-preview-action="toggle-mute"]');
      const syncControls = () => {
        if (playPauseButton) {
          const paused = video.paused;
          setVideoPreviewButtonState(playPauseButton, paused ? 'play' : 'pause', paused ? '播放' : '暂停');
        }
        if (muteButton) {
          const muted = video.muted || video.volume === 0;
          setVideoPreviewButtonState(muteButton, muted ? 'muted' : 'volume', muted ? '取消静音' : '静音');
        }
      };
      playPauseButton?.addEventListener('click', () => {
        if (video.paused) {
          video.play().catch(() => {});
        } else {
          video.pause();
        }
      });
      restartButton?.addEventListener('click', () => {
        video.currentTime = 0;
        video.play().catch(() => {});
      });
      muteButton?.addEventListener('click', () => {
        video.muted = !video.muted;
        syncControls();
      });
      ['loadedmetadata', 'play', 'pause', 'volumechange', 'ended'].forEach((eventName) => {
        video.addEventListener(eventName, syncControls);
      });
      syncControls();
    }
