    function readVideoPreviewExtensionBatchFrames(stageGrid) {
      try {
        const frames = JSON.parse(String(stageGrid?.dataset.extensionBatchFrames || '[]'));
        return Array.isArray(frames) ? frames.slice(0, 4) : [];
      } catch (_) {
        return [];
      }
    }

    function isVideoPreviewExtensionBatchMode(stageGrid = null) {
      const grid = stageGrid || els.videoPreviewBody?.querySelector('.video-preview-stage-grid');
      return grid?.dataset.extensionBatchMode === 'true' && readVideoPreviewExtensionBatchFrames(grid).length === 4;
    }

    function isVideoPreviewExtensionBatchBusy(stageGrid, frames = null) {
      const currentFrames = frames || readVideoPreviewExtensionBatchFrames(stageGrid);
      return stageGrid?.dataset.extensionBatchBusy === 'true'
        || currentFrames.some((frame) => frame.status === 'repairing' || frame.status === 'video-generating');
    }

    function batchFrameMarkup(frame, index, busy = false) {
      const hasVideo = Boolean(frame.videoUrl);
      const media = hasVideo
        ? `<video controls playsinline preload="metadata" src="${escapeHtml(frame.videoUrl)}"></video>`
        : (frame.frameUrl ? `<img src="${escapeHtml(frame.frameUrl)}" alt="批量截帧">` : '<span>等待截图</span>');
      return `<div class="video-preview-extension-variant${frame.selected ? ' selected' : ''}${hasVideo ? ' has-video' : ''}" data-extension-frame-key="${escapeHtml(frame.frameKey || '')}">
        ${media}
        <button type="button" class="video-preview-extension-variant-select" data-extension-batch-select="${index}" aria-label="选择第 ${index + 1} 张" aria-pressed="${frame.selected ? 'true' : 'false'}"${busy ? ' disabled' : ''}>${frame.selected ? '✓' : ''}</button>
      </div>`;
    }

    function syncVideoPreviewExtensionBatchControls(stageGrid, frames = null) {
      const currentFrames = frames || readVideoPreviewExtensionBatchFrames(stageGrid);
      const stage = stageGrid?.querySelector('.video-preview-extension-stage');
      const busy = isVideoPreviewExtensionBatchBusy(stageGrid, currentFrames);
      const selectedIndex = currentFrames.findIndex((frame) => frame.selected);
      stage?.classList.toggle('is-busy', busy);
      stage?.querySelectorAll('[data-extension-batch-select]').forEach((button) => { button.disabled = busy; });
      const confirmButton = stage?.querySelector('[data-extension-batch-toggle]');
      if (confirmButton) confirmButton.disabled = busy || selectedIndex < 0;
    }

    function createVideoPreviewExtensionBatchCopies(frameKey, frameUrl) {
      return Array.from({ length: 4 }, () => ({ frameKey, frameUrl }));
    }

    function restoreVideoPreviewExtensionBatchFrames(frames) {
      return frames.map((frame) => {
        if (frame?.status === 'repairing' || frame?.status === 'video-generating') {
          return { ...frame, status: 'completed', progressLabel: '' };
        }
        return frame;
      });
    }

    function applyVideoPreviewExtensionBatchStage(stageGrid, frames, restore = false) {
      const stage = stageGrid?.querySelector('.video-preview-extension-stage');
      const actionBar = stage?.querySelector('.video-preview-extension-action-bar');
      if (!stage || !actionBar || !Array.isArray(frames) || frames.length !== 4) return;
      const displayFrames = restore ? restoreVideoPreviewExtensionBatchFrames(frames) : frames;
      const busy = isVideoPreviewExtensionBatchBusy(stageGrid, displayFrames);
      stage.querySelector('video[data-frame-preview], .video-preview-extension-variants, :scope > img')?.remove();
      actionBar.insertAdjacentHTML('beforebegin', `<div class="video-preview-extension-variants">${displayFrames.map((frame, index) => batchFrameMarkup(frame, index, busy)).join('')}</div>`);
      stage.classList.add('batch-active');
      stage.classList.toggle('is-busy', busy);
      stageGrid.dataset.extensionBatchMode = 'true';
      stageGrid.dataset.extensionBatchFrames = JSON.stringify(displayFrames);
      const selectedIndex = displayFrames.findIndex((frame) => frame.selected);
      const confirmButton = stage.querySelector('[data-extension-batch-toggle]');
      confirmButton.textContent = '确认';
      stageGrid.dataset.extensionBatchSelectedIndex = String(selectedIndex);
      displayFrames.forEach((frame, index) => {
        if (frame.status === 'repairing') setVideoPreviewBatchVariantLoading(stageGrid, index, true);
        if (frame.status === 'video-generating') setVideoPreviewBatchVariantLoading(stageGrid, index, true, frame.progressLabel || '视频生成中');
      });
      syncVideoPreviewExtensionBatchControls(stageGrid, displayFrames);
      syncVideoPreviewMergeAvailability();
      renderVideoPreviewFrameRepairActions();
    }

    function persistVideoPreviewExtensionBatchState(stageGrid) {
      const key = String(stageGrid?.dataset.leftVideoKey || '').trim();
      if (!key) return;
      persistVideoPreviewExtensionState(key, {
        ...(loadVideoPreviewExtensionStates()[key] || {}),
        active: true,
        batchMode: isVideoPreviewExtensionBatchMode(stageGrid),
        batchFrames: readVideoPreviewExtensionBatchFrames(stageGrid),
      });
    }

    async function hydrateVideoPreviewExtensionBatchStatus(stageGrid) {
      const frameKey = String(stageGrid?.dataset.extensionFrameKey || '').trim();
      if (!frameKey || !isVideoPreviewExtensionBatchMode(stageGrid)) return;
      try {
        const selectedKey = readVideoPreviewExtensionBatchFrames(stageGrid).find((frame) => frame.selected)?.frameKey;
        const res = await fetch('/api/user-generated-results/extension-frame/batch-status', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ frameKey }),
        });
        const data = await res.json().catch(() => ({}));
        if (!isVideoPreviewExtensionBatchMode(stageGrid) || !res.ok || !data.ok || !Array.isArray(data.frames)) return;
        applyVideoPreviewExtensionBatchStage(stageGrid, data.frames.map((frame) => ({
          ...frame, selected: frame.frameKey === selectedKey,
        })));
        persistVideoPreviewExtensionBatchState(stageGrid);
      } catch (_) {
        // 状态查询失败时保留本地已知状态，不把结果误标为失败。
      }
    }

    async function hydrateVideoPreviewExtensionBatchVideos(stageGrid) {
      const frames = readVideoPreviewExtensionBatchFrames(stageGrid);
      if (!isVideoPreviewExtensionBatchMode(stageGrid) || !frames.length) return;
      if (frames.every((frame) => frame.videoUrl)) return;
      try {
        const res = await fetch('/api/user-generated-results/extension-video/batch-status', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ frames }),
        });
        const data = await res.json().catch(() => ({}));
        if (!isVideoPreviewExtensionBatchMode(stageGrid) || !res.ok || !data.ok || !Array.isArray(data.videos)) return;
        const nextFrames = frames.map((frame, index) => {
          const match = data.videos.find((item) => item.index === index && item.status === 'completed');
          if (!match?.videoUrl) return frame;
          return {
            ...frame,
            status: 'completed',
            userGeneratedKey: match.userGeneratedKey,
            videoUrl: match.videoUrl,
          };
        });
        if (nextFrames.some((frame, index) => frame.videoUrl && frame.videoUrl !== frames[index]?.videoUrl)) {
          applyVideoPreviewExtensionBatchStage(stageGrid, nextFrames);
          persistVideoPreviewExtensionBatchState(stageGrid);
        }
      } catch (_) {
        // 恢复视频归档失败时保留截图，不触发生成或误报失败。
      }
    }

    function setVideoPreviewBatchVariantLoading(stageGrid, index, loading, label = '处理中') {
      const tile = stageGrid?.querySelectorAll('.video-preview-extension-variant')?.[index];
      if (!tile) return;
      tile.classList.toggle('is-loading', loading);
      if (loading) tile.classList.remove('is-failed');
      tile.querySelector('.video-preview-extension-variant-spinner')?.remove();
      tile.querySelector('.video-preview-extension-variant-status')?.remove();
      if (loading) {
        tile.insertAdjacentHTML('beforeend', `<span class="video-preview-extension-variant-spinner" aria-label="${escapeHtml(label)}"></span>
          <span class="video-preview-extension-variant-status">${escapeHtml(label)}</span>`);
      }
    }

    function updateVideoPreviewExtensionBatchFrame(stageGrid, index, patch) {
      const frames = readVideoPreviewExtensionBatchFrames(stageGrid);
      if (!frames[index]) return frames;
      const nextFrames = frames.map((frame, itemIndex) => itemIndex === index ? { ...frame, ...patch } : frame);
      stageGrid.dataset.extensionBatchFrames = JSON.stringify(nextFrames);
      persistVideoPreviewExtensionBatchState(stageGrid);
      return nextFrames;
    }

    function batchVideoProgressLabel(payload) {
      const item = Array.isArray(payload?.generationProgress?.items) ? payload.generationProgress.items[0] : null;
      if (!item) return '';
      const status = typeof formatGenerationProgressStatus === 'function'
        ? formatGenerationProgressStatus(item)
        : String(item.statusLabel || item.status || '视频生成中');
      const percent = typeof generationProgressPercent === 'function' ? generationProgressPercent(item) : 0;
      return `${status}${percent ? ` · ${percent}%` : ''}`;
    }

    async function pollVideoPreviewBatchVariantProgress(stageGrid, index, sessionId, signal) {
      while (!signal.done && isVideoPreviewExtensionBatchMode(stageGrid)) {
        try {
          const params = new URLSearchParams({ sessionId, videoCount: '1' });
          const res = await fetch(`/api/chat-status?${params.toString()}`);
          const payload = await res.json().catch(() => ({}));
          const label = res.ok ? batchVideoProgressLabel(payload) : '';
          if (label) {
            setVideoPreviewBatchVariantLoading(stageGrid, index, true, label);
            updateVideoPreviewExtensionBatchFrame(stageGrid, index, { status: 'video-generating', progressLabel: label });
          }
        } catch (_) {
          // 轮询失败不覆盖真实生成请求结果，下一轮继续尝试。
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    }

    function setVideoPreviewBatchVariantFailure(stageGrid, index, message) {
      const tile = stageGrid?.querySelectorAll('.video-preview-extension-variant')?.[index];
      if (!tile) return;
      tile.classList.remove('is-loading');
      tile.classList.add('is-failed');
      tile.innerHTML = `<span class="video-preview-extension-variant-failure-title">生成失败</span>
        <span class="video-preview-extension-variant-failure-message">${escapeHtml(String(message || '请稍后重试'))}</span>`;
    }

    function selectVideoPreviewExtensionBatchFrame(index) {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid.extension-active');
      if (isVideoPreviewExtensionBatchBusy(stageGrid)) return;
      const frames = readVideoPreviewExtensionBatchFrames(stageGrid);
      if (!frames[index]) return;
      applyVideoPreviewExtensionBatchStage(stageGrid, frames.map((frame, itemIndex) => ({ ...frame, selected: itemIndex === index })));
      persistVideoPreviewExtensionBatchState(stageGrid);
    }

    function confirmVideoPreviewExtensionBatch() {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid.extension-active');
      if (isVideoPreviewExtensionBatchBusy(stageGrid)) return;
      const frames = readVideoPreviewExtensionBatchFrames(stageGrid);
      const selected = frames.find((frame) => frame.selected);
      const stage = stageGrid?.querySelector('.video-preview-extension-stage');
      if (!selected || !stage) return;
      stage.classList.remove('batch-active');
      stage.querySelector('.video-preview-extension-variants')?.remove();
      const mediaMarkup = selected.videoUrl
        ? `<video data-frame-preview controls playsinline preload="metadata" src="${escapeHtml(selected.videoUrl)}"></video>`
        : `<img src="${escapeHtml(selected.frameUrl)}" alt="已确认截图">`;
      stage.insertAdjacentHTML('afterbegin', mediaMarkup);
      stageGrid.dataset.extensionFrameKey = selected.frameKey;
      stageGrid.dataset.extensionFrameUrl = selected.frameUrl;
      stageGrid.dataset.extensionVideoUrl = selected.videoUrl || '';
      stageGrid.dataset.extensionBatchMode = 'false';
      stageGrid.dataset.extensionBatchFrames = '[]';
      stage.querySelector('[data-extension-batch-toggle]').textContent = '批量';
      stage.querySelector('[data-extension-batch-toggle]').disabled = false;
      const key = String(stageGrid.dataset.leftVideoKey || '').trim();
      persistVideoPreviewExtensionState(key, {
        ...(loadVideoPreviewExtensionStates()[key] || {}), active: true,
        frameKey: selected.frameKey,
        frameUrl: selected.frameUrl,
        rightVideoKey: selected.userGeneratedKey || '',
        rightVideoUrl: selected.videoUrl || '',
        batchMode: false,
        batchFrames: [],
      });
      renderVideoPreviewFrameRepairActions();
      syncVideoPreviewMergeAvailability();
    }

    async function toggleVideoPreviewExtensionBatch() {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid.extension-active');
      const toggle = stageGrid?.querySelector('[data-extension-batch-toggle]');
      if (!stageGrid || !toggle || toggle.disabled) return;
      if (isVideoPreviewExtensionBatchMode(stageGrid)) return;
      const frameKey = String(stageGrid.dataset.extensionFrameKey || '').trim();
      const frameUrl = String(stageGrid.dataset.extensionFrameUrl || '').trim();
      applyVideoPreviewExtensionBatchStage(stageGrid, createVideoPreviewExtensionBatchCopies(frameKey, frameUrl));
      persistVideoPreviewExtensionBatchState(stageGrid);
    }

    async function repairVideoPreviewFrameBatch(button) {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid.extension-active');
      const frames = readVideoPreviewExtensionBatchFrames(stageGrid);
      const referencePaths = frameRepairReferencePaths();
      const customPrompt = String(state.videoPreviewModal?.frameRepairPrompt || '').trim();
      if (!frames.length || !referencePaths.length || button.disabled) return;
      button.disabled = true;
      button.textContent = '四份修图中';
      const pendingFrames = frames.map((frame) => ({ ...frame, status: 'repairing' }));
      stageGrid.dataset.extensionBatchFrames = JSON.stringify(pendingFrames);
      stageGrid.dataset.extensionBatchBusy = 'true';
      applyVideoPreviewExtensionBatchStage(stageGrid, pendingFrames);
      persistVideoPreviewExtensionBatchState(stageGrid);
      const results = await Promise.allSettled(pendingFrames.map(async (frame, index) => {
        setVideoPreviewBatchVariantLoading(stageGrid, index, true, '修图中');
        try {
          const res = await fetch('/api/user-generated-results/extension-frame/repair', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ frameKey: frame.frameKey, referencePaths, customPrompt, batch: true, variantIndex: index + 1 }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) throw new Error(data.error || '修图失败');
          return { frameKey: data.frameKey, frameUrl: data.frameUrl };
        } finally {
          setVideoPreviewBatchVariantLoading(stageGrid, index, false);
        }
      }));
      const failed = results.filter((item) => item.status === 'rejected');
      stageGrid.dataset.extensionBatchBusy = 'false';
      if (failed.length) {
        const settledFrames = results.map((item, index) => item.status === 'fulfilled'
          ? { ...item.value, status: 'completed' }
          : { ...pendingFrames[index], status: 'failed' });
        applyVideoPreviewExtensionBatchStage(stageGrid, settledFrames);
        persistVideoPreviewExtensionBatchState(stageGrid);
        window.alert(`${failed.length}/4 份修图失败：${failed[0].reason?.message || '请稍后重试'}`);
        return;
      }
      const repaired = results.map((item) => ({ ...item.value, status: 'completed' }));
      applyVideoPreviewExtensionBatchStage(stageGrid, repaired);
      persistVideoPreviewExtensionBatchState(stageGrid);
    }

    function showVideoPreviewBatchVideos(stageGrid, videos) {
      let frames = readVideoPreviewExtensionBatchFrames(stageGrid);
      videos.forEach((item, fallbackIndex) => {
        const index = Number.isInteger(item.index) ? item.index : fallbackIndex;
        if (!frames[index]) return;
        frames = frames.map((frame, itemIndex) => itemIndex === index ? {
          ...frame,
          status: 'completed',
          progressLabel: '',
          userGeneratedKey: item.userGeneratedKey,
          videoUrl: item.videoUrl,
        } : frame);
      });
      applyVideoPreviewExtensionBatchStage(stageGrid, frames);
      persistVideoPreviewExtensionBatchState(stageGrid);
    }

    async function generateVideoPreviewExtensionBatch(userGeneratedKey, button) {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid.extension-active');
      const frames = readVideoPreviewExtensionBatchFrames(stageGrid);
      if (!frames.length || button.disabled) return;
      button.disabled = true;
      button.textContent = '四份生成中';
      stageGrid.dataset.extensionBatchBusy = 'true';
      applyVideoPreviewExtensionBatchStage(stageGrid, frames);
      try {
        const prompt = await postVideoPrompt(userGeneratedKey);
        if (!String(prompt.text || '').trim()) throw new Error('当前没有视频提示词，请先编辑并保存');
        const sessionIdBase = String(state.activeId || '').trim() || `extension-${Date.now()}`;
        const pendingFrames = frames.map((frame, index) => ({
          ...frame,
          status: 'video-generating',
          progressLabel: '视频生成中',
          videoSessionId: `${sessionIdBase}-batch-${index + 1}`,
        }));
        applyVideoPreviewExtensionBatchStage(stageGrid, pendingFrames);
        persistVideoPreviewExtensionBatchState(stageGrid);
        const results = await Promise.allSettled(pendingFrames.map(async (frame, index) => {
          const signal = { done: false };
          void pollVideoPreviewBatchVariantProgress(stageGrid, index, frame.videoSessionId, signal);
          setVideoPreviewBatchVariantLoading(stageGrid, index, true, '视频生成中');
          try {
            const res = await fetch('/api/user-generated-results/extension-video/generate', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                userGeneratedKey,
                sessionId: frame.videoSessionId,
                frameKey: frame.frameKey,
              }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) throw new Error(data.error || '生成视频失败');
            return { ...data, index };
          } finally {
            signal.done = true;
            setVideoPreviewBatchVariantLoading(stageGrid, index, false);
          }
        }));
        const videos = results.filter((item) => item.status === 'fulfilled').map((item) => item.value);
        const failed = results.filter((item) => item.status === 'rejected');
        if (!hasActiveVideoPreviewExtensionState(userGeneratedKey)) {
          await Promise.allSettled(videos.map((video) => discardDetachedVideoPreviewExtensionResult(userGeneratedKey, video.userGeneratedKey)));
          return;
        }
        if (videos.length) showVideoPreviewBatchVideos(stageGrid, videos);
        results.forEach((item, index) => {
          if (item.status === 'rejected') {
            updateVideoPreviewExtensionBatchFrame(stageGrid, index, { status: 'failed', progressLabel: '' });
            setVideoPreviewBatchVariantFailure(stageGrid, index, item.reason?.message);
          }
        });
        await refreshUserGeneratedResults();
        if (failed.length) {
          button.disabled = false;
          button.textContent = '生成视频';
          throw new Error(`${failed.length}/4 份生成失败：${failed[0].reason?.message || '请稍后重试'}`);
        }
        button.textContent = '已生成 4 份';
      } catch (error) {
        if (button.textContent !== '已生成 4 份') {
          button.disabled = false;
          button.textContent = '生成视频';
        }
        window.alert(error?.message || '批量生成视频失败');
      } finally {
        stageGrid.dataset.extensionBatchBusy = 'false';
        if (isVideoPreviewExtensionBatchMode(stageGrid)) syncVideoPreviewExtensionBatchControls(stageGrid);
      }
    }

    els.videoPreviewBody?.addEventListener('click', (event) => {
      const select = event.target?.closest?.('[data-extension-batch-select]');
      if (select) {
        selectVideoPreviewExtensionBatchFrame(Number(select.dataset.extensionBatchSelect));
        return;
      }
      if (event.target?.closest?.('[data-extension-batch-toggle]')) {
        if (isVideoPreviewExtensionBatchMode()) confirmVideoPreviewExtensionBatch();
        else void toggleVideoPreviewExtensionBatch();
      }
    });
