    function htmlMotionTimelinePanel() {
      return els.videoPreviewBody?.querySelector('[data-video-preview-html-motion-timeline]');
    }

    function cloneHtmlMotionTimelineChunks(chunks) {
      return (Array.isArray(chunks) ? chunks : []).map((chunk) => ({ ...chunk }));
    }

    function normalizeHtmlMotionTimelineChunks(chunks) {
      return cloneHtmlMotionTimelineChunks(chunks).map((chunk, index) => {
        const start = Math.max(0, Number(chunk.startSeconds || 0));
        const duration = Math.max(0.1, Number(chunk.durationSeconds || 0.1));
        const rawSourceIndex = Number(chunk.sourceIndex ?? chunk.index ?? index);
        return {
          ...chunk,
          index,
          sourceIndex: Number.isInteger(rawSourceIndex) && rawSourceIndex >= 0 ? rawSourceIndex : index,
          startSeconds: start,
          durationSeconds: duration,
          endSeconds: start + duration,
        };
      });
    }

    function currentHtmlMotionSelectedChunkIndex() {
      const index = state.videoPreviewModal?.htmlMotionSelectedChunkIndex;
      return Number.isInteger(index) ? index : null;
    }

    function isHtmlMotionScissorMode() {
      return state.videoPreviewModal?.htmlMotionScissorMode === true;
    }

    function setHtmlMotionTimelineStatus(message, tone = '') {
      const status = htmlMotionTimelinePanel()?.querySelector('[data-video-preview-html-motion-timeline-status]');
      if (!status) return;
      status.textContent = message;
      status.classList.remove('is-working', 'is-success', 'is-error');
      if (tone) status.classList.add(`is-${tone}`);
    }

    function htmlMotionTimelineDefaultStatus() {
      return state.videoPreviewModal?.htmlMotionTimelineDirty
        ? '已修改，等待确认烧录'
        : '可切块、删除或拖动';
    }

    function setHtmlMotionSelectedChunkIndex(index) {
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const selected = Number.isInteger(index) && index >= 0 && index < chunks.length ? index : null;
      if (state.videoPreviewModal) state.videoPreviewModal.htmlMotionSelectedChunkIndex = selected;
      htmlMotionTimelinePanel()?.querySelectorAll('[data-video-preview-html-motion-chunk]').forEach((element) => {
        const active = Number(element.dataset.chunkIndex) === selected;
        element.classList.toggle('is-selected', active);
        element.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      syncHtmlMotionDeleteButton();
    }

    function syncHtmlMotionDeleteButton() {
      const button = htmlMotionTimelinePanel()?.querySelector('[data-video-preview-action="delete-selected-html-motion-chunk"]');
      if (!button) return;
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const index = currentHtmlMotionSelectedChunkIndex();
      const selected = index !== null && index >= 0 && index < chunks.length;
      button.disabled = isHtmlMotionScissorMode() || chunks.length <= 1 || !selected;
      button.title = isHtmlMotionScissorMode()
        ? '关闭剪刀工具后再选择要删除的动效片段'
        : chunks.length <= 1
        ? '至少保留一个动效片段，请先使用剪刀切块'
        : selected
        ? `删除${chunks[index]?.label || `动效 ${index + 1}`}`
        : '请先点击选择一个动效片段';
    }

    function setHtmlMotionScissorMode(active, options = {}) {
      const panel = htmlMotionTimelinePanel();
      const button = panel?.querySelector('[data-video-preview-action="toggle-html-motion-scissors"]');
      const enabled = Boolean(active && panel?.classList.contains('is-open'));
      if (state.videoPreviewModal) state.videoPreviewModal.htmlMotionScissorMode = enabled;
      panel?.classList.toggle('is-scissor-mode', enabled);
      if (enabled) setHtmlMotionSelectedChunkIndex(null);
      if (button) {
        button.classList.toggle('is-active', enabled);
        button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
        button.title = enabled ? '关闭剪刀工具' : '开启剪刀工具';
      }
      if (options.render !== false) renderCurrentHtmlMotionTimeline();
      if (options.updateStatus !== false) {
        setHtmlMotionTimelineStatus(
          enabled ? '剪刀工具已开启：点击动效片段切块' : htmlMotionTimelineDefaultStatus(),
        );
      }
    }

    function toggleHtmlMotionScissorMode() {
      setHtmlMotionScissorMode(!isHtmlMotionScissorMode());
    }

    function htmlMotionReviewIdentity(data = {}) {
      return String(data?.reviewId || data?.preparedAt || '').trim();
    }

    function clearHtmlMotionTimelineState() {
      if (!state.videoPreviewModal) return;
      state.videoPreviewModal.htmlMotionTimelineChunks = [];
      state.videoPreviewModal.htmlMotionOriginalTimelineChunks = [];
      state.videoPreviewModal.htmlMotionTimelineReviewIdentity = '';
      state.videoPreviewModal.htmlMotionTimelineDirty = false;
      state.videoPreviewModal.htmlMotionSelectedChunkIndex = null;
      setHtmlMotionScissorMode(false, { render: false, updateStatus: false });
    }

    function configureHtmlMotionTimeline(data = {}) {
      const panel = htmlMotionTimelinePanel();
      const incoming = normalizeHtmlMotionTimelineChunks(data?.timelineChunks || []);
      const adjustable = data?.timelineAdjustable === true && incoming.length > 0;
      if (!adjustable) {
        clearHtmlMotionTimelineState();
        if (panel) {
          panel.classList.remove('is-open');
          panel.setAttribute('aria-hidden', 'true');
          panel.hidden = true;
        }
        return;
      }
      const identity = htmlMotionReviewIdentity(data);
      const changedReview = identity && identity !== state.videoPreviewModal?.htmlMotionTimelineReviewIdentity;
      if (changedReview || !state.videoPreviewModal?.htmlMotionOriginalTimelineChunks?.length) {
        state.videoPreviewModal.htmlMotionTimelineReviewIdentity = identity;
        state.videoPreviewModal.htmlMotionOriginalTimelineChunks = cloneHtmlMotionTimelineChunks(incoming);
        state.videoPreviewModal.htmlMotionTimelineChunks = cloneHtmlMotionTimelineChunks(incoming);
        state.videoPreviewModal.htmlMotionTimelineDirty = false;
        state.videoPreviewModal.htmlMotionSelectedChunkIndex = null;
      } else if (!state.videoPreviewModal.htmlMotionTimelineDirty) {
        state.videoPreviewModal.htmlMotionTimelineChunks = cloneHtmlMotionTimelineChunks(incoming);
      }
      panel.hidden = false;
      panel.setAttribute('aria-hidden', open ? 'false' : 'true');
      const video = els.videoPreviewBody?.querySelector('video');
      const duration = Math.max(0, Number(data?.durationSeconds || video?.duration || 0));
      state.videoPreviewModal.htmlMotionTimelineDuration = duration;
      panel.querySelector('[data-video-preview-html-motion-duration]')?.replaceChildren(`${duration.toFixed(1)} 秒`);
      renderCurrentHtmlMotionTimeline();
      setHtmlMotionTimelineStatus(htmlMotionTimelineDefaultStatus());
    }

    function toggleHtmlMotionTimelineEditor(_userGeneratedKey, button) {
      const panel = htmlMotionTimelinePanel();
      if (!panel) return;
      const open = !panel.classList.contains('is-open');
      panel.hidden = false;
      panel.classList.toggle('is-open', open);
      panel.setAttribute('aria-hidden', open ? 'false' : 'true');
      button?.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (!open) {
        setHtmlMotionScissorMode(false, { render: false, updateStatus: false });
        setHtmlMotionSelectedChunkIndex(null);
      }
    }

    function renderCurrentHtmlMotionTimeline() {
      renderHtmlMotionTimelineChunks(
        htmlMotionTimelinePanel()?.querySelector('[data-video-preview-html-motion-chunks]'),
        state.videoPreviewModal?.htmlMotionTimelineChunks || [],
        Number(state.videoPreviewModal?.htmlMotionTimelineDuration || 0),
      );
    }

    function renderHtmlMotionTimelineChunks(track, chunks, duration) {
      if (!track || duration <= 0) return;
      const selectedIndex = currentHtmlMotionSelectedChunkIndex();
      const scissorMode = isHtmlMotionScissorMode();
      const markup = chunks.map((chunk, index) => {
        const start = Math.max(0, Number(chunk.startSeconds || 0));
        const label = chunk.label || `动效 ${index + 1}`;
        const left = start / duration * 100;
        const width = Math.max(2, Number(chunk.durationSeconds || 0.1) / duration * 100);
        const action = scissorMode
          ? `剪刀工具：点击${label}中的位置切块`
          : `选择并跳转到${label}，${start.toFixed(1)}秒；可左右拖动调整`;
        return `<button type="button" class="video-preview-html-motion-chunk${index === selectedIndex ? ' is-selected' : ''}" data-video-preview-html-motion-chunk data-chunk-index="${index}" data-boundary-base-title="${escapeHtml(action)}" aria-label="${escapeHtml(action)}" aria-pressed="${index === selectedIndex ? 'true' : 'false'}" title="${escapeHtml(action)}" style="left:${left}%;width:${Math.min(width, 100 - left)}%;top:${1 + index % 2 * 22}px"><span>${escapeHtml(label)}</span><small>${start.toFixed(1)}s</small></button>`;
      }).join('');
      track.innerHTML = `<div class="video-preview-html-motion-chunk-lane">${timelineOverflowZoneMarkup(duration)}${markup}</div>`;
      track.querySelectorAll('[data-video-preview-html-motion-chunk]').forEach((element) => {
        element.addEventListener('pointerdown', (event) => {
          if (!isHtmlMotionScissorMode()) beginHtmlMotionChunkDrag(event, element, duration);
        });
        element.addEventListener('click', (event) => handleHtmlMotionChunkClick(event, element, duration));
      });
      syncHtmlMotionDeleteButton();
      syncTimelineBoundaryUi();
    }

    function handleHtmlMotionChunkClick(event, element, duration) {
      if (isHtmlMotionScissorMode()) {
        if (event.detail === 0) splitHtmlMotionTimelineAtPlayhead();
        else splitHtmlMotionTimelineAtPointer(event, element, duration);
        return;
      }
      if (event.detail === 0) seekVideoPreviewToHtmlMotionChunk(Number(element.dataset.chunkIndex));
    }

    function seekVideoPreviewToHtmlMotionChunk(index) {
      const item = state.videoPreviewModal?.htmlMotionTimelineChunks?.[index];
      const video = els.videoPreviewBody?.querySelector('video');
      if (!item || !video) return;
      const seek = () => {
        const start = Math.max(0, Number(item.startSeconds || 0));
        setHtmlMotionSelectedChunkIndex(index);
        video.pause();
        video.currentTime = Number.isFinite(video.duration) && video.duration > 0
          ? Math.min(start, video.duration)
          : start;
        syncLiveHtmlMotionPreview(video);
      };
      if (video.readyState >= 1) seek();
      else video.addEventListener('loadedmetadata', seek, { once: true });
    }

    function commitLocalHtmlMotionTimeline(message, dirty = true) {
      if (!state.videoPreviewModal) return;
      state.videoPreviewModal.htmlMotionTimelineChunks = normalizeHtmlMotionTimelineChunks(
        state.videoPreviewModal.htmlMotionTimelineChunks,
      );
      state.videoPreviewModal.htmlMotionTimelineDirty = dirty;
      renderCurrentHtmlMotionTimeline();
      const video = els.videoPreviewBody?.querySelector('video');
      video?.pause();
      syncLiveHtmlMotionPreview(video);
      syncTimelineBoundaryUi();
      setHtmlMotionTimelineStatus(`${message}，等待确认烧录`, 'success');
    }

    function splitHtmlMotionTimelineAtTime(currentTime) {
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const current = Math.max(0, Number(currentTime || 0));
      const index = chunks.findIndex((chunk) => current > Number(chunk.startSeconds || 0) + 0.12
        && current < Number(chunk.endSeconds || 0) - 0.12);
      if (index < 0) {
        setHtmlMotionTimelineStatus('请在动效片段内部切块，且距边缘至少 0.12 秒', 'error');
        return;
      }
      const chunk = chunks[index];
      const start = Number(chunk.startSeconds || 0);
      const end = Number(chunk.endSeconds || start + Number(chunk.durationSeconds || 0));
      const label = chunk.label || `动效 ${index + 1}`;
      const first = { ...chunk, label: `${label} A`, durationSeconds: current - start, endSeconds: current };
      const second = { ...chunk, label: `${label} B`, startSeconds: current, durationSeconds: end - current, endSeconds: end };
      state.videoPreviewModal.htmlMotionTimelineChunks = [
        ...chunks.slice(0, index), first, second, ...chunks.slice(index + 1),
      ];
      setHtmlMotionSelectedChunkIndex(null);
      commitLocalHtmlMotionTimeline(`已在 ${current.toFixed(1)} 秒切块`);
    }

    function splitHtmlMotionTimelineAtPlayhead() {
      splitHtmlMotionTimelineAtTime(Number(els.videoPreviewBody?.querySelector('video')?.currentTime || 0));
    }

    function splitHtmlMotionTimelineAtPointer(event, element, duration) {
      const bounds = element.closest('.video-preview-html-motion-chunk-lane')?.getBoundingClientRect();
      if (!bounds || bounds.width <= 0 || duration <= 0) return;
      event.preventDefault();
      const current = Math.round(Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)) * duration * 1000) / 1000;
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) {
        video.pause();
        video.currentTime = current;
      }
      splitHtmlMotionTimelineAtTime(current);
    }

    function deleteSelectedHtmlMotionChunk() {
      if (isHtmlMotionScissorMode()) return;
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const index = currentHtmlMotionSelectedChunkIndex();
      if (index === null || !chunks[index]) {
        setHtmlMotionTimelineStatus('请先点击选择要删除的动效片段', 'error');
        return;
      }
      if (chunks.length <= 1) {
        setHtmlMotionTimelineStatus('至少保留一个动效片段，请先使用剪刀切块', 'error');
        return;
      }
      const removed = chunks[index];
      state.videoPreviewModal.htmlMotionTimelineChunks = chunks.filter((_, position) => position !== index);
      setHtmlMotionSelectedChunkIndex(null);
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) video.currentTime = Math.max(0, Number(removed.startSeconds || 0));
      commitLocalHtmlMotionTimeline(`${removed.label || `动效 ${index + 1}`}已删除`);
    }

    function resetHtmlMotionTimeline() {
      const original = state.videoPreviewModal?.htmlMotionOriginalTimelineChunks || [];
      if (!original.length) return;
      state.videoPreviewModal.htmlMotionTimelineChunks = cloneHtmlMotionTimelineChunks(original);
      setHtmlMotionSelectedChunkIndex(null);
      commitLocalHtmlMotionTimeline('已恢复完整动效', false);
    }

    function beginHtmlMotionChunkDrag(event, element, duration) {
      const index = Number(element.dataset.chunkIndex);
      const item = state.videoPreviewModal?.htmlMotionTimelineChunks?.[index];
      const lane = element.closest('.video-preview-html-motion-chunk-lane');
      if (!item || !lane || element.disabled || isHtmlMotionScissorMode()) return;
      setHtmlMotionSelectedChunkIndex(index);
      event.preventDefault();
      element.setPointerCapture(event.pointerId);
      const originX = event.clientX;
      const originStart = Number(item.startSeconds || 0);
      const maxStart = Math.max(0, duration - Number(item.durationSeconds || 0.1));
      let dragged = false;
      const move = (moveEvent) => {
        const deltaX = moveEvent.clientX - originX;
        if (!dragged && Math.abs(deltaX) < 3) return;
        dragged = true;
        const delta = deltaX / Math.max(1, lane.clientWidth) * duration;
        item.startSeconds = Math.round(Math.min(Math.max(originStart + delta, 0), maxStart) * 1000) / 1000;
        item.endSeconds = item.startSeconds + Number(item.durationSeconds || 0.1);
        element.style.left = `${item.startSeconds / duration * 100}%`;
        element.querySelector('small').textContent = `${item.startSeconds.toFixed(1)}s`;
        const video = els.videoPreviewBody?.querySelector('video');
        if (video) {
          video.pause();
          video.currentTime = item.startSeconds;
          syncLiveHtmlMotionPreview(video);
        }
        syncTimelineBoundaryUi();
      };
      const end = (endEvent) => {
        element.removeEventListener('pointermove', move);
        element.removeEventListener('pointerup', end);
        element.removeEventListener('pointercancel', end);
        if (endEvent.type === 'pointercancel') return;
        if (!dragged) seekVideoPreviewToHtmlMotionChunk(index);
        else commitLocalHtmlMotionTimeline(`动效 ${index + 1} 已移动到 ${item.startSeconds.toFixed(1)} 秒`);
      };
      element.addEventListener('pointermove', move);
      element.addEventListener('pointerup', end);
      element.addEventListener('pointercancel', end);
    }
