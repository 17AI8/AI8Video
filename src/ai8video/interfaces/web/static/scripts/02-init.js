
    init();

    async function init() {
      document.title = `${BRAND_NAME} 工作台`;

      if (els.brandName) {
        els.brandName.textContent = BRAND_NAME;
      }
      if (els.brandSlug) {
      els.brandSlug.textContent = '批量创作助手';
      }
      if (RESET_SESSIONS) {
        removeProductStorageEntry(SESSION_STORAGE_KEY);
        history.replaceState(null, '', location.pathname);
      }
      if (!state.sessions.length) {
        createSession(NEW_SESSION_TITLE);
      }
      state.activeId = state.sessions[0].id;
      pruneSettledPendingProgressFromSessions();
      persistSessions();
      await refreshHealth();
      await recoverSessionsAfterReload();
      persistSessions();
      await refreshAuthSettings();
      await refreshVideoModelSettings();
      await refreshAssets();
      await refreshUserGeneratedResults();
      await refreshUserMaterials();
      await refreshBackgroundMusic();
      await refreshDefaultReferenceImage();
      await refreshScriptReference();
      await refreshFlowerText();
      await refreshGenerationMode();
      await refreshHtmlMotionOverlay();


      await refreshBatchAlerts();
      await refreshBatchReports();
      render();
    }

    els.supervisorConfigForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      await submitSupervisorConfigModal();
    });

    els.supervisorConfigCloseButton.addEventListener('click', () => {
      closeSupervisorConfigModal();
    });

    els.supervisorConfigCancelButton.addEventListener('click', () => {
      closeSupervisorConfigModal();
    });

    els.materialLibraryCloseButton.addEventListener('click', () => {
      closeMaterialLibraryModal();
    });

    els.scriptKnowledgeSearchInput?.addEventListener('input', () => {
      state.scriptKnowledge.query = els.scriptKnowledgeSearchInput.value;
      if (scriptKnowledgeSearchTimer) clearTimeout(scriptKnowledgeSearchTimer);
      scriptKnowledgeSearchTimer = setTimeout(() => {
        refreshScriptKnowledge({ preserveSelection: true });
      }, 260);
    });

    els.scriptKnowledgeSearchClearButton?.addEventListener('click', () => {
      state.scriptKnowledge.query = '';
      els.scriptKnowledgeSearchInput.value = '';
      refreshScriptKnowledge({ preserveSelection: false });
    });


    els.recycleBinCloseButton.addEventListener('click', () => {
      closeRecycleBinModal();
    });



























    els.progressModalCloseButton.addEventListener('click', () => {
      closeProgressModal();
    });

    els.progressModal.addEventListener('click', (event) => {
      if (event.target === els.progressModal) {
        closeProgressModal();
      }
    });

    els.resultModalCloseButton.addEventListener('click', () => {
      closeResultModal();
    });

    els.resultModalOpenFolderButton.addEventListener('click', async () => {
      try {
        await openUserGeneratedResultsFolder(els.resultModalOpenFolderButton);
      } catch (error) {
        console.error(error);
      }
    });
    els.resultModalRefreshButton.addEventListener('click', async () => {
      try {
        await refreshResultModalData(els.resultModalRefreshButton);
      } catch (error) {
        console.error(error);
      }
    });







    els.resultModal.addEventListener('click', (event) => {
      if (event.target === els.resultModal) {
        closeResultModal();
      }
    });

    els.videoPreviewCloseButton.addEventListener('click', () => {
      closeVideoPreviewModal();
    });

    els.videoPreviewModal.addEventListener('click', (event) => {
      if (event.target === els.videoPreviewModal) {
        closeVideoPreviewModal();
      }
    });

    els.settingsEntryButton.addEventListener('click', async () => {
      await openSettingsModal();
    });
    els.mobileSettingsEntryButton?.addEventListener('click', async () => {
      await openSettingsModal();
    });

    els.systemPromptButton.addEventListener('click', async () => {
      await openSystemPromptModal();
    });

    els.backgroundMusicButton?.addEventListener('click', async () => {
      await openBackgroundMusicDrawer();
    });

    els.defaultReferenceButton?.addEventListener('click', async () => {
      await openDefaultReferenceDrawer();
    });

    els.scriptReferenceButton?.addEventListener('click', async () => {
      await openScriptReferenceDrawer();
    });

    els.flowerTextButton?.addEventListener('click', async () => {
      await openFlowerTextDrawer();
    });

    els.generationModeButton?.addEventListener('click', async () => {
      await openGenerationModeDrawer();
    });

    els.htmlMotionOverlayButton?.addEventListener('click', async () => {
      await openHtmlMotionOverlayDrawer();
    });

    els.clearConversationButton?.addEventListener('click', () => {
      openClearConversationConfirmModal();
    });

    els.clearConversationConfirmCloseButton?.addEventListener('click', () => {
      closeClearConversationConfirmModal();
    });

    els.clearConversationConfirmCancelButton?.addEventListener('click', () => {
      closeClearConversationConfirmModal();
    });

    els.clearConversationConfirmModal?.addEventListener('click', (event) => {
      if (event.target === els.clearConversationConfirmModal) {
        closeClearConversationConfirmModal();
      }
    });

    els.clearConversationConfirmSubmitButton?.addEventListener('click', () => {
      closeClearConversationConfirmModal();
      clearActiveConversationTextMessages();
    });

    els.messageEditor.addEventListener('focus', () => {
      closeComposerToolDrawers();
    });

    els.messageEditor.addEventListener('click', () => {
      closeComposerToolDrawers();
    });

    els.backgroundMusicDrawer?.addEventListener('click', async (event) => {
      if (event.target.closest('[data-background-music-volume-control]')) {
        event.stopPropagation();
        return;
      }
      const addButton = event.target.closest('[data-add-background-music]');
      if (addButton) {
        beginBackgroundMusicUpload();
        return;
      }
      const folderButton = event.target.closest('[data-open-background-music-folder]');
      if (folderButton) {
        await openBackgroundMusicFolder(folderButton);
        return;
      }
      const audioModeButton = event.target.closest('[data-background-audio-mode]');
      if (audioModeButton) {
        await updateBackgroundAudioMode(audioModeButton.getAttribute('data-background-audio-mode') || 'original');
        return;
      }
      const selectButton = event.target.closest('[data-select-background-music]');
      if (selectButton) {
        const isSelected = selectButton.getAttribute('data-background-music-selected') === '1';
        if (isSelected) {
          await clearBackgroundMusicSelection();
        } else {
          await selectBackgroundMusic(selectButton.getAttribute('data-select-background-music') || '');
        }
      }
    });

    els.backgroundMusicDrawer?.addEventListener('input', (event) => {
      const slider = event.target.closest('[data-background-music-volume]');
      if (!slider) return;
      const percent = normalizeBackgroundMusicVolumePercent(slider.value);
      state.backgroundMusic = {
        ...(state.backgroundMusic || {}),
        volumePercent: percent,
        volume: percent / 100,
        error: '',
      };
      const label = els.backgroundMusicDrawer?.querySelector('[data-background-music-volume-label]');
      if (label) label.textContent = `音量 ${percent}%`;
    });

    els.settingsModalBody?.addEventListener('input', (event) => {
      const narrationReviewInput = event.target.closest('[data-narration-review-count]');
      if (narrationReviewInput) {
        narrationReviewInput.value = String(normalizeNarrationReviewCount(narrationReviewInput.value));
        scheduleNarrationReviewSave(narrationReviewInput);
        return;
      }
      const retryInput = event.target.closest('[data-html-motion-quality-retry]');
      if (retryInput) {
        retryInput.value = String(normalizeHtmlMotionQualityRetryCount(retryInput.value));
        scheduleHtmlMotionSettingSave(retryInput, 'retry');
        return;
      }
      const beatIntervalInput = event.target.closest('[data-html-motion-beat-interval]');
      if (beatIntervalInput) {
        beatIntervalInput.value = String(normalizeHtmlMotionBeatIntervalSeconds(beatIntervalInput.value));
        scheduleHtmlMotionSettingSave(beatIntervalInput, 'interval');
        return;
      }
      const slider = event.target.closest('[data-local-tts-volume]');
      if (!slider) return;
      const percent = normalizeLocalTtsVolumePercent(slider.value);
      slider.value = String(percent);
      state.localTts = {
        ...(state.localTts || {}),
        volume: percent / 100,
      };
      const label = slider.closest('[data-local-tts-volume-control]')?.querySelector('[data-local-tts-volume-label]');
      if (label) label.textContent = `音量 ${percent}%`;
    });

    els.settingsModalBody?.addEventListener('change', async (event) => {
      const narrationReviewInput = event.target.closest('[data-narration-review-count]');
      if (narrationReviewInput) {
        clearNarrationReviewSaveTimer();
        await saveNarrationReviewCount(narrationReviewInput);
        return;
      }
      const retryInput = event.target.closest('[data-html-motion-quality-retry]');
      const beatIntervalInput = event.target.closest('[data-html-motion-beat-interval]');
      if (!retryInput && !beatIntervalInput) return;
      clearHtmlMotionSettingSaveTimer();
      if (beatIntervalInput) {
        await saveHtmlMotionBeatInterval(beatIntervalInput);
        return;
      }
      await saveHtmlMotionQualityRetry(retryInput);
    });

    function clearHtmlMotionSettingSaveTimer() {
      if (!state.settingsModal.htmlMotionSaveTimer) return;
      clearTimeout(state.settingsModal.htmlMotionSaveTimer);
      state.settingsModal.htmlMotionSaveTimer = null;
    }

    function clearNarrationReviewSaveTimer() {
      if (!state.settingsModal.narrationReviewSaveTimer) return;
      clearTimeout(state.settingsModal.narrationReviewSaveTimer);
      state.settingsModal.narrationReviewSaveTimer = null;
    }

    function scheduleNarrationReviewSave(input) {
      clearNarrationReviewSaveTimer();
      state.settingsModal.narrationReviewSaveTimer = setTimeout(() => {
        state.settingsModal.narrationReviewSaveTimer = null;
        void saveNarrationReviewCount(input);
      }, 450);
    }

    async function saveNarrationReviewCount(input) {
      const previous = normalizeNarrationReviewCount(state.narrationReview?.reviewCount);
      const reviewCount = normalizeNarrationReviewCount(input.value);
      state.narrationReview.reviewCount = reviewCount;
      input.disabled = true;
      try {
        const res = await fetch('/api/narration-review', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reviewCount }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        state.narrationReview = { ...state.narrationReview, ...data };
        await refreshAuthSettings();
        showSettingsSavedBadge();
      } catch (error) {
        state.narrationReview.reviewCount = previous;
        window.alert(error?.message || '台词审核次数保存失败');
      } finally {
        renderSettingsModal();
      }
    }

    function scheduleHtmlMotionSettingSave(input, kind) {
      clearHtmlMotionSettingSaveTimer();
      state.settingsModal.htmlMotionSaveTimer = setTimeout(() => {
        state.settingsModal.htmlMotionSaveTimer = null;
        if (kind === 'interval') {
          void saveHtmlMotionBeatInterval(input);
          return;
        }
        void saveHtmlMotionQualityRetry(input);
      }, 450);
    }

    async function saveHtmlMotionQualityRetry(input) {
      const previous = normalizeHtmlMotionQualityRetryCount(state.htmlMotionOverlay?.qualityRetryCount);
      const qualityRetryCount = normalizeHtmlMotionQualityRetryCount(input.value);
      state.htmlMotionOverlay.qualityRetryCount = qualityRetryCount;
      input.disabled = true;
      try {
        const res = await fetch('/api/html-motion-overlay', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ qualityRetryCount }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        state.htmlMotionOverlay = { ...state.htmlMotionOverlay, ...data };
        await refreshAuthSettings();
        showSettingsSavedBadge();
      } catch (error) {
        state.htmlMotionOverlay.qualityRetryCount = previous;
        window.alert(error?.message || 'HTML 动效重试次数保存失败');
      } finally {
        renderSettingsModal();
      }
    }

    async function saveHtmlMotionBeatInterval(input) {
      if (state.htmlMotionOverlay?.smartBeatInterval) return;
      const previous = normalizeHtmlMotionBeatIntervalSeconds(state.htmlMotionOverlay?.beatIntervalSeconds);
      const beatIntervalSeconds = normalizeHtmlMotionBeatIntervalSeconds(input.value);
      state.htmlMotionOverlay.beatIntervalSeconds = beatIntervalSeconds;
      input.disabled = true;
      try {
        const res = await fetch('/api/html-motion-overlay', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ beatIntervalSeconds }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        state.htmlMotionOverlay = { ...state.htmlMotionOverlay, ...data };
        await refreshAuthSettings();
        showSettingsSavedBadge();
      } catch (error) {
        state.htmlMotionOverlay.beatIntervalSeconds = previous;
        window.alert(error?.message || 'HTML 动效每拍间隔保存失败');
      } finally {
        renderSettingsModal();
      }
    }

    async function saveHtmlMotionSmartBeatInterval(enabled) {
      const previous = !!state.htmlMotionOverlay?.smartBeatInterval;
      state.htmlMotionOverlay.smartBeatInterval = !!enabled;
      renderSettingsModal();
      try {
        const res = await fetch('/api/html-motion-overlay', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ smartBeatInterval: !!enabled }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        state.htmlMotionOverlay = { ...state.htmlMotionOverlay, ...data };
        showSettingsSavedBadge();
      } catch (error) {
        state.htmlMotionOverlay.smartBeatInterval = previous;
        window.alert(error?.message || 'HTML 动效智能间隔模式保存失败');
      } finally {
        renderSettingsModal();
      }
    }

    els.backgroundMusicDrawer?.addEventListener('change', async (event) => {
      const slider = event.target.closest('[data-background-music-volume]');
      if (!slider) return;
      await updateBackgroundMusicVolume(slider.value);
    });

    els.defaultReferenceDrawer?.addEventListener('click', async (event) => {
      const addImageButton = event.target.closest('[data-add-default-reference-image]');
      if (addImageButton) {
        beginUserMaterialUpload('image');
        return;
      }
      const selectButton = event.target.closest('[data-select-default-reference]');
      if (selectButton) {
        const isSelected = selectButton.getAttribute('data-default-reference-selected') === '1';
        if (isSelected) {
          await clearDefaultReferenceImage();
        } else {
          await selectDefaultReferenceImage(selectButton.getAttribute('data-select-default-reference') || '');
        }
      }
    });

    els.defaultReferenceDrawer?.addEventListener('change', async (event) => {
      const option = event.target.closest('[data-default-reference-option]');
      if (!option) return;
      const key = option.getAttribute('data-default-reference-option') || '';
      await updateDefaultReferenceOptions({ [key]: !!option.checked });
    });

    els.defaultReferenceDrawer?.addEventListener('input', (event) => {
      const textarea = event.target.closest('[data-default-reference-custom-prompt]');
      if (!textarea) return;
      syncDefaultReferenceCustomPromptDraft(textarea.value || '');
      if (event.isComposing || state.defaultReferenceDrawer.customPromptComposing) {
        clearDefaultReferenceCustomPromptSaveTimer();
        return;
      }
      scheduleDefaultReferenceCustomPromptSave(textarea.value || '');
    });

    els.defaultReferenceDrawer?.addEventListener('compositionstart', (event) => {
      const textarea = event.target.closest('[data-default-reference-custom-prompt]');
      if (!textarea) return;
      state.defaultReferenceDrawer.customPromptComposing = true;
      syncDefaultReferenceCustomPromptDraft(textarea.value || '');
      clearDefaultReferenceCustomPromptSaveTimer();
    });

    els.defaultReferenceDrawer?.addEventListener('compositionend', (event) => {
      const textarea = event.target.closest('[data-default-reference-custom-prompt]');
      if (!textarea) return;
      state.defaultReferenceDrawer.customPromptComposing = false;
      scheduleDefaultReferenceCustomPromptSave(textarea.value || '');
    });
