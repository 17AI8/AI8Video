    document.getElementById('hotRadarModal')?.addEventListener('click', async (event) => {
      const topicButton = event.target?.closest?.('[data-hot-radar-topic-id]');
      try {
        if (topicButton) {
          state.hotRadar.selectedTopicId = String(topicButton.getAttribute('data-hot-radar-topic-id') || '');
          state.hotRadar.summaryText = '';
          state.hotRadar.promptText = '';
          persistHotRadarViewState(state.hotRadar);
          renderHotRadarWorkbench();
        }
      } catch (error) {
        console.error(error);
        state.hotRadar.loading = false;
        state.hotRadar.error = error?.message || String(error);
        renderHotRadarWorkbench();
      }
    });

    document.getElementById('progressModalBody')?.addEventListener('click', (event) => {
      const button = event.target?.closest?.('[data-retry-generation-video]');
      if (button) retryFailedGenerationVideo(button);
    });

    els.messages?.addEventListener('click', (event) => {
      const button = event.target?.closest?.('[data-retry-generation-video]');
      if (button) retryFailedGenerationVideo(button);
    });

    document.getElementById('hotRadarSummaryButton')?.addEventListener('click', async () => {
      try {
        await summarizeSelectedHotRadarTopic();
      } catch (error) {
        console.error(error);
        state.hotRadar.summarizing = false;
        state.hotRadar.error = error?.message || String(error);
        state.hotRadar.summaryText = state.hotRadar.error;
        renderHotRadarWorkbench();
      }
    });

    document.getElementById('hotRadarPromptButton')?.addEventListener('click', async () => {
      try {
        await buildSelectedHotRadarPrompt();
      } catch (error) {
        console.error(error);
        state.hotRadar.promptBuilding = false;
        state.hotRadar.error = error?.message || String(error);
        renderHotRadarWorkbench();
      }
    });

    document.getElementById('hotRadarFillPromptButton')?.addEventListener('click', fillHotRadarPromptIntoComposer);

    document.getElementById('hotRadarCloseButton')?.addEventListener('click', closeHotRadarModal);

    document.getElementById('hotRadarModal')?.addEventListener('click', (event) => {
      if (event.target === document.getElementById('hotRadarModal')) {
        closeHotRadarModal();
      }
    });

    els.supervisorConfigModal.addEventListener('click', (event) => {
      if (event.target === els.supervisorConfigModal) {
        closeSupervisorConfigModal();
      }
    });

    els.supervisorConfigForm.addEventListener('input', () => {
      if (!state.supervisorModal.visible) return;
      state.supervisorModal.error = '';
      state.supervisorModal.draftSource = '本机暂存草稿';
      saveSupervisorConfigDraft(readSupervisorConfigFormValue());
      renderSupervisorConfigModal();
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !document.getElementById('viralBreakdownModal')?.classList.contains('hidden')) {
        event.preventDefault();
        closeViralBreakdownModal();
        return;
      }
      if (event.key === 'Escape' && state.progressModal.visible) {
        event.preventDefault();
        closeProgressModal();
        return;
      }
      if (event.key === 'Escape' && state.resultModal.visible) {
        event.preventDefault();
        closeResultModal();
        return;
      }
      if (event.key === 'Escape' && state.videoPreviewModal.visible) {
        event.preventDefault();
        closeVideoPreviewModal();
        return;
      }
      if (event.key === 'Escape' && state.settingsModal.visible) {
        event.preventDefault();
        closeSettingsModal();
        return;
      }
      if (event.key === 'Escape' && state.videoParamsModal.visible) {
        event.preventDefault();
        closeVideoParamsModal();
        return;
      }
      if (event.key === 'Escape' && state.systemPromptModal.visible) {
        event.preventDefault();
        closeSystemPromptModal();
        return;
      }
      if (event.key === 'Escape' && state.materialModal.visible) {
        event.preventDefault();
        closeMaterialLibraryModal();
        return;
      }
      if (event.key === 'Escape' && state.supervisorModal.visible) {
        event.preventDefault();
        closeSupervisorConfigModal();
        return;
      }
      if (event.key === 'Escape' && state.clearConversationModal.visible) {
        event.preventDefault();
        closeClearConversationConfirmModal();
      }
    });

    async function refreshHealth() {
      const res = await fetch('/api/health');
      state.health = await res.json();
      if (Object.prototype.hasOwnProperty.call(state.health || {}, 'batchSupervisorAdminResult')) {
        const backendAdminResult = state.health?.batchSupervisorAdminResult;
        persistSupervisorAdminResult(
          backendAdminResult && typeof backendAdminResult === 'object' ? backendAdminResult : null
        );
      }
    }

    async function refreshAuthSettings() {
      const res = await fetch('/api/auth-settings');
      state.authSettings = await res.json();
      state.localTts = state.authSettings?.localTts || state.localTts;
      state.archiveArtifacts = state.authSettings?.archiveArtifacts || state.archiveArtifacts || null;
      const mergeField = (state.authSettings?.fields || []).find((field) => field?.envName === 'AI8VIDEO_VIDEO_MERGE');
      if (mergeField) {
        state.settingsModal.videoMergeMode = normalizeVideoMergeMode(mergeField.value);
      }
      const htmlMotionRetryField = (state.authSettings?.fields || []).find(
        (field) => field?.envName === 'HTML_MOTION_QUALITY_RETRY_COUNT'
      );
      if (htmlMotionRetryField) {
        state.htmlMotionOverlay.qualityRetryCount = normalizeHtmlMotionQualityRetryCount(htmlMotionRetryField.value);
      }
      const narrationReviewField = (state.authSettings?.fields || []).find(
        (field) => field?.envName === 'NARRATION_REVIEW_COUNT'
      );
      if (narrationReviewField) {
        state.narrationReview.reviewCount = normalizeNarrationReviewCount(narrationReviewField.value);
      }
      const htmlMotionBeatField = (state.authSettings?.fields || []).find(
        (field) => field?.envName === 'HTML_MOTION_BEAT_INTERVAL_SECONDS'
      );
      if (htmlMotionBeatField) {
        state.htmlMotionOverlay.beatIntervalSeconds = normalizeHtmlMotionBeatIntervalSeconds(htmlMotionBeatField.value);
      }
      state.settingsModal.authModelCatalogs = {
        ...(state.settingsModal.authModelCatalogs || {}),
        ...(state.authSettings?.modelCatalogs || {}),
      };
    }

    async function refreshVideoMergeMode() {
      const res = await fetch('/api/video-merge-mode');
      const data = await res.json().catch(() => ({}));
      state.settingsModal.videoMergeMode = normalizeVideoMergeMode(data?.mergeMode);
      return state.settingsModal.videoMergeMode;
    }

    function normalizeVideoMergeMode(value) {
      const text = String(value || '').trim();
      return ['none', 'merge2', 'merge4'].includes(text) ? text : 'none';
    }

    function videoMergeModeLabel(mode) {
      const normalized = normalizeVideoMergeMode(mode);
      if (normalized === 'merge4') return '合并 4 个';
      if (normalized === 'merge2') return '合并 2 个';
      return '不合并';
    }

    function normalizeHtmlMotionQualityRetryCount(value) {
      const number = Number.parseInt(String(value ?? ''), 10);
      if (!Number.isFinite(number)) return 5;
      return Math.min(10, Math.max(0, number));
    }

    function normalizeNarrationReviewCount(value) {
      const number = Number.parseInt(String(value ?? ''), 10);
      if (!Number.isFinite(number)) return 2;
      return Math.min(10, Math.max(0, number));
    }

    function normalizeHtmlMotionBeatIntervalSeconds(value) {
      const number = Number.parseFloat(String(value ?? ''));
      if (!Number.isFinite(number)) return 5;
      return Math.round(Math.min(30, Math.max(1, number)) * 10) / 10;
    }

    function showSettingsSavedBadge() {
      const badge = els.settingsSaveBadge;
      if (!badge) return;
      if (state.settingsModal.saveBadgeTimer) {
        clearTimeout(state.settingsModal.saveBadgeTimer);
      }
      badge.textContent = '已保存';
      badge.classList.add('show');
      state.settingsModal.saveBadgeTimer = setTimeout(() => {
        badge.classList.remove('show');
        state.settingsModal.saveBadgeTimer = null;
      }, 1600);
    }

    async function refreshVideoModelSettings() {
      const res = await fetch('/api/video-model-settings');
      const data = await res.json().catch(() => ({}));
      state.videoModelSettings = data?.settings || {};
      state.settingsModal.videoModelCatalog = Array.isArray(data?.modelCatalog) ? data.modelCatalog : state.settingsModal.videoModelCatalog;
    }

    async function refreshAssets() {
      const res = await fetch('/api/assets?limit=12');
      const data = await res.json();
      state.assets = data.items || [];
    }

    async function refreshUserGeneratedResults() {
      const res = await fetch('/api/user-generated-results?limit=200');
      const data = await res.json();
      state.userGeneratedResults = data.items || [];
      await refreshRecycleBin();
      if (scrubMissingUserGeneratedProgressFromSessions()) {
        persistSessions();
      }
    }

    async function refreshRecycleBin() {
      const res = await fetch('/api/user-recycle-bin?limit=100');
      const data = await res.json().catch(() => ({}));
      const items = Array.isArray(data?.items) ? data.items : [];
      const availableFolders = new Set(items.map((item) => String(item?.folder || '').trim()).filter(Boolean));
      state.recycleBin = {
        root: String(data?.root || ''),
        count: Number(data?.count || 0) || 0,
        items,
      };
      state.recycleBinModal.selectedFolders = (state.recycleBinModal.selectedFolders || [])
        .filter((folder) => availableFolders.has(folder));
      syncRecycleBinBatchDeleteButton();
    }

    async function refreshUserMaterials() {
      const res = await fetch('/api/user-materials');
      const data = await res.json();
      state.userMaterials = {
        ...(data || {}),
        images: Array.isArray(data?.images) ? data.images : [],
        scripts: Array.isArray(data?.scripts) ? data.scripts : [],
        flowerWatermarks: Array.isArray(data?.flowerWatermarks) ? data.flowerWatermarks : [],
        imageCount: Number(data?.imageCount || 0) || 0,
        scriptCount: Number(data?.scriptCount || 0) || 0,
        flowerWatermarkCount: Number(data?.flowerWatermarkCount || 0) || 0,
      };
    }

    async function refreshScriptKnowledge(options = {}) {
      const knowledge = state.scriptKnowledge;
      const requestSeq = Number(knowledge.requestSeq || 0) + 1;
      knowledge.requestSeq = requestSeq;
      knowledge.loading = true;
      knowledge.error = '';
      renderMaterialLibraryModal();
      const query = String(knowledge.query || '').trim();
      try {
        const params = new URLSearchParams({ limit: '100' });
        if (query) params.set('q', query);
        const res = await fetch(`/api/script-knowledge?${params.toString()}`);
        const data = await res.json().catch(() => ({}));
        if (requestSeq !== knowledge.requestSeq) return;
        knowledge.status = data?.status || null;
        knowledge.items = Array.isArray(data?.items) ? data.items : [];
        knowledge.error = !res.ok || data?.ok === false
          ? formatScriptKnowledgeError(data?.error || data?.status?.error || '剧本知识库不可用')
          : '';
        const previousId = Number(knowledge.selectedId || 0);
        knowledge.selectedId = resolveScriptKnowledgeSelection(options.preserveSelection);
        if (knowledge.selectedId !== previousId) knowledge.resetDetailScroll = true;
        if (knowledge.selectedId) {
          await loadScriptKnowledgeDocument(knowledge.selectedId, { renderAfter: false });
        } else {
          knowledge.detail = null;
        }
      } catch (error) {
        if (requestSeq === knowledge.requestSeq) knowledge.error = formatScriptKnowledgeError(error?.message || String(error));
      } finally {
        if (requestSeq === knowledge.requestSeq) {
          knowledge.loading = false;
          renderMaterialLibraryModal();
        }
      }
    }

    function resolveScriptKnowledgeSelection(preserveSelection) {
      const items = state.scriptKnowledge.items || [];
      const currentId = Number(state.scriptKnowledge.selectedId || 0);
      if (preserveSelection && items.some((item) => Number(item?.id || 0) === currentId)) {
        return currentId;
      }
      return Number(items[0]?.id || 0);
    }

    async function loadScriptKnowledgeDocument(documentId, options = {}) {
      const id = Number(documentId || 0);
      if (!id) return;
      if (Number(state.scriptKnowledge.selectedId || 0) !== id) {
        state.scriptKnowledge.resetDetailScroll = true;
      }
      state.scriptKnowledge.selectedId = id;
      if (options.renderAfter !== false) renderMaterialLibraryModal();
      try {
        const res = await fetch(`/api/script-knowledge/${id}`);
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        if (Number(state.scriptKnowledge.selectedId || 0) !== id) return;
        state.scriptKnowledge.detail = data?.document || null;
        state.scriptKnowledge.error = '';
      } catch (error) {
        state.scriptKnowledge.error = formatScriptKnowledgeError(error?.message || String(error));
      }
      if (options.renderAfter !== false) renderMaterialLibraryModal();
    }

    async function syncScriptKnowledge() {
      if (state.scriptKnowledge.syncing) return;
      state.scriptKnowledge.syncing = true;
      state.scriptKnowledge.error = '';
      renderMaterialLibraryModal();
      try {
        const res = await fetch('/api/script-knowledge/sync', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        await refreshUserMaterials();
        await refreshScriptKnowledge({ preserveSelection: true });
        renderUserMaterials();
      } catch (error) {
        state.scriptKnowledge.error = formatScriptKnowledgeError(error?.message || String(error));
      } finally {
        state.scriptKnowledge.syncing = false;
        renderMaterialLibraryModal();
      }
    }

    async function saveScriptKnowledgeMetadata() {
      const detail = state.scriptKnowledge.detail;
      if (!detail || state.scriptKnowledge.saving) return;
      const payload = readScriptKnowledgeMetadataForm(detail);
      state.scriptKnowledge.saving = true;
      renderMaterialLibraryModal();
      try {
        const res = await fetch(`/api/script-knowledge/${detail.id}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        state.scriptKnowledge.detail = data?.document || detail;
        await refreshScriptKnowledge({ preserveSelection: true });
      } catch (error) {
        state.scriptKnowledge.error = formatScriptKnowledgeError(error?.message || String(error));
      } finally {
        state.scriptKnowledge.saving = false;
        renderMaterialLibraryModal();
      }
    }

    function readScriptKnowledgeMetadataForm(detail) {
      const title = document.getElementById('scriptKnowledgeTitleInput')?.value || detail.title || '';
      const summary = document.getElementById('scriptKnowledgeSummaryInput')?.value || '';
      const rawTags = document.getElementById('scriptKnowledgeTagsInput')?.value || '';
      const tags = rawTags.split(/[，,]/).map((tag) => tag.trim()).filter(Boolean);
      return { title, summary, tags };
    }

    async function handleScriptKnowledgeModalClick(event) {
      if (state.materialModal.kind !== 'script') return false;
      const documentTrigger = event.target.closest('[data-script-knowledge-document]');
      const referenceTrigger = event.target.closest('[data-script-knowledge-reference]');
      const saveTrigger = event.target.closest('[data-script-knowledge-save]');
      if (!documentTrigger && !referenceTrigger && !saveTrigger) return false;
      event.preventDefault();
      event.stopPropagation();
      if (documentTrigger) {
        await loadScriptKnowledgeDocument(documentTrigger.getAttribute('data-script-knowledge-document'));
        return true;
      }
      if (referenceTrigger) {
        await selectScriptReference(referenceTrigger.getAttribute('data-script-knowledge-reference') || '');
        renderMaterialLibraryModal();
        return true;
      }
      await saveScriptKnowledgeMetadata();
      return true;
    }

    async function refreshBackgroundMusic() {
      const res = await fetch('/api/background-music');
      const data = await res.json().catch(() => ({}));
      state.backgroundMusic = {
        ...(state.backgroundMusic || {}),
        ...(data || {}),
        uploading: false,
        error: data?.error || '',
      };
    }

    async function refreshDefaultReferenceImage() {
      const res = await fetch('/api/default-reference-image');
      const data = await res.json().catch(() => ({}));
      state.defaultReferenceImage = {
        ...(state.defaultReferenceImage || {}),
        ...(data || {}),
        selecting: false,
        error: data?.error || '',
      };
    }

    async function refreshScriptReference() {
      const res = await fetch('/api/default-script-reference');
      const data = await res.json().catch(() => ({}));
      state.scriptReference = {
        ...(state.scriptReference || {}),
        ...(data || {}),
        selecting: false,
        error: data?.error || '',
      };
    }

    async function refreshFlowerText() {
      const res = await fetch('/api/video-text-overlay');
      const data = await res.json().catch(() => ({}));
      state.flowerText = {
        ...(state.flowerText || {}),
        enabled: !!data?.enabled,
        text: String(data?.text || ''),
        canvasWidth: normalizeFlowerTextSide(data?.canvasWidth, 9),
        canvasHeight: normalizeFlowerTextSide(data?.canvasHeight, 16),
        textColor: normalizeFlowerTextColor(data?.textColor, '#ffee43'),
        strokeColor: normalizeFlowerTextColor(data?.strokeColor, '#121826'),
        fontFamily: normalizeFlowerTextFamily(data?.fontFamily, data?.availableFonts),
        availableFonts: normalizeFlowerTextFonts(data?.availableFonts),
        fontSize: normalizeFlowerTextPercent(data?.fontSize, 16, 6, 28),
        fontWeight: normalizeFlowerTextWeight(data?.fontWeight, 800),
        strokeWidth: normalizeFlowerTextPercent(data?.strokeWidth, 8, 0, 18),
        position: normalizeFlowerTextPosition(data?.position),
        textX: normalizeFlowerTextCoordinate(data?.textX, 50),
        textY: normalizeFlowerTextCoordinate(data?.textY, flowerTextPositionY(data?.position)),
        animationDelaySeconds: normalizeFlowerTextAnimationDelay(data?.animationDelaySeconds),
        animationType: normalizeFlowerTextAnimationType(data?.animationType),
        watermarkEnabled: !!data?.watermarkEnabled,
        watermarkImage: normalizeFlowerTextWatermarkImage(data?.watermarkImage, state.userMaterials?.flowerWatermarks),
        watermarkSize: normalizeFlowerTextPercent(data?.watermarkSize, 18, 5, 200),
        watermarkOpacity: normalizeFlowerTextPercent(data?.watermarkOpacity, 100, 5, 100),
        watermarkAnimationDelaySeconds: normalizeFlowerTextAnimationDelay(data?.watermarkAnimationDelaySeconds),
        watermarkAnimationType: normalizeFlowerTextAnimationType(data?.watermarkAnimationType),
        watermarkPosition: normalizeFlowerTextWatermarkPosition(data?.watermarkPosition),
        watermarkX: normalizeFlowerTextCoordinate(data?.watermarkX, flowerTextWatermarkPositionX(data?.watermarkPosition)),
        watermarkY: normalizeFlowerTextCoordinate(data?.watermarkY, flowerTextWatermarkPositionY(data?.watermarkPosition)),
        watermark2Enabled: !!data?.watermark2Enabled,
        watermark2Image: normalizeFlowerTextWatermarkImage(data?.watermark2Image, state.userMaterials?.flowerWatermarks),
        watermark2Size: normalizeFlowerTextPercent(data?.watermark2Size, 18, 5, 200),
        watermark2Opacity: normalizeFlowerTextPercent(data?.watermark2Opacity, 100, 5, 100),
        watermark2AnimationDelaySeconds: normalizeFlowerTextAnimationDelay(data?.watermark2AnimationDelaySeconds),
        watermark2AnimationType: normalizeFlowerTextAnimationType(data?.watermark2AnimationType),
        watermark2Position: normalizeFlowerTextWatermarkPosition(data?.watermark2Position),
        watermark2X: normalizeFlowerTextCoordinate(data?.watermark2X, flowerTextWatermarkPositionX(data?.watermark2Position)),
        watermark2Y: normalizeFlowerTextCoordinate(data?.watermark2Y, flowerTextWatermarkPositionY(data?.watermark2Position)),
        previewBackgroundColor: normalizeFlowerTextColor(data?.previewBackgroundColor, '#303844'),
        previewBackgroundImage: normalizeUserMaterialImageRelativePath(data?.previewBackgroundImage),
        previewBackgroundImageUrl: String(data?.previewBackgroundImageUrl || ''),
        backgroundUploading: false,
        saving: false,
        error: data?.error || '',
        notice: '',
      };
    }
