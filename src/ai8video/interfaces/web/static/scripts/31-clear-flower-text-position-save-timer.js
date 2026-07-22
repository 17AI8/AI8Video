    function clearFlowerTextPositionSaveTimer() {
      if (state.flowerText?.positionSaveTimer) {
        clearTimeout(state.flowerText.positionSaveTimer);
        state.flowerText.positionSaveTimer = null;
      }
    }

    function clearFlowerTextPreviewTimer() {
      if (state.flowerText?.previewTimer) {
        clearTimeout(state.flowerText.previewTimer);
        state.flowerText.previewTimer = null;
      }
    }

    function isFlowerTextColorStyleControl(control) {
      if (!control) return false;
      const key = String(control.getAttribute?.('data-flower-text-style') || '');
      const type = String(control.type || '').toLowerCase();
      return type === 'color' && (key === 'textColor' || key === 'strokeColor');
    }

    function flowerTextColorToRgb(color) {
      const hex = normalizeFlowerTextColor(color, '#000000').replace('#', '');
      const value = /^[0-9a-f]{6}$/i.test(hex) ? hex : '000000';
      return {
        r: parseInt(value.slice(0, 2), 16),
        g: parseInt(value.slice(2, 4), 16),
        b: parseInt(value.slice(4, 6), 16),
      };
    }

    function flowerTextRgbToHsv(rgb) {
      const r = Math.max(0, Math.min(255, Number(rgb?.r) || 0)) / 255;
      const g = Math.max(0, Math.min(255, Number(rgb?.g) || 0)) / 255;
      const b = Math.max(0, Math.min(255, Number(rgb?.b) || 0)) / 255;
      const max = Math.max(r, g, b);
      const min = Math.min(r, g, b);
      const delta = max - min;
      let h = 0;
      if (delta) {
        if (max === r) h = ((g - b) / delta) % 6;
        else if (max === g) h = (b - r) / delta + 2;
        else h = (r - g) / delta + 4;
        h *= 60;
        if (h < 0) h += 360;
      }
      return {
        h,
        s: max === 0 ? 0 : delta / max * 100,
        v: max * 100,
      };
    }

    function flowerTextColorToHsv(color) {
      return flowerTextRgbToHsv(flowerTextColorToRgb(color));
    }

    function flowerTextRgbToHex(rgb) {
      return `#${['r', 'g', 'b'].map((channel) => {
        const value = Math.max(0, Math.min(255, Math.round(Number(rgb?.[channel]) || 0)));
        return value.toString(16).padStart(2, '0');
      }).join('')}`;
    }

    function flowerTextHsvToHex(hsv) {
      const h = ((Number(hsv?.h) || 0) % 360 + 360) % 360;
      const s = Math.max(0, Math.min(100, Number(hsv?.s) || 0)) / 100;
      const v = Math.max(0, Math.min(100, Number(hsv?.v) || 0)) / 100;
      const c = v * s;
      const x = c * (1 - Math.abs((h / 60) % 2 - 1));
      const m = v - c;
      let r = 0;
      let g = 0;
      let b = 0;
      if (h < 60) [r, g, b] = [c, x, 0];
      else if (h < 120) [r, g, b] = [x, c, 0];
      else if (h < 180) [r, g, b] = [0, c, x];
      else if (h < 240) [r, g, b] = [0, x, c];
      else if (h < 300) [r, g, b] = [x, 0, c];
      else [r, g, b] = [c, 0, x];
      return flowerTextRgbToHex({
        r: (r + m) * 255,
        g: (g + m) * 255,
        b: (b + m) * 255,
      });
    }

    function updateFlowerTextColorChannel(control) {
      if (!control) return;
      const key = control.getAttribute('data-flower-text-color-key') === 'strokeColor' ? 'strokeColor' : 'textColor';
      const channel = ['h', 's', 'v'].includes(control.getAttribute('data-flower-text-color-channel'))
        ? control.getAttribute('data-flower-text-color-channel')
        : 'h';
      const current = flowerTextColorToHsv(state.flowerText?.[key] || (key === 'strokeColor' ? '#121826' : '#ffee43'));
      if (channel === 'h') {
        current.h = Math.max(0, Math.min(360, Math.round(Number(control.value) || 0)));
        if (current.s <= 2) current.s = 100;
      }
      if (channel === 's') current.s = Math.max(0, Math.min(100, Math.round(Number(control.value) || 0)));
      if (channel === 'v') current.v = Math.max(0, Math.min(100, Math.round(Number(control.value) || 0)));
      const nextColor = flowerTextHsvToHex(current);
      syncFlowerTextEditorDraft();
      state.flowerText = {
        ...(state.flowerText || {}),
        [key]: nextColor,
        activeColorPicker: key,
        error: '',
        notice: '点击外部后自动保存',
      };
      applyFlowerTextEditorStyle();
      applyFlowerTextColorControlStyle(key);
      setFlowerTextSaveStatus(state.flowerText.notice);
      clearFlowerTextAutoSaveTimer();
      clearFlowerTextPreviewTimer();
    }

    function applyFlowerTextColorControlStyle(key) {
      const safeKey = key === 'strokeColor' ? 'strokeColor' : 'textColor';
      const color = normalizeFlowerTextColor(state.flowerText?.[safeKey], safeKey === 'strokeColor' ? '#121826' : '#ffee43');
      const hsv = flowerTextColorToHsv(color);
      const hueColor = flowerTextHsvToHex({ h: hsv.h, s: 100, v: 100 });
      document.querySelectorAll(`[data-flower-text-color-field="${safeKey}"]`).forEach((field) => {
        field.querySelectorAll('.flower-text-color-button, .flower-text-color-popover').forEach((node) => {
          node.style.setProperty('--flower-text-color', color);
          node.style.setProperty('--flower-text-hue-color', hueColor);
        });
        field.querySelectorAll('[data-flower-text-color-channel]').forEach((input) => {
          const channel = input.getAttribute('data-flower-text-color-channel');
          if (channel === 'h') input.value = String(Math.round(hsv.h));
          if (channel === 's') input.value = String(Math.round(hsv.s));
          if (channel === 'v') input.value = String(Math.round(hsv.v));
        });
      });
    }

    async function closeFlowerTextColorPicker({ save = false } = {}) {
      const key = state.flowerText?.activeColorPicker;
      if (!key) return;
      syncFlowerTextEditorDraft();
      const patch = flowerTextStylePatch(key, state.flowerText?.[key]);
      state.flowerText = {
        ...(state.flowerText || {}),
        activeColorPicker: '',
      };
      if (save && patch) {
        await saveFlowerText(patch, { rerender: false });
        scheduleFlowerTextPreviewRefresh(0);
      }
      renderFlowerTextDrawer();
    }

    function scheduleFlowerTextAutoSave(options = {}) {
      clearFlowerTextAutoSaveTimer();
      state.flowerText.autoSaveTimer = setTimeout(() => {
        state.flowerText.autoSaveTimer = null;
        saveFlowerText({}, options);
      }, 650);
    }

    function scheduleFlowerTextPreviewRefresh(delay = 180, options = {}) {
      clearFlowerTextPreviewTimer();
      if (!state.flowerTextDrawer?.visible) return;
      if (!options.force && document.activeElement?.id === 'flowerTextEditor') return;
      state.flowerText.previewTimer = setTimeout(() => {
        state.flowerText.previewTimer = null;
        refreshFlowerTextRenderedPreview();
      }, Math.max(0, delay));
    }

    function scheduleFlowerTextPositionSave() {
      clearFlowerTextPositionSaveTimer();
      state.flowerText.positionSaveTimer = setTimeout(() => {
        state.flowerText.positionSaveTimer = null;
        saveFlowerText({
          textX: normalizeFlowerTextCoordinate(state.flowerText?.textX, 50),
          textY: normalizeFlowerTextCoordinate(state.flowerText?.textY, flowerTextPositionY(state.flowerText?.position)),
        }, { rerender: false });
      }, 650);
    }

    function setFlowerTextSaveStatus(text) {
      const status = document.getElementById('flowerTextSaveStatus');
      if (status) {
        status.textContent = text || '';
      }
    }

    function enqueueFlowerTextSave(task) {
      const chained = flowerTextSavePipeline.catch(() => {}).then(task);
      flowerTextSavePipeline = chained.catch(() => {});
      return chained;
    }

    function syncFlowerTextEditorDraft() {
      const editor = document.getElementById('flowerTextEditor');
      if (!editor) return String(state.flowerText?.text || '');
      syncFlowerTextEditorHeight(editor);
      const text = readFlowerTextEditorText();
      state.flowerText = {
        ...(state.flowerText || {}),
        text,
      };
      return text;
    }

    function flushFlowerTextEditor(options = {}) {
      const editor = document.getElementById('flowerTextEditor');
      if (!editor) return null;
      syncFlowerTextEditorDraft();
      return saveFlowerText({ text: state.flowerText.text }, options);
    }

    function readFlowerTextEditorText() {
      const editor = document.getElementById('flowerTextEditor');
      if (!editor) return String(state.flowerText?.text || '');
      if ('value' in editor) return String(editor.value || '');
      return String(editor.innerText || '').replace(/\u00a0/g, ' ').trim();
    }

    function syncFlowerTextEditorHeight(editor = document.getElementById('flowerTextEditor')) {
      if (!editor || !('value' in editor)) return;
      const wrap = editor.closest('.flower-text-editor-wrap');
      const maxWidth = Math.max(32, (wrap?.clientWidth || 0) * 0.86);
      editor.style.width = `${maxWidth}px`;
      editor.style.maxWidth = `${maxWidth}px`;
      editor.style.height = 'auto';
      editor.style.height = `${Math.max(1, editor.scrollHeight)}px`;
      updateFlowerTextDragHandlePosition(editor);
    }

    function updateFlowerTextDragHandlePosition(editor = document.getElementById('flowerTextEditor')) {
      const handle = document.getElementById('flowerTextDragHandle');
      const wrap = editor?.closest?.('.flower-text-editor-wrap');
      if (!editor || !handle || !wrap) return;
      const wrapWidth = Math.max(1, wrap.clientWidth || 1);
      const wrapHeight = Math.max(1, wrap.clientHeight || 1);
      const centerX = normalizeFlowerTextCoordinate(state.flowerText?.textX, 50) / 100 * wrapWidth;
      const centerY = normalizeFlowerTextCoordinate(state.flowerText?.textY, flowerTextPositionY(state.flowerText?.position)) / 100 * wrapHeight;
      const editorHeight = Math.max(1, editor.offsetHeight || editor.scrollHeight || 1);
      const handleHalfWidth = Math.max(12, (handle.offsetWidth || 24) / 2);
      const handleHalfHeight = Math.max(12, (handle.offsetHeight || 24) / 2);
      const topBorderY = centerY - editorHeight / 2;
      const topBorderHandleY = topBorderY - handleHalfHeight;
      const handleX = Math.min(wrapWidth - handleHalfWidth, Math.max(handleHalfWidth, centerX));
      const handleY = Math.min(wrapHeight - handleHalfHeight, Math.max(handleHalfHeight, topBorderHandleY));
      handle.style.left = `${Math.round(handleX)}px`;
      handle.style.top = `${Math.round(handleY)}px`;
    }

    function formatFlowerTextEditorHtml(text) {
      return escapeHtml(text).replace(/\n/g, '<br>');
    }

    function flowerTextPayloadFromState() {
      const position = normalizeFlowerTextPosition(state.flowerText?.position);
      return {
        enabled: !!state.flowerText?.enabled,
        text: String(state.flowerText?.text || ''),
        canvasWidth: normalizeFlowerTextSide(state.flowerText?.canvasWidth, 9),
        canvasHeight: normalizeFlowerTextSide(state.flowerText?.canvasHeight, 16),
        textColor: normalizeFlowerTextColor(state.flowerText?.textColor, '#ffee43'),
        strokeColor: normalizeFlowerTextColor(state.flowerText?.strokeColor, '#121826'),
        fontFamily: normalizeFlowerTextFamily(state.flowerText?.fontFamily, state.flowerText?.availableFonts),
        fontSize: normalizeFlowerTextPercent(state.flowerText?.fontSize, 16, 6, 28),
        fontWeight: normalizeFlowerTextWeight(state.flowerText?.fontWeight, 800),
        strokeWidth: normalizeFlowerTextPercent(state.flowerText?.strokeWidth, 8, 0, 18),
        position,
        textX: normalizeFlowerTextCoordinate(state.flowerText?.textX, 50),
        textY: normalizeFlowerTextCoordinate(state.flowerText?.textY, flowerTextPositionY(position)),
        animationDelaySeconds: normalizeFlowerTextAnimationDelay(state.flowerText?.animationDelaySeconds),
        animationType: normalizeFlowerTextAnimationType(state.flowerText?.animationType),
        watermarkEnabled: !!state.flowerText?.watermarkEnabled,
        watermarkImage: normalizeFlowerTextWatermarkImage(state.flowerText?.watermarkImage, state.userMaterials?.flowerWatermarks),
        watermarkSize: normalizeFlowerTextPercent(state.flowerText?.watermarkSize, 18, 5, 200),
        watermarkOpacity: normalizeFlowerTextPercent(state.flowerText?.watermarkOpacity, 100, 5, 100),
        watermarkAnimationDelaySeconds: normalizeFlowerTextAnimationDelay(state.flowerText?.watermarkAnimationDelaySeconds),
        watermarkAnimationType: normalizeFlowerTextAnimationType(state.flowerText?.watermarkAnimationType),
        watermarkPosition: normalizeFlowerTextWatermarkPosition(state.flowerText?.watermarkPosition),
        watermarkX: normalizeFlowerTextCoordinate(state.flowerText?.watermarkX, flowerTextWatermarkPositionX(state.flowerText?.watermarkPosition)),
        watermarkY: normalizeFlowerTextCoordinate(state.flowerText?.watermarkY, flowerTextWatermarkPositionY(state.flowerText?.watermarkPosition)),
        watermark2Enabled: !!state.flowerText?.watermark2Enabled,
        watermark2Image: normalizeFlowerTextWatermarkImage(state.flowerText?.watermark2Image, state.userMaterials?.flowerWatermarks),
        watermark2Size: normalizeFlowerTextPercent(state.flowerText?.watermark2Size, 18, 5, 200),
        watermark2Opacity: normalizeFlowerTextPercent(state.flowerText?.watermark2Opacity, 100, 5, 100),
        watermark2AnimationDelaySeconds: normalizeFlowerTextAnimationDelay(state.flowerText?.watermark2AnimationDelaySeconds),
        watermark2AnimationType: normalizeFlowerTextAnimationType(state.flowerText?.watermark2AnimationType),
        watermark2Position: normalizeFlowerTextWatermarkPosition(state.flowerText?.watermark2Position),
        watermark2X: normalizeFlowerTextCoordinate(state.flowerText?.watermark2X, flowerTextWatermarkPositionX(state.flowerText?.watermark2Position)),
        watermark2Y: normalizeFlowerTextCoordinate(state.flowerText?.watermark2Y, flowerTextWatermarkPositionY(state.flowerText?.watermark2Position)),
        previewBackgroundColor: normalizeFlowerTextColor(state.flowerText?.previewBackgroundColor, '#303844'),
        previewBackgroundImage: normalizeUserMaterialImageRelativePath(state.flowerText?.previewBackgroundImage),
      };
    }

    function setFlowerTextRenderedPreview(url) {
      const image = document.getElementById('flowerTextRenderedPreview');
      const wrap = document.getElementById('flowerTextEditorWrap');
      if (image) {
        image.src = url || '';
      }
      if (wrap) {
        wrap.classList.toggle('has-render-preview', !!url);
      }
    }

    async function refreshFlowerTextRenderedPreview() {
      if (!state.flowerTextDrawer?.visible) return;
      const payload = flowerTextPayloadFromState();
      const hasWatermark = (!!payload.watermarkEnabled && !!payload.watermarkImage) || (!!payload.watermark2Enabled && !!payload.watermark2Image);
      const hasRenderableText = !!payload.text.trim();
      if (!hasRenderableText && !hasWatermark) {
        if (state.flowerText?.previewUrl?.startsWith?.('blob:')) {
          URL.revokeObjectURL(state.flowerText.previewUrl);
        }
        state.flowerText = {
          ...(state.flowerText || {}),
          previewUrl: '',
        };
        setFlowerTextRenderedPreview('');
        return;
      }
      if (!hasRenderableText) {
        if (state.flowerText?.previewUrl?.startsWith?.('blob:')) {
          URL.revokeObjectURL(state.flowerText.previewUrl);
        }
        state.flowerText = {
          ...(state.flowerText || {}),
          previewUrl: '',
        };
        setFlowerTextRenderedPreview('');
        return;
      }
      const target = flowerTextPreviewTargetSize(payload.canvasWidth, payload.canvasHeight);
      const previewSeq = (state.flowerText?.previewSeq || 0) + 1;
      state.flowerText = {
        ...(state.flowerText || {}),
        previewSeq,
      };
      const previewPayload = {
        ...payload,
        // The live draggable watermark layers already show the current watermark positions.
        // Keep the rendered preview focused on text to avoid a second baked-in watermark copy.
        watermarkEnabled: false,
        watermarkImage: '',
        watermark2Enabled: false,
        watermark2Image: '',
      };
      try {
        const res = await fetch('/api/video-text-overlay/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...previewPayload,
            targetWidth: target.width,
            targetHeight: target.height,
          }),
        });
        if (!res.ok) throw new Error(`花字预览失败（HTTP ${res.status}）`);
        const blob = await res.blob();
        if (previewSeq !== state.flowerText.previewSeq) return;
        const previousUrl = state.flowerText.previewUrl || '';
        const nextUrl = URL.createObjectURL(blob);
        state.flowerText = {
          ...(state.flowerText || {}),
          previewUrl: nextUrl,
        };
        setFlowerTextRenderedPreview(nextUrl);
        if (previousUrl.startsWith('blob:')) {
          URL.revokeObjectURL(previousUrl);
        }
      } catch (error) {
        if (previewSeq !== state.flowerText.previewSeq) return;
        setFlowerTextRenderedPreview(state.flowerText?.previewUrl || '');
      }
    }

    function applyFlowerTextEditorStyle() {
      const editor = document.getElementById('flowerTextEditor');
      if (!editor) return;
      const width = normalizeFlowerTextSide(state.flowerText?.canvasWidth, 9);
      const height = normalizeFlowerTextSide(state.flowerText?.canvasHeight, 16);
      const textColor = normalizeFlowerTextColor(state.flowerText?.textColor, '#ffee43');
      const strokeColor = normalizeFlowerTextColor(state.flowerText?.strokeColor, '#121826');
      const availableFonts = normalizeFlowerTextFonts(state.flowerText?.availableFonts);
      ensureFlowerTextFontFaces(availableFonts);
      const fontFamily = normalizeFlowerTextFamily(state.flowerText?.fontFamily, availableFonts);
      const selectedFont = availableFonts.find((font) => font.id === fontFamily) || null;
      const fontSize = normalizeFlowerTextPercent(state.flowerText?.fontSize, 16, 6, 28);
      const fontWeight = normalizeFlowerTextWeight(state.flowerText?.fontWeight, 800);
      const strokeWidth = normalizeFlowerTextPercent(state.flowerText?.strokeWidth, 8, 0, 18);
      const previewFontSize = flowerTextPreviewFontSize(width, height, fontSize);
      editor.style.color = textColor;
      editor.style.setProperty('--flower-text-live-color', textColor);
      editor.style.setProperty('--flower-text-live-stroke-color', strokeColor);
      editor.style.setProperty('--flower-text-live-stroke-width', `${Math.max(0, Math.round(previewFontSize * strokeWidth / 100))}px`);
      editor.style.fontFamily = flowerTextEditorFontFamily(selectedFont);
      editor.style.fontSize = `${previewFontSize}px`;
      editor.style.fontWeight = String(fontWeight);
      editor.style.left = `${normalizeFlowerTextCoordinate(state.flowerText?.textX, 50)}%`;
      editor.style.top = `${normalizeFlowerTextCoordinate(state.flowerText?.textY, flowerTextPositionY(state.flowerText?.position))}%`;
      editor.style.setProperty('-webkit-text-stroke', `${Math.max(0, Math.round(previewFontSize * strokeWidth / 100))}px ${strokeColor}`);
      syncFlowerTextEditorHeight(editor);
      const handle = document.getElementById('flowerTextDragHandle');
      if (handle) {
        updateFlowerTextDragHandlePosition(editor);
      }
      applyFlowerTextWatermarkHandleStyle();
      applyFlowerTextPreviewBackgroundStyle();
    }

    function applyFlowerTextWatermarkHandleStyle() {
      const handle = document.getElementById('flowerTextWatermarkDragHandle');
      if (!handle) return;
      const position = normalizeFlowerTextWatermarkPosition(state.flowerText?.watermarkPosition);
      handle.style.left = `${normalizeFlowerTextCoordinate(state.flowerText?.watermarkX, flowerTextWatermarkPositionX(position))}%`;
      handle.style.top = `${normalizeFlowerTextCoordinate(state.flowerText?.watermarkY, flowerTextWatermarkPositionY(position))}%`;
    }

    function applyFlowerTextWatermark2HandleStyle() {
      const handle = document.getElementById('flowerTextWatermark2DragHandle');
      if (!handle) return;
      const position = normalizeFlowerTextWatermarkPosition(state.flowerText?.watermark2Position);
      handle.style.left = `${normalizeFlowerTextCoordinate(state.flowerText?.watermark2X, flowerTextWatermarkPositionX(position))}%`;
      handle.style.top = `${normalizeFlowerTextCoordinate(state.flowerText?.watermark2Y, flowerTextWatermarkPositionY(position))}%`;
    }

    function applyFlowerTextPreviewBackgroundStyle() {
      const wrap = document.getElementById('flowerTextEditorWrap');
      if (!wrap) return;
      const color = normalizeFlowerTextColor(state.flowerText?.previewBackgroundColor, '#303844');
      const imageUrl = String(state.flowerText?.previewBackgroundImageUrl || '').trim();
      wrap.style.setProperty('--flower-text-preview-background-color', color);
      wrap.style.setProperty('--flower-text-preview-background-image', imageUrl ? `url('${imageUrl.replace(/'/g, "\\'")}')` : 'none');
    }

    // #region debug-cad45c flower-text flicker tracing
    function logFlowerTextFlicker(location, message, data = {}) {
      fetch('http://127.0.0.1:7352/ingest/a6129daf-2746-4e4a-84ac-54d0dd03e374',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cad45c'},body:JSON.stringify({sessionId:'cad45c',hypothesisId:'Hflicker',location,message,data,timestamp:Date.now()})}).catch(()=>{});
    }
    // #endregion

    async function saveFlowerWatermarkCheckbox(checked, wmIdx = '1') {
      const isWm2 = wmIdx === '2';
      const patch = isWm2
        ? { watermark2Enabled: !!checked }
        : { watermarkEnabled: !!checked };
      state.flowerText = {
        ...(state.flowerText || {}),
        enabled: !!checked || !!state.flowerText?.enabled,
        ...patch,
        error: '',
        notice: '保存中...',
      };
      renderFlowerTextButton();
      setFlowerTextSaveStatus(state.flowerText.notice);
      renderFlowerTextDrawer();
      scheduleFlowerTextPreviewRefresh(0);
      await saveFlowerText({
        enabled: !!checked || !!state.flowerText?.enabled,
        ...patch,
      }, { rerender: false });
    }

    async function saveFlowerWatermarkToggle(checked, wmIdx = '1') {
      const isWm2 = wmIdx === '2';
      const patch = isWm2
        ? { watermark2Enabled: !!checked }
        : { watermarkEnabled: !!checked };
      state.flowerText = {
        ...(state.flowerText || {}),
        enabled: !!checked || !!state.flowerText?.enabled,
        ...patch,
        error: '',
        notice: '保存中...',
      };
      renderFlowerTextButton();
      // #region debug-cad45c flower-text flicker tracing
      logFlowerTextFlicker('saveFlowerWatermarkToggle:beforeRenderDrawer', 'toggle triggers drawer render SKIPPED (setFlowerTextSaveStatus instead)', { wmIdx, checked, saving: state.flowerText?.saving, notice: state.flowerText?.notice });
      // #endregion
      setFlowerTextSaveStatus(state.flowerText.notice);
      await saveFlowerText({
        enabled: !!checked || !!state.flowerText?.enabled,
        ...patch,
      });
    }

