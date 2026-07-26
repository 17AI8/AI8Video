    const SMART_IMAGE_MAX_BYTES = 30 * 1024 * 1024;
    const SMART_IMAGE_MAX_EDGE = 4096;
    const SMART_IMAGE_MAX_LAYERS = 3;
    const SMART_IMAGE_ACCEPTED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
    const SMART_IMAGE_PROJECT_KEY = 'ai8video-smart-image-canvas-v2';

    const AI8SmartImage = {
      version: 2,
      state: {
        nodes: [],
        selectedIds: [],
        viewport: { x: 120, y: 90, zoom: 1 },
        tool: 'select',
        background: 'grid',
        history: [],
        future: [],
        clipboard: [],
        interaction: null,
        spacePanning: false,
        processing: false,
        processingDone: 0,
        processingTotal: 0,
        modelBatchCount: 1,
        renderQueued: false,
        hasOpened: false,
        modelName: '',
        projectName: '未命名画布',
        materialQuery: '',
        materialLoading: false,
        status: '导入图片或从左侧创建内容',
        statusTone: '',
      },
      imageCache: new Map(),
    };

    function smartImageIcon(name) {
      const paths = {
        select: '<path d="M5 3l6.8 15.6 2.2-5.3 5.5-2.2L5 3z"/>',
        hand: '<path d="M8 11V6a1.5 1.5 0 013 0v4-6a1.5 1.5 0 013 0v6-4a1.5 1.5 0 013 0v5-2a1.5 1.5 0 013 0v5c0 4-2.5 7-7 7h-1c-2 0-3.5-1-4.8-2.6L3 13a1.7 1.7 0 012.7-2l2.3 2.5V11z"/>',
        mask: '<path d="M12 3a9 9 0 100 18h1.5a2.5 2.5 0 001.2-4.7l-.8-.4a1.5 1.5 0 01.7-2.9H17a4 4 0 004-4c0-3.3-4-6-9-6z"/><circle cx="7.5" cy="10" r="1"/><circle cx="10" cy="6.8" r="1"/><circle cx="14" cy="6.8" r="1"/>',
        upload: '<path d="M12 16V4m0 0L7 9m5-5l5 5"/><path d="M4 15v5h16v-5"/>',
        text: '<path d="M5 5V3h14v2M12 3v18M8 21h8"/>',
        rect: '<rect x="4" y="5" width="16" height="14" rx="2"/>',
        ellipse: '<ellipse cx="12" cy="12" rx="8" ry="6"/>',
        undo: '<path d="M9 7H4v-5"/><path d="M4 7c2-3 5-4 8-4a8 8 0 110 16h-2"/>',
        redo: '<path d="M15 7h5v-5"/><path d="M20 7c-2-3-5-4-8-4a8 8 0 100 16h2"/>',
        fit: '<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>',
        trash: '<path d="M4 7h16M9 7V4h6v3m3 0l-1 14H7L6 7"/>',
        duplicate: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
        layers: '<path d="M12 3L3 8l9 5 9-5-9-5z"/><path d="M3 12l9 5 9-5M3 16l9 5 9-5"/>',
        export: '<path d="M12 3v12m0 0l-5-5m5 5l5-5"/><path d="M4 19v2h16v-2"/>',
        close: '<path d="M5 5l14 14M19 5L5 19"/>',
      };
      return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.rect}</svg>`;
    }

    function smartImageToolButton(tool, label, icon) {
      return `<button type="button" class="smart-image-tool" data-smart-image-tool="${tool}" title="${label}" aria-label="${label}" aria-pressed="false">${smartImageIcon(icon)}</button>`;
    }

    function smartImageTopbarMarkup() {
      return `
        <div class="smart-image-brand">
          <span class="smart-image-brand-mark">AI</span>
          <div><strong id="smartImageEditorTitle">智能修图画布</strong></div>
        </div>
        <div class="smart-image-head-center-actions"><button data-smart-image-action="export">导出画布</button></div>
        <div class="smart-image-head-actions">
          <button type="button" class="smart-image-icon-button" data-smart-image-action="close" aria-label="关闭智能修图">${smartImageIcon('close')}</button>
        </div>
      `;
    }

    function smartImageAssetPanelMarkup() {
      return `
        <aside class="smart-image-side smart-image-assets" aria-label="素材与创建">
          <button type="button" class="smart-image-upload-button" data-smart-image-action="upload">${smartImageIcon('upload')}<span><strong>导入图片</strong><small>JPG / PNG / WebP，可多选</small></span></button>
          <section><div class="smart-image-panel-title"><strong>快速创建</strong></div>
            <div class="smart-image-create-grid">
              <button type="button" data-smart-image-action="add-text">${smartImageIcon('text')}<span>文字</span></button>
            </div>
          </section>
          <section class="smart-image-library-entry"><div class="smart-image-panel-title"><strong>导入图片素材</strong><button type="button" data-smart-image-library-manage>管理</button></div></section>
          <section class="smart-image-asset-section"><span id="smartImageAssetCount" hidden>0</span><div id="smartImageAssetList" class="smart-image-asset-list"></div></section>
        </aside>
      `;
    }

    function smartImageCanvasMarkup() {
      return `
        <main class="smart-image-canvas-column">
          <div class="smart-image-toolbar" role="toolbar" aria-label="画布工具">
            <div class="smart-image-toolbar-group">${smartImageToolButton('select', '选择工具 V', 'select')}${smartImageToolButton('hand', '抓手工具 H', 'hand')}${smartImageToolButton('mask', '局部蒙版 B', 'mask')}</div>
            <span class="smart-image-toolbar-divider"></span>
            <div class="smart-image-toolbar-group"><button type="button" data-smart-image-action="undo" title="撤销 Ctrl+Z">${smartImageIcon('undo')}</button><button type="button" data-smart-image-action="redo" title="重做 Ctrl+Shift+Z">${smartImageIcon('redo')}</button></div>
            <span class="smart-image-toolbar-spacer"></span>
            <div class="smart-image-toolbar-group"><button type="button" data-smart-image-action="background">网格背景</button></div>
            <div class="smart-image-toolbar-group smart-image-zoom-controls"><button type="button" data-smart-image-action="zoom-out" aria-label="缩小">−</button><button type="button" id="smartImageZoomValue" data-smart-image-action="zoom-reset">100%</button><button type="button" data-smart-image-action="zoom-in" aria-label="放大">＋</button><button type="button" data-smart-image-action="fit" title="适应画布">${smartImageIcon('fit')}</button></div>
          </div>
          <div id="smartImageCanvasViewport" class="smart-image-canvas-viewport is-grid" tabindex="0" aria-label="无限画布">
            <div id="smartImageCanvasScene" class="smart-image-canvas-scene"></div>
            <div id="smartImageMarquee" class="smart-image-marquee hidden"></div>
            <div id="smartImageEmpty" class="smart-image-canvas-empty"><span>${smartImageIcon('upload')}</span><strong>把图片拖到这里开始创作</strong><small>滚轮缩放，空格拖动画布，支持多选和撤销</small><button type="button" data-smart-image-action="upload">选择图片</button></div>
            <div id="smartImageMinimap" class="smart-image-minimap" aria-label="画布小地图"></div>
            <div class="smart-image-processing-overlay" aria-live="polite" aria-hidden="true"><div class="smart-image-processing-card"><strong id="smartImageProcessingPercent">0% · 0/0</strong><div class="smart-image-processing-track"><i id="smartImageProcessingBar"></i></div></div></div>
          </div>
        </main>
      `;
    }

    function smartImageInspectorMarkup() {
      return `
        <aside class="smart-image-side smart-image-inspector" aria-label="属性与图层">
          <section><div class="smart-image-panel-title"><strong>图层</strong><span id="smartImageSelectionMeta">未选择</span></div><div id="smartImageLayerList" class="smart-image-layer-list"></div></section>
          <section id="smartImageContentSection"><div class="smart-image-panel-title"><strong>内容与样式</strong><span id="smartImageContentMeta">选择文字或形状</span></div>
            <textarea class="smart-image-content-input" data-smart-image-content="text" maxlength="500" rows="3" placeholder="文字内容"></textarea>
            <div class="smart-image-style-fields"><label>颜色<input type="color" data-smart-image-content="color" value="#ffffff"></label><label>字号<input type="number" data-smart-image-content="fontSize" min="8" max="240" value="36"></label><label>形状<input type="color" data-smart-image-content="fill" value="#725cf3"></label></div>
          </section>
          <section id="smartImageAppearanceSection"><div class="smart-image-panel-title"><strong>画面调整</strong><button data-smart-image-action="reset-adjustments">重置</button></div>
            <label class="smart-image-slider-row"><span>透明度 <b data-smart-image-value="opacity">100</b></span><input type="range" min="0" max="100" value="100" data-smart-image-adjustment="opacity"></label>
            <label class="smart-image-slider-row"><span>亮度 <b data-smart-image-value="brightness">100</b></span><input type="range" min="50" max="160" value="100" data-smart-image-adjustment="brightness"></label>
            <label class="smart-image-slider-row"><span>对比度 <b data-smart-image-value="contrast">100</b></span><input type="range" min="50" max="180" value="100" data-smart-image-adjustment="contrast"></label>
            <label class="smart-image-slider-row"><span>饱和度 <b data-smart-image-value="saturation">100</b></span><input type="range" min="0" max="200" value="100" data-smart-image-adjustment="saturation"></label>
            <label class="smart-image-slider-row"><span>模糊 <b data-smart-image-value="blur">0</b></span><input type="range" min="0" max="20" value="0" data-smart-image-adjustment="blur"></label>
          </section>
          <section class="smart-image-model-section"><div class="smart-image-panel-title"><strong>图片模型</strong><span id="smartImageModelMeta">检查配置中</span></div>
            <textarea id="smartImagePrompt" class="smart-image-prompt" maxlength="2000" rows="4" placeholder="描述希望图片模型如何修改">自然提升画面质感、曝光、色彩和清晰度，保持主体身份、构图与原图含义不变。</textarea>
            <div class="smart-image-model-actions"><button class="primary" data-smart-image-action="model-edit">AI 修图</button><select id="smartImageModelBatchCount" aria-label="批量生图数量"><option value="1">X1</option><option value="2">X2</option><option value="3">X3</option><option value="4">X4</option><option value="5">X5</option><option value="6">X6</option><option value="7">X7</option><option value="8">X8</option></select></div>
          </section>
        </aside>
      `;
    }

    function smartImageEditorMarkup() {
      return `
        <div id="smartImageEditorModal" class="modal-shell hidden" role="dialog" aria-modal="true" aria-labelledby="smartImageEditorTitle">
          <div class="smart-image-editor-panel">
            <header class="smart-image-editor-head">${smartImageTopbarMarkup()}</header>
            <div class="smart-image-editor-workspace">${smartImageAssetPanelMarkup()}${smartImageCanvasMarkup()}${smartImageInspectorMarkup()}</div>
            <footer class="smart-image-message-bar"><span id="smartImageStatus" aria-live="polite">导入图片或添加文字</span></footer>
            <input id="smartImageUploadInput" class="hidden" type="file" multiple accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp">
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
        viewport: modal.querySelector('#smartImageCanvasViewport'),
        scene: modal.querySelector('#smartImageCanvasScene'),
        empty: modal.querySelector('#smartImageEmpty'),
        marquee: modal.querySelector('#smartImageMarquee'),
        minimap: modal.querySelector('#smartImageMinimap'),
        uploadInput: modal.querySelector('#smartImageUploadInput'),
        assetList: modal.querySelector('#smartImageAssetList'),
        assetCount: modal.querySelector('#smartImageAssetCount'),
        layerList: modal.querySelector('#smartImageLayerList'),
        selectionMeta: modal.querySelector('#smartImageSelectionMeta'),
        status: modal.querySelector('#smartImageStatus'),
        zoomValue: modal.querySelector('#smartImageZoomValue'),
        modelMeta: modal.querySelector('#smartImageModelMeta'),
        prompt: modal.querySelector('#smartImagePrompt'),
      };
    }

    function setSmartImageStatus(text, tone = '') {
      AI8SmartImage.state.status = String(text || '');
      AI8SmartImage.state.statusTone = tone;
      const status = smartImageElements().status;
      status.textContent = AI8SmartImage.state.status;
      status.dataset.tone = tone;
    }

    function updateSmartImageModelMeta() {
      const elements = smartImageElements();
      const modelField = (state.authSettings?.fields || []).find((field) => field?.envName === 'AI8VIDEO_IMAGE_MODEL');
      const configured = !!state.health?.hasImageModel;
      AI8SmartImage.state.modelName = String(modelField?.value || '').trim();
      elements.modelMeta.textContent = configured ? (AI8SmartImage.state.modelName || '已配置') : '未配置';
      elements.modal.querySelectorAll('[data-smart-image-action^="model-"]').forEach((button) => {
        button.title = configured ? `使用 ${AI8SmartImage.state.modelName || '当前图片模型'}` : '请先在设置中配置图片模型';
      });
    }

    function openSmartImageEditor() {
      const elements = smartImageElements();
      elements.modal.classList.remove('hidden');
      updateSmartImageModelMeta();
      document.querySelector('[data-open-smart-image-editor-entry]')?.classList.add('is-active');
      document.querySelector('[data-open-smart-image-editor-entry]')?.setAttribute('aria-expanded', 'true');
      if (!AI8SmartImage.state.hasOpened) {
        AI8SmartImage.state.hasOpened = true;
        AI8SmartImage.restoreProject?.();
      }
      AI8SmartImage.render?.();
      window.setTimeout(() => elements.viewport.focus(), 0);
    }

    function closeSmartImageEditor() {
      document.getElementById('smartImageEditorModal')?.classList.add('hidden');
      document.querySelector('[data-open-smart-image-editor-entry]')?.classList.remove('is-active');
      document.querySelector('[data-open-smart-image-editor-entry]')?.setAttribute('aria-expanded', 'false');
      AI8SmartImage.saveProject?.();
    }
