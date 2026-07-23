    async function generateVideoPreviewExtension(userGeneratedKey, button) {
      if (!button || button.disabled) return;
      button.disabled = true;
      button.dataset.generating = 'true';
      button.textContent = '生成中';
      updateVideoPreviewExtensionState(userGeneratedKey, {
        generating: true,
        generationStartedAt: new Date().toISOString(),
        generationError: '',
      });
      const generationSessionId = String(state.activeId || '').trim();
      if (generationSessionId) {
        startGenerationProgress(generationSessionId, '延长视频', { count: 1, kind: 'extension' });
      }
      try {
        const prompt = await postVideoPrompt(userGeneratedKey);
        if (!String(prompt.text || '').trim()) throw new Error('当前没有视频提示词，请先编辑并保存');
        const res = await fetch('/api/user-generated-results/extension-video/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey, sessionId: generationSessionId }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || '生成延长视频失败');
        updateVideoPreviewExtensionState(userGeneratedKey, {
          generating: false,
          generationCompletedAt: new Date().toISOString(),
          generationError: '',
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
          generationError: error?.message || '生成延长视频失败',
        });
        delete button.dataset.generating;
        button.disabled = false;
        button.textContent = '生成视频';
        void syncVideoPreviewExtensionGenerateButton(userGeneratedKey);
        if (state.generationProgress?.kind === 'extension') {
          void refreshExtensionGenerationProgress(state.generationProgress);
        }
        window.alert(error?.message || '生成延长视频失败');
      }
    }

    async function prepareVideoExtensionPreview(userGeneratedKey, button, savedState = null) {
      const key = String(userGeneratedKey || '').trim();
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      const video = stageGrid?.querySelector('video');
      if (!key || !stageGrid || !video || button?.disabled) return;
      button.disabled = true;
      setVideoPreviewButtonLabel(button, '截取画面中');
      try {
        video.pause();
        if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
          throw new Error('当前视频画面尚未加载完成，请稍后再试');
        }
        const frameAsset = savedState?.frameKey
          ? savedState
          : await saveVideoPreviewExtensionFrame(key, video.currentTime);
        stageGrid.querySelector('.video-preview-extension-stage')?.remove();
        stageGrid.querySelector('.video-preview-merge-control')?.remove();
        stageGrid.querySelector('.video-preview-merge-settings')?.remove();
        const extensionStage = document.createElement('div');
        extensionStage.className = 'video-preview-extension-stage';
        const framePreview = video.cloneNode(true);
        const restoredFrame = Boolean(savedState?.frameKey && savedState?.frameUrl);
        framePreview.controls = false;
        framePreview.autoplay = false;
        framePreview.muted = true;
        framePreview.dataset.framePreview = 'true';
        extensionStage.appendChild(framePreview);
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
        const syncFrameTime = () => {
          framePreview.currentTime = video.currentTime;
          framePreview.pause();
        };
        if (restoredFrame) {
          framePreview.replaceWith(Object.assign(document.createElement('img'), {
            src: String(frameAsset.frameUrl), alt: '已确认截图',
          }));
        } else if (framePreview.readyState >= 1) syncFrameTime();
        else framePreview.addEventListener('loadedmetadata', syncFrameTime, { once: true });
        stageGrid.insertAdjacentHTML('beforeend', `
          <div class="video-preview-merge-control">
            <button type="button" data-video-preview-merge disabled>待生成</button>
            <button type="button" data-video-preview-merge-settings-toggle>设置</button>
          </div>
          <div class="video-preview-merge-settings hidden">
            <div class="video-preview-merge-mode" role="radiogroup" aria-label="合并模式">
              <label><input type="radio" name="video-preview-merge-mode" value="direct" ${savedState?.mergeMode === 'continuation' ? '' : 'checked'}>直接合并</label>
              <label><input type="radio" name="video-preview-merge-mode" value="continuation" ${savedState?.mergeMode === 'continuation' ? 'checked' : ''}>延续合并</label>
            </div>
            <label>合并视频名称<input data-video-preview-merge-name value="${escapeHtml(String(savedState?.outputName || els.videoPreviewTitle?.textContent || '延长合并视频'))}"></label>
          </div>
        `);
        stageGrid.classList.add('extension-active');
        setVideoPreviewMainControlsDisabled(true);
        stageGrid.dataset.extensionFrameTime = String(video.currentTime);
        stageGrid.dataset.extensionFrameKey = String(frameAsset.frameKey || '');
        stageGrid.dataset.extensionFrameUrl = String(frameAsset.frameUrl || '');
        if (savedState?.batchMode && Array.isArray(savedState.batchFrames)) {
          applyVideoPreviewExtensionBatchStage(stageGrid, savedState.batchFrames, true);
          void hydrateVideoPreviewExtensionBatchVideos(stageGrid);
        }
        state.videoPreviewModal = { ...(state.videoPreviewModal || {}), frameRepairPrompt: String(savedState?.frameRepairPrompt || '') };
        renderVideoPreviewFrameRepairActions();
        const nameInput = stageGrid.querySelector('[data-video-preview-merge-name]');
        const saveState = () => persistVideoPreviewExtensionState(key, {
          active: true,
          frameTime: video.currentTime,
          outputName: String(nameInput?.value || '').trim(),
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
        nameInput?.addEventListener('input', saveState);
        stageGrid.querySelectorAll('[name="video-preview-merge-mode"]').forEach((input) => {
          input.addEventListener('change', saveState);
        });
        saveState();
        syncVideoPreviewMergeAvailability();
        setVideoPreviewButtonLabel(button, '重新截取');
        button.disabled = false;
      } catch (error) {
        setVideoPreviewButtonLabel(button, '延长');
        button.disabled = false;
        if (savedState) console.warn('恢复视频延长状态失败', error);
        else window.alert(error?.message || '截取当前视频画面失败');
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
        mergeButton.textContent = mergeButton.disabled ? '待生成' : '合并';
      }
    }

    function setVideoPreviewExtensionVideo(videoUrl, userGeneratedKey) {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      const rightStage = stageGrid?.querySelector('.video-preview-extension-stage');
      if (!stageGrid || !rightStage || !videoUrl || !userGeneratedKey) return;
      rightStage.dataset.videoKey = String(userGeneratedKey);
      rightStage.innerHTML = `<video controls playsinline preload="metadata" src="${escapeHtml(videoUrl)}"></video>`;
      const extendButton = stageGrid.querySelector('[data-video-preview-action="extend-video"]');
      if (extendButton) {
        extendButton.disabled = true;
        setVideoPreviewButtonLabel(extendButton, '已生成延长视频');
      }
      const leftKey = String(stageGrid.dataset.leftVideoKey || '').trim();
      const existing = loadVideoPreviewExtensionStates()[leftKey] || {};
      persistVideoPreviewExtensionState(leftKey, {
        ...existing,
        active: true,
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
      const outputName = String(stageGrid.querySelector('[data-video-preview-merge-name]')?.value || '延长合并视频').trim();
      const mergeMode = String(stageGrid.querySelector('[name="video-preview-merge-mode"]:checked')?.value || 'direct');
      const splitTime = Number(stageGrid.dataset.extensionFrameTime || 0);
      button.disabled = true;
      button.dataset.merging = 'true';
      button.textContent = '合并中';
      try {
        const res = await fetch('/api/user-generated-results/merge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ leftKey: normalizedLeftKey, rightKey, outputName, mergeMode, splitTime }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || '合并视频失败');
        persistVideoPreviewExtensionState(normalizedLeftKey, null);
        await refreshUserGeneratedResults();
        openVideoPreviewModal({
          src: data.videoUrl,
          title: outputName || '延长合并视频',
          userGeneratedKey: data.userGeneratedKey,
        });
      } catch (error) {
        delete button.dataset.merging;
        syncVideoPreviewMergeAvailability();
        window.alert(error?.message || '合并视频失败');
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
      const userGeneratedPreviewKey = String(options?.userGeneratedPreviewKey || deriveLocalPreviewKey(userGeneratedKey)).trim();
      const userGeneratedCoverKey = String(options?.userGeneratedCoverKey || deriveLocalCoverKey(userGeneratedKey)).trim();
      const playlist = Array.isArray(options?.playlist) && options.playlist.length
        ? options.playlist.filter((item) => item?.src)
        : [{ src, title, cover, userGeneratedKey, userGeneratedPreviewKey, userGeneratedCoverKey }];
      const playlistIndex = Math.max(0, Math.min(playlist.length - 1, Number(options?.playlistIndex || 0)));
      const hasPlaylistNav = playlist.length > 1;
      els.videoPreviewTitle.textContent = title;
      els.videoPreviewSub.textContent = hasPlaylistNav ? `当前页面播放 · ${playlistIndex + 1}/${playlist.length}` : '当前页面播放';
      els.videoPreviewBody.innerHTML = `
        <button type="button" class="video-preview-nav-button prev" data-video-preview-action="previous" ${hasPlaylistNav ? '' : 'disabled'}>上一个</button>
        <div class="video-preview-stage-grid" data-left-video-key="${escapeHtml(userGeneratedKey)}">
          <div class="video-preview-stage">
            <video class="video-preview-large" controls autoplay playsinline preload="metadata" ${cover ? `poster="${escapeHtml(cover)}"` : ''} src="${escapeHtml(src)}"></video>
            <span class="video-preview-extend-actions">
              <button type="button" class="video-preview-button video-preview-extend-button" data-video-preview-action="extend-video" data-icon="sparkles" data-video-user-generated-key="${escapeHtml(userGeneratedKey)}" ${userGeneratedKey ? '' : 'disabled'}>${videoPreviewButtonInnerHtml('sparkles', '延长')}</button>
              <button type="button" class="video-preview-button video-preview-extension-close-button" data-video-preview-action="delete-extension" aria-label="删除延长内容">×</button>
            </span>
          </div>
        </div>
        <button type="button" class="video-preview-nav-button next" data-video-preview-action="next" ${hasPlaylistNav ? '' : 'disabled'}>下一个</button>
        <div class="video-preview-controls">
          <div class="video-preview-control-group">
            <span class="video-preview-html-motion-status" data-video-preview-html-motion-status aria-live="polite"></span>
            <span class="video-preview-split-button" role="group" aria-label="播放控制">
              <button type="button" class="video-preview-button" data-video-preview-action="toggle-play" data-icon="pause">${videoPreviewButtonInnerHtml('pause', '暂停')}</button>
              <button type="button" class="video-preview-button" data-video-preview-action="restart" data-icon="replay">${videoPreviewButtonInnerHtml('replay', '重播')}</button>
              <button type="button" class="video-preview-button" data-video-preview-action="toggle-mute" data-icon="volume">${videoPreviewButtonInnerHtml('volume', '静音')}</button>
            </span>
            <span class="video-preview-split-button" role="group" aria-label="TTS 配音">
              <button
                type="button"
                class="video-preview-button"
                data-video-preview-action="regenerate-tts"
                data-icon="mic"
                data-video-user-generated-key="${escapeHtml(userGeneratedKey)}"
                ${userGeneratedKey ? '' : 'disabled'}
              >${videoPreviewButtonInnerHtml('mic', '重新生成TTS配音')}</button>
              <button
                type="button"
                class="video-preview-button"
                data-video-preview-action="edit-tts-text"
                data-icon="edit"
                data-video-user-generated-key="${escapeHtml(userGeneratedKey)}"
                ${userGeneratedKey ? '' : 'disabled'}
              >${videoPreviewButtonInnerHtml('edit', '修改台词')}</button>
            </span>
            <span class="video-preview-split-button" role="group" aria-label="HTML 动效预览与烧录">
              <button
                type="button"
                class="video-preview-button"
                data-video-preview-action="regenerate-html-motion"
                data-icon="sparkles"
                data-video-user-generated-key="${escapeHtml(userGeneratedKey)}"
                ${userGeneratedKey ? '' : 'disabled'}
              >${videoPreviewButtonInnerHtml('sparkles', '重新生成 HTML 动效')}</button>
              <button
                type="button"
                class="video-preview-button"
                data-video-preview-action="confirm-html-motion"
                data-icon="check"
                data-video-user-generated-key="${escapeHtml(userGeneratedKey)}"
                disabled
              >${videoPreviewButtonInnerHtml('check', '确认烧录')}</button>
            </span>
          </div>
          <div class="video-preview-side-actions">
            <div class="video-preview-time" data-video-preview-time>00:00 / 00:00</div>
            ${userGeneratedKey ? `
              <button
                type="button"
                class="video-preview-button danger"
                data-video-preview-action="delete-video"
                data-icon="trash"
                data-video-user-generated-key="${escapeHtml(userGeneratedKey)}"
              >${videoPreviewButtonInnerHtml('trash', '删除视频')}</button>
            ` : ''}
          </div>
        </div>
      `;
      const video = els.videoPreviewBody.querySelector('video');
      const previousButton = els.videoPreviewBody.querySelector('[data-video-preview-action="previous"]');
      const nextButton = els.videoPreviewBody.querySelector('[data-video-preview-action="next"]');
      const deleteButton = els.videoPreviewBody.querySelector('[data-video-preview-action="delete-video"]');
      const regenerateTtsButton = els.videoPreviewBody.querySelector('[data-video-preview-action="regenerate-tts"]');
      const editTtsTextButton = els.videoPreviewBody.querySelector('[data-video-preview-action="edit-tts-text"]');
      const extendVideoButton = els.videoPreviewBody.querySelector('[data-video-preview-action="extend-video"]');
      const deleteExtensionButton = els.videoPreviewBody.querySelector('[data-video-preview-action="delete-extension"]');
      const regenerateHtmlMotionButton = els.videoPreviewBody.querySelector('[data-video-preview-action="regenerate-html-motion"]');
      const confirmHtmlMotionButton = els.videoPreviewBody.querySelector('[data-video-preview-action="confirm-html-motion"]');
      previousButton?.addEventListener('click', () => navigateVideoPreview(-1));
      nextButton?.addEventListener('click', () => navigateVideoPreview(1));
      deleteButton?.addEventListener('click', () => {
        deleteUserGeneratedVideoFromPreview(userGeneratedKey, deleteButton);
      });
      regenerateTtsButton?.addEventListener('click', () => {
        regenerateTtsFromVideoPreview(userGeneratedKey, regenerateTtsButton);
      });
      editTtsTextButton?.addEventListener('click', () => {
        openTtsNarrationEditorFromVideoPreview(userGeneratedKey);
      });
      extendVideoButton?.addEventListener('click', () => {
        prepareVideoExtensionPreview(userGeneratedKey, extendVideoButton);
      });
      deleteExtensionButton?.addEventListener('click', () => {
        deleteVideoPreviewExtensionState(userGeneratedKey, deleteExtensionButton);
      });
      els.videoPreviewBody.querySelector('.video-preview-stage-grid')?.addEventListener('click', (event) => {
        if (event.target?.closest?.('[data-video-preview-merge-settings-toggle]')) {
          els.videoPreviewBody.querySelector('.video-preview-merge-settings')?.classList.toggle('hidden');
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
        void regenerateHtmlMotionFromVideoPreview(userGeneratedKey, regenerateHtmlMotionButton, confirmHtmlMotionButton);
      });
      confirmHtmlMotionButton?.addEventListener('click', () => {
        confirmHtmlMotionFromVideoPreview(userGeneratedKey, confirmHtmlMotionButton);
      });
      video?.addEventListener('loadedmetadata', () => {
        if (video.videoWidth && video.videoHeight) {
          const ratioValue = `${video.videoWidth} / ${video.videoHeight}`;
          const stage = els.videoPreviewBody.querySelector('.video-preview-stage');
          stage?.style.setProperty('--preview-stage-aspect', ratioValue);
          video.style.setProperty('--preview-video-aspect', ratioValue);
        }
      }, { once: true });
      bindVideoPreviewControls(video);
      if (video) video.dataset.officialSrc = src;
      state.videoPreviewModal = {
        ...(state.videoPreviewModal || {}),
        visible: true,
        playlist,
        index: playlistIndex,
        htmlMotionTaskId: '',
        htmlMotionPollTimer: null,
        htmlMotionSubmitting: false,
        htmlMotionCancelRequested: false,
      };
      els.videoPreviewModal.classList.remove('hidden');
      restoreVideoPreviewExtensionState(video, userGeneratedKey, extendVideoButton);
      // Resume in-flight backend job if any; otherwise sync finished preview.
      void resumeHtmlMotionFromVideoPreview(
        userGeneratedKey,
        regenerateHtmlMotionButton,
        confirmHtmlMotionButton,
        video,
      );
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
      state.videoPreviewModal = {
        ...(state.videoPreviewModal || {}),
        visible: false,
        playlist: [],
        index: 0,
        htmlMotionTaskId: '',
        htmlMotionPollTimer: null,
      };
      els.videoPreviewModal.classList.add('hidden');
      els.videoPreviewBody.innerHTML = '';
    }

    function renderSettingsModal() {
      if (!els.settingsModal) return;
      const visible = !!state.settingsModal.visible;
      els.settingsModal.classList.toggle('hidden', !visible);
      if (!visible) return;
      const settings = state.authSettings || {};
      const videoSettings = state.videoModelSettings || {};
      const fields = Array.isArray(settings.fields) ? settings.fields : [];
      const groups = groupSettingsFields(fields);
      const activeCategory = resolveActiveSettingsCategory(groups);
      const templateText = currentVideoTemplateStatusText(videoSettings);
      const videoMergeText = `视频合并：${videoMergeModeLabel(state.settingsModal.videoMergeMode)}`;
      const videoResolutionText = videoResolutionStatusText(videoSettings);
      els.settingsModalSub.innerHTML = `
        <div class="settings-status">
          ${pill(templateText, 'info')}
          ${pill(videoMergeText, 'info')}
          ${pill(videoResolutionText, 'info')}
          ${pill(`单个${Number(videoSettings.seconds || 10) || 10}秒`, 'info')}
        </div>
      `;
      els.settingsModalBody.innerHTML = `
        ${buildSettingsTabsMarkup(groups, activeCategory)}
        ${buildAuthSettingsMarkup(groups, activeCategory)}
      `;
    }

    function buildSettingsTabsMarkup(groups, activeCategory) {
      if (!groups.length) return '';
      return `
        <div class="settings-tabs" role="tablist" aria-label="设置分类">
          ${groups.map((group) => `
            <button type="button" class="settings-tab${group.label === activeCategory ? ' active' : ''}" data-settings-category="${escapeHtml(group.label)}" role="tab" aria-selected="${group.label === activeCategory ? 'true' : 'false'}">
              ${escapeHtml(group.label)}
            </button>
          `).join('')}
        </div>
      `;
    }

    function buildAuthSettingsMarkup(groups, activeCategory) {
      if (!groups.length) {
        return '<div class="empty">当前没有可显示的鉴权信息。</div>';
      }
      const group = groups.find((item) => item.label === activeCategory) || groups[0];
      return `
        <div class="settings-grid">
          <section class="settings-section">
            <div class="settings-section-head">
              <div class="settings-section-title">${escapeHtml(group.label)}</div>
              ${group.label === '归档' ? `<button type="button" class="settings-section-refresh" data-refresh-archive-settings ${state.settingsModal.refreshingArchive ? 'disabled' : ''}>${state.settingsModal.refreshingArchive ? '刷新中' : '刷新'}</button>` : ''}
            </div>
            ${group.fields.map((field) => buildSettingsRowMarkup(field)).join('')}
          </section>
        </div>
      `;
    }
    const settingsCategoryOrder = ['运行模式', 'TTS', 'AI8video', '文本/视频规划模型', '多模态模型', '图片模型', '视频模型', 'HTML 动效', '归档', '其他'];
    const settingsCategoryAliasMap = {};
