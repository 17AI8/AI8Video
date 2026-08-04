    const SMART_IMAGE_MAX_BYTES = 30 * 1024 * 1024;
    const SMART_IMAGE_MAX_EDGE = 4096;
    const SMART_IMAGE_RECENT_LIBRARY_LIMIT = 6;
    const SMART_IMAGE_ACCEPTED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
    const SMART_IMAGE_PROJECT_KEY = 'ai8video-smart-image-studio-v1';
    const SMART_IMAGE_DEFAULT_PROMPT = '自然提升曝光、白平衡、色彩层次、清晰度和画面质感，保持主体身份、构图与原图含义不变，避免过度锐化或失真。';
    const SMART_IMAGE_PRESETS = [
      {
        id: 'natural', label: '自然增强', meta: '曝光 · 色彩 · 清晰度', icon: 'sparkle',
        prompt: SMART_IMAGE_DEFAULT_PROMPT,
      },
      {
        id: 'portrait', label: '人像精修', meta: '肤色 · 质感 · 五官保护', icon: 'portrait',
        prompt: '自然优化人物肤色、光线、发丝和服装细节，保留真实皮肤纹理、人物身份、五官比例、年龄感和原始构图，不做过度磨皮。',
      },
      {
        id: 'product', label: '商品质感', meta: '材质 · 光影 · 干净背景', icon: 'product',
        prompt: '提升商品材质、边缘、光影层次和背景洁净度，保持商品结构、颜色、包装文字、Logo 与品牌标识准确，不改变产品形态。',
      },
      {
        id: 'food', label: '美食提亮', meta: '食欲感 · 层次 · 自然色', icon: 'food',
        prompt: '自然提升食物色泽、纹理、热气感和明暗层次，保持菜品真实形态与分量，不添加原图不存在的食材或装饰。',
      },
      {
        id: 'restore', label: '旧照修复', meta: '划痕 · 偏色 · 清晰度', icon: 'restore',
        prompt: '修复老照片的褪色、偏色、噪点、折痕和轻微划痕，恢复自然细节，严格保留人物身份、年代感、原始构图以及已有署名和标识。',
      },
      {
        id: 'background', label: '背景优化', meta: '主体保护 · 氛围统一', icon: 'scene',
        prompt: '保持前景主体、人物身份和商品结构不变，优化背景的杂乱元素、景深、光线和整体氛围，使主体更突出且边缘自然。',
      },
    ];

    const AI8SmartImage = {
      version: 7,
      state: {
        source: null,
        sourceSessions: {},
        recentLibraryHistory: [],
        results: [],
        selectedResultId: '',
        selectedJobId: '',
        deletedResultKeys: [],
        deletedJobIds: [],
        selectedPresetId: 'natural',
        prompt: SMART_IMAGE_DEFAULT_PROMPT,
        batchCount: 1,
        jobs: [],
        processing: false,
        promptOptimizing: false,
        viewMode: 'result',
        comparePosition: 50,
        exportFormat: 'png',
        exportQuality: 92,
        managingLibrary: false,
        hasOpened: false,
        saveTimer: null,
        modelName: '',
        status: '导入一张图片开始修图',
        statusTone: '',
      },
      imageCache: new Map(),
    };

    function smartImageId(prefix = 'item') {
      return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    }

    function smartImageClamp(value, min, max) {
      return Math.min(max, Math.max(min, Number(value) || 0));
    }

    function smartImageIcon(name) {
      const iconNames = {
        sparkle: 'wand-magic-sparkles',
        export: 'download',
        library: 'images',
        close: 'xmark',
        trash: 'trash-can',
        rotate: 'rotate-right',
        flip: 'left-right',
        save: 'floppy-disk',
        check: 'check',
        chevron: 'chevron-down',
      };
      return fontAwesomeIconMarkup(iconNames[name] || 'wand-magic-sparkles', 'smart-image-icon');
    }

    function smartImageLeftPanelMarkup() {
      return `
        <aside class="smart-image-studio-left" aria-label="素材图片与其任务队列">
          <section class="smart-image-step-card smart-image-library-section" aria-label="最近选择的图片">
            <div class="smart-image-step-heading smart-image-step-heading--inline"><span>1</span><div><strong>选择图片</strong><small>最近选择 · 最多 6 张</small></div></div>
            <div id="smartImageLibraryList" class="smart-image-library-list"></div>
            <button type="button" class="smart-image-text-action" data-smart-image-action="manage-library">管理素材库</button>
          </section>
          <section id="smartImageJobSection" class="smart-image-job-section smart-image-sidebar-job-section" aria-label="当前素材的任务队列"><div class="smart-image-section-title"><div><strong>任务队列</strong><span id="smartImageJobCount">0 项</span></div></div><div id="smartImageJobList" class="smart-image-job-list"></div></section>
        </aside>
      `;
    }

    function smartImageMainPanelMarkup() {
      return `
        <main class="smart-image-studio-main">
          <div class="smart-image-preview-head">
            <div><strong id="smartImagePreviewTitle">等待导入图片</strong></div>
            <div class="smart-image-view-switch" role="group" aria-label="预览模式"><button type="button" data-smart-image-view="result" aria-pressed="false">结果</button><button type="button" data-smart-image-view="source" aria-pressed="true">原图</button><button type="button" data-smart-image-view="compare" aria-pressed="false">前后对比</button></div>
          </div>
          <section id="smartImagePreview" class="smart-image-preview" aria-live="polite"></section>
          <section class="smart-image-result-section"><div class="smart-image-section-title"><div><strong>当前任务结果</strong><span id="smartImageResultCount">0 张</span></div><small>切换任务会同步切换这里的结果</small></div><div id="smartImageResultList" class="smart-image-result-list"></div></section>
        </main>
      `;
    }

    function smartImageRightPanelMarkup() {
      return `
        <aside class="smart-image-studio-right" aria-label="描述、生成与导出">
          <section class="smart-image-ai-card smart-image-step-card smart-image-step-card--primary">
            <div class="smart-image-step-heading smart-image-step-heading--inline"><span>2</span><div><strong>描述并生成</strong><small id="smartImagePresetSummary">按描述执行</small></div></div>
            <textarea id="smartImagePrompt" aria-label="图片修改描述" maxlength="2000" rows="6" placeholder="例如：自然提亮人物，保留真实皮肤纹理，不改变五官和构图">${SMART_IMAGE_DEFAULT_PROMPT}</textarea>
            <div class="smart-image-prompt-actions"><button type="button" data-smart-image-action="prompt-optimize">${smartImageIcon('sparkle')}AI 优化描述</button><button type="button" data-smart-image-action="prompt-reset">恢复建议</button></div>
            <div class="smart-image-generate-options"><label for="smartImageBatchCount"><span>本次生成</span><select id="smartImageBatchCount" aria-label="生成结果数量"><option value="1">1 张</option><option value="2">2 张</option><option value="3">3 张</option><option value="4">4 张</option><option value="5">5 张</option><option value="6">6 张</option><option value="7">7 张</option><option value="8">8 张</option></select></label></div>
            <button type="button" class="smart-image-generate-button primary" data-smart-image-action="enqueue">${smartImageIcon('sparkle')}<span id="smartImageGenerateLabel">开始 AI 修图</span></button>
          </section>

          <details id="smartImageAdjustSection" class="smart-image-disclosure">
            <summary><span>${smartImageIcon('sparkle')}<span><strong>可选：快速微调</strong><small>亮度、对比度、比例和旋转</small></span></span>${smartImageIcon('chevron')}</summary>
            <div class="smart-image-disclosure-body"><div class="smart-image-panel-title"><span>当前预览</span><button type="button" data-smart-image-action="reset-adjustments">重置</button></div><label class="smart-image-slider-row"><span>亮度 <b data-smart-image-adjustment-value="brightness">100</b></span><input type="range" min="50" max="160" value="100" data-smart-image-adjustment="brightness"></label><label class="smart-image-slider-row"><span>对比度 <b data-smart-image-adjustment-value="contrast">100</b></span><input type="range" min="50" max="180" value="100" data-smart-image-adjustment="contrast"></label><label class="smart-image-slider-row"><span>饱和度 <b data-smart-image-adjustment-value="saturation">100</b></span><input type="range" min="0" max="200" value="100" data-smart-image-adjustment="saturation"></label><div class="smart-image-transform-row"><button type="button" data-smart-image-action="rotate">${smartImageIcon('rotate')}旋转</button><button type="button" data-smart-image-action="flip">${smartImageIcon('flip')}翻转</button></div><div class="smart-image-ratio-row" role="group" aria-label="裁切比例"><button type="button" data-smart-image-ratio="original" aria-pressed="true">原比例</button><button type="button" data-smart-image-ratio="1:1" aria-pressed="false">1:1</button><button type="button" data-smart-image-ratio="4:5" aria-pressed="false">4:5</button><button type="button" data-smart-image-ratio="9:16" aria-pressed="false">9:16</button><button type="button" data-smart-image-ratio="16:9" aria-pressed="false">16:9</button></div></div>
          </details>

          <section id="smartImageFinishSection" class="smart-image-finish-card smart-image-step-card">
            <div class="smart-image-step-heading smart-image-step-heading--inline"><span>3</span><div><strong>对比并导出</strong><small>导出为副本，不覆盖原图</small></div></div>
            <div class="smart-image-export-actions"><button type="button" class="primary" data-smart-image-action="export-current">${smartImageIcon('export')}导出当前</button><button type="button" data-smart-image-action="save-library">${smartImageIcon('library')}存入素材库</button></div>
            <details class="smart-image-export-settings"><summary><span>格式与质量</span>${smartImageIcon('chevron')}</summary><div class="smart-image-export-grid"><label for="smartImageExportFormat">格式<select id="smartImageExportFormat"><option value="png">PNG</option><option value="jpeg">JPEG</option><option value="webp">WebP</option></select></label><label id="smartImageExportQualityRow" for="smartImageExportQuality">质量 <b id="smartImageExportQualityValue">92</b><input id="smartImageExportQuality" type="range" min="60" max="100" value="92"></label></div></details>
          </section>
        </aside>
      `;
    }

    function smartImageEditorMarkup() {
      return `
        <div id="smartImageEditorModal" class="modal-shell hidden" role="dialog" aria-modal="true" aria-labelledby="smartImageEditorTitle">
          <div class="smart-image-studio">
            <header class="smart-image-studio-head">
              <div class="smart-image-brand"><span class="smart-image-brand-mark" aria-hidden="true"></span><div><strong id="smartImageEditorTitle">智能修图</strong><span>选图、描述、生成，再对比导出</span></div></div>
              <div class="smart-image-head-actions"><span id="smartImageModelMeta" class="smart-image-model-badge">检查图片模型</span><span id="smartImageSaveState" class="smart-image-save-state" data-tone="saved">${smartImageIcon('save')}本地自动保存</span><button type="button" class="smart-image-icon-button" data-smart-image-action="close" aria-label="关闭智能修图">${smartImageIcon('close')}</button></div>
            </header>
            <div class="smart-image-studio-body">${smartImageLeftPanelMarkup()}${smartImageMainPanelMarkup()}${smartImageRightPanelMarkup()}</div>
            <footer class="smart-image-message-bar"><span id="smartImageStatus" aria-live="polite">导入一张图片开始修图</span><span>原图保留 · Esc 关闭</span></footer>
          </div>
        </div>
      `;
    }

    function ensureSmartImageEditorModal() {
      let modal = document.getElementById('smartImageEditorModal');
      if (modal) return modal;
      document.body.insertAdjacentHTML('beforeend', smartImageEditorMarkup());
      return document.getElementById('smartImageEditorModal');
    }

    function smartImageElements() {
      const modal = ensureSmartImageEditorModal();
      return {
        modal,
        libraryList: modal.querySelector('#smartImageLibraryList'),
        preview: modal.querySelector('#smartImagePreview'),
        previewTitle: modal.querySelector('#smartImagePreviewTitle'),
        resultList: modal.querySelector('#smartImageResultList'),
        resultCount: modal.querySelector('#smartImageResultCount'),
        jobList: modal.querySelector('#smartImageJobList'),
        jobCount: modal.querySelector('#smartImageJobCount'),
        prompt: modal.querySelector('#smartImagePrompt'),
        batchCount: modal.querySelector('#smartImageBatchCount'),
        presetSummary: modal.querySelector('#smartImagePresetSummary'),
        generateLabel: modal.querySelector('#smartImageGenerateLabel'),
        modelMeta: modal.querySelector('#smartImageModelMeta'),
        saveState: modal.querySelector('#smartImageSaveState'),
        status: modal.querySelector('#smartImageStatus'),
        exportFormat: modal.querySelector('#smartImageExportFormat'),
        exportQuality: modal.querySelector('#smartImageExportQuality'),
      };
    }

    function setSmartImageStatus(text, tone = '') {
      AI8SmartImage.state.status = String(text || '');
      AI8SmartImage.state.statusTone = tone;
      const status = smartImageElements().status;
      status.textContent = AI8SmartImage.state.status;
      status.dataset.tone = tone;
    }

    function setSmartImageSaveState(text, tone = 'saved') {
      const element = smartImageElements().saveState;
      if (!element) return;
      element.dataset.tone = tone;
      element.innerHTML = `${smartImageIcon(tone === 'saved' ? 'check' : 'save')}${escapeHtml(text)}`;
    }

    function updateSmartImageModelMeta() {
      const elements = smartImageElements();
      const modelField = (state.authSettings?.fields || []).find((field) => field?.envName === 'AI8VIDEO_IMAGE_MODEL');
      AI8SmartImage.state.modelName = String(modelField?.value || '').trim();
      const configured = !!state.health?.hasImageModel;
      elements.modelMeta.textContent = configured ? `图片模型 · ${AI8SmartImage.state.modelName || '已配置'}` : '图片模型未配置';
      elements.modelMeta.dataset.tone = configured ? 'ok' : 'error';
    }

    function openSmartImageEditor() {
      const elements = smartImageElements();
      elements.modal.classList.remove('hidden');
      document.querySelector('[data-open-smart-image-editor-entry]')?.classList.add('is-active');
      document.querySelector('[data-open-smart-image-editor-entry]')?.setAttribute('aria-expanded', 'true');
      updateSmartImageModelMeta();
      if (!AI8SmartImage.state.hasOpened) {
        AI8SmartImage.state.hasOpened = true;
        void AI8SmartImage.restoreProject?.();
        void AI8SmartImage.refreshLibrary?.();
      }
      AI8SmartImage.render?.();
      window.setTimeout(() => {
        const firstControl = AI8SmartImage.state.source
          ? elements.prompt
          : elements.modal.querySelector('[data-smart-image-action="manage-library"]');
        firstControl?.focus();
      }, 0);
    }

    function closeSmartImageEditor() {
      document.getElementById('smartImageEditorModal')?.classList.add('hidden');
      document.querySelector('[data-open-smart-image-editor-entry]')?.classList.remove('is-active');
      document.querySelector('[data-open-smart-image-editor-entry]')?.setAttribute('aria-expanded', 'false');
      AI8SmartImage.saveProject?.();
    }

    function smartImageModalIsOpen() {
      const modal = document.getElementById('smartImageEditorModal');
      return !!modal && !modal.classList.contains('hidden');
    }
