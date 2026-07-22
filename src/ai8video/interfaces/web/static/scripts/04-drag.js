    els.flowerTextDrawer?.addEventListener('dragend', async (event) => {
      const drag = state.flowerText?.drag;
      if (!drag || drag.pointerId !== 'native-drag') return;
      state.flowerText.drag = null;
      document.getElementById('flowerTextEditor')?.classList.remove('dragging');
      document.getElementById('flowerTextDragHandle')?.classList.remove('dragging');
      if (!drag.active) {
        document.getElementById('flowerTextEditorWrap')?.classList.remove('is-dragging');
        if (document.activeElement?.id !== 'flowerTextEditor') {
          document.getElementById('flowerTextEditorWrap')?.classList.remove('is-editing');
        }
        return;
      }
      event.preventDefault();
      clearFlowerTextPositionSaveTimer();
      await saveFlowerText({
        textX: normalizeFlowerTextCoordinate(state.flowerText?.textX, 50),
        textY: normalizeFlowerTextCoordinate(state.flowerText?.textY, 50),
      }, { rerender: false });
      await refreshFlowerTextRenderedPreview();
      document.getElementById('flowerTextEditorWrap')?.classList.remove('is-dragging');
      if (document.activeElement?.id !== 'flowerTextEditor') {
        document.getElementById('flowerTextEditorWrap')?.classList.remove('is-editing');
      }
    });

    els.flowerTextDrawer?.addEventListener('input', (event) => {
      const wmSlider = event.target.closest?.('[data-flower-watermark-style="1"]');
      const wm2Slider = event.target.closest?.('[data-flower-watermark-style="2"]');
      if (wmSlider) {
        const h = document.getElementById('flowerTextWatermarkDragHandle');
        if (h) h.style.setProperty('--flower-watermark-size', wmSlider.value + '%');
      }
      if (wm2Slider) {
        const h = document.getElementById('flowerTextWatermark2DragHandle');
        if (h) h.style.setProperty('--flower-watermark-size', wm2Slider.value + '%');
      }
    });

    els.flowerTextDrawer?.addEventListener('change', async (event) => {
      const immediateStyleControl = event.target.closest('[data-flower-text-style]');
      if (immediateStyleControl) {
        const styleKey = immediateStyleControl.getAttribute('data-flower-text-style') || '';
        if (styleKey === 'watermarkSize' || styleKey === 'watermark2Size') {
          const patch = flowerTextStylePatch(styleKey, immediateStyleControl.value);
          if (!patch) return;
          syncFlowerTextEditorDraft();
          state.flowerText = {
            ...(state.flowerText || {}),
            ...patch,
            error: '',
            notice: '松手后立即保存',
            _suppressRender: true,
          };
          setFlowerTextSaveStatus(state.flowerText.notice);
          if (state.flowerText._suppressTimer) clearTimeout(state.flowerText._suppressTimer);
          state.flowerText._suppressTimer = setTimeout(() => {
            if (state.flowerText) state.flowerText._suppressRender = false;
          }, 800);
          await saveFlowerText(patch, { rerender: false });
          return;
        }
      }
      const watermarkFileInput = event.target.closest('[data-flower-watermark-file-input]');
      if (watermarkFileInput) {
        const files = Array.from(watermarkFileInput.files || []);
        watermarkFileInput.value = '';
        if (files.length) {
          const watermarkIdx = watermarkFileInput.getAttribute('data-flower-watermark-file-input') || '1';
          await uploadFlowerWatermarkFiles(files, watermarkIdx);
        }
        return;
      }
      const backgroundFileInput = event.target.closest('[data-flower-background-file-input]');
      if (backgroundFileInput) {
        const file = Array.from(backgroundFileInput.files || [])[0] || null;
        backgroundFileInput.value = '';
        if (file) {
          await uploadFlowerPreviewBackgroundImage(file);
        }
        return;
      }
      const backgroundColorInput = event.target.closest('[data-flower-background-color-input]');
      if (backgroundColorInput) {
        await updateFlowerPreviewBackgroundColor(backgroundColorInput.value);
        return;
      }
      const toggle = event.target.closest('[data-flower-text-toggle]');
      if (toggle) {
        await saveFlowerText({ enabled: !!toggle.checked });
        return;
      }
      const watermarkStyleControl = event.target.closest('[data-flower-watermark-style]');
      if (watermarkStyleControl) {
        const wmIdx = watermarkStyleControl.getAttribute('data-flower-watermark-style') || '1';
        const value = parseFloat(watermarkStyleControl.value);
        if (isNaN(value)) return;
        if (wmIdx === '2') {
          await saveFlowerText({ watermark2Size: value });
        } else {
          await saveFlowerText({ watermarkSize: value });
        }
        return;
      }
      const ratioSelect = event.target.closest('[data-flower-text-ratio-select]');
      if (ratioSelect) {
        const ratio = flowerTextRatioParts(ratioSelect.value);
        state.htmlMotionSafeZone = { editing: false, saving: false, draft: null, drag: null };
        await saveFlowerText({ canvasWidth: ratio.width, canvasHeight: ratio.height });
        return;
      }
      const styleControl = event.target.closest('[data-flower-text-style]');
      if (styleControl) {
        const key = styleControl.getAttribute('data-flower-text-style');
        const patch = flowerTextStylePatch(key, styleControl.value);
        if (patch) {
          syncFlowerTextEditorDraft();
          state.flowerText = {
            ...(state.flowerText || {}),
            ...patch,
            error: '',
            notice: '保存中...',
          };
          applyFlowerTextEditorStyle();
          setFlowerTextSaveStatus(state.flowerText.notice);
          await saveFlowerText(patch, { rerender: false });
          scheduleFlowerTextPreviewRefresh(0);
        }
      }
    });

    els.backgroundMusicUploadInput?.addEventListener('change', async () => {
      const file = els.backgroundMusicUploadInput.files?.[0];
      els.backgroundMusicUploadInput.value = '';
      if (!file) return;
      await uploadBackgroundMusic(file);
    });

    els.localTtsVoiceCloneUploadInput?.addEventListener('change', async () => {
      const file = els.localTtsVoiceCloneUploadInput.files?.[0];
      els.localTtsVoiceCloneUploadInput.value = '';
      if (!file) return;
      await uploadLocalTtsVoiceClone(file);
    });

    els.systemPromptDrawer?.addEventListener('input', (event) => {
      if (event.target?.id === 'systemPromptEditor') {
        scheduleSystemPromptAutoSave(event.target.value);
      }
    });

    els.systemPromptDrawer?.addEventListener('focusout', async (event) => {
      if (event.target?.id === 'systemPromptEditor') {
        clearSystemPromptAutoSaveTimer();
        await saveSystemPromptContent(event.target.value);
      }
    });

    els.settingsModalCloseButton.addEventListener('click', () => {
      closeSettingsModal();
    });

    els.settingsModal.addEventListener('click', (event) => {
      if (event.target === els.settingsModal) {
        closeSettingsModal();
      }
    });

    els.videoParamsModalCloseButton?.addEventListener('click', () => {
      closeVideoParamsModal();
    });

    els.videoParamsModal?.addEventListener('click', (event) => {
      if (event.target === els.videoParamsModal) {
        closeVideoParamsModal();
      }
    });

    els.videoParamsModal?.addEventListener('input', (event) => {
      if (isVideoParamsControl(event.target)) {
        scheduleVideoParamsAutoSave();
      }
    });

    els.videoParamsModal?.addEventListener('change', async (event) => {
      if (isVideoParamsControl(event.target)) {
        clearVideoParamsAutoSaveTimer();
        await autoSaveVideoParamsFromCurrentForm();
        if (event.target.name === 'resolutionMode' || event.target.name === 'ratio') {
          renderVideoParamsModal();
        }
      }
    });

    els.videoParamsModal?.addEventListener('focusout', async (event) => {
      if (isVideoParamsControl(event.target)) {
        clearVideoParamsAutoSaveTimer();
        await autoSaveVideoParamsFromCurrentForm();
      }
    });

    els.systemPromptModalCloseButton.addEventListener('click', () => {
      closeSystemPromptModal();
    });

    els.systemPromptModal.addEventListener('click', (event) => {
      if (event.target === els.systemPromptModal) {
        closeSystemPromptModal();
      }
    });

    els.materialLibraryOpenFolderButton.addEventListener('click', async () => {
      const kind = state.materialModal.kind || 'image';
      try {
        await openUserMaterialFolder(kind, els.materialLibraryOpenFolderButton);
        await refreshUserMaterials();
        if (kind === 'script') {
          await refreshScriptKnowledge({ preserveSelection: true });
        }
        renderUserMaterials();
        renderDefaultReferenceDrawer();
        renderScriptReferenceDrawer();
        renderFlowerTextDrawer();
        renderMaterialLibraryModal();
        renderMaterialMentionPicker();
      } catch (error) {
        console.error(error);
      }
    });

    els.recycleBinOpenFolderButton.addEventListener('click', async () => {
      try {
        await openUserRecycleBinFolder(els.recycleBinOpenFolderButton);
        await refreshRecycleBin();
        renderRecycleBin();
        renderRecycleBinModal();
      } catch (error) {
        console.error(error);
      }
    });

    els.recycleBinSelectAllButton.addEventListener('click', () => {
      toggleAllRecycleBinTasks();
    });

    els.recycleBinBatchDeleteButton.addEventListener('click', async () => {
      await deleteSelectedRecycleBinTasks();
    });

    els.recycleBinWall.addEventListener('change', (event) => {
      const checkbox = event.target?.closest?.('[data-select-recycle-bin-folder]');
      if (!checkbox) return;
      const folder = String(checkbox.getAttribute('data-select-recycle-bin-folder') || '').trim();
      if (!folder) return;
      const selectedFolders = new Set(state.recycleBinModal.selectedFolders || []);
      if (checkbox.checked) selectedFolders.add(folder);
      else selectedFolders.delete(folder);
      state.recycleBinModal.selectedFolders = [...selectedFolders];
      syncRecycleBinBatchDeleteButton();
    });

    els.materialLibraryModal.addEventListener('click', async (event) => {
      if (await handleScriptKnowledgeModalClick(event)) return;
      if (event.target === els.materialLibraryModal) {
        closeMaterialLibraryModal();
      }
    });

    els.recycleBinModal.addEventListener('click', (event) => {
      if (event.target === els.recycleBinModal) {
        closeRecycleBinModal();
      }
    });

    els.userMaterialUploadInput.addEventListener('change', async () => {
      const files = Array.from(els.userMaterialUploadInput.files || []);
      const kind = els.userMaterialUploadInput.dataset.kind || 'image';
      const purpose = els.userMaterialUploadInput.dataset.purpose || '';
      els.userMaterialUploadInput.value = '';
      els.userMaterialUploadInput.dataset.purpose = '';
      if (!files.length) return;
      try {
        const uploadKind = purpose === 'flower-watermark' && kind !== 'script' ? 'flower-watermark' : kind;
        if (uploadKind === 'flower-watermark') {
          await ensureFlowerWatermarkLibraryReady();
        }
        const uploadData = await uploadUserMaterials(uploadKind, files);
        const savedImageItems = uploadKind === 'image' ? normalizeUploadedImageMaterialItems(uploadData?.saved) : [];
        const savedWatermarkItems = uploadKind === 'flower-watermark' ? normalizeUploadedFlowerWatermarkItems(uploadData?.saved) : [];
        rememberUploadedImageMaterials(savedImageItems);
        rememberUploadedFlowerWatermarks(savedWatermarkItems);
        await refreshUserMaterials();
        if (uploadKind === 'script') {
          await refreshScriptKnowledge({ preserveSelection: false });
        }
        rememberUploadedImageMaterials(savedImageItems);
        rememberUploadedFlowerWatermarks(savedWatermarkItems);
        if (purpose === 'flower-watermark' && uploadKind === 'flower-watermark') {
          const firstSaved = savedWatermarkItems[0] || null;
          const nextWatermarkImage = String(firstSaved?.relativePath || '').trim();
          if (nextWatermarkImage) {
            state.flowerText = {
              ...(state.flowerText || {}),
              enabled: true,
              watermarkEnabled: true,
              watermarkImage: nextWatermarkImage,
              error: '',
              notice: '水印已上传，保存中...',
            };
            renderFlowerTextButton();
            renderFlowerTextDrawer();
            await saveFlowerText({
              enabled: true,
              watermarkEnabled: true,
              watermarkImage: nextWatermarkImage,
            }, { rerender: false });
          }
        }
        renderUserMaterials();
        renderDefaultReferenceDrawer();
        renderScriptReferenceDrawer();
        renderFlowerTextDrawer();
        renderMaterialLibraryModal();
        renderMaterialMentionPicker();
      } catch (error) {
        console.error(error);
      }
    });

    document.getElementById('viralBreakdownUploadInput')?.addEventListener('change', async (event) => {
      const input = event.target;
      const files = Array.from(input?.files || []);
      if (input) input.value = '';
      if (!files.length) return;
      try {
        await uploadViralBreakdownVideos(files);
      } catch (error) {
        console.error(error);
        state.viralBreakdown.error = error?.message || String(error);
        renderViralBreakdownWorkbench();
      }
    });

    document.getElementById('viralBreakdownOpenFolderButton')?.addEventListener('click', async (event) => {
      try {
        await openViralBreakdownFolder(event.currentTarget);
      } catch (error) {
        console.error(error);
        state.viralBreakdown.error = error?.message || String(error);
        renderViralBreakdownWorkbench();
      }
    });

    document.getElementById('viralBreakdownVideoSelect')?.addEventListener('change', (event) => {
      state.viralBreakdown.selectedVideoKey = String(event.target?.value || '');
      state.viralBreakdown.error = '';
      if (!state.viralBreakdown.loading) {
        state.viralBreakdown.notice = state.viralBreakdown.selectedVideoKey ? '已切换当前视频。' : state.viralBreakdown.notice;
      }
      renderViralBreakdownWorkbench();
    });

    document.getElementById('viralBreakdownIntervalInput')?.addEventListener('change', (event) => {
      const nextValue = Number(event.target?.value || 1);
      state.viralBreakdown.intervalSeconds = Number.isFinite(nextValue) && nextValue > 0 ? nextValue : 1;
      renderViralBreakdownWorkbench();
    });

    document.getElementById('viralBreakdownTargetRatio')?.addEventListener('change', (event) => {
      state.viralBreakdown.targetRatio = String(event.target?.value || '16:9');
      renderViralBreakdownWorkbench();
    });

    document.getElementById('viralBreakdownProcessFramesButton')?.addEventListener('click', async () => {
      try {
        await processSelectedViralBreakdownFrames();
      } catch (error) {
        console.error(error);
        state.viralBreakdown.error = error?.message || String(error);
        renderViralBreakdownWorkbench();
      }
    });

    document.getElementById('viralBreakdownTranscribeButton')?.addEventListener('click', async () => {
      try {
        await transcribeSelectedViralBreakdownVideo();
      } catch (error) {
        console.error(error);
        state.viralBreakdown.error = error?.message || String(error);
        renderViralBreakdownWorkbench();
      }
    });

    document.getElementById('viralBreakdownGuessScriptButton')?.addEventListener('click', async () => {
      try {
        await guessSelectedViralBreakdownScript();
      } catch (error) {
        console.error(error);
        state.viralBreakdown.error = error?.message || String(error);
        renderViralBreakdownWorkbench();
      }
    });

    document.getElementById('viralBreakdownCloseButton')?.addEventListener('click', () => {
      closeViralBreakdownModal();
    });

    document.getElementById('viralBreakdownCancelButton')?.addEventListener('click', () => {
      closeViralBreakdownModal();
    });


    document.getElementById('hotRadarKeywordInput')?.addEventListener('change', async (event) => {
      state.hotRadar.keyword = String(event.target?.value || '').trim();
      persistHotRadarViewState(state.hotRadar);
      resetHotRadarGeneratedContent();
      try {
        await refreshHotRadarTopics();
      } catch (error) {
        console.error(error);
        state.hotRadar.loading = false;
        state.hotRadar.error = error?.message || String(error);
        renderHotRadarWorkbench();
      }
    });

    document.getElementById('hotRadarRefreshButton')?.addEventListener('click', async () => {
      try {
        await refreshHotRadarTopics({ forceRefresh: true });
      } catch (error) {
        console.error(error);
        state.hotRadar.loading = false;
        state.hotRadar.error = error?.message || String(error);
        renderHotRadarWorkbench();
      }
    });

    document.getElementById('hotRadarSourceManagerCloseButton')?.addEventListener('click', closeHotRadarSourceManager);
    document.getElementById('hotRadarSourceManagerCancelButton')?.addEventListener('click', closeHotRadarSourceManager);
    document.getElementById('hotRadarCustomSourceAddButton')?.addEventListener('click', addHotRadarCustomSourceDraft);
    document.getElementById('hotRadarSourceManagerSaveButton')?.addEventListener('click', saveHotRadarCustomSources);
    document.getElementById('hotRadarSourceManagerList')?.addEventListener('click', (event) => {
      const button = event.target?.closest?.('[data-remove-hot-radar-source]');
      if (!button) return;
      const sourceId = String(button.getAttribute('data-remove-hot-radar-source') || '');
      state.hotRadar.sourceDrafts = state.hotRadar.sourceDrafts.filter((item) => String(item.id) !== sourceId);
      renderHotRadarSourceManager();
    });

    document.getElementById('hotRadarClearButton')?.addEventListener('click', async () => {
      state.hotRadar.selectedCategory = '';
      state.hotRadar.selectedSourceId = '';
      state.hotRadar.selectedTopicId = '';
      state.hotRadar.keyword = '';
      persistHotRadarViewState(state.hotRadar);
      resetHotRadarGeneratedContent();
      try {
        await refreshHotRadarTopics();
      } catch (error) {
        console.error(error);
        state.hotRadar.error = error?.message || String(error);
        renderHotRadarWorkbench();
      }
    });

    document.getElementById('hotRadarColumnToggleButton')?.addEventListener('click', () => {
      state.hotRadar.columnCount = Number(state.hotRadar.columnCount) === 2 ? 1 : 2;
      try {
        localStorage.setItem(HOT_RADAR_COLUMN_COUNT_STORAGE_KEY, String(state.hotRadar.columnCount));
      } catch {
        // 本地存储不可用时，本次页面内的列数切换仍然有效。
      }
      renderHotRadarWorkbench();
    });

    document.getElementById('hotRadarSourceSelect')?.addEventListener('change', async (event) => {
      const selectedValue = String(event.target?.value || '');
      if (selectedValue === '__add_source__') {
        event.target.value = String(state.hotRadar.selectedSourceId || '');
        openHotRadarSourceManager();
        return;
      }
      state.hotRadar.selectedSourceId = selectedValue;
      state.hotRadar.selectedCategory = '';
      persistHotRadarViewState(state.hotRadar);
      resetHotRadarGeneratedContent();
      try {
        await refreshHotRadarTopics();
      } catch (error) {
        console.error(error);
        state.hotRadar.loading = false;
        state.hotRadar.error = error?.message || String(error);
        renderHotRadarWorkbench();
      }
    });

