    function videoPreviewExtensionMode(stageGrid = null) {
      const grid = stageGrid || els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      return grid?.dataset.extensionMode === 'replace' ? 'replace' : 'extend';
    }

    function videoPreviewExtensionActionLabel(mode, phase = 'ready') {
      if (mode === 'replace') {
        return phase === 'working' ? '替换中' : phase === 'pending' ? '待生成' : '替换';
      }
      return phase === 'working' ? '合并中' : phase === 'pending' ? '待生成' : '合并';
    }

    function setVideoPreviewHeaderStatus(message = '', tone = '') {
      const status = els.videoPreviewStatus;
      if (!status) return;
      const text = String(message || '').trim();
      status.textContent = text;
      status.hidden = !text;
      status.dataset.tone = text ? String(tone || 'info') : '';
    }

    function videoPreviewExtensionMergeMarkup(mode, savedState = null) {
      if (mode === 'replace') {
        return `<div class="video-preview-merge-control video-preview-replace-control">
          <button type="button" data-video-preview-merge disabled>待生成</button>
        </div>`;
      }
      return `<div class="video-preview-merge-control">
        <button type="button" data-video-preview-merge disabled>待生成</button>
        <button type="button" data-video-preview-merge-settings-toggle>设置</button>
      </div>
      <div class="video-preview-merge-settings">
        <div class="video-preview-merge-mode" role="radiogroup" aria-label="合并模式">
          <label><input type="radio" name="video-preview-merge-mode" value="direct" ${savedState?.mergeMode === 'continuation' ? '' : 'checked'}>直接合并</label>
          <label><input type="radio" name="video-preview-merge-mode" value="continuation" ${savedState?.mergeMode === 'continuation' ? 'checked' : ''}>延续合并</label>
        </div>
        <p class="video-preview-merge-tip" data-video-preview-merge-tip>${savedState?.mergeMode === 'continuation' ? '右视频续接到左视频截图处' : '直接拼接左右两个视频'}</p>
      </div>`;
    }

    async function generateVideoPreviewExtension(userGeneratedKey, button) {
      if (!button || button.disabled) return;
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      const mode = videoPreviewExtensionMode(stageGrid);
      button.disabled = true;
      button.dataset.generating = 'true';
      button.textContent = '生成中';
      updateVideoPreviewExtensionState(userGeneratedKey, {
        generating: true,
        generationStartedAt: new Date().toISOString(),
        generationError: '',
      });
      const parentSessionId = String(state.activeId || 'session').trim() || 'session';
      const generationNonce = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const generationSessionId = `extension-${parentSessionId}-${generationNonce}`;
      startGenerationProgress(generationSessionId, mode === 'replace' ? '重新生成视频' : '延长视频', { count: 1, kind: 'extension' });
      try {
        const videoPrompt = String(loadVideoPreviewExtensionStates()[userGeneratedKey]?.videoPrompt || '').trim();
        if (!videoPrompt) throw new Error('当前没有视频提示词，请先编辑并保存');
        const res = await fetch('/api/user-generated-results/extension-video/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            userGeneratedKey,
            sessionId: generationSessionId,
            videoPrompt,
            mode,
            frameKey: String(stageGrid?.dataset.extensionFrameKey || ''),
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || '生成视频失败');
        if (!hasActiveVideoPreviewExtensionState(userGeneratedKey)) {
          await discardDetachedVideoPreviewExtensionResult(userGeneratedKey, data.userGeneratedKey);
          return;
        }
        updateVideoPreviewExtensionState(userGeneratedKey, {
          generating: false,
          generationCompletedAt: new Date().toISOString(),
          generationError: '',
          mode,
          rightVideoKey: data.userGeneratedKey,
          rightVideoUrl: data.videoUrl,
        });
        setVideoPreviewExtensionVideo(data.videoUrl, data.userGeneratedKey);
        if (state.generationProgress?.kind === 'extension') {
          void refreshExtensionGenerationProgress(state.generationProgress);
        }
        await refreshUserGeneratedResults();
      } catch (error) {
        updateVideoPreviewExtensionState(userGeneratedKey, {
          generating: false,
          generationError: error?.message || '生成视频失败',
        });
        delete button.dataset.generating;
        button.disabled = false;
        button.textContent = '生成视频';
        void syncVideoPreviewExtensionGenerateButton(userGeneratedKey);
        if (state.generationProgress?.kind === 'extension') {
          void refreshExtensionGenerationProgress(state.generationProgress);
        }
        window.alert(error?.message || '生成视频失败');
      }
    }

    async function prepareVideoExtensionPreview(userGeneratedKey, button, savedState = null, options = {}) {
      const key = String(userGeneratedKey || '').trim();
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      const video = stageGrid?.querySelector('video');
      if (!key || !stageGrid || !video || button?.disabled) return;
      const mode = options.mode === 'replace' || savedState?.mode === 'replace' ? 'replace' : 'extend';
      button.disabled = true;
      setVideoPreviewHeaderStatus(mode === 'replace' ? '正在准备重新生成' : '正在准备延长视频');
      try {
        video.pause();
        if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
          throw new Error('当前视频画面尚未加载完成，请稍后再试');
        }
        if (mode === 'replace' && !savedState) {
          const sourcePrompt = await postVideoPrompt(key, undefined, 'original');
          updateVideoPreviewExtensionState(key, { videoPrompt: String(sourcePrompt.text || '').trim() });
        }
        const previewTime = Math.max(0, Number(savedState?.previewTime ?? video.currentTime));
        const sourceFrameTime = Math.max(
          0,
          Number(savedState?.frameTime ?? videoOutputTimeToSourceTime(previewTime)),
        );
        const frameAsset = savedState?.frameKey
          ? savedState
          : await saveVideoPreviewExtensionFrame(key, sourceFrameTime);
        stageGrid.querySelector('.video-preview-extension-stage')?.remove();
        stageGrid.querySelector('.video-preview-merge-control')?.remove();
        stageGrid.querySelector('.video-preview-merge-settings')?.remove();
        const deleteExtensionButton = stageGrid.querySelector('[data-video-preview-action="delete-extension"]');
        if (deleteExtensionButton) deleteExtensionButton.disabled = false;
        const extensionStage = document.createElement('div');
        extensionStage.className = 'video-preview-extension-stage';
        extensionStage.insertAdjacentHTML('afterbegin', `<img src="${escapeHtml(String(frameAsset.frameUrl || ''))}" alt="原视频当前时间点截图">`);
        extensionStage.insertAdjacentHTML('beforeend', `
          <button type="button" class="video-preview-extension-batch-toggle" data-extension-batch-toggle>批量</button>
          <div class="video-preview-extension-action-bar">
            <div class="video-preview-extension-generate-actions video-preview-split-button">
              <button type="button" class="video-preview-button" data-video-preview-action="edit-video-prompt">视频提示词</button>
              <button type="button" class="video-preview-button" data-video-preview-generate disabled>生成视频</button>
            </div>
          </div>
        `);
        if (savedState?.generating) {
          const generateButton = extensionStage.querySelector('[data-video-preview-generate]');
          generateButton.disabled = true;
          generateButton.dataset.generating = 'true';
          generateButton.textContent = '生成中';
          void reconcileVideoPreviewExtensionGeneration(key);
        } else {
          void syncVideoPreviewExtensionGenerateButton(key);
        }
        stageGrid.appendChild(extensionStage);
        stageGrid.insertAdjacentHTML('beforeend', videoPreviewExtensionMergeMarkup(mode, savedState));
        stageGrid.classList.add('extension-active');
        stageGrid.classList.toggle('regeneration-active', mode === 'replace');
        setVideoPreviewMainControlsDisabled(true);
        stageGrid.dataset.extensionMode = mode;
        stageGrid.dataset.extensionFrameTime = String(previewTime);
        stageGrid.dataset.extensionSourceFrameTime = String(sourceFrameTime);
        stageGrid.dataset.extensionFrameKey = String(frameAsset.frameKey || '');
        stageGrid.dataset.extensionFrameUrl = String(frameAsset.frameUrl || '');
        if (savedState?.batchMode && Array.isArray(savedState.batchFrames)) {
          applyVideoPreviewExtensionBatchStage(stageGrid, savedState.batchFrames, true);
          void resumeVideoPreviewExtensionBatchPolling(stageGrid);
        }
        state.videoPreviewModal = { ...(state.videoPreviewModal || {}), frameRepairPrompt: String(savedState?.frameRepairPrompt || '') };
        renderVideoPreviewFrameRepairActions();
        const defaultOutputName = String(els.videoPreviewTitle?.textContent || '延长合并视频').trim();
        const mergeTip = stageGrid.querySelector('[data-video-preview-merge-tip]');
        const mergeTips = {
          direct: '直接拼接左右两个视频',
          continuation: '右视频续接到左视频截图处',
        };
        const syncMergeTip = () => {
          const mode = String(stageGrid.querySelector('[name="video-preview-merge-mode"]:checked')?.value || 'direct');
          if (mergeTip) mergeTip.textContent = mergeTips[mode] || mergeTips.direct;
        };
        const saveState = () => persistVideoPreviewExtensionState(key, {
          ...(loadVideoPreviewExtensionStates()[key] || {}),
          active: true,
          mode,
          frameTime: sourceFrameTime,
          previewTime,
          outputName: defaultOutputName,
          mergeMode: String(stageGrid.querySelector('[name="video-preview-merge-mode"]:checked')?.value || 'direct'),
          frameKey: String(stageGrid.dataset.extensionFrameKey || '').trim(),
          frameUrl: String(stageGrid.dataset.extensionFrameUrl || '').trim(),
          generating: !!savedState?.generating,
          generationStartedAt: String(savedState?.generationStartedAt || ''),
          generationCompletedAt: String(savedState?.generationCompletedAt || ''),
          generationError: String(savedState?.generationError || ''),
          rightVideoKey: String(savedState?.rightVideoKey || '').trim(),
          rightVideoUrl: String(savedState?.rightVideoUrl || '').trim(),
          frameRepairPrompt: String(state.videoPreviewModal?.frameRepairPrompt || ''),
          batchMode: isVideoPreviewExtensionBatchMode(stageGrid),
          batchFrames: readVideoPreviewExtensionBatchFrames(stageGrid),
        });
        stageGrid.querySelectorAll('[name="video-preview-merge-mode"]').forEach((input) => {
          input.addEventListener('change', () => {
            syncMergeTip();
            saveState();
          });
        });
        syncMergeTip();
        saveState();
        syncVideoPreviewMergeAvailability();
        setVideoPreviewButtonLabel(button, mode === 'replace' ? '重新生成' : '延长视频');
        setVideoPreviewHeaderStatus('');
        button.disabled = false;
      } catch (error) {
        setVideoPreviewButtonLabel(button, mode === 'replace' ? '重新生成' : '延长视频');
        setVideoPreviewHeaderStatus(error?.message || '操作准备失败', 'error');
        button.disabled = false;
        if (savedState) console.warn('恢复视频延长状态失败', error);
        else window.alert(error?.message || '截取视频画面失败');
      }
    }

    function syncVideoPreviewMergeAvailability() {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      const mergeButton = stageGrid?.querySelector('[data-video-preview-merge]');
      const rightStage = stageGrid?.querySelector('.video-preview-extension-stage');
      if (!mergeButton) return;
      const hasLeftVideo = !!stageGrid.querySelector('.video-preview-stage video');
      const hasRightVideo = !!rightStage?.querySelector('video');
      const hasVideoKeys = !!stageGrid.dataset.leftVideoKey && !!rightStage?.dataset.videoKey;
      mergeButton.disabled = !(hasLeftVideo && hasRightVideo && hasVideoKeys);
      if (mergeButton.dataset.merging !== 'true') {
        mergeButton.textContent = videoPreviewExtensionActionLabel(
          videoPreviewExtensionMode(stageGrid),
          mergeButton.disabled ? 'pending' : 'ready',
        );
      }
    }

    function setVideoPreviewExtensionVideo(videoUrl, userGeneratedKey) {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      const rightStage = stageGrid?.querySelector('.video-preview-extension-stage');
      if (!stageGrid || !rightStage || !videoUrl || !userGeneratedKey) return;
      rightStage.dataset.videoKey = String(userGeneratedKey);
      rightStage.innerHTML = `<video controls playsinline preload="metadata" src="${escapeHtml(videoUrl)}"></video>`;
      const mode = videoPreviewExtensionMode(stageGrid);
      const action = mode === 'replace' ? 'regenerate-video' : 'extend-video';
      const activeButton = stageGrid.querySelector(`[data-video-preview-action="${action}"]`);
      if (activeButton) {
        activeButton.disabled = true;
        setVideoPreviewButtonLabel(activeButton, mode === 'replace' ? '重新生成' : '延长视频');
      }
      setVideoPreviewHeaderStatus(mode === 'replace' ? '已生成替换视频' : '已生成延长视频', 'success');
      const leftKey = String(stageGrid.dataset.leftVideoKey || '').trim();
      const existing = loadVideoPreviewExtensionStates()[leftKey] || {};
      persistVideoPreviewExtensionState(leftKey, {
        ...existing,
        active: true,
        mode,
        rightVideoKey: String(userGeneratedKey),
        rightVideoUrl: String(videoUrl),
      });
      syncVideoPreviewMergeAvailability();
    }

    async function mergeExtendedPreviewVideos(leftKey, button) {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      const rightStage = stageGrid?.querySelector('.video-preview-extension-stage');
      const rightKey = String(rightStage?.dataset.videoKey || '').trim();
      const normalizedLeftKey = String(leftKey || stageGrid?.dataset.leftVideoKey || '').trim();
      if (!normalizedLeftKey || !rightKey || button?.disabled) return;
      const outputName = String(els.videoPreviewTitle?.textContent || '延长合并视频').trim();
      const operationMode = videoPreviewExtensionMode(stageGrid);
      const mergeMode = String(stageGrid.querySelector('[name="video-preview-merge-mode"]:checked')?.value || 'direct');
      const splitTime = Number(stageGrid.dataset.extensionFrameTime || 0);
      button.disabled = true;
      button.dataset.merging = 'true';
      button.textContent = videoPreviewExtensionActionLabel(operationMode, 'working');
      try {
        const endpoint = operationMode === 'replace'
          ? '/api/user-generated-results/replace'
          : '/api/user-generated-results/merge';
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ leftKey: normalizedLeftKey, rightKey, outputName, mergeMode, splitTime }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || (operationMode === 'replace' ? '替换视频失败' : '合并视频失败'));
        persistVideoPreviewExtensionState(normalizedLeftKey, null);
        await refreshUserGeneratedResults();
        const resultUrl = operationMode === 'replace'
          ? `${data.videoUrl}${String(data.videoUrl || '').includes('?') ? '&' : '?'}v=${Date.now()}`
          : data.videoUrl;
        openVideoPreviewModal({
          src: resultUrl,
          title: outputName || (operationMode === 'replace' ? '重新生成视频' : '延长合并视频'),
          userGeneratedKey: data.userGeneratedKey,
        });
      } catch (error) {
        delete button.dataset.merging;
        syncVideoPreviewMergeAvailability();
        window.alert(error?.message || (operationMode === 'replace' ? '替换视频失败' : '合并视频失败'));
      }
    }

    function openVideoPreviewModal(options) {
      const src = options?.src || '';
      if (!src || !els.videoPreviewModal) return;
      // Detach any previous modal poll UI; backend jobs keep running.
      invalidateHtmlMotionPreviewRequest();
      const title = options?.title || '全屏播放';
      const cover = options?.cover || '';
      const userGeneratedKey = String(options?.userGeneratedKey || deriveUserGeneratedKeyFromMediaUrl(src)).trim();
      const artifactKind = String(options?.artifactKind || 'editable').trim();
      const userGeneratedPreviewKey = String(options?.userGeneratedPreviewKey || deriveLocalPreviewKey(userGeneratedKey)).trim();
      const userGeneratedCoverKey = String(options?.userGeneratedCoverKey || deriveLocalCoverKey(userGeneratedKey)).trim();
      const playlist = Array.isArray(options?.playlist) && options.playlist.length
        ? options.playlist.filter((item) => item?.src)
        : [{ src, title, cover, userGeneratedKey, userGeneratedPreviewKey, userGeneratedCoverKey }];
      const playlistIndex = Math.max(0, Math.min(playlist.length - 1, Number(options?.playlistIndex || 0)));
      const hasPlaylistNav = playlist.length > 1;
      els.videoPreviewTitle.textContent = title;
      els.videoPreviewSub.textContent = hasPlaylistNav ? `当前页面播放 · ${playlistIndex + 1}/${playlist.length}` : '当前页面播放';
      setVideoPreviewHeaderStatus('');
      els.videoPreviewBody.innerHTML = `
        <button type="button" class="video-preview-nav-button prev" data-video-preview-action="previous" ${hasPlaylistNav ? '' : 'disabled'}>上一个</button>
        <div class="video-preview-stage-grid" data-left-video-key="${escapeHtml(userGeneratedKey)}">
          <div class="video-preview-stage">
            <video class="video-preview-large" controls autoplay playsinline preload="metadata" ${cover ? `poster="${escapeHtml(cover)}"` : ''} src="${escapeHtml(src)}"></video>
            <span class="video-preview-extend-actions">
              <button type="button" class="video-preview-button video-preview-regenerate-button" data-video-preview-action="regenerate-video" data-icon="regenerate" data-video-user-generated-key="${escapeHtml(userGeneratedKey)}" ${userGeneratedKey ? '' : 'disabled'}>${videoPreviewButtonInnerHtml('regenerate', '重新生成')}</button>
              <button type="button" class="video-preview-button video-preview-extend-button" data-video-preview-action="extend-video" data-icon="extend" data-video-user-generated-key="${escapeHtml(userGeneratedKey)}" ${userGeneratedKey ? '' : 'disabled'}>${videoPreviewButtonInnerHtml('extend', '延长视频')}</button>
              <button type="button" class="video-preview-button video-preview-extension-close-button" data-video-preview-action="delete-extension" data-icon="trash" aria-label="删除右侧延长内容">${videoPreviewButtonInnerHtml('trash', '')}</button>
            </span>
          </div>
        </div>
        <button type="button" class="video-preview-nav-button next" data-video-preview-action="next" ${hasPlaylistNav ? '' : 'disabled'}>下一个</button>
        <div class="video-preview-controls">
          ${videoPreviewEditingControlsMarkup(userGeneratedKey)}
        </div>
      `;
      const video = els.videoPreviewBody.querySelector('video');
      const previousButton = els.videoPreviewBody.querySelector('[data-video-preview-action="previous"]');
      const nextButton = els.videoPreviewBody.querySelector('[data-video-preview-action="next"]');
      const deleteButton = els.videoPreviewBody.querySelector('[data-video-preview-action="delete-video"]');
      const editVideoTimelineButton = els.videoPreviewBody.querySelector('[data-video-preview-action="edit-video-timeline"]');
      const regenerateTtsButton = els.videoPreviewBody.querySelector('[data-video-preview-action="regenerate-tts"]');
      const editTtsTextButton = els.videoPreviewBody.querySelector('[data-video-preview-action="edit-tts-text"]');
      const regenerateVideoButton = els.videoPreviewBody.querySelector('[data-video-preview-action="regenerate-video"]');
      const extendVideoButton = els.videoPreviewBody.querySelector('[data-video-preview-action="extend-video"]');
      const deleteExtensionButton = els.videoPreviewBody.querySelector('[data-video-preview-action="delete-extension"]');
      const regenerateHtmlMotionButton = els.videoPreviewBody.querySelector('[data-video-preview-action="regenerate-html-motion"]');
      const confirmBurnButton = els.videoPreviewBody.querySelector('[data-video-preview-action="confirm-burn"]');
      previousButton?.addEventListener('click', () => navigateVideoPreview(-1));
      nextButton?.addEventListener('click', () => navigateVideoPreview(1));
      deleteButton?.addEventListener('click', () => {
        deleteUserGeneratedVideoFromPreview(userGeneratedKey, deleteButton, artifactKind);
      });
      editVideoTimelineButton?.addEventListener('click', () => {
        void toggleAllTimelineEditors(userGeneratedKey, editVideoTimelineButton);
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="toggle-video-scissors"]')?.addEventListener('click', () => {
        toggleVideoScissorMode();
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="delete-selected-video-chunk"]')?.addEventListener('click', () => {
        deleteSelectedVideoChunk(userGeneratedKey);
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="reset-video-timeline"]')?.addEventListener('click', () => {
        void resetVideoTimeline(userGeneratedKey);
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="toggle-background-music"]')?.addEventListener('click', () => {
        void toggleVideoPreviewBackgroundMusicDrawer();
      });
      regenerateTtsButton?.addEventListener('click', () => {
        regenerateTtsFromVideoPreview(userGeneratedKey, regenerateTtsButton);
      });
      const ttsScissorsButton = els.videoPreviewBody.querySelector('[data-video-preview-action="toggle-tts-scissors"]');
      ttsScissorsButton?.addEventListener('click', () => {
        toggleTtsScissorMode();
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="smart-split-tts"]')?.addEventListener('click', () => {
        smartSplitTtsTimeline(userGeneratedKey);
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="delete-selected-tts-chunk"]')?.addEventListener('click', () => {
        deleteSelectedTtsChunk(userGeneratedKey);
      });
      const exportTtsMp3Button = els.videoPreviewBody.querySelector('[data-video-preview-action="export-tts-mp3"]');
      exportTtsMp3Button?.addEventListener('click', () => {
        void exportTtsMp3FromVideoPreview(userGeneratedKey, exportTtsMp3Button);
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="reset-tts-timeline"]')?.addEventListener('click', () => {
        resetTtsTimeline(userGeneratedKey);
      });
      editTtsTextButton?.addEventListener('click', () => {
        const popover = els.videoPreviewBody.querySelector('[data-video-preview-tts-editor]');
        if (popover?.classList.contains('is-open')) {
          closeVideoPreviewTtsEditor();
          return;
        }
        openTtsNarrationEditorFromVideoPreview(userGeneratedKey, editTtsTextButton);
      });
      regenerateVideoButton?.addEventListener('click', () => {
        prepareVideoExtensionPreview(userGeneratedKey, regenerateVideoButton, null, { mode: 'replace' });
      });
      extendVideoButton?.addEventListener('click', () => {
        prepareVideoExtensionPreview(userGeneratedKey, extendVideoButton, null, { mode: 'extend' });
      });
      deleteExtensionButton?.addEventListener('click', () => {
        deleteVideoPreviewExtensionState(userGeneratedKey, deleteExtensionButton);
      });
      els.videoPreviewBody.querySelector('.video-preview-stage-grid')?.addEventListener('click', (event) => {
        if (event.target?.closest?.('[data-video-preview-merge-settings-toggle]')) {
          const settings = els.videoPreviewBody.querySelector('.video-preview-merge-settings');
          const toggle = event.target.closest('[data-video-preview-merge-settings-toggle]');
          if (!settings || !toggle) return;
          const open = !settings.classList.contains('is-open');
          settings.classList.toggle('is-open', open);
          toggle.classList.toggle('is-open', open);
          return;
        }
        const mergeButton = event.target?.closest?.('[data-video-preview-merge]');
        if (mergeButton) mergeExtendedPreviewVideos(userGeneratedKey, mergeButton);
        const promptButton = event.target?.closest?.('[data-video-preview-action="edit-video-prompt"]');
        if (promptButton) openVideoPromptEditor(userGeneratedKey);
        const generateButton = event.target?.closest?.('[data-video-preview-generate]');
        if (generateButton) {
          if (isVideoPreviewExtensionBatchMode()) generateVideoPreviewExtensionBatch(userGeneratedKey, generateButton);
          else generateVideoPreviewExtension(userGeneratedKey, generateButton);
        }
      });
      regenerateHtmlMotionButton?.addEventListener('click', () => {
        const taskId = String(state.videoPreviewModal?.htmlMotionTaskId || '').trim();
        const submitting = !!state.videoPreviewModal?.htmlMotionSubmitting;
        if (taskId || submitting) {
          void cancelHtmlMotionFromVideoPreview(regenerateHtmlMotionButton);
          return;
        }
        void regenerateHtmlMotionFromVideoPreview(userGeneratedKey, regenerateHtmlMotionButton, confirmBurnButton);
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="toggle-html-motion-scissors"]')?.addEventListener('click', () => {
        toggleHtmlMotionScissorMode();
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="delete-selected-html-motion-chunk"]')?.addEventListener('click', () => {
        deleteSelectedHtmlMotionChunk();
      });
      els.videoPreviewBody.querySelector('[data-video-preview-action="reset-html-motion-timeline"]')?.addEventListener('click', () => {
        resetHtmlMotionTimeline();
      });
      els.videoPreviewBody.querySelectorAll('[data-video-preview-html-motion-toggle]').forEach((button) => {
        button.addEventListener('click', () => toggleHtmlMotionPreviewDrawer());
      });
      confirmBurnButton?.addEventListener('click', () => {
        confirmBurnFromVideoPreview(userGeneratedKey, confirmBurnButton);
      });
      video?.addEventListener('loadedmetadata', () => {
        if (video.videoWidth && video.videoHeight) {
          const ratioValue = `${video.videoWidth} / ${video.videoHeight}`;
          const stage = els.videoPreviewBody.querySelector('.video-preview-stage');
          stage?.style.setProperty('--preview-stage-aspect', ratioValue);
          video.style.setProperty('--preview-video-aspect', ratioValue);
        }
      }, { once: true });
      bindSmoothTimelinePlayheadSync(video);
      bindVideoPreviewBackgroundMusic(video);
      if (video) video.dataset.officialSrc = src;
      state.videoPreviewModal = {
        ...(state.videoPreviewModal || {}),
        visible: true,
        playlist,
        index: playlistIndex,
        htmlMotionTaskId: '',
        htmlMotionPollTimer: null,
        htmlMotionTaskSnapshot: null,
        htmlMotionDetailsOpen: false,
        htmlMotionSubmitting: false,
        htmlMotionCancelRequested: false,
        htmlMotionTimelineChunks: [],
        htmlMotionTimelineDuration: 0,
        htmlMotionOriginalTimelineChunks: [],
        htmlMotionTimelineReviewIdentity: '',
        htmlMotionTimelineDirty: false,
        htmlMotionScissorMode: false,
        htmlMotionSelectedChunkIndex: null,
        htmlMotionSelectedChunkIndexes: [],
        htmlMotionChunkIdSequence: 0,
        videoTimelineChunks: [],
        videoTimelineSourceDuration: 0,
        videoTimelineOutputDuration: 0,
        videoTimelineFilmstripUrl: '',
        videoTimelineFilmstripFrameCount: 0,
        videoTimelineScissorMode: false,
        videoTimelineSelectedChunkIndex: null,
        videoTimelineBusy: false,
        ttsTimelineChunks: [],
        ttsTimelineDuration: 0,
        ttsAudioDuration: 0,
        ttsWaveformPeaks: [],
        ttsScissorMode: false,
        ttsSelectedChunkIndex: null,
        ttsTimelineBusy: false,
        timelineInteractionCount: 0,
        backgroundMusicDrawerOpen: false,
        burnReview: null,
      };
      initializeTimelineHistory(userGeneratedKey);
      els.videoPreviewModal.classList.remove('hidden');
      renderHtmlMotionPreviewDrawer();
      requestAnimationFrame(() => syncHtmlMotionDrawerWidth());
      restoreVideoPreviewExtensionState(video, userGeneratedKey, extendVideoButton, regenerateVideoButton);
      // Resume in-flight backend job if any; otherwise sync finished preview.
      void resumeHtmlMotionFromVideoPreview(
        userGeneratedKey,
        regenerateHtmlMotionButton,
        confirmBurnButton,
        video,
      );
      void syncBurnReviewFromVideoPreview(userGeneratedKey, video, { silent: true });
    }

    function closeVideoPreviewModal() {
      if (!els.videoPreviewModal) return;
      const key = currentVideoPreviewUserGeneratedKey();
      const taskId = String(state.videoPreviewModal?.htmlMotionTaskId || '').trim();
      if (key && taskId) {
        rememberHtmlMotionJob(
          key,
          taskId,
          `/api/user-generated-results/html-motion-tasks/${encodeURIComponent(taskId)}`,
        );
      }
      // Only detach UI polling — never cancel the backend worker.
      invalidateHtmlMotionPreviewRequest();
      const video = els.videoPreviewBody.querySelector('video');
      video?.pause();
      stopVideoPreviewBackgroundMusic();
      state.videoPreviewModal = {
        ...(state.videoPreviewModal || {}),
        visible: false,
        playlist: [],
        index: 0,
        htmlMotionTaskId: '',
        htmlMotionPollTimer: null,
        htmlMotionTaskSnapshot: null,
        htmlMotionDetailsOpen: false,
      };
      els.videoPreviewModal.classList.add('hidden');
      els.videoPreviewBody.innerHTML = '';
    }
