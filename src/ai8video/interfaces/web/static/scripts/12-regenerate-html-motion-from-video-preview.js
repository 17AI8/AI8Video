    async function regenerateHtmlMotionFromVideoPreview(userGeneratedKey, button, confirmButton) {
      const key = String(userGeneratedKey || '').trim();
      if (!key) return;
      const requestSeq = invalidateHtmlMotionPreviewRequest();
      const previous = getVideoPreviewButtonLabel(button) || '重新生成 HTML 动效';
      state.videoPreviewModal.htmlMotionSubmitting = true;
      state.videoPreviewModal.htmlMotionCancelRequested = false;
      if (button) {
        button.disabled = false;
        setVideoPreviewButtonLabel(button, '强行停止');
      }
      if (confirmButton) confirmButton.disabled = true;
      setHtmlMotionPreviewStatus('正在提交 HTML 动效预览任务');
      try {
        await persistOpenTtsEditorBeforeHtmlMotion(key);
        const res = await fetch('/api/user-generated-results/regenerate-html-motion', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw buildRequestError(data);
        }
        if (res.status === 202 && data?.taskId) {
          rememberHtmlMotionJob(key, data.taskId, data.pollUrl);
          state.videoPreviewModal.htmlMotionTaskId = data.taskId;
          state.videoPreviewModal.htmlMotionSubmitting = false;
          if (state.videoPreviewModal.htmlMotionCancelRequested) {
            await cancelHtmlMotionFromVideoPreview(button);
          }
          await waitForHtmlMotionTask(
            data.taskId,
            data.pollUrl,
            requestSeq,
            button,
            confirmButton,
            key,
          );
          return;
        }
        const overlay = data?.htmlMotionOverlay || {};
        if (String(overlay.status || '').toLowerCase() !== 'preview_ready') {
          const reason = overlay.reason || 'HTML 动效未叠加';
          setHtmlMotionPreviewStatus(`预览失败：${reason}`, 'warning');
          window.alert(`HTML 动效未叠加：${reason}`);
          if (button) setVideoPreviewButtonLabel(button, '预览失败');
          if (confirmButton) confirmButton.disabled = true;
          return;
        }
        const video = els.videoPreviewBody?.querySelector('video');
        showHtmlMotionPreview(video, overlay.previewUrl || data.videoUrl);
        if (confirmButton) confirmButton.disabled = false;
        setHtmlMotionPreviewStatus('预览已生成，确认后才会替换正式视频', 'success');
        if (button) setVideoPreviewButtonLabel(button, '预览已生成');
      } catch (error) {
        if (!htmlMotionRequestIsCurrent(requestSeq)) return;
        const message = error?.message || '重新生成 HTML 动效失败';
        setHtmlMotionPreviewStatus(`预览失败：${message}`, 'warning');
        window.alert(message.includes('视频提示词已删除') ? '视频提示词已删除，无法重新生成 HTML 动效' : message);
      } finally {
        if (htmlMotionRequestIsCurrent(requestSeq)) {
          state.videoPreviewModal.htmlMotionTaskId = '';
          state.videoPreviewModal.htmlMotionPollTimer = null;
          state.videoPreviewModal.htmlMotionSubmitting = false;
          state.videoPreviewModal.htmlMotionCancelRequested = false;
        }
        if (button) {
          setTimeout(() => {
            if (!htmlMotionRequestIsCurrent(requestSeq)) return;
            setVideoPreviewButtonLabel(button, previous);
            button.disabled = false;
          }, 1400);
        }
      }
    }

    async function confirmHtmlMotionFromVideoPreview(userGeneratedKey, button) {
      const key = String(userGeneratedKey || '').trim();
      if (!key || !button) return;
      const previous = getVideoPreviewButtonLabel(button) || '确认烧录';
      button.disabled = true;
      setVideoPreviewButtonLabel(button, '烧录中');
      try {
        const res = await fetch('/api/user-generated-results/confirm-html-motion', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        const video = els.videoPreviewBody?.querySelector('video');
        if (video) {
          const officialSrc = video.dataset.officialSrc || data.videoUrl || '';
          video.src = `${String(officialSrc).split('?')[0]}?htmlMotion=${Date.now()}`;
          video.load();
          video.play().catch(() => {});
        }
        await refreshUserGeneratedResults();
        renderResultModal();
        renderStatus();
        setVideoPreviewButtonLabel(button, '已烧录');
      } catch (error) {
        window.alert(error?.message || '确认烧录 HTML 动效失败');
        button.disabled = false;
        setVideoPreviewButtonLabel(button, previous);
      }
    }

    function closeVideoPreviewTtsEditor() {
      const popover = els.videoPreviewBody?.querySelector('[data-video-preview-tts-editor]');
      popover?.classList.add('hidden');
    }

    async function openTtsNarrationEditorFromVideoPreview(userGeneratedKey) {
      const key = String(userGeneratedKey || '').trim();
      if (!key || !els.videoPreviewBody) return;
      let popover = els.videoPreviewBody.querySelector('[data-video-preview-tts-editor]');
      if (!popover) {
        els.videoPreviewBody.insertAdjacentHTML('beforeend', `
          <div class="video-preview-tts-popover" data-video-preview-tts-editor>
            <div class="video-preview-tts-header">
              <strong>修改台词</strong>
              <button type="button" class="video-preview-button" data-video-preview-action="close-tts-editor">关闭</button>
            </div>
            <div class="video-preview-tts-status" data-video-preview-tts-status>正在读取台词</div>
            <textarea class="video-preview-tts-textarea" data-video-preview-tts-textarea placeholder="台词已删除或为空"></textarea>
            <div class="video-preview-tts-actions">
              <div class="video-preview-tts-ai-group">
                <button type="button" class="video-preview-button" data-video-preview-action="polish-tts-text">AI 润色</button>
                <button type="button" class="video-preview-button" data-video-preview-action="expand-tts-text">AI 扩写</button>
              </div>
              <button type="button" class="video-preview-button" data-video-preview-action="save-tts-text">保存台词</button>
            </div>
          </div>
        `);
        popover = els.videoPreviewBody.querySelector('[data-video-preview-tts-editor]');
      }
      const status = popover.querySelector('[data-video-preview-tts-status]');
      const textarea = popover.querySelector('[data-video-preview-tts-textarea]');
      const polishButton = popover.querySelector('[data-video-preview-action="polish-tts-text"]');
      const expandButton = popover.querySelector('[data-video-preview-action="expand-tts-text"]');
      const saveButton = popover.querySelector('[data-video-preview-action="save-tts-text"]');
      const closeButton = popover.querySelector('[data-video-preview-action="close-tts-editor"]');
      const setTtsStatus = (text, tone = '') => {
        if (!status) return;
        status.textContent = text;
        status.classList.remove('is-working', 'is-success', 'is-error');
        if (tone) status.classList.add(`is-${tone}`);
      };
      popover.classList.remove('hidden');
      setTtsStatus('正在读取台词');
      if (textarea) {
        textarea.value = '';
        textarea.disabled = true;
      }
      if (polishButton) polishButton.disabled = true;
      if (expandButton) expandButton.disabled = true;
      if (saveButton) saveButton.disabled = true;
      const bindOnce = (button, handler) => {
        if (!button || button.dataset.bound === 'true') return;
        button.dataset.bound = 'true';
        button.addEventListener('click', handler);
      };
      bindOnce(closeButton, closeVideoPreviewTtsEditor);
      const runAiTextTransform = async (button, options) => {
        if (!textarea) return;
        const text = String(textarea.value || '').trim();
        if (!text) {
          setTtsStatus('台词已删除');
          return;
        }
        const previous = button.textContent || options.idleText;
        if (polishButton) polishButton.disabled = true;
        if (expandButton) expandButton.disabled = true;
        if (saveButton) saveButton.disabled = true;
        button.textContent = options.loadingText;
        setTtsStatus(options.statusText, 'working');
        try {
          const currentVideo = els.videoPreviewBody?.querySelector('.video-preview-stage video');
          const durationSeconds = Number(currentVideo?.duration || 0);
          const res = await fetch(options.endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, durationSeconds }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || data?.ok === false) {
            throw buildRequestError(data);
          }
          textarea.value = data?.text || text;
          setTtsStatus(`${options.donePrefix}，${Number(data?.textChars || textarea.value.length || 0)} 字，保存后生效`, 'success');
        } catch (error) {
          const message = error?.message || options.errorText;
          setTtsStatus(message.includes('台词已删除') ? '台词已删除' : message, 'error');
        } finally {
          button.textContent = previous;
          if (polishButton) polishButton.disabled = false;
          if (expandButton) expandButton.disabled = false;
          if (saveButton) saveButton.disabled = false;
        }
      };
      bindOnce(polishButton, async () => {
        await runAiTextTransform(polishButton, {
          endpoint: '/api/user-generated-results/tts-narration/polish',
          idleText: 'AI 润色',
          loadingText: '润色中',
          statusText: '正在检索知识库并润色',
          donePrefix: '已润色',
          errorText: 'AI 润色失败',
        });
      });
      bindOnce(expandButton, async () => {
        await runAiTextTransform(expandButton, {
          endpoint: '/api/user-generated-results/tts-narration/expand',
          idleText: 'AI 扩写',
          loadingText: '扩写中',
          statusText: '正在检索知识库并扩写',
          donePrefix: '已扩写',
          errorText: 'AI 扩写失败',
        });
      });
      bindOnce(saveButton, async () => {
        if (!textarea) return;
        const previous = saveButton.textContent || '保存台词';
        saveButton.disabled = true;
        saveButton.textContent = '保存中';
        try {
          const res = await fetch('/api/user-generated-results/tts-narration', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ userGeneratedKey: key, text: textarea.value }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || data?.ok === false) {
            throw buildRequestError(data);
          }
          if (status) {
            setTtsStatus(data?.deleted ? '台词已删除' : `已保存，${Number(data?.textChars || 0)} 字`, 'success');
          }
        } catch (error) {
          const message = error?.message || '保存台词失败';
          setTtsStatus(message.includes('台词已删除') ? '台词已删除' : message, 'error');
        } finally {
          saveButton.textContent = previous;
          saveButton.disabled = false;
        }
      });
      try {
        const res = await fetch('/api/user-generated-results/tts-narration', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw buildRequestError(data);
        }
        if (textarea) {
          textarea.value = data?.text || '';
          textarea.disabled = false;
          textarea.focus();
        }
        if (polishButton) polishButton.disabled = false;
        if (expandButton) expandButton.disabled = false;
        if (saveButton) saveButton.disabled = false;
        setTtsStatus(`当前台词，${Number(data?.textChars || 0)} 字`);
      } catch (error) {
        const message = error?.message || '读取台词失败';
        if (textarea) {
          textarea.value = '';
          textarea.disabled = false;
          textarea.focus();
        }
        if (polishButton) polishButton.disabled = false;
        if (expandButton) expandButton.disabled = false;
        if (saveButton) saveButton.disabled = false;
        setTtsStatus(message.includes('台词已删除') ? '台词已删除' : message, 'error');
      }
    }

    async function deleteUserGeneratedVideoFromPreview(userGeneratedKey, button) {
      const key = String(userGeneratedKey || '').trim();
      if (!key) return;
      if (!window.confirm('确定删除这个视频？删除后会同步从查看结果里移除。')) {
        return;
      }
      if (button) {
        button.disabled = true;
        setVideoPreviewButtonLabel(button, '删除中');
      }
      try {
        const res = await fetch('/api/user-generated-results/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw buildRequestError(data);
        }
        const nextPreview = getNextVideoPreviewAfterDelete(key);
        removeDeletedUserGeneratedResultFromState(data);
        await refreshUserGeneratedResults();
        renderProgress();
        renderProgressModal();
        renderResultModal();
        renderStatus();
        if (nextPreview) {
          openVideoPreviewModal(nextPreview);
        } else {
          closeVideoPreviewModal();
        }
      } catch (error) {
        window.alert(error?.message || '删除失败');
        if (button) {
          button.disabled = false;
          setVideoPreviewButtonLabel(button, '删除视频');
        }
      }
    }

    function getNextVideoPreviewAfterDelete(userGeneratedKey) {
      const deletedKey = String(userGeneratedKey || '').trim();
      const playlist = Array.isArray(state.videoPreviewModal?.playlist)
        ? state.videoPreviewModal.playlist.filter((item) => item?.src)
        : [];
      if (!deletedKey || playlist.length <= 1) return null;
      const currentIndex = Math.max(0, Math.min(
        playlist.length - 1,
        Number(state.videoPreviewModal?.index || 0) || 0
      ));
      const remaining = playlist.filter((item) => String(item?.userGeneratedKey || '').trim() !== deletedKey);
      if (!remaining.length) return null;
      const nextIndex = Math.min(currentIndex, remaining.length - 1);
      return {
        ...remaining[nextIndex],
        playlist: remaining,
        playlistIndex: nextIndex,
      };
    }

    function removeDeletedUserGeneratedResultFromState(data = {}) {
      const deleted = new Set(
        [
          ...(Array.isArray(data.deleted) ? data.deleted : []),
          data.userGeneratedKey,
          data.userGeneratedPreviewKey,
          data.userGeneratedCoverKey,
        ]
          .map((item) => String(item || '').trim())
          .filter(Boolean)
      );
      if (!deleted.size) return;
      const deletedIdentity = collectDeletedUserGeneratedIdentity(deleted, {
        keys: Array.isArray(data.relatedKeys) ? data.relatedKeys : [],
        jobIds: Array.isArray(data.relatedJobIds) ? data.relatedJobIds : [],
      });
      state.deletedUserGeneratedKeys = [
        ...new Set([
          ...(Array.isArray(state.deletedUserGeneratedKeys) ? state.deletedUserGeneratedKeys : []),
          ...deleted,
          ...deletedIdentity.basenames,
        ]),
      ].slice(-80);
      state.deletedUserGeneratedJobIds = [
        ...new Set([
          ...(Array.isArray(state.deletedUserGeneratedJobIds) ? state.deletedUserGeneratedJobIds : []),
          ...deletedIdentity.jobIds,
        ]),
      ].slice(-80);
      const isDeletedItem = (item) => {
        const keys = [
          item?.userGeneratedKey,
          item?.userGeneratedPreviewKey,
          item?.userGeneratedCoverKey,
          item?.relativePath,
          item?.previewRelativePath,
          item?.coverRelativePath,
        ]
          .map((value) => String(value || '').trim())
          .filter(Boolean);
        return keys.some((key) => deleted.has(key));
      };
      state.userGeneratedResults = (state.userGeneratedResults || []).filter((item) => !isDeletedItem(item));
      if (Array.isArray(state.videoPreviewModal?.playlist)) {
        state.videoPreviewModal.playlist = state.videoPreviewModal.playlist.filter((item) => {
          const key = String(item?.userGeneratedKey || '').trim();
          const previewKey = String(item?.userGeneratedPreviewKey || '').trim();
          const coverKey = String(item?.userGeneratedCoverKey || '').trim();
          return !(deleted.has(key) || deleted.has(previewKey) || deleted.has(coverKey));
        });
      }
      scrubDeletedUserGeneratedReferences(deletedIdentity);
      persistSessions();
    }

    function collectDeletedUserGeneratedIdentity(deleted, extra = {}) {
      const deletedKeys = new Set([
        ...[...deleted].map((item) => String(item || '').trim()),
        ...(Array.isArray(extra.keys) ? extra.keys.map((item) => String(item || '').trim()) : []),
      ].filter(Boolean));
      const deletedBasenames = new Set([...deletedKeys].map(mediaKeyBasename).filter(Boolean));
      const jobIds = new Set(
        (Array.isArray(extra.jobIds) ? extra.jobIds : [])
          .map((item) => String(item || '').trim())
          .filter(Boolean)
      );
      const episodes = new Set();
      const addItem = (item) => {
        if (!item || typeof item !== 'object') return;
        const keys = [
          item.userGeneratedKey,
          item.userGeneratedPreviewKey,
          item.userGeneratedCoverKey,
          item.archiveKey,
          item.archiveUrl,
          item.archiveLocalPath,
          item.localVideoPath,
          item.videoUrl,
        ].map((value) => String(value || '').trim()).filter(Boolean);
        const matched = keys.some((key) => deletedKeys.has(key) || deletedBasenames.has(mediaKeyBasename(key)));
        if (!matched) return;
        collectProgressItemJobIds(item).forEach((jobId) => jobIds.add(jobId));
        const episode = Number(item.episodeIndex || item.index || 0);
        if (episode > 0) episodes.add(episode);
      };
      (state.userGeneratedResults || []).forEach(addItem);
      (state.videoPreviewModal?.playlist || []).forEach(addItem);
      return {
        keys: deletedKeys,
        basenames: deletedBasenames,
        jobIds,
        episodes,
      };
    }

    function collectProgressItemJobIds(item) {
      const jobIds = new Set();
      const addJobId = (value) => {
        const jobId = String(value || '').trim();
        if (jobId) jobIds.add(jobId);
      };
      const addSegments = (segments) => {
        if (!Array.isArray(segments)) return;
        segments.forEach((segment) => {
          if (segment && typeof segment === 'object') addJobId(segment.jobId);
        });
      };
      if (!item || typeof item !== 'object') return jobIds;
      addJobId(item.jobId);
      addSegments(item.generationMeta?.segmentRecords);
      addSegments(item.archiveMeta?.segmentRecords);
      addSegments(item.archiveMeta?.segments);
      addSegments(item.usage?.segments);
      if (item.assetRecord && item.assetRecord !== item) {
        collectProgressItemJobIds(item.assetRecord).forEach((jobId) => jobIds.add(jobId));
      }
      return jobIds;
    }

    function currentDeletedUserGeneratedIdentity() {
      return collectDeletedUserGeneratedIdentity(
        new Set(Array.isArray(state.deletedUserGeneratedKeys) ? state.deletedUserGeneratedKeys : []),
        { jobIds: Array.isArray(state.deletedUserGeneratedJobIds) ? state.deletedUserGeneratedJobIds : [] },
      );
    }

    function itemMatchesDeletedUserGeneratedIdentity(item, identity) {
      if (!item || typeof item !== 'object' || !identity) return false;
      const itemJobIds = collectProgressItemJobIds(item);
      if ([...itemJobIds].some((jobId) => identity.jobIds?.has(jobId))) return true;
      if ([...itemJobIds].some((jobId) => (
        [...(identity.keys || [])].some((key) => String(key || '').includes(jobId))
        || [...(identity.basenames || [])].some((key) => String(key || '').includes(jobId))
      ))) return true;
      const episode = Number(item.episodeIndex || item.index || 0);
      if (episode > 0 && identity.episodes?.has(episode) && identity.jobIds?.size) return true;
      const keys = [
        item.userGeneratedKey,
        item.userGeneratedCoverKey,
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

