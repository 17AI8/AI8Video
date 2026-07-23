    function scrubDeletedGenerationProgress(progress, identity) {
      if (!progress || !Array.isArray(progress.items)) return progress;
      let changed = false;
      const items = progress.items.map((item) => {
        if (!itemMatchesDeletedUserGeneratedIdentity(item, identity)) return item;
        changed = true;
        return markDeletedProgressItem(item);
      });
      if (!changed) return progress;
      return {
        ...progress,
        items,
        succeededCount: items.filter((item) => item?.status === 'succeeded').length,
        failedCount: items.filter((item) => item?.status === 'failed').length,
        skippedCount: items.filter((item) => item?.status === 'skipped').length,
        deletedCount: items.filter((item) => item?.status === 'deleted').length,
      };
    }

    function markDeletedProgressItem(item) {
      return {
        ...(item || {}),
        status: 'deleted',
        statusLabel: '已生成，文件已删除',
        providerStatus: 'deleted',
        providerProgress: 100,
        hasLocalAsset: false,
      };
    }

    function scrubDeletedUserGeneratedReferences(identity) {
      if (!identity) return;
      const scrubProgress = (progress) => scrubDeletedGenerationProgress(progress, identity);
      const scrubArray = (items) => {
        if (!Array.isArray(items)) return items;
        return items.filter((item) => !itemMatchesDeletedUserGeneratedIdentity(item, identity));
      };
      state.sessions = (state.sessions || []).map((session) => ({
        ...session,
        messages: (session.messages || []).map((message) => {
          const payload = message?.payload;
          if (!payload || typeof payload !== 'object') return message;
          const nextPayload = {
            ...payload,
            items: scrubArray(payload.items),
            videos: scrubArray(payload.videos),
            groups: scrubArray(payload.groups),
          };
          if (payload.result && typeof payload.result === 'object') {
            nextPayload.result = {
              ...payload.result,
              items: scrubArray(payload.result.items),
              videos: scrubArray(payload.result.videos),
              groups: scrubArray(payload.result.groups),
            };
          }
          if (payload.pendingStatus && typeof payload.pendingStatus === 'object') {
            nextPayload.pendingStatus = {
              ...payload.pendingStatus,
              generationProgress: scrubProgress(payload.pendingStatus.generationProgress),
            };
          }
          return { ...message, payload: nextPayload };
        }),
      }));
      if (state.generationProgress?.cards) {
        state.generationProgress = {
          ...state.generationProgress,
          cards: (state.generationProgress.cards || []).map((item) => (
            itemMatchesDeletedUserGeneratedIdentity(item, identity) ? markDeletedProgressItem(item) : item
          )),
        };
      }
    }

    function currentUserGeneratedExistingIdentity() {
      const keys = new Set();
      const basenames = new Set();
      const jobIds = new Set();
      const addKey = (value) => {
        const key = String(value || '').trim();
        if (!key) return;
        keys.add(key);
        const basename = mediaKeyBasename(key);
        if (basename) basenames.add(basename);
      };
      (state.userGeneratedResults || []).forEach((item) => {
        addKey(item?.userGeneratedKey);
        addKey(item?.userGeneratedPreviewKey);
        addKey(item?.userGeneratedCoverKey);
        addKey(item?.relativePath);
        addKey(item?.coverRelativePath);
        addKey(item?.archiveKey);
        addKey(item?.archiveUrl);
        addKey(item?.archiveLocalPath);
        addKey(item?.localVideoPath);
        addKey(item?.videoUrl);
        collectProgressItemJobIds(item).forEach((jobId) => jobIds.add(jobId));
      });
      return { keys, basenames, jobIds };
    }

    function progressItemHasExistingUserGeneratedMirror(item, identity) {
      if (!item || typeof item !== 'object' || !identity) return false;
      const itemJobIds = collectProgressItemJobIds(item);
      if ([...itemJobIds].some((jobId) => identity.jobIds?.has(jobId))) return true;
      const keys = [
        item.userGeneratedKey,
        item.userGeneratedPreviewKey,
        item.userGeneratedCoverKey,
        item.relativePath,
        item.coverRelativePath,
        item.archiveKey,
        item.archiveUrl,
        item.archiveLocalPath,
        item.localVideoPath,
        item.videoUrl,
        item.coverImageUrl,
        item.userGeneratedPreviewKey,
        item.archiveCoverKey,
        item.archiveCoverUrl,
      ].map((value) => String(value || '').trim()).filter(Boolean);
      return keys.some((key) => identity.keys?.has(key) || identity.basenames?.has(mediaKeyBasename(key)));
    }

    function progressItemLooksLikeDeletedLocalResult(item, identity) {
      if (!item || typeof item !== 'object') return false;
      const status = String(item.status || '').trim().toLowerCase();
      const providerStatus = String(item.providerStatus || '').trim().toLowerCase();
      const label = String(item.statusLabel || '').trim();
      if (status === 'deleted' || providerStatus === 'deleted' || label.includes('删除')) return true;
      if (status !== 'succeeded') return false;
      if (item.hasLocalAsset === false) return true;
      if (!progressItemHasExistingUserGeneratedMirror(item, identity)) {
        return collectProgressItemJobIds(item).size > 0 || itemRequiresUserGeneratedMirror(item);
      }
      return false;
    }

    function scrubMissingUserGeneratedProgress(progress, identity) {
      if (!progress || !Array.isArray(progress.items)) return progress;
      let changed = false;
      const items = progress.items.map((item) => {
        if (!progressItemLooksLikeDeletedLocalResult(item, identity)) return item;
        changed = true;
        return markDeletedProgressItem(item);
      });
      if (!changed) return progress;
      return {
        ...progress,
        items,
        succeededCount: items.filter((item) => item?.status === 'succeeded').length,
        failedCount: items.filter((item) => item?.status === 'failed').length,
        skippedCount: items.filter((item) => item?.status === 'skipped').length,
        deletedCount: items.filter((item) => item?.status === 'deleted').length,
      };
    }

    function scrubMissingUserGeneratedProgressFromSessions() {
      const identity = currentUserGeneratedExistingIdentity();
      let changed = false;
      state.sessions = (state.sessions || []).map((session) => {
        const messages = (session.messages || []).map((message) => {
          const payload = message?.payload;
          const progress = payload?.pendingStatus?.generationProgress;
          if (!progress) return message;
          const nextProgress = scrubMissingUserGeneratedProgress(progress, identity);
          if (nextProgress === progress) return message;
          changed = true;
          return {
            ...message,
            payload: {
              ...payload,
              pendingStatus: {
                ...payload.pendingStatus,
                generationProgress: nextProgress,
              },
            },
          };
        });
        return messages === session.messages ? session : { ...session, messages };
      });
      if (state.generationProgress?.cards) {
        const cards = (state.generationProgress.cards || []).map((item) => (
          progressItemLooksLikeDeletedLocalResult(item, identity) ? markDeletedProgressItem(item) : item
        ));
        if (JSON.stringify(cards) !== JSON.stringify(state.generationProgress.cards || [])) {
          state.generationProgress = { ...state.generationProgress, cards };
          changed = true;
        }
      }
      return changed;
    }

    function videoPreviewOptionsFromTrigger(trigger) {
      const src = trigger?.getAttribute?.('data-fullscreen-video') || '';
      const explicitKey = trigger?.getAttribute?.('data-video-user-generated-key') || '';
      const userGeneratedKey = explicitKey || deriveUserGeneratedKeyFromMediaUrl(src);
      return {
        trigger,
        src,
        title: trigger?.getAttribute?.('data-video-title') || '全屏播放',
        cover: trigger?.getAttribute?.('data-video-cover') || '',
        userGeneratedKey,
        userGeneratedPreviewKey: trigger?.getAttribute?.('data-video-user-generated-preview-key') || deriveLocalPreviewKey(userGeneratedKey),
        userGeneratedCoverKey: trigger?.getAttribute?.('data-video-user-generated-cover-key') || deriveLocalCoverKey(userGeneratedKey),
      };
    }

    function buildVideoPreviewPlaylist(trigger) {
      const container = trigger?.closest?.('#resultModal, .message, .progress-card, main') || document;
      const items = Array.from(container.querySelectorAll('[data-fullscreen-video]'))
        .map((item) => videoPreviewOptionsFromTrigger(item))
        .filter((item) => item.src);
      if (!items.length && trigger) {
        return [videoPreviewOptionsFromTrigger(trigger)].filter((item) => item.src);
      }
      return items;
    }

    function navigateVideoPreview(delta) {
      const playlist = Array.isArray(state.videoPreviewModal?.playlist) ? state.videoPreviewModal.playlist : [];
      if (playlist.length <= 1) return;
      const currentIndex = Number(state.videoPreviewModal?.index || 0);
      const nextIndex = (currentIndex + delta + playlist.length) % playlist.length;
      openVideoPreviewModal({
        ...playlist[nextIndex],
        playlist,
        playlistIndex: nextIndex,
      });
    }

    function loadVideoPreviewExtensionStates() {
      try {
        const value = JSON.parse(localStorage.getItem(VIDEO_PREVIEW_EXTENSION_STORAGE_KEY) || '{}');
        return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
      } catch (_error) {
        return {};
      }
    }

    function persistVideoPreviewExtensionState(userGeneratedKey, extensionState) {
      const key = String(userGeneratedKey || '').trim();
      if (!key) return;
      const states = loadVideoPreviewExtensionStates();
      if (extensionState) states[key] = extensionState;
      else delete states[key];
      localStorage.setItem(VIDEO_PREVIEW_EXTENSION_STORAGE_KEY, JSON.stringify(states));
    }

    function updateVideoPreviewExtensionState(userGeneratedKey, patch) {
      const key = String(userGeneratedKey || '').trim();
      if (!key) return {};
      const current = loadVideoPreviewExtensionStates()[key] || {};
      const next = { ...current, ...patch };
      persistVideoPreviewExtensionState(key, next);
      return next;
    }

    async function saveVideoPreviewExtensionFrame(userGeneratedKey, frameTime) {
      const res = await fetch('/api/user-generated-results/extension-frame', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userGeneratedKey, frameTime }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.error || '保存延长截帧失败');
      return data;
    }

    function setVideoPreviewMainControlsDisabled(disabled) {
      els.videoPreviewBody?.querySelectorAll('.video-preview-controls button').forEach((control) => {
        if (disabled) {
          if (!control.hasAttribute('data-extension-disabled-before')) {
            control.dataset.extensionDisabledBefore = control.disabled ? 'true' : 'false';
          }
          control.disabled = true;
          return;
        }
        const previous = control.dataset.extensionDisabledBefore;
        if (previous === undefined) return;
        control.disabled = previous === 'true';
        delete control.dataset.extensionDisabledBefore;
      });
    }

    function hasActiveVideoPreviewExtensionState(userGeneratedKey) {
      const key = String(userGeneratedKey || '').trim();
      return Boolean(key && loadVideoPreviewExtensionStates()[key]?.active);
    }

    async function discardDetachedVideoPreviewExtensionResult(leftKey, rightKey) {
      const res = await fetch('/api/user-generated-results/extension-state/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ leftKey: String(leftKey || '').trim(), rightKey: String(rightKey || '').trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.error || '清理已删除延长任务的结果失败');
    }

    async function deleteVideoPreviewExtensionState(userGeneratedKey, button) {
      const key = String(userGeneratedKey || '').trim();
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      if (!key || !stageGrid || button?.disabled) return;
      const savedState = loadVideoPreviewExtensionStates()[key] || {};
      button.disabled = true;
      try {
        await discardDetachedVideoPreviewExtensionResult(key, savedState.rightVideoKey || '');
        persistVideoPreviewExtensionState(key, null);
        stageGrid.querySelector('.video-preview-extension-stage')?.remove();
        stageGrid.querySelector('.video-preview-merge-control')?.remove();
        stageGrid.querySelector('.video-preview-merge-settings')?.remove();
        stageGrid.classList.remove('extension-active');
        delete stageGrid.dataset.extensionFrameTime;
        setVideoPreviewMainControlsDisabled(false);
        const extendButton = stageGrid.querySelector('[data-video-preview-action="extend-video"]');
        if (extendButton) {
          extendButton.disabled = false;
          setVideoPreviewButtonLabel(extendButton, '延长');
        }
        button.disabled = false;
        await refreshUserGeneratedResults();
      } catch (error) {
        button.disabled = false;
        window.alert(error?.message || '删除右侧延长内容失败');
      }
    }

    async function openVideoPromptEditor(userGeneratedKey) {
      const key = String(userGeneratedKey || '').trim();
      if (!key || !els.videoPreviewBody) return;
      els.videoPreviewBody.querySelector('[data-video-preview-prompt-editor]')?.remove();
      els.videoPreviewBody.insertAdjacentHTML('beforeend', `
        <div class="video-preview-tts-popover" data-video-preview-prompt-editor>
          <div class="video-preview-tts-header"><strong>视频提示词</strong><button type="button" class="video-preview-button" data-close-video-prompt>关闭</button></div>
          <div class="video-preview-tts-status" data-video-prompt-status>正在读取视频提示词</div>
          <textarea class="video-preview-tts-textarea" data-video-prompt-textarea></textarea>
          <div class="video-preview-tts-actions">
            <div class="video-preview-tts-ai-group">
              <button type="button" class="video-preview-button" data-continue-video-prompt>续写视频</button>
              <button type="button" class="video-preview-button" data-transform-video-prompt="polish">润色</button>
              <button type="button" class="video-preview-button" data-transform-video-prompt="expand">扩写</button>
            </div>
            <button type="button" class="video-preview-button" data-save-video-prompt>保存视频提示词</button>
          </div>
        </div>
      `);
      const editor = els.videoPreviewBody.querySelector('[data-video-preview-prompt-editor]');
      showVideoPreviewPopover(editor);
      const textarea = editor.querySelector('[data-video-prompt-textarea]');
      const status = editor.querySelector('[data-video-prompt-status]');
      const saveButton = editor.querySelector('[data-save-video-prompt]');
      const continueButton = editor.querySelector('[data-continue-video-prompt]');
      const transformButtons = Array.from(editor.querySelectorAll('[data-transform-video-prompt]'));
      const setPromptActionsDisabled = (disabled) => {
        if (continueButton) continueButton.disabled = disabled;
        transformButtons.forEach((item) => { item.disabled = disabled; });
        if (saveButton) saveButton.disabled = disabled;
      };
      editor.querySelector('[data-close-video-prompt]')?.addEventListener('click', () => editor.remove());
      continueButton?.addEventListener('click', async () => {
        setPromptActionsDisabled(true);
        status.textContent = '正在使用文本模型续写视频';
        try {
          const res = await fetch('/api/user-generated-results/video-prompt/continue', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ userGeneratedKey: key }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) throw new Error(data.error || '续写视频失败');
          textarea.value = data.text || '';
          status.textContent = `已续写，${data.textChars} 字，保存后才会用于生成`;
        } catch (error) {
          status.textContent = error?.message || '续写视频失败';
        } finally {
          setPromptActionsDisabled(false);
        }
      });
      transformButtons.forEach((transformButton) => {
        transformButton.addEventListener('click', async () => {
          const mode = String(transformButton.dataset.transformVideoPrompt || '');
          const action = mode === 'polish' ? '润色' : '扩写';
          if (!String(textarea.value || '').trim()) {
            status.textContent = '视频提示词为空';
            return;
          }
          setPromptActionsDisabled(true);
          status.textContent = `正在检索知识库并${action}`;
          try {
            const res = await fetch(`/api/user-generated-results/video-prompt/${mode}`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: textarea.value }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) throw new Error(data.error || `视频提示词${action}失败`);
            textarea.value = data.text || textarea.value;
            const topK = Number(data.knowledge?.topK || 0);
            status.textContent = `已${action}，${data.textChars} 字${topK ? `，知识库 Top ${topK}` : ''}，保存后生效`;
          } catch (error) {
            status.textContent = error?.message || `视频提示词${action}失败`;
          } finally {
            setPromptActionsDisabled(false);
          }
        });
      });
      saveButton?.addEventListener('click', async () => {
        saveButton.disabled = true;
        try {
          const data = await postVideoPrompt(key, textarea.value);
          status.textContent = data.text ? `已保存，${data.textChars} 字` : '视频提示词已删除';
          await syncVideoPreviewExtensionGenerateButton(key);
        } catch (error) {
          status.textContent = error?.message || '保存视频提示词失败';
        } finally {
          saveButton.disabled = false;
        }
      });
      try {
        const data = await postVideoPrompt(key);
        textarea.value = data.text || '';
        status.textContent = data.text ? `当前视频提示词，${data.textChars} 字` : '当前没有视频提示词';
      } catch (error) {
        status.textContent = error?.message || '读取视频提示词失败';
      }
    }

    async function postVideoPrompt(userGeneratedKey, text) {
      const payload = { userGeneratedKey };
      if (text !== undefined) payload.text = text;
      const res = await fetch('/api/user-generated-results/video-prompt', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.error || '视频提示词请求失败');
      return data;
    }

    async function syncVideoPreviewExtensionGenerateButton(userGeneratedKey) {
      const button = els.videoPreviewBody?.querySelector('[data-video-preview-generate]');
      if (!button || button.dataset.generating === 'true') return;
      const savedState = loadVideoPreviewExtensionStates()[String(userGeneratedKey || '').trim()] || {};
      if (savedState.generating) {
        button.disabled = true;
        button.dataset.generating = 'true';
        button.textContent = '生成中';
        void reconcileVideoPreviewExtensionGeneration(userGeneratedKey);
        return;
      }
      try {
        const data = await postVideoPrompt(userGeneratedKey);
        button.disabled = !String(data.text || '').trim();
      } catch (_error) {
        button.disabled = true;
      }
    }

    async function reconcileVideoPreviewExtensionGeneration(userGeneratedKey) {
      const key = String(userGeneratedKey || '').trim();
      if (!key) return false;
      try {
        const res = await fetch('/api/user-generated-results/extension-video/status', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok || data.status !== 'completed') return false;
        if (!hasActiveVideoPreviewExtensionState(key)) {
          await discardDetachedVideoPreviewExtensionResult(key, data.userGeneratedKey);
          return true;
        }
        updateVideoPreviewExtensionState(key, {
          generating: false,
          generationCompletedAt: new Date().toISOString(),
          generationError: '',
          rightVideoKey: data.userGeneratedKey,
          rightVideoUrl: data.videoUrl,
        });
        setVideoPreviewExtensionVideo(data.videoUrl, data.userGeneratedKey);
        if (state.generationProgress?.kind === 'extension') clearGenerationProgress();
        await refreshUserGeneratedResults();
        return true;
      } catch (error) {
        console.warn('对账延长视频完成状态失败', error);
        return false;
      }
    }
