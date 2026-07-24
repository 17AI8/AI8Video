    async function uploadFlowerWatermarkFiles(files, wmIdx = '1') {
      const imageFiles = Array.isArray(files) ? files : [];
      if (!imageFiles.length) return;
      const isWm2 = wmIdx === '2';
      const wmLabel = isWm2 ? '水印 2' : '水印 1';
      fetch('http://127.0.0.1:7352/ingest/a6129daf-2746-4e4a-84ac-54d0dd03e374',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cad45c'},body:JSON.stringify({sessionId:'cad45c',hypothesisId:'H1-H4',location:'uploadFlowerWatermarkFiles:start',message:'upload start',data:{wmIdx,wmLabel,watermarkEnabled:state.flowerText?.watermarkEnabled,watermarkImage:state.flowerText?.watermarkImage,watermark2Enabled:state.flowerText?.watermark2Enabled,watermark2Image:state.flowerText?.watermark2Image},timestamp:Date.now()})}).catch(()=>{});
      state.flowerText = {
        ...(state.flowerText || {}),
        saving: true,
        error: '',
        notice: `${wmLabel}上传中...`,
      };
      renderFlowerTextButton();
      // #region debug-cad45c flower-text flicker tracing
      logFlowerTextFlicker('uploadFlowerWatermarkFiles:firstRender', 'upload set saving, SKIPPED drawer render (setFlowerTextSaveStatus instead)', { wmIdx, notice: state.flowerText?.notice, saving: state.flowerText?.saving });
      // #endregion
      setFlowerTextSaveStatus(state.flowerText.notice);
      try {
        await ensureFlowerWatermarkLibraryReady();
        const uploadData = await uploadUserMaterials('flower-watermark', imageFiles);
        const savedWatermarkItems = normalizeUploadedFlowerWatermarkItems(uploadData?.saved);
        rememberUploadedFlowerWatermarks(savedWatermarkItems);
        try {
          await refreshUserMaterials();
        } catch (error) {
          console.warn('refresh user materials failed after watermark upload', error);
        }
        rememberUploadedFlowerWatermarks(savedWatermarkItems);
        const firstSaved = savedWatermarkItems[0] || null;
        const nextWatermarkImage = String(firstSaved?.relativePath || '').trim();
        if (!nextWatermarkImage) {
          throw new Error('水印图片上传失败');
        }
        const enabledPatch = isWm2
          ? { watermark2Enabled: true, watermark2Image: nextWatermarkImage, watermark2Opacity: 100 }
          : { watermarkEnabled: true, watermarkImage: nextWatermarkImage, watermarkOpacity: 100 };
        state.flowerText = {
          ...(state.flowerText || {}),
          enabled: true,
          ...enabledPatch,
          saving: false,
          error: '',
          notice: `${wmLabel}已上传，保存中...`,
        };
        fetch('http://127.0.0.1:7352/ingest/a6129daf-2746-4e4a-84ac-54d0dd03e374',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cad45c'},body:JSON.stringify({sessionId:'cad45c',hypothesisId:'H1-H4',location:'uploadFlowerWatermarkFiles:beforeSave',message:'state before saveFlowerText',data:{watermarkEnabled:state.flowerText?.watermarkEnabled,watermarkImage:state.flowerText?.watermarkImage,watermark2Enabled:state.flowerText?.watermark2Enabled,watermark2Image:state.flowerText?.watermark2Image,enabledPatch},timestamp:Date.now()})}).catch(()=>{});
        renderFlowerTextButton();
        // #region debug-cad45c flower-text flicker tracing
        logFlowerTextFlicker('uploadFlowerWatermarkFiles:secondRender', 'upload success then render drawer again', { wmIdx, notice: state.flowerText?.notice, saving: state.flowerText?.saving, watermarkImage: state.flowerText?.watermarkImage, watermark2Image: state.flowerText?.watermark2Image });
        // #endregion
        renderFlowerTextDrawer();
        scheduleFlowerTextPreviewRefresh(0);
        await saveFlowerText({
          enabled: true,
          ...enabledPatch,
        });
        renderUserMaterials();
        renderDefaultReferenceDrawer();
        renderMaterialLibraryModal();
        renderMaterialMentionPicker();
      } catch (error) {
        state.flowerText = {
          ...(state.flowerText || {}),
          saving: false,
          error: error?.message || String(error),
          notice: '',
        };
        renderFlowerTextButton();
        renderFlowerTextDrawer();
      }
    }

    async function updateFlowerPreviewBackgroundColor(color) {
      syncFlowerTextEditorDraft();
      const nextColor = normalizeFlowerTextColor(color, '#303844');
      state.flowerText = {
        ...(state.flowerText || {}),
        previewBackgroundColor: nextColor,
        previewBackgroundImage: '',
        previewBackgroundImageUrl: '',
        error: '',
        notice: '背景保存中...',
      };
      applyFlowerTextPreviewBackgroundStyle();
      setFlowerTextSaveStatus(state.flowerText.notice);
      try {
        const res = await fetch('/api/video-text-overlay/preview-background-color', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ previewBackgroundColor: nextColor }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || `背景保存失败（HTTP ${res.status}）`);
        }
        state.flowerText = {
          ...(state.flowerText || {}),
          previewBackgroundColor: normalizeFlowerTextColor(data.previewBackgroundColor, nextColor),
          previewBackgroundImage: normalizeUserMaterialImageRelativePath(data.previewBackgroundImage),
          previewBackgroundImageUrl: String(data.previewBackgroundImageUrl || ''),
          error: '',
          notice: '背景已保存',
        };
        applyFlowerTextPreviewBackgroundStyle();
        setFlowerTextSaveStatus(state.flowerText.notice);
      } catch (error) {
        state.flowerText = {
          ...(state.flowerText || {}),
          error: error?.message || String(error),
          notice: '',
        };
        applyFlowerTextPreviewBackgroundStyle();
        setFlowerTextSaveStatus(`提示：${state.flowerText.error}`);
      }
    }

    async function uploadFlowerPreviewBackgroundImage(file) {
      if (!file) return;
      state.flowerText = {
        ...(state.flowerText || {}),
        backgroundUploading: true,
        error: '',
        notice: '背景图上传中...',
      };
      renderFlowerTextDrawer();
      try {
        const formData = new FormData();
        formData.append('file', file, file.name);
        const res = await fetch('/api/video-text-overlay/preview-background', {
          method: 'POST',
          body: formData,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || `背景图上传失败（HTTP ${res.status}）`);
        }
        const settings = data.settings || {};
        state.flowerText = {
          ...(state.flowerText || {}),
          previewBackgroundColor: normalizeFlowerTextColor(settings.previewBackgroundColor, '#303844'),
          previewBackgroundImage: normalizeUserMaterialImageRelativePath(settings.previewBackgroundImage || data.background?.relativePath),
          previewBackgroundImageUrl: String(settings.previewBackgroundImageUrl || data.background?.url || ''),
          backgroundUploading: false,
          error: '',
          notice: '背景图已上传',
        };
        renderFlowerTextDrawer();
      } catch (error) {
        state.flowerText = {
          ...(state.flowerText || {}),
          backgroundUploading: false,
          error: error?.message || String(error),
          notice: '',
        };
        renderFlowerTextDrawer();
      }
    }

    async function saveFlowerText(patch = {}, options = {}) {
      syncFlowerTextEditorDraft();
      fetch('http://127.0.0.1:7352/ingest/a6129daf-2746-4e4a-84ac-54d0dd03e374',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cad45c'},body:JSON.stringify({sessionId:'cad45c',hypothesisId:'H4',location:'saveFlowerText:start',message:'saveFlowerText called',data:{patchKeys:Object.keys(patch||{})},timestamp:Date.now()})}).catch(()=>{});
      const editorHadFocus = document.activeElement?.id === 'flowerTextEditor';
      const shouldRerender = options.rerender !== false && !editorHadFocus;
      // #region debug-cad45c flower-text flicker tracing
      logFlowerTextFlicker('saveFlowerText:rerenderDecision', 'saveFlowerText rerender decision', { shouldRerender, editorHadFocus, patchKeys: Object.keys(patch || {}) });
      // #endregion
      clearFlowerTextAutoSaveTimer();
      const previous = {
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
        position: normalizeFlowerTextPosition(state.flowerText?.position),
        textX: normalizeFlowerTextCoordinate(state.flowerText?.textX, 50),
        textY: normalizeFlowerTextCoordinate(state.flowerText?.textY, flowerTextPositionY(state.flowerText?.position)),
        watermarkEnabled: !!state.flowerText?.watermarkEnabled,
        watermarkImage: normalizeFlowerTextWatermarkImage(state.flowerText?.watermarkImage, state.userMaterials?.flowerWatermarks),
        watermarkSize: normalizeFlowerTextPercent(state.flowerText?.watermarkSize, 18, 5, 200),
        watermarkOpacity: normalizeFlowerTextPercent(state.flowerText?.watermarkOpacity, 100, 5, 100),
        watermarkPosition: normalizeFlowerTextWatermarkPosition(state.flowerText?.watermarkPosition),
        watermarkX: normalizeFlowerTextCoordinate(state.flowerText?.watermarkX, flowerTextWatermarkPositionX(state.flowerText?.watermarkPosition)),
        watermarkY: normalizeFlowerTextCoordinate(state.flowerText?.watermarkY, flowerTextWatermarkPositionY(state.flowerText?.watermarkPosition)),
        watermark2Enabled: !!state.flowerText?.watermark2Enabled,
        watermark2Image: normalizeFlowerTextWatermarkImage(state.flowerText?.watermark2Image, state.userMaterials?.flowerWatermarks),
        watermark2Size: normalizeFlowerTextPercent(state.flowerText?.watermark2Size, 18, 5, 200),
        watermark2Opacity: normalizeFlowerTextPercent(state.flowerText?.watermark2Opacity, 100, 5, 100),
        watermark2Position: normalizeFlowerTextWatermarkPosition(state.flowerText?.watermark2Position),
        watermark2X: normalizeFlowerTextCoordinate(state.flowerText?.watermark2X, flowerTextWatermarkPositionX(state.flowerText?.watermark2Position)),
        watermark2Y: normalizeFlowerTextCoordinate(state.flowerText?.watermark2Y, flowerTextWatermarkPositionY(state.flowerText?.watermark2Position)),
        previewBackgroundColor: normalizeFlowerTextColor(state.flowerText?.previewBackgroundColor, '#303844'),
        previewBackgroundImage: normalizeUserMaterialImageRelativePath(state.flowerText?.previewBackgroundImage),
      };
      fetch('http://127.0.0.1:7352/ingest/a6129daf-2746-4e4a-84ac-54d0dd03e374',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cad45c'},body:JSON.stringify({sessionId:'cad45c',hypothesisId:'H4',location:'saveFlowerText:previous',message:'previous from state',data:{watermarkEnabled:previous.watermarkEnabled,watermarkImage:previous.watermarkImage,watermark2Enabled:previous.watermark2Enabled,watermark2Image:previous.watermark2Image,patchWatermarkEnabled:patch?.watermarkEnabled,patchWatermarkImage:patch?.watermarkImage,patchWatermark2Enabled:patch?.watermark2Enabled,patchWatermark2Image:patch?.watermark2Image},timestamp:Date.now()})}).catch(()=>{});
      const next = {
        ...previous,
        ...(patch || {}),
      };
      next.enabled = !!next.enabled;
      next.text = String(next.text || '');
      next.canvasWidth = normalizeFlowerTextSide(next.canvasWidth, 9);
      next.canvasHeight = normalizeFlowerTextSide(next.canvasHeight, 16);
      next.textColor = normalizeFlowerTextColor(next.textColor, '#ffee43');
      next.strokeColor = normalizeFlowerTextColor(next.strokeColor, '#121826');
      next.fontFamily = normalizeFlowerTextFamily(next.fontFamily, state.flowerText?.availableFonts);
      next.fontSize = normalizeFlowerTextPercent(next.fontSize, 16, 6, 28);
      next.fontWeight = normalizeFlowerTextWeight(next.fontWeight, 800);
      next.strokeWidth = normalizeFlowerTextPercent(next.strokeWidth, 8, 0, 18);
      next.position = normalizeFlowerTextPosition(next.position);
      next.textX = normalizeFlowerTextCoordinate(next.textX, 50);
      next.textY = normalizeFlowerTextCoordinate(next.textY, flowerTextPositionY(next.position));
      next.watermarkEnabled = !!next.watermarkEnabled;
      next.watermarkImage = normalizeFlowerTextWatermarkImage(next.watermarkImage, state.userMaterials?.flowerWatermarks);
      next.watermarkSize = normalizeFlowerTextPercent(next.watermarkSize, 18, 5, 200);
      next.watermarkOpacity = normalizeFlowerTextPercent(next.watermarkOpacity, 100, 5, 100);
      next.watermarkPosition = normalizeFlowerTextWatermarkPosition(next.watermarkPosition);
      next.watermarkX = normalizeFlowerTextCoordinate(next.watermarkX, flowerTextWatermarkPositionX(next.watermarkPosition));
      next.watermarkY = normalizeFlowerTextCoordinate(next.watermarkY, flowerTextWatermarkPositionY(next.watermarkPosition));
      next.watermark2Enabled = !!next.watermark2Enabled;
      next.watermark2Image = normalizeFlowerTextWatermarkImage(next.watermark2Image, state.userMaterials?.flowerWatermarks);
      next.watermark2Size = normalizeFlowerTextPercent(next.watermark2Size, 18, 5, 200);
      next.watermark2Opacity = normalizeFlowerTextPercent(next.watermark2Opacity, 100, 5, 100);
      next.watermark2Position = normalizeFlowerTextWatermarkPosition(next.watermark2Position);
      next.watermark2X = normalizeFlowerTextCoordinate(next.watermark2X, flowerTextWatermarkPositionX(next.watermark2Position));
      next.watermark2Y = normalizeFlowerTextCoordinate(next.watermark2Y, flowerTextWatermarkPositionY(next.watermark2Position));
      next.previewBackgroundColor = normalizeFlowerTextColor(next.previewBackgroundColor, '#303844');
      next.previewBackgroundImage = normalizeUserMaterialImageRelativePath(next.previewBackgroundImage);
      const saveSeq = (state.flowerText?.autoSaveSeq || 0) + 1;
      state.flowerText = {
        ...(state.flowerText || {}),
        ...next,
        autoSaveSeq: saveSeq,
        saving: true,
        error: '',
        notice: '保存中...',
      };
      renderFlowerTextButton();
      if (shouldRerender) {
        // #region debug-cad45c flower-text flicker tracing
        logFlowerTextFlicker('saveFlowerText:beforeRenderDrawer', 'saveFlowerText triggers drawer render SKIPPED (setFlowerTextSaveStatus instead)', { patchKeys: Object.keys(patch || {}), saving: state.flowerText?.saving, notice: state.flowerText?.notice });
        // #endregion
        setFlowerTextSaveStatus(state.flowerText.notice);
      } else {
        setFlowerTextSaveStatus(state.flowerText.notice);
      }
      const requestPayload = { ...next };
      return enqueueFlowerTextSave(async () => {
        try {
          fetch('http://127.0.0.1:7352/ingest/a6129daf-2746-4e4a-84ac-54d0dd03e374',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cad45c'},body:JSON.stringify({sessionId:'cad45c',hypothesisId:'H1-H4',location:'saveFlowerText:send',message:'sending to /api/video-text-overlay',data:{nextWatermarkEnabled:requestPayload.watermarkEnabled,nextWatermarkImage:requestPayload.watermarkImage,nextWatermark2Enabled:requestPayload.watermark2Enabled,nextWatermark2Image:requestPayload.watermark2Image},timestamp:Date.now()})}).catch(()=>{});
          const res = await fetch('/api/video-text-overlay', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestPayload),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) {
            throw new Error(data.error || `花字设置保存失败（HTTP ${res.status}）`);
          }
          if (saveSeq !== state.flowerText.autoSaveSeq) return;
          fetch('http://127.0.0.1:7352/ingest/a6129daf-2746-4e4a-84ac-54d0dd03e374',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cad45c'},body:JSON.stringify({sessionId:'cad45c',hypothesisId:'H1',location:'saveFlowerText:response',message:'server response data',data:{dataWatermarkEnabled:data.watermarkEnabled,dataWatermarkImage:data.watermarkImage,dataWatermark2Enabled:data.watermark2Enabled,dataWatermark2Image:data.watermark2Image,allKeys:Object.keys(data)},timestamp:Date.now()})}).catch(()=>{});
          const liveEditorText = document.getElementById('flowerTextEditor') ? readFlowerTextEditorText() : null;
          state.flowerText = {
            ...(state.flowerText || {}),
            enabled: !!data.enabled,
            text: liveEditorText === null ? String(data.text || '') : liveEditorText,
            canvasWidth: normalizeFlowerTextSide(data.canvasWidth, 9),
            canvasHeight: normalizeFlowerTextSide(data.canvasHeight, 16),
            textColor: normalizeFlowerTextColor(data.textColor, '#ffee43'),
            strokeColor: normalizeFlowerTextColor(data.strokeColor, '#121826'),
            fontFamily: normalizeFlowerTextFamily(data.fontFamily, data.availableFonts),
            availableFonts: normalizeFlowerTextFonts(data.availableFonts),
            fontSize: normalizeFlowerTextPercent(data.fontSize, 16, 6, 28),
            fontWeight: normalizeFlowerTextWeight(data.fontWeight, 800),
            strokeWidth: normalizeFlowerTextPercent(data.strokeWidth, 8, 0, 18),
            position: normalizeFlowerTextPosition(data.position),
            textX: normalizeFlowerTextCoordinate(data.textX, 50),
            textY: normalizeFlowerTextCoordinate(data.textY, flowerTextPositionY(data.position)),
            watermarkEnabled: !!data.watermarkEnabled,
            watermarkImage: normalizeFlowerTextWatermarkImage(data.watermarkImage, state.userMaterials?.flowerWatermarks),
            watermarkSize: normalizeFlowerTextPercent(data.watermarkSize, 18, 5, 200),
            watermarkOpacity: normalizeFlowerTextPercent(data.watermarkOpacity, 100, 5, 100),
            watermarkPosition: normalizeFlowerTextWatermarkPosition(data.watermarkPosition),
            watermarkX: normalizeFlowerTextCoordinate(data.watermarkX, flowerTextWatermarkPositionX(data.watermarkPosition)),
            watermarkY: normalizeFlowerTextCoordinate(data.watermarkY, flowerTextWatermarkPositionY(data.watermarkPosition)),
            watermark2Enabled: !!data.watermark2Enabled,
            watermark2Image: normalizeFlowerTextWatermarkImage(data.watermark2Image, state.userMaterials?.flowerWatermarks),
            watermark2Size: normalizeFlowerTextPercent(data.watermark2Size, 18, 5, 200),
            watermark2Opacity: normalizeFlowerTextPercent(data.watermark2Opacity, 100, 5, 100),
            watermark2Position: normalizeFlowerTextWatermarkPosition(data.watermark2Position),
            watermark2X: normalizeFlowerTextCoordinate(data.watermark2X, flowerTextWatermarkPositionX(data.watermark2Position)),
            watermark2Y: normalizeFlowerTextCoordinate(data.watermark2Y, flowerTextWatermarkPositionY(data.watermark2Position)),
            previewBackgroundColor: normalizeFlowerTextColor(data.previewBackgroundColor, '#303844'),
            previewBackgroundImage: normalizeUserMaterialImageRelativePath(data.previewBackgroundImage),
            previewBackgroundImageUrl: String(data.previewBackgroundImageUrl || ''),
            saving: false,
            error: '',
            notice: '已自动保存',
          };
          if (!shouldRerender) {
            applyFlowerTextFontPickerSelection(state.flowerText.fontFamily);
            setFlowerTextSaveStatus(state.flowerText.notice);
          }
        } catch (error) {
          if (saveSeq !== state.flowerText.autoSaveSeq) return;
          state.flowerText = {
            ...(state.flowerText || {}),
            ...previous,
            saving: false,
            error: error?.message || String(error),
            notice: '',
          };
          if (!shouldRerender) setFlowerTextSaveStatus(`提示：${state.flowerText.error}`);
        } finally {
          if (saveSeq === state.flowerText.autoSaveSeq) {
            renderFlowerTextButton();
            if (shouldRerender) {
              // #region debug-cad45c flower-text flicker tracing
              logFlowerTextFlicker('saveFlowerText:finallyRender', 'post-API finally render drawer', { notice: state.flowerText?.notice, saving: state.flowerText?.saving });
              // #endregion
              if (!state.flowerText?._suppressRender) renderFlowerTextDrawer();
            }
            scheduleFlowerTextPreviewRefresh(0);
          }
        }
      });
    }

    async function saveGenerationMode(patch) {
      const previous = { ...(state.generationMode || {}) };
      const changes = typeof patch === 'boolean' ? { concurrentGeneration: patch } : (patch || {});
      const nextMode = {
        concurrentGeneration: !!(changes.concurrentGeneration ?? previous.concurrentGeneration),
        smartSplit: !!(changes.smartSplit ?? previous.smartSplit),
        confirmSmartSplit: !!(changes.confirmSmartSplit ?? previous.confirmSmartSplit),
        tailFrameChaining: !!(changes.tailFrameChaining ?? previous.tailFrameChaining),
      };
      if (!nextMode.smartSplit) {
        nextMode.confirmSmartSplit = false;
        nextMode.tailFrameChaining = false;
      }
      if (nextMode.tailFrameChaining) nextMode.concurrentGeneration = false;
      state.generationMode = {
        ...(state.generationMode || {}),
        ...nextMode,
        saving: true,
        error: '',
      };
      renderGenerationModeButton();
      renderGenerationModeDrawer();
      renderSmartSplitButton();
      renderSmartSplitDrawer();
      try {
        const res = await fetch('/api/generation-mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(nextMode),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '并发模式保存失败');
        }
        state.generationMode = {
          ...(state.generationMode || {}),
          concurrentGeneration: !!data.concurrentGeneration,
          smartSplit: !!data.smartSplit,
          confirmSmartSplit: !!data.confirmSmartSplit,
          tailFrameChaining: !!data.tailFrameChaining,
          saving: false,
          error: '',
        };
      } catch (error) {
        state.generationMode = {
          ...(state.generationMode || {}),
          ...previous,
          saving: false,
          error: error?.message || String(error),
        };
      } finally {
        renderGenerationModeButton();
        renderGenerationModeDrawer();
        renderSmartSplitButton();
        renderSmartSplitDrawer();
      }
    }

    async function saveHtmlMotionOverlay(enabled) {
      const previous = !!state.htmlMotionOverlay?.enabled;
      state.htmlMotionOverlay = {
        ...(state.htmlMotionOverlay || {}),
        enabled: !!enabled,
        saving: true,
        error: '',
      };
      renderHtmlMotionOverlayButton();
      renderHtmlMotionOverlayDrawer();
      try {
        const res = await fetch('/api/html-motion-overlay', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: !!enabled }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || 'HTML 动效保存失败');
        }
        state.htmlMotionOverlay = {
          ...(state.htmlMotionOverlay || {}),
          enabled: !!data.enabled,
          runtime: data.runtime || null,
          saving: false,
          error: '',
        };
      } catch (error) {
        state.htmlMotionOverlay = {
          ...(state.htmlMotionOverlay || {}),
          enabled: previous,
          saving: false,
          error: error?.message || String(error),
        };
      } finally {
        renderHtmlMotionOverlayButton();
        renderHtmlMotionOverlayDrawer();
      }
    }

    function normalizeFlowerTextSide(value, fallback) {
      const number = Number.parseInt(String(value ?? ''), 10);
      if (!Number.isFinite(number)) return fallback;
      return Math.min(100, Math.max(1, number));
    }

    function flowerTextRatioValue(width, height) {
      const w = normalizeFlowerTextSide(width, 9);
      const h = normalizeFlowerTextSide(height, 16);
      if (w === 16 && h === 9) return '16:9';
      if (w === 1 && h === 1) return '1:1';
      return '9:16';
    }

    function flowerTextRatioParts(value) {
      if (value === '16:9') return { width: 16, height: 9 };
      if (value === '1:1') return { width: 1, height: 1 };
      return { width: 9, height: 16 };
    }

    function defaultHtmlMotionSafeZone(ratio) {
      if (ratio === '9:16') return { x: 8, y: 8, width: 84, height: 38 };
      return { x: 8, y: 8, width: 84, height: 46 };
    }

    function normalizeHtmlMotionSafeZone(value, ratio = '9:16') {
      const fallback = defaultHtmlMotionSafeZone(ratio);
      const source = value && typeof value === 'object' ? value : {};
      const numberOr = (candidate, defaultValue) => Number.isFinite(Number(candidate)) ? Number(candidate) : defaultValue;
      const width = Math.min(96, Math.max(16, numberOr(source.width, fallback.width)));
      const height = Math.min(96, Math.max(16, numberOr(source.height, fallback.height)));
      return {
        x: Math.min(100 - width, Math.max(0, numberOr(source.x, fallback.x))),
        y: Math.min(100 - height, Math.max(0, numberOr(source.y, fallback.y))),
        width,
        height,
      };
    }

    function currentHtmlMotionSafeZone(ratio = flowerTextRatioValue(state.flowerText?.canvasWidth, state.flowerText?.canvasHeight)) {
      const editor = state.htmlMotionSafeZone || {};
      if (editor.editing && editor.draftRatio === ratio && editor.draft) {
        return normalizeHtmlMotionSafeZone(editor.draft, ratio);
      }
      return normalizeHtmlMotionSafeZone(state.htmlMotionOverlay?.safeZones?.[ratio], ratio);
    }

    function applyHtmlMotionSafeZoneBox() {
      const box = document.getElementById('htmlMotionSafeZoneBox');
      if (!box) return;
      const ratio = flowerTextRatioValue(state.flowerText?.canvasWidth, state.flowerText?.canvasHeight);
      const zone = currentHtmlMotionSafeZone(ratio);
      box.style.left = `${zone.x}%`;
      box.style.top = `${zone.y}%`;
      box.style.width = `${zone.width}%`;
      box.style.height = `${zone.height}%`;
    }

    function toggleHtmlMotionSafeZoneEditor() {
      const ratio = flowerTextRatioValue(state.flowerText?.canvasWidth, state.flowerText?.canvasHeight);
      const editing = !state.htmlMotionSafeZone?.editing;
      state.htmlMotionSafeZone = {
        ...(state.htmlMotionSafeZone || {}),
        editing,
        saving: false,
        draftRatio: editing ? ratio : '',
        draft: editing ? currentHtmlMotionSafeZone(ratio) : null,
        drag: null,
      };
      renderFlowerTextDrawer();
    }
