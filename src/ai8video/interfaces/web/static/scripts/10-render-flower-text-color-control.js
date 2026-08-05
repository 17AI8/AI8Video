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

    function getResultFolderCompletedCount(gallery) {
      return getPlayableResultItems(gallery).length;
    }

    function getResultFolderTotalCount(session) {
      return getResultFolderCompletedCount(buildResultFolderGalleryModel(session, 'source'))
        + getResultFolderCompletedCount(buildResultFolderGalleryModel(session, 'burned'));
    }

    function renderProgress() {
      const session = getActiveSession();
      const model = buildProgressModel(session);
      if (!model) {
        els.progressPanel.innerHTML = '';
        if (els.sidebarResultsSection) els.sidebarResultsSection.hidden = true;
        return;
      }
      if (els.sidebarResultsSection) els.sidebarResultsSection.hidden = false;
      const resultCount = getResultFolderTotalCount(session);
      els.progressPanel.innerHTML = buildSidebarNavItemMarkup({
        icon: 'progress',
        title: '查看结果',
        meta: `${resultCount} 个结果`,
        count: resultCount,
        actionLabel: '查看结果',
        attrs: 'data-show-result-modal="1"',
        extraClass: 'progress-card',
      });
    }

    function renderProgressOverview(overview, stepChainMarkup = '') {
      if (!overview) return '';
      const pendingClass = overview.pending ? ' pending' : '';
      const terminalClass = overview.terminal ? ' terminal' : '';
      const percent = Math.max(0, Math.min(100, Number(overview.percent || 0)));
      const stepChain = String(stepChainMarkup || '').trim();
      const middle = stepChain || `
        <div class="progress-overview-track${pendingClass}${terminalClass}" aria-hidden="true">
          <div class="progress-overview-fill" style="--progress: ${percent}%"></div>
        </div>
      `;
      return `
        <div class="progress-overview">
          <div
            class="progress-overview-row${stepChain ? ' has-step-chain' : ''}"
            role="progressbar"
            aria-label="总体进度"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow="${percent}"
            aria-valuetext="${escapeHtml(overview.label || `${percent}%`)}"
          >
            <strong>总体进度</strong>
            ${middle}
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
        state.resultModal = { visible: false, directory: 'burned', batchMerge: false, batchMergeSubmitting: false, selectedKeys: [] };
      }
      if (!['source', 'burned'].includes(state.resultModal.directory)) state.resultModal.directory = 'burned';
      if (!Array.isArray(state.resultModal.selectedKeys)) state.resultModal.selectedKeys = [];
      return state.resultModal;
    }



























    function renderResultModal({ preserveScroll = false } = {}) {
      if (!els.resultModal) return;
      const modalState = ensureResultModalState();
      const visible = !!state.resultModal.visible;
      els.resultModal.classList.toggle('hidden', !visible);
      const activeDirectory = modalState.directory;
      const isSourceDirectory = activeDirectory === 'source';
      const gallery = buildResultFolderGalleryModel(getActiveSession(), activeDirectory);
      const completedCount = getResultFolderCompletedCount(gallery);
      els.resultModalTitle.textContent = '全部生成结果';
      els.resultModalSub.textContent = completedCount
        ? `${isSourceDirectory ? '原片' : '烧录结果'}目录中 ${completedCount} 个视频`
        : `${isSourceDirectory ? '原片' : '烧录结果'}目录当前没有视频。`;
      [els.resultModalSourceTab, els.resultModalBurnedTab].forEach((tab) => {
        const selected = tab?.dataset.resultDirectory === activeDirectory;
        tab?.classList.toggle('is-active', selected);
        tab?.setAttribute('aria-selected', selected ? 'true' : 'false');
        tab?.setAttribute('tabindex', selected ? '0' : '-1');
      });
      els.resultModalOpenFolderButton.textContent = isSourceDirectory ? '打开原片文件夹' : '打开成片文件夹';
      els.resultModalOpenFolderButton.title = isSourceDirectory ? '打开纯净原片文件夹' : '打开最终烧录成片文件夹';
      els.resultModalOpenFolderButton.dataset.artifactKind = activeDirectory;
      els.resultModalOpenFolderButton.disabled = false;
      els.resultModalOpenFolderButton.dataset.archiveKey = '';
      els.resultModalOpenFolderButton.dataset.localPath = '';
      const playableKeys = new Set(getPlayableResultItems(gallery).map(resolveResultBatchMergeKey).filter(Boolean));
      modalState.selectedKeys = modalState.selectedKeys.filter((key) => playableKeys.has(key));
      els.resultModalBatchMergeGroup.classList.toggle('is-active', !!modalState.batchMerge);
      els.resultModalBatchMergeButton.setAttribute('aria-pressed', modalState.batchMerge ? 'true' : 'false');
      els.resultModalBatchMergeConfirmButton.classList.toggle('hidden', !modalState.batchMerge);
      els.resultModalBatchMergeConfirmButton.disabled = modalState.batchMergeSubmitting || modalState.selectedKeys.length < 2;
      els.resultModalBatchMergeConfirmButton.textContent = modalState.batchMergeSubmitting ? '合并中' : '确认';
      els.resultModalRefreshButton.disabled = modalState.batchMergeSubmitting;
      els.resultModalOpenFolderButton.disabled = modalState.batchMergeSubmitting;
      if (!visible) return;
      const previousScrollTop = preserveScroll ? els.resultModalBody.scrollTop : 0;
      els.resultModalBody.innerHTML = renderResultMediaWall(gallery, {
        wall: true,
        batchMerge: !!modalState.batchMerge,
        selectedKeys: modalState.selectedKeys,
      });
      if (preserveScroll) {
        els.resultModalBody.scrollTop = previousScrollTop;
      }
    }

    function openResultModal() {
      state.resultModal.visible = true;
      renderResultModal();
    }

    function selectResultDirectory(directory) {
      const modalState = ensureResultModalState();
      if (modalState.batchMergeSubmitting || !['source', 'burned'].includes(directory)) return;
      modalState.directory = directory;
      modalState.batchMerge = false;
      modalState.selectedKeys = [];
      renderResultModal();
    }

    function closeResultModal() {
      state.resultModal.visible = false;
      state.resultModal.batchMerge = false;
      state.resultModal.selectedKeys = [];
      renderResultModal();
    }

    const VIDEO_PREVIEW_ICON_NAMES = {
      mic: 'microphone',
      edit: 'pen-to-square',
      crop: 'crop-simple',
      scissors: 'scissors',
      settings: 'gear',
      chevron: 'chevron-down',
      sparkles: 'wand-magic-sparkles',
      check: 'check',
      undo: 'rotate-left',
      redo: 'rotate-right',
      trash: 'trash-can',
      regenerate: 'arrows-rotate',
      extend: 'arrow-right-long',
    };

    function videoPreviewIconSvg(iconKey) {
      return fontAwesomeIconMarkup(
        VIDEO_PREVIEW_ICON_NAMES[iconKey] || 'triangle-exclamation',
        'video-preview-button-icon',
      );
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
