    els.defaultReferenceDrawer?.addEventListener('focusout', (event) => {
      const textarea = event.target.closest('[data-default-reference-custom-prompt]');
      if (!textarea || state.defaultReferenceDrawer.customPromptComposing) return;
      scheduleDefaultReferenceCustomPromptSave(textarea.value || '', { immediate: true });
    });

    els.scriptReferenceDrawer?.addEventListener('click', async (event) => {
      const addScriptButton = event.target.closest('[data-add-script-reference]');
      if (addScriptButton) {
        beginUserMaterialUpload('script');
        return;
      }
      const selectButton = event.target.closest('[data-select-script-reference]');
      if (selectButton) {
        const isSelected = selectButton.getAttribute('data-script-reference-selected') === '1';
        if (isSelected) {
          await clearScriptReference();
        } else {
          await selectScriptReference(selectButton.getAttribute('data-select-script-reference') || '');
        }
      }
    });

    els.generationModeDrawer?.addEventListener('change', async (event) => {
      const toggle = event.target.closest('[data-generation-mode-toggle]');
      if (!toggle) return;
      await saveGenerationMode(!!toggle.checked);
    });

    els.smartSplitDrawer?.addEventListener('change', async (event) => {
      const smartToggle = event.target.closest('[data-smart-split-toggle]');
      const confirmToggle = event.target.closest('[data-smart-split-confirm-toggle]');
      const tailFrameToggle = event.target.closest('[data-tail-frame-chaining-toggle]');
      if (!smartToggle && !confirmToggle && !tailFrameToggle) return;
      await saveGenerationMode({
        smartSplit: smartToggle ? !!smartToggle.checked : !!state.generationMode.smartSplit,
        confirmSmartSplit: confirmToggle ? !!confirmToggle.checked : !!state.generationMode.confirmSmartSplit,
        tailFrameChaining: tailFrameToggle ? !!tailFrameToggle.checked : !!state.generationMode.tailFrameChaining,
      });
    });

    els.htmlMotionOverlayDrawer?.addEventListener('change', async (event) => {
      const toggle = event.target.closest('[data-html-motion-overlay-toggle]');
      if (!toggle) return;
      await saveHtmlMotionOverlay(!!toggle.checked);
    });

    els.flowerTextDrawer?.addEventListener('click', async (event) => {
      const safeZoneSave = event.target.closest('[data-html-motion-safe-zone-save]');
      if (safeZoneSave) {
        event.preventDefault();
        await saveHtmlMotionSafeZone();
        return;
      }
      const safeZoneToggle = event.target.closest('[data-html-motion-safe-zone-toggle]');
      if (safeZoneToggle) {
        event.preventDefault();
        toggleHtmlMotionSafeZoneEditor();
        return;
      }
      const colorPreviewButton = event.target.closest('[data-flower-text-color-preview]');
      if (colorPreviewButton) {
        event.preventDefault();
        syncFlowerTextEditorDraft();
        const key = colorPreviewButton.getAttribute('data-flower-text-color-preview') === 'strokeColor' ? 'strokeColor' : 'textColor';
        state.flowerText = {
          ...(state.flowerText || {}),
          activeColorPicker: key,
          notice: '正在刷新预览...',
        };
        setFlowerTextSaveStatus(state.flowerText.notice);
        scheduleFlowerTextPreviewRefresh(0, { force: true });
        return;
      }
      const colorToggle = event.target.closest('[data-flower-text-color-toggle]');
      if (colorToggle) {
        event.preventDefault();
        const key = colorToggle.getAttribute('data-flower-text-color-toggle') === 'strokeColor' ? 'strokeColor' : 'textColor';
        if (state.flowerText?.activeColorPicker === key) {
          await closeFlowerTextColorPicker({ save: true });
        } else {
          state.flowerText = {
            ...(state.flowerText || {}),
            activeColorPicker: key,
            _suppressEntryStatus: true,
          };
          renderFlowerTextDrawer();
          setTimeout(() => {
            if (state.flowerText) state.flowerText._suppressEntryStatus = false;
          }, 1200);
        }
        return;
      }
      const watermarkCheckbox = event.target.closest('[data-flower-watermark-checkbox]');
      if (watermarkCheckbox) {
        const wmIdx = watermarkCheckbox.getAttribute('data-flower-watermark-checkbox') || '1';
        const checked = watermarkCheckbox.checked;
        state.flowerText = {
          ...(state.flowerText || {}),
          _suppressEntryStatus: true,
        };
        await saveFlowerWatermarkCheckbox(checked, wmIdx);
        setTimeout(() => {
          if (state.flowerText) state.flowerText._suppressEntryStatus = false;
        }, 1200);
        return;
      }
      const fontSummary = event.target.closest('.flower-text-font-picker > summary');
      if (fontSummary) {
        event.preventDefault();
        const picker = fontSummary.closest('.flower-text-font-picker');
        if (!picker) return;
        if (picker.open) {
          picker.open = false;
          return;
        }
        picker.open = true;
        positionFlowerTextFontMenu(picker);
        scrollSelectedFlowerTextFontIntoView(picker);
        return;
      }
      const fontOption = event.target.closest('[data-flower-text-font-option]');
      if (!fontOption) return;
      event.preventDefault();
      const patch = flowerTextStylePatch('fontFamily', fontOption.getAttribute('data-flower-text-font-option') || '');
      if (!patch) return;
      const picker = fontOption.closest('.flower-text-font-picker');
      if (picker) picker.open = false;
      state.flowerText = {
        ...(state.flowerText || {}),
        ...patch,
        error: '',
        notice: '选择后自动保存',
        _suppressEntryStatus: true,
      };
      applyFlowerTextFontPickerSelection(patch.fontFamily);
      scheduleFlowerTextPreviewRefresh(0);
      applyFlowerTextEditorStyle();
      await saveFlowerText(patch, { rerender: false });
      setTimeout(() => {
        if (state.flowerText) state.flowerText._suppressEntryStatus = false;
      }, 1200);
    });

    window.addEventListener('resize', () => {
      positionOpenFlowerTextFontMenu();

    });

    document.addEventListener('scroll', () => {
      positionOpenFlowerTextFontMenu();
    }, true);

    document.addEventListener('pointerdown', (event) => {
      const activeColorPicker = state.flowerText?.activeColorPicker;
      if (!activeColorPicker) return;
      if (event.target.closest?.('[data-flower-text-color-field]')) return;
      closeFlowerTextColorPicker({ save: true });
    }, true);


    els.flowerTextDrawer?.addEventListener('change', (event) => {
      const styleControl = event.target.closest?.('[data-flower-text-style]');
      if (!styleControl) return;
      const patch = flowerTextStylePatch(styleControl.getAttribute('data-flower-text-style'), styleControl.value);
      if (!patch) return;
      syncFlowerTextEditorDraft();
      state.flowerText = {
        ...(state.flowerText || {}),
        ...patch,
        error: '',
        notice: '选择后自动保存',
        _suppressEntryStatus: true,
      };
      scheduleFlowerTextPreviewRefresh(80);
      scheduleFlowerTextAutoSave();
      setTimeout(() => {
        if (state.flowerText) state.flowerText._suppressEntryStatus = false;
      }, 1200);
    });

    els.flowerTextDrawer?.addEventListener('input', (event) => {
      if (event.target?.id === 'flowerTextEditor') {
        state.flowerText.text = readFlowerTextEditorText();
        state.flowerText.error = '';
        state.flowerText.notice = '失焦后自动保存';
        syncFlowerTextEditorHeight(event.target);
        renderFlowerTextButton();
        setFlowerTextSaveStatus(state.flowerText.notice);
        clearFlowerTextPreviewTimer();
        return;
      }
      const colorChannel = event.target.closest('[data-flower-text-color-channel]');
      if (colorChannel) {
        updateFlowerTextColorChannel(colorChannel);
        return;
      }
      const styleControl = event.target.closest('[data-flower-text-style]');
      if (styleControl) {
        const patch = flowerTextStylePatch(styleControl.getAttribute('data-flower-text-style'), styleControl.value);
        if (!patch) return;
        const isColorControl = isFlowerTextColorStyleControl(styleControl);
        syncFlowerTextEditorDraft();
        state.flowerText = {
          ...(state.flowerText || {}),
          ...patch,
          error: '',
          notice: isColorControl ? '松手后自动保存' : '输入后自动保存',
        };
        applyFlowerTextEditorStyle();
        setFlowerTextSaveStatus(state.flowerText.notice);
        if (isColorControl) {
          clearFlowerTextAutoSaveTimer();
          clearFlowerTextPreviewTimer();
          return;
        }
        state.flowerText = { ...(state.flowerText || {}), _suppressRender: true };
        scheduleFlowerTextPreviewRefresh(80);
        scheduleFlowerTextAutoSave({ rerender: false });
        // Clear suppress flag after auto-save completes (800ms covers 650ms timer + API)
        if (state.flowerText._suppressTimer) clearTimeout(state.flowerText._suppressTimer);
        state.flowerText._suppressTimer = setTimeout(() => {
          if (state.flowerText) state.flowerText._suppressRender = false;
        }, 800);
        // Update watermark handle size directly to avoid state race
        if (styleControl.getAttribute('data-flower-text-style') === 'watermarkSize') {
          const wmHandle = document.getElementById('flowerTextWatermarkDragHandle');
          if (wmHandle) wmHandle.style.setProperty('--flower-watermark-size', patch.watermarkSize + '%');
        }
        if (styleControl.getAttribute('data-flower-text-style') === 'watermark2Size') {
          const wm2Handle = document.getElementById('flowerTextWatermark2DragHandle');
          if (wm2Handle) wm2Handle.style.setProperty('--flower-watermark-size', patch.watermark2Size + '%');
        }
      }
    });

    els.flowerTextDrawer?.addEventListener('focus', (event) => {
      if (event.target?.id !== 'flowerTextEditor') return;
      document.getElementById('flowerTextEditorWrap')?.classList.add('is-editing');
      syncFlowerTextEditorHeight(event.target);
    }, true);

    els.flowerTextDrawer?.addEventListener('blur', async (event) => {
      if (event.target?.id !== 'flowerTextEditor') return;
      state.flowerText.text = readFlowerTextEditorText();
      await saveFlowerText({ text: state.flowerText.text }, { rerender: false });
      document.getElementById('flowerTextEditorWrap')?.classList.remove('is-editing');
      scheduleFlowerTextPreviewRefresh(0, { force: true });
    }, true);

    els.flowerTextDrawer?.addEventListener('pointerdown', (event) => {
      const safeZoneBox = event.target.closest?.('#htmlMotionSafeZoneBox');
      if (safeZoneBox && state.htmlMotionSafeZone?.editing) {
        const wrap = safeZoneBox.closest('.flower-text-editor-wrap');
        if (!wrap) return;
        const rect = wrap.getBoundingClientRect();
        const ratio = flowerTextRatioValue(state.flowerText?.canvasWidth, state.flowerText?.canvasHeight);
        const zone = currentHtmlMotionSafeZone(ratio);
        state.htmlMotionSafeZone.drag = {
          pointerId: event.pointerId,
          mode: event.target.closest?.('[data-html-motion-safe-zone-resize]') ? 'resize' : 'move',
          startClientX: event.clientX,
          startClientY: event.clientY,
          startZone: zone,
          width: rect.width,
          height: rect.height,
        };
        try {
          safeZoneBox.setPointerCapture?.(event.pointerId);
        } catch (error) {}
        event.preventDefault();
        return;
      }
      const watermarkHandle = event.target.closest?.('#flowerTextWatermarkDragHandle');
      const watermark2Handle = event.target.closest?.('#flowerTextWatermark2DragHandle');
      if (watermarkHandle || watermark2Handle) {
        const isWm2 = !!watermark2Handle;
        const activeHandle = watermark2Handle || watermarkHandle;
        const wrap = activeHandle.closest('.flower-text-editor-wrap');
        if (!wrap) return;
        const rect = wrap.getBoundingClientRect();
        const wmKey = isWm2 ? 'watermark2' : 'watermark';
        const position = normalizeFlowerTextWatermarkPosition(isWm2 ? state.flowerText?.watermark2Position : state.flowerText?.watermarkPosition);
        state.flowerText.drag = {
          target: wmKey,
          pointerId: event.pointerId,
          startClientX: event.clientX,
          startClientY: event.clientY,
          startX: normalizeFlowerTextCoordinate(isWm2 ? state.flowerText?.watermark2X : state.flowerText?.watermarkX, flowerTextWatermarkPositionX(position)),
          startY: normalizeFlowerTextCoordinate(isWm2 ? state.flowerText?.watermark2Y : state.flowerText?.watermarkY, flowerTextWatermarkPositionY(position)),
          width: rect.width,
          height: rect.height,
          active: false,
        };
        try {
          activeHandle.setPointerCapture?.(event.pointerId);
        } catch (error) {}
        event.preventDefault();
        return;
      }
      const handle = event.target.closest?.('#flowerTextDragHandle');
      if (!handle) return;
      const editor = document.getElementById('flowerTextEditor');
      if (!editor) return;
      const wrap = editor.closest('.flower-text-editor-wrap');
      if (!wrap) return;
      const rect = wrap.getBoundingClientRect();
      wrap.classList.remove('is-editing');
      state.flowerText.drag = {
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startX: normalizeFlowerTextCoordinate(state.flowerText?.textX, 50),
        startY: normalizeFlowerTextCoordinate(state.flowerText?.textY, 50),
        width: rect.width,
        height: rect.height,
        active: false,
      };
      clearFlowerTextPreviewTimer();
      try {
        (handle || editor).setPointerCapture?.(event.pointerId);
      } catch (error) {
        // Some embedded browsers do not support pointer capture on contenteditable nodes.
      }
      if (handle) event.preventDefault();
    });

    els.flowerTextDrawer?.addEventListener('pointermove', (event) => {
      const safeDrag = state.htmlMotionSafeZone?.drag;
      if (safeDrag && safeDrag.pointerId === event.pointerId) {
        const dx = (event.clientX - safeDrag.startClientX) / Math.max(1, safeDrag.width) * 100;
        const dy = (event.clientY - safeDrag.startClientY) / Math.max(1, safeDrag.height) * 100;
        const start = safeDrag.startZone;
        const draft = safeDrag.mode === 'resize'
          ? normalizeHtmlMotionSafeZone({
              ...start,
              width: Math.max(16, Math.min(100 - start.x, start.width + dx)),
              height: Math.max(16, Math.min(100 - start.y, start.height + dy)),
            }, state.htmlMotionSafeZone.draftRatio)
          : normalizeHtmlMotionSafeZone({
              ...start,
              x: Math.max(0, Math.min(100 - start.width, start.x + dx)),
              y: Math.max(0, Math.min(100 - start.height, start.y + dy)),
            }, state.htmlMotionSafeZone.draftRatio);
        state.htmlMotionSafeZone.draft = draft;
        applyHtmlMotionSafeZoneBox();
        event.preventDefault();
        return;
      }
      const drag = state.flowerText?.drag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - drag.startClientX;
      const dy = event.clientY - drag.startClientY;
      if (!drag.active && Math.hypot(dx, dy) < 4) return;
      drag.active = true;
      event.preventDefault();
      const editorWrap = document.getElementById('flowerTextEditorWrap');
      const nextX = normalizeFlowerTextCoordinate(drag.startX + dx / Math.max(1, drag.width) * 100, 50);
      const nextY = normalizeFlowerTextCoordinate(drag.startY + dy / Math.max(1, drag.height) * 100, 50);
      if (drag.target === 'watermark') {
        state.flowerText.watermarkX = nextX;
        state.flowerText.watermarkY = nextY;
        state.flowerText.notice = '松手后保存水印位置';
        applyFlowerTextWatermarkHandleStyle();
        document.getElementById('flowerTextWatermarkDragHandle')?.classList.add('dragging');
      } else if (drag.target === 'watermark2') {
        state.flowerText.watermark2X = nextX;
        state.flowerText.watermark2Y = nextY;
        state.flowerText.notice = '松手后保存水印2位置';
        applyFlowerTextWatermark2HandleStyle();
        document.getElementById('flowerTextWatermark2DragHandle')?.classList.add('dragging');
      } else {
        state.flowerText.textX = nextX;
        state.flowerText.textY = nextY;
        state.flowerText.notice = '松手后保存位置';
        editorWrap?.classList.add('is-dragging');
        applyFlowerTextEditorStyle();
        document.getElementById('flowerTextEditor')?.classList.add('dragging');
        document.getElementById('flowerTextDragHandle')?.classList.add('dragging');
      }
      setFlowerTextSaveStatus(state.flowerText.notice);
      if (!drag.target) scheduleFlowerTextPositionSave();
    });

    els.flowerTextDrawer?.addEventListener('pointerup', async (event) => {
      const safeDrag = state.htmlMotionSafeZone?.drag;
      if (safeDrag && safeDrag.pointerId === event.pointerId) {
        state.htmlMotionSafeZone.drag = null;
        try {
          event.target?.releasePointerCapture?.(event.pointerId);
        } catch (error) {}
        event.preventDefault();
        return;
      }
      const drag = state.flowerText?.drag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      state.flowerText.drag = null;
      try {
        event.target?.releasePointerCapture?.(event.pointerId);
      } catch (error) {
        // Ignore capture release failures; the drag state has already been cleared.
      }
      document.getElementById('flowerTextEditor')?.classList.remove('dragging');
      document.getElementById('flowerTextDragHandle')?.classList.remove('dragging');
      document.getElementById('flowerTextWatermarkDragHandle')?.classList.remove('dragging');
      document.getElementById('flowerTextWatermark2DragHandle')?.classList.remove('dragging');
      if (!drag.active) {
        document.getElementById('flowerTextEditorWrap')?.classList.remove('is-dragging');
        if (document.activeElement?.id !== 'flowerTextEditor') {
          document.getElementById('flowerTextEditorWrap')?.classList.remove('is-editing');
        }
        return;
      }
      clearFlowerTextPositionSaveTimer();
      if (drag.target === 'watermark') {
        await saveFlowerText({
          watermarkX: normalizeFlowerTextCoordinate(state.flowerText?.watermarkX, flowerTextWatermarkPositionX(state.flowerText?.watermarkPosition)),
          watermarkY: normalizeFlowerTextCoordinate(state.flowerText?.watermarkY, flowerTextWatermarkPositionY(state.flowerText?.watermarkPosition)),
        }, { rerender: false });
      } else if (drag.target === 'watermark2') {
        await saveFlowerText({
          watermark2X: normalizeFlowerTextCoordinate(state.flowerText?.watermark2X, flowerTextWatermarkPositionX(state.flowerText?.watermark2Position)),
          watermark2Y: normalizeFlowerTextCoordinate(state.flowerText?.watermark2Y, flowerTextWatermarkPositionY(state.flowerText?.watermark2Position)),
        }, { rerender: false });
      } else {
        await saveFlowerText({
          textX: normalizeFlowerTextCoordinate(state.flowerText?.textX, 50),
          textY: normalizeFlowerTextCoordinate(state.flowerText?.textY, 50),
        }, { rerender: false });
      }
      if (drag.target) {
        scheduleFlowerTextPreviewRefresh(0);
      } else {
        await refreshFlowerTextRenderedPreview();
        document.getElementById('flowerTextEditorWrap')?.classList.remove('is-dragging');
      }
      if (document.activeElement?.id !== 'flowerTextEditor') {
        document.getElementById('flowerTextEditorWrap')?.classList.remove('is-editing');
      }
    });

    els.flowerTextDrawer?.addEventListener('pointercancel', () => {
      if (state.htmlMotionSafeZone) state.htmlMotionSafeZone.drag = null;
      state.flowerText.drag = null;
      document.getElementById('flowerTextEditor')?.classList.remove('dragging');
      document.getElementById('flowerTextDragHandle')?.classList.remove('dragging');
      document.getElementById('flowerTextWatermarkDragHandle')?.classList.remove('dragging');
      document.getElementById('flowerTextWatermark2DragHandle')?.classList.remove('dragging');
      document.getElementById('flowerTextEditorWrap')?.classList.remove('is-dragging');
      if (document.activeElement?.id !== 'flowerTextEditor') {
        document.getElementById('flowerTextEditorWrap')?.classList.remove('is-editing');
      }
    });

    els.flowerTextDrawer?.addEventListener('dragstart', (event) => {
      const handle = event.target.closest?.('#flowerTextDragHandle');
      if (!handle) return;
      const editor = document.getElementById('flowerTextEditor');
      const wrap = editor?.closest('.flower-text-editor-wrap');
      if (!editor || !wrap) return;
      const rect = wrap.getBoundingClientRect();
      wrap.classList.remove('is-editing');
      state.flowerText.drag = {
        pointerId: 'native-drag',
        startClientX: event.clientX,
        startClientY: event.clientY,
        startX: normalizeFlowerTextCoordinate(state.flowerText?.textX, 50),
        startY: normalizeFlowerTextCoordinate(state.flowerText?.textY, 50),
        width: rect.width,
        height: rect.height,
        active: false,
      };
      clearFlowerTextPreviewTimer();
      event.dataTransfer?.setData('text/plain', '');
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
      document.getElementById('flowerTextEditor')?.classList.add('dragging');
      handle.classList.add('dragging');
      wrap.classList.add('is-dragging');
    });

    els.flowerTextDrawer?.addEventListener('drag', (event) => {
      const drag = state.flowerText?.drag;
      if (!drag || drag.pointerId !== 'native-drag') return;
      if (!event.clientX && !event.clientY) return;
      const dx = event.clientX - drag.startClientX;
      const dy = event.clientY - drag.startClientY;
      if (!drag.active && Math.hypot(dx, dy) < 4) return;
      drag.active = true;
      event.preventDefault();
      state.flowerText.textX = normalizeFlowerTextCoordinate(drag.startX + dx / Math.max(1, drag.width) * 100, 50);
      state.flowerText.textY = normalizeFlowerTextCoordinate(drag.startY + dy / Math.max(1, drag.height) * 100, 50);
      state.flowerText.notice = '松手后保存位置';
      applyFlowerTextEditorStyle();
      setFlowerTextSaveStatus(state.flowerText.notice);
      scheduleFlowerTextPositionSave();
    });
