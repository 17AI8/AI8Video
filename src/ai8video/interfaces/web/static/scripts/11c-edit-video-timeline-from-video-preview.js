    function videoTimelinePanel() {
      return els.videoPreviewBody?.querySelector('[data-video-preview-video-timeline]');
    }

    function currentVideoSelectedChunkIndex() {
      const index = state.videoPreviewModal?.videoTimelineSelectedChunkIndex;
      return Number.isInteger(index) ? index : null;
    }

    function isVideoScissorMode() {
      return state.videoPreviewModal?.videoTimelineScissorMode === true;
    }

    function videoTimelineDefaultStatus(data = {}) {
      if (data?.pending) return '已裁剪，等待确认烧录';
      if (data?.filmstripStatus === 'failed') return '缩略图读取失败，仍可切块和删除';
      return '完整视频，使用剪刀切块后选择删除';
    }

    function setVideoTimelineStatus(message, tone = '') {
      const status = videoTimelinePanel()?.querySelector('[data-video-preview-video-status]');
      if (!status) return;
      status.textContent = message;
      status.classList.remove('is-working', 'is-success', 'is-error');
      if (tone) status.classList.add(`is-${tone}`);
    }

    function setVideoSelectedChunkIndex(index) {
      const chunks = state.videoPreviewModal?.videoTimelineChunks || [];
      const selected = Number.isInteger(index) && index >= 0 && index < chunks.length ? index : null;
      if (state.videoPreviewModal) state.videoPreviewModal.videoTimelineSelectedChunkIndex = selected;
      els.videoPreviewBody?.querySelectorAll('[data-video-preview-video-chunk]').forEach((element) => {
        const active = Number(element.dataset.chunkIndex) === selected;
        element.classList.toggle('is-selected', active);
        element.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      syncVideoTimelineDeleteButton();
    }

    function syncVideoTimelineDeleteButton() {
      const button = videoTimelinePanel()?.querySelector('[data-video-preview-action="delete-selected-video-chunk"]');
      if (!button) return;
      const chunks = state.videoPreviewModal?.videoTimelineChunks || [];
      const selectedIndex = currentVideoSelectedChunkIndex();
      const selected = selectedIndex !== null && selectedIndex < chunks.length;
      const busy = state.videoPreviewModal?.videoTimelineBusy === true;
      button.disabled = busy || isVideoScissorMode() || chunks.length <= 1 || !selected;
      button.title = busy
        ? '正在生成裁剪预览'
        : isVideoScissorMode()
        ? '关闭剪刀工具后再选择要删除的片段'
        : chunks.length <= 1
        ? '至少保留一个视频片段，请先使用剪刀切块'
        : selected
        ? `删除${chunks[selectedIndex]?.label || `片段 ${selectedIndex + 1}`}`
        : '请先点击选择一个视频片段';
    }

    function setVideoScissorMode(active, options = {}) {
      const panel = videoTimelinePanel();
      const button = panel?.querySelector('[data-video-preview-action="toggle-video-scissors"]');
      const enabled = Boolean(active && panel?.classList.contains('is-open'));
      if (state.videoPreviewModal) state.videoPreviewModal.videoTimelineScissorMode = enabled;
      panel?.classList.toggle('is-scissor-mode', enabled);
      if (enabled) setVideoSelectedChunkIndex(null);
      if (button) {
        button.classList.toggle('is-active', enabled);
        button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
        button.title = enabled ? '关闭剪刀工具' : '开启剪刀工具';
      }
      if (options.render !== false) renderVideoTimelineChunks();
      if (options.updateStatus !== false) {
        setVideoTimelineStatus(enabled
          ? '剪刀工具已开启：点击胶片位置切块'
          : videoTimelineDefaultStatus(state.videoPreviewModal?.burnReview?.videoTimeline || {}));
      }
    }

    function toggleVideoScissorMode() {
      setVideoScissorMode(!isVideoScissorMode());
    }

    function packVideoTimelineChunks(chunks) {
      let outputStart = 0;
      return chunks.map((chunk, index) => {
        const duration = Math.max(0, Number(chunk.sourceEndSeconds || 0) - Number(chunk.sourceStartSeconds || 0));
        const packed = {
          ...chunk,
          index,
          label: chunks.length === 1 ? '完整视频' : `片段 ${index + 1}`,
          startSeconds: Math.round(outputStart * 1000) / 1000,
          durationSeconds: Math.round(duration * 1000) / 1000,
          endSeconds: Math.round((outputStart + duration) * 1000) / 1000,
        };
        outputStart += duration;
        return packed;
      });
    }

    function buildVideoFilmstripTiles(chunk) {
      const url = String(state.videoPreviewModal?.videoTimelineFilmstripUrl || '');
      const frameCount = Math.max(1, Number(state.videoPreviewModal?.videoTimelineFilmstripFrameCount || 1));
      const sourceDuration = Math.max(0.001, Number(state.videoPreviewModal?.videoTimelineSourceDuration || 0));
      if (!url) return '<span class="video-preview-video-frame is-empty" aria-hidden="true"></span>';
      const start = Math.max(0, Number(chunk.sourceStartSeconds || 0));
      const end = Math.max(start, Number(chunk.sourceEndSeconds || start));
      const tileCount = Math.max(1, Math.min(frameCount, Math.ceil((end - start) / sourceDuration * frameCount)));
      return Array.from({ length: tileCount }, (_, index) => {
        const sourceTime = start + (index + 0.5) / tileCount * (end - start);
        const frameIndex = Math.min(frameCount - 1, Math.max(0, Math.floor(sourceTime / sourceDuration * frameCount)));
        const position = frameCount <= 1 ? 0 : frameIndex / (frameCount - 1) * 100;
        const style = `background-image:url('${url}');background-size:${frameCount * 100}% 100%;background-position:${position}% 50%`;
        return `<span class="video-preview-video-frame" aria-hidden="true" style="${style}"></span>`;
      }).join('');
    }

    function syncVideoTimelineDurationLabels() {
      const panel = videoTimelinePanel();
      const duration = Math.max(0, Number(state.videoPreviewModal?.videoTimelineOutputDuration || 0));
      panel?.querySelector('[data-video-preview-video-duration]')?.replaceChildren(`${duration.toFixed(1)} 秒`);
    }

    function renderVideoTimelineChunks() {
      const track = videoTimelinePanel()?.querySelector('[data-video-preview-video-chunks]');
      const chunks = state.videoPreviewModal?.videoTimelineChunks || [];
      const duration = Number(state.videoPreviewModal?.videoTimelineOutputDuration || 0);
      syncVideoTimelineDurationLabels();
      if (!track || duration <= 0) return;
      const selectedIndex = currentVideoSelectedChunkIndex();
      const markup = chunks.map((chunk, index) => {
        const start = Math.max(0, Number(chunk.startSeconds || 0));
        const left = start / duration * 100;
        const width = Math.max(1.5, Number(chunk.durationSeconds || 0.1) / duration * 100);
        const label = chunk.label || `片段 ${index + 1}`;
        const action = isVideoScissorMode()
          ? `剪刀工具：点击${label}中的位置切块`
          : `选择并跳转到${label}，${start.toFixed(1)}秒`;
        return `<button type="button" class="video-preview-tts-chunk video-preview-video-chunk${index === selectedIndex ? ' is-selected' : ''}" data-video-preview-video-chunk data-chunk-index="${index}" aria-label="${escapeHtml(action)}" aria-pressed="${index === selectedIndex ? 'true' : 'false'}" title="${escapeHtml(action)}" style="left:${left}%;width:${Math.min(width, 100 - left)}%"><span class="video-preview-video-frames">${buildVideoFilmstripTiles(chunk)}</span><span class="video-preview-tts-chunk-meta"><span>${escapeHtml(label)}</span><small>${start.toFixed(1)}s</small></span></button>`;
      }).join('');
      track.innerHTML = `<div class="video-preview-tts-chunk-lane"><span class="video-preview-tts-playhead" data-video-preview-video-playhead></span>${markup}</div>`;
      track.querySelectorAll('[data-video-preview-video-chunk]').forEach((element) => {
        element.addEventListener('click', (event) => handleVideoTimelineChunkClick(event, element));
      });
      syncVideoTimelineDeleteButton();
      syncVideoTimelinePlayhead();
    }

    function handleVideoTimelineChunkClick(event, element) {
      const index = Number(element.dataset.chunkIndex);
      if (isVideoScissorMode()) {
        if (event.detail === 0) splitVideoTimelineAtPlayhead(currentVideoPreviewUserGeneratedKey());
        else splitVideoTimelineAtPointer(event, element);
        return;
      }
      const chunk = state.videoPreviewModal?.videoTimelineChunks?.[index];
      const video = els.videoPreviewBody?.querySelector('video');
      if (!chunk || !video) return;
      setVideoSelectedChunkIndex(index);
      video.pause();
      video.currentTime = Math.max(0, Number(chunk.startSeconds || 0));
      syncVideoTimelinePlayhead();
    }

    function syncVideoTimelinePlayhead() {
      const video = els.videoPreviewBody?.querySelector('video');
      const playhead = videoTimelinePanel()?.querySelector('[data-video-preview-video-playhead]');
      const duration = Number(state.videoPreviewModal?.videoTimelineOutputDuration || video?.duration || 0);
      if (!video || !playhead || duration <= 0) return;
      playhead.style.left = `${Math.min(100, Math.max(0, Number(video.currentTime || 0) / duration * 100))}%`;
    }

    function splitVideoTimelineAtTime(userGeneratedKey, currentTime) {
      if (state.videoPreviewModal?.videoTimelineBusy) return;
      const chunks = state.videoPreviewModal?.videoTimelineChunks || [];
      const current = Math.max(0, Number(currentTime || 0));
      const index = chunks.findIndex((chunk) => current > Number(chunk.startSeconds || 0) + 0.12
        && current < Number(chunk.endSeconds || 0) - 0.12);
      if (index < 0) {
        setVideoTimelineStatus('请在片段内部切块，且距边缘至少 0.12 秒', 'error');
        return;
      }
      const chunk = chunks[index];
      const offset = current - Number(chunk.startSeconds || 0);
      const sourceSplit = Number(chunk.sourceStartSeconds || 0) + offset;
      const next = packVideoTimelineChunks([
        ...chunks.slice(0, index),
        { ...chunk, sourceEndSeconds: sourceSplit },
        { ...chunk, sourceStartSeconds: sourceSplit },
        ...chunks.slice(index + 1),
      ]);
      const video = els.videoPreviewBody?.querySelector('video');
      video?.pause();
      setVideoSelectedChunkIndex(null);
      state.videoPreviewModal.videoTimelineChunks = next;
      state.videoPreviewModal.videoTimelineOutputDuration = next.reduce((sum, item) => sum + Number(item.durationSeconds || 0), 0);
      renderVideoTimelineChunks();
      void previewVideoTimeline(userGeneratedKey, `已在 ${current.toFixed(1)} 秒切块`);
    }

    function splitVideoTimelineAtPlayhead(userGeneratedKey) {
      splitVideoTimelineAtTime(userGeneratedKey, Number(els.videoPreviewBody?.querySelector('video')?.currentTime || 0));
    }

    function splitVideoTimelineAtPointer(event, element) {
      const bounds = element.closest('.video-preview-tts-chunk-lane')?.getBoundingClientRect();
      const duration = Number(state.videoPreviewModal?.videoTimelineOutputDuration || 0);
      if (!bounds || bounds.width <= 0 || duration <= 0) return;
      event.preventDefault();
      const current = Math.round(Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)) * duration * 1000) / 1000;
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) {
        video.pause();
        video.currentTime = current;
      }
      syncVideoTimelinePlayhead();
      splitVideoTimelineAtTime(currentVideoPreviewUserGeneratedKey(), current);
    }

    function deleteSelectedVideoChunk(userGeneratedKey) {
      if (state.videoPreviewModal?.videoTimelineBusy || isVideoScissorMode()) return;
      const chunks = state.videoPreviewModal?.videoTimelineChunks || [];
      const index = currentVideoSelectedChunkIndex();
      if (index === null || !chunks[index]) {
        setVideoTimelineStatus('请先点击选择要删除的视频片段', 'error');
        return;
      }
      if (chunks.length <= 1) {
        setVideoTimelineStatus('至少保留一个视频片段，请先使用剪刀切块', 'error');
        return;
      }
      const removed = chunks[index];
      const next = packVideoTimelineChunks(chunks.filter((_, chunkIndex) => chunkIndex !== index));
      state.videoPreviewModal.videoTimelineChunks = next;
      state.videoPreviewModal.videoTimelineOutputDuration = next.reduce((sum, item) => sum + Number(item.durationSeconds || 0), 0);
      setVideoSelectedChunkIndex(null);
      els.videoPreviewBody?.querySelector('video')?.pause();
      renderVideoTimelineChunks();
      void previewVideoTimeline(userGeneratedKey, `${removed.label || `片段 ${index + 1}`}已删除，后续片段已前移`);
    }

    async function resetVideoTimeline(userGeneratedKey) {
      await previewVideoTimeline(userGeneratedKey, '已恢复为完整视频', { reset: true });
    }

    function configureVideoTimeline(data = {}) {
      if (!state.videoPreviewModal) return;
      const panel = videoTimelinePanel();
      if (data?.needsLoad === true) {
        state.videoPreviewModal.videoTimelineChunks = [];
        state.videoPreviewModal.videoTimelineSourceDuration = 0;
        state.videoPreviewModal.videoTimelineOutputDuration = 0;
        state.videoPreviewModal.videoTimelineFilmstripUrl = '';
        state.videoPreviewModal.videoTimelineFilmstripFrameCount = 0;
        return;
      }
      const chunks = Array.isArray(data?.timelineChunks) ? data.timelineChunks.map((item) => ({ ...item })) : [];
      if (!chunks.length) return;
      state.videoPreviewModal.videoTimelineChunks = chunks;
      state.videoPreviewModal.videoTimelineSourceDuration = Math.max(0, Number(data?.sourceDurationSeconds || 0));
      state.videoPreviewModal.videoTimelineOutputDuration = Math.max(0, Number(data?.outputDurationSeconds || data?.durationSeconds || 0));
      if (data?.filmstripUrl) state.videoPreviewModal.videoTimelineFilmstripUrl = data.filmstripUrl;
      if (data?.filmstripFrameCount) state.videoPreviewModal.videoTimelineFilmstripFrameCount = data.filmstripFrameCount;
      const selectedIndex = currentVideoSelectedChunkIndex();
      state.videoPreviewModal.videoTimelineSelectedChunkIndex = selectedIndex !== null && selectedIndex < chunks.length ? selectedIndex : null;
      setVideoTimelineStatus(videoTimelineDefaultStatus(data));
      renderVideoTimelineChunks();
    }

    async function toggleVideoTimelineEditor(userGeneratedKey, button) {
      const panel = videoTimelinePanel();
      if (!panel) return;
      if (!state.videoPreviewModal?.videoTimelineFilmstripUrl) {
        button.disabled = true;
        setVideoTimelineStatus('正在生成胶片缩略图', 'working');
        try {
          const res = await fetch('/api/user-generated-results/video-timeline-review', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ userGeneratedKey }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || data?.ok === false) throw buildRequestError(data);
          configureVideoTimeline(data.videoTimeline || {});
        } catch (error) {
          setVideoTimelineStatus(error?.message || '读取视频片段失败', 'error');
          button.disabled = false;
          return;
        }
        button.disabled = false;
      }
      const open = !panel.classList.contains('is-open');
      panel.hidden = false;
      panel.classList.toggle('is-open', open);
      panel.setAttribute('aria-hidden', open ? 'false' : 'true');
      button?.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) {
        closeDependentTimelinePanels();
        syncVideoTimelinePlayhead();
      } else {
        setVideoScissorMode(false, { render: false, updateStatus: false });
        setVideoSelectedChunkIndex(null);
      }
    }

    async function toggleAllTimelineEditors(userGeneratedKey, button) {
      const videoPanel = videoTimelinePanel();
      const ttsPanel = els.videoPreviewBody?.querySelector('[data-video-preview-tts-timeline]');
      const htmlPanel = els.videoPreviewBody?.querySelector('[data-video-preview-html-motion-timeline]');
      const opening = !videoPanel?.classList.contains('is-open');
      await toggleVideoTimelineEditor(userGeneratedKey, button);
      if (!opening) {
        if (ttsPanel?.classList.contains('is-open')) await toggleTtsTimelineEditor(userGeneratedKey, null);
        if (htmlPanel?.classList.contains('is-open')) toggleHtmlMotionTimelineEditor(userGeneratedKey, null);
        button?.setAttribute('aria-expanded', 'false');
        if (button) button.title = '展开全部时间轴';
        return;
      }
      if (!videoPanel?.classList.contains('is-open')) return;
      let review = state.videoPreviewModal?.burnReview;
      if (!review) {
        review = await syncBurnReviewFromVideoPreview(
          userGeneratedKey,
          els.videoPreviewBody?.querySelector('video'),
          { showPreview: false, silent: true },
        );
      }
      if (review?.tts?.available === true && !ttsPanel?.classList.contains('is-open')) {
        await toggleTtsTimelineEditor(userGeneratedKey, null);
      }
      if (review?.htmlMotion?.timelineAdjustable === true && !htmlPanel?.classList.contains('is-open')) {
        toggleHtmlMotionTimelineEditor(userGeneratedKey, null);
      }
      button?.setAttribute('aria-expanded', 'true');
      if (button) button.title = '收起全部时间轴';
    }

    function closeDependentTimelinePanels() {
      const ttsPanel = els.videoPreviewBody?.querySelector('[data-video-preview-tts-timeline]');
      const htmlPanel = els.videoPreviewBody?.querySelector('[data-video-preview-html-motion-timeline]');
      [ttsPanel, htmlPanel].forEach((panel) => {
        panel?.classList.remove('is-open');
        if (panel) panel.hidden = true;
      });
      setTtsScissorMode(false, { render: false, updateStatus: false });
      setHtmlMotionScissorMode(false, { render: false, updateStatus: false });
      setHtmlMotionSelectedChunkIndex(null);
    }

    function syncVideoTimelineDependentEditors() {
      syncTimelineBoundaryUi();
    }

    async function previewVideoTimeline(userGeneratedKey, successMessage, options = {}) {
      const key = String(userGeneratedKey || '').trim();
      if (!key || state.videoPreviewModal?.videoTimelineBusy) return;
      state.videoPreviewModal.videoTimelineBusy = true;
      const controls = videoTimelinePanel()?.querySelectorAll('[data-video-preview-video-editor-action]') || [];
      controls.forEach((button) => { button.disabled = true; });
      setVideoTimelineStatus(options.reset ? '正在恢复完整视频预览' : '正在生成裁剪预览', 'working');
      try {
        const res = await fetch('/api/user-generated-results/video-timeline-preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            userGeneratedKey: key,
            chunks: state.videoPreviewModal?.videoTimelineChunks || [],
            reset: options.reset === true,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        setVideoSelectedChunkIndex(null);
        applyBurnReviewToVideoPreview(data.burnReview || {}, els.videoPreviewBody?.querySelector('video'));
        if (options.reset && data?.burnReview?.reviewReady !== true) restoreOfficialVideoPreview();
        setVideoTimelineStatus(options.reset ? successMessage : `${successMessage}，等待确认烧录`, 'success');
      } catch (error) {
        setVideoTimelineStatus(error?.message || '视频裁剪预览生成失败', 'error');
      } finally {
        state.videoPreviewModal.videoTimelineBusy = false;
        controls.forEach((button) => { button.disabled = false; });
        syncVideoTimelineDeleteButton();
      }
    }

    function restoreOfficialVideoPreview() {
      const video = els.videoPreviewBody?.querySelector('video');
      const official = String(video?.dataset.officialSrc || '').split('?')[0];
      if (!video || !official) return;
      video.pause();
      video.src = `${official}?preview=${Date.now()}`;
      video.load();
    }
