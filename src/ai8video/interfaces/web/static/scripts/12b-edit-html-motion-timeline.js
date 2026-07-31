    function htmlMotionTimelinePanel() {
      return els.videoPreviewBody?.querySelector('[data-video-preview-html-motion-timeline]');
    }

    function cloneHtmlMotionTimelineChunks(chunks) {
      return (Array.isArray(chunks) ? chunks : []).map((chunk) => ({ ...chunk }));
    }

    function normalizeHtmlMotionTimelineChunks(chunks) {
      return cloneHtmlMotionTimelineChunks(chunks).map((chunk, index) => {
        const start = timelineRoundSeconds(Math.max(0, Number(chunk.startSeconds || 0)));
        const restored = timelineChunkWithRestoreBounds({
          ...chunk,
          sourceStartSeconds: chunk.sourceStartSeconds ?? start,
          sourceEndSeconds: chunk.sourceEndSeconds ?? (
            Number(chunk.sourceStartSeconds ?? start) + Number(chunk.durationSeconds || 0.1)
          ),
        });
        const sourceStart = timelineRoundSeconds(Math.max(0, Number(restored.sourceStartSeconds || 0)));
        const sourceEnd = timelineRoundSeconds(Math.max(
          sourceStart + 0.1,
          Number(restored.sourceEndSeconds || 0),
        ));
        const duration = timelineRoundSeconds(Math.max(
          0.1,
          sourceEnd - sourceStart,
        ));
        const rawSourceIndex = Number(chunk.sourceIndex ?? chunk.index ?? index);
        return {
          ...restored,
          index,
          sourceIndex: Number.isInteger(rawSourceIndex) && rawSourceIndex >= 0 ? rawSourceIndex : index,
          sourceStartSeconds: sourceStart,
          sourceEndSeconds: sourceEnd,
          startSeconds: start,
          durationSeconds: duration,
          endSeconds: timelineRoundSeconds(start + duration),
        };
      });
    }

    function currentHtmlMotionSelectedChunkIndex() {
      return currentHtmlMotionSelectedChunkIndexes()[0] ?? null;
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
        : '可框选、批量拖动或删除';
    }

    function setHtmlMotionSelectedChunkIndex(index, exclusive = true) {
      setHtmlMotionSelectedChunkIndexes(Number.isInteger(index) ? [index] : [], exclusive);
    }

    function syncHtmlMotionDeleteButton() {
      const button = htmlMotionTimelinePanel()?.querySelector('[data-video-preview-action="delete-selected-html-motion-chunk"]');
      if (!button) return;
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const selected = currentHtmlMotionSelectedChunkIndexes();
      button.disabled = isHtmlMotionScissorMode() || !selected.length;
      button.title = isHtmlMotionScissorMode()
        ? '关闭剪刀工具后再选择要删除的动效片段'
        : selected.length
        ? `删除所选 ${selected.length} 个动效片段${selected.length === chunks.length ? '并移除该轴' : ''}`
        : '请先点击或框选动效片段';
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
      state.videoPreviewModal.htmlMotionTimelineRevision = 0;
      state.videoPreviewModal.htmlMotionLivePreviewUrl = '';
      state.videoPreviewModal.htmlMotionTimelineDirty = false;
      state.videoPreviewModal.htmlMotionSelectedChunkIndex = null;
      state.videoPreviewModal.htmlMotionSelectedChunkIndexes = [];
      setHtmlMotionScissorMode(false, { render: false, updateStatus: false });
    }

    function configureHtmlMotionTimeline(data = {}) {
      const panel = htmlMotionTimelinePanel();
      const incoming = normalizeHtmlMotionTimelineChunks(data?.timelineChunks || []);
      const livePreviewUrl = String(data?.livePreviewUrl || '').trim();
      if (livePreviewUrl) state.videoPreviewModal.htmlMotionLivePreviewUrl = livePreviewUrl;
      state.videoPreviewModal.htmlMotionTimelineRevision = Number(data?.revision || 0);
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
        state.videoPreviewModal.htmlMotionOriginalTimelineChunks = cloneHtmlMotionTimelineChunks(
          normalizeHtmlMotionTimelineChunks(data?.originalTimelineChunks || incoming),
        );
        state.videoPreviewModal.htmlMotionTimelineChunks = cloneHtmlMotionTimelineChunks(incoming);
        state.videoPreviewModal.htmlMotionTimelineDirty = false;
        state.videoPreviewModal.htmlMotionSelectedChunkIndex = null;
        state.videoPreviewModal.htmlMotionSelectedChunkIndexes = [];
      } else if (!state.videoPreviewModal.htmlMotionTimelineDirty) {
        state.videoPreviewModal.htmlMotionTimelineChunks = cloneHtmlMotionTimelineChunks(incoming);
      }
      panel.hidden = false;
      const open = panel.classList.contains('is-open');
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
      const selectedIndexes = new Set(currentHtmlMotionSelectedChunkIndexes());
      const scissorMode = isHtmlMotionScissorMode();
      const markup = chunks.map((chunk, index) => {
        const start = Math.max(0, Number(chunk.startSeconds || 0));
        const label = chunk.label || `动效 ${index + 1}`;
        const left = start / duration * 100;
        const width = Math.max(0.1, Number(chunk.durationSeconds || 0.1)) / duration * 100;
        const action = scissorMode
          ? `剪刀工具：点击${label}中的位置切块`
          : `选择${label}，起点 ${start.toFixed(1)}秒；拖动整体可移动，拖动左右边缘可裁剪或恢复`;
        const selected = selectedIndexes.has(index);
        return `<button type="button" class="video-preview-html-motion-chunk${selected ? ' is-selected' : ''}" data-video-preview-html-motion-chunk data-chunk-index="${index}" data-full-label="${escapeHtml(label)}" data-boundary-base-title="${escapeHtml(action)}" aria-label="${escapeHtml(action)}" aria-pressed="${selected ? 'true' : 'false'}" title="${escapeHtml(action)}" style="left:${left}%;width:${Math.min(width, 100 - left)}%;top:${1 + index % 2 * 22}px"><span>${escapeHtml(label)}</span><small>${start.toFixed(1)}s</small>${timelineTrimHandleMarkup(label)}</button>`;
      }).join('');
      const boundary = timelineBoundaryDetails();
      track.innerHTML = `${timelineRulerMarkup(duration)}<div class="video-preview-html-motion-chunk-lane" tabindex="0"><span class="video-preview-html-motion-marquee" data-video-preview-html-motion-marquee hidden></span>${timelineOverflowZoneMarkup(duration, boundary.htmlMotionOverflowIndexes, boundary)}<span class="video-preview-timeline-cut-guide" data-video-preview-cut-guide hidden></span>${timelineSnapGuideMarkup()}<span class="video-preview-tts-playhead" data-video-preview-html-motion-playhead aria-label="HTML 动效时间轴播放头" title="拖动播放头；按住 Shift 临时关闭吸附"></span>${markup}</div>`;
      const lane = track.querySelector('.video-preview-html-motion-chunk-lane');
      bindHtmlMotionMarqueeSelection(lane);
      bindTimelineScissorGuide(lane, duration, scissorMode);
      bindTimelinePlayheadDrag(
        track.querySelector('[data-video-preview-html-motion-playhead]'),
        lane,
        duration,
        'html',
      );
      track.querySelectorAll('[data-video-preview-html-motion-chunk]').forEach((element) => {
        bindHtmlMotionChunkTrimHandles(element, duration);
        element.addEventListener('pointerdown', (event) => {
          if (event.shiftKey || event.metaKey || event.ctrlKey) return;
          if (!isHtmlMotionScissorMode()) beginHtmlMotionChunkDrag(event, element, duration);
        });
        element.addEventListener('click', (event) => handleHtmlMotionChunkClick(event, element, duration));
      });
      syncHtmlMotionDeleteButton();
      syncHtmlMotionTimelinePlayhead();
      syncTimelineBoundaryUi();
    }

    function handleHtmlMotionChunkClick(event, element, duration) {
      if (isHtmlMotionScissorMode()) {
        if (event.detail === 0) splitHtmlMotionTimelineAtPlayhead();
        else splitHtmlMotionTimelineAtPointer(event, element, duration);
        return;
      }
      if (event.shiftKey || event.metaKey || event.ctrlKey) {
        toggleHtmlMotionChunkSelection(Number(element.dataset.chunkIndex));
        return;
      }
      setHtmlMotionSelectedChunkIndex(Number(element.dataset.chunkIndex));
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
        syncHtmlMotionTimelinePlayhead();
      };
      if (video.readyState >= 1) seek();
      else video.addEventListener('loadedmetadata', seek, { once: true });
    }

    function commitLocalHtmlMotionTimeline(message, dirty = true) {
      if (!state.videoPreviewModal) return Promise.resolve(false);
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
      return persistHtmlMotionTimeline(message);
    }

    function persistHtmlMotionTimeline(message) {
      const modal = state.videoPreviewModal;
      const key = currentVideoPreviewUserGeneratedKey();
      if (!modal || !key) return Promise.resolve();
      const chunks = cloneHtmlMotionTimelineChunks(modal.htmlMotionTimelineChunks || []);
      const previous = modal.htmlMotionPersistChain || Promise.resolve();
      modal.htmlMotionPersistChain = previous.catch(() => {}).then(async () => {
        const res = await fetch('/api/user-generated-results/save-html-motion-timeline', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            userGeneratedKey: key,
            chunks,
            expectedRevision: Number(modal.htmlMotionTimelineRevision || 0),
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        if (state.videoPreviewModal === modal) {
          modal.htmlMotionTimelineChunks = normalizeHtmlMotionTimelineChunks(data.timelineChunks || chunks);
          modal.htmlMotionTimelineRevision = Number(data.revision || modal.htmlMotionTimelineRevision || 0);
          setHtmlMotionTimelineStatus(`${message}，已保存，等待确认烧录`, 'success');
        }
        return true;
      }).catch((error) => {
        if (state.videoPreviewModal === modal) {
          setHtmlMotionTimelineStatus(error?.message || 'HTML 动效时间轴保存失败', 'error');
        }
        return false;
      });
      return modal.htmlMotionPersistChain;
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
      const historyBefore = captureTimelineHistorySnapshot();
      const start = Number(chunk.startSeconds || 0);
      const end = Number(chunk.endSeconds || start + Number(chunk.durationSeconds || 0));
      const sourceSplit = Number(chunk.sourceStartSeconds || 0) + current - start;
      const bounds = splitTimelineRestoreBounds(chunk, sourceSplit);
      const label = chunk.label || `动效 ${index + 1}`;
      const first = { ...bounds.first, label: `${label} A`, durationSeconds: current - start, endSeconds: current };
      const second = { ...bounds.second, label: `${label} B`, startSeconds: current, durationSeconds: end - current, endSeconds: end };
      state.videoPreviewModal.htmlMotionTimelineChunks = [
        ...chunks.slice(0, index), first, second, ...chunks.slice(index + 1),
      ];
      setHtmlMotionSelectedChunkIndex(null);
      recordTimelineHistory('html', `切分动效 ${index + 1}`, historyBefore);
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

    async function deleteSelectedHtmlMotionChunk() {
      if (isHtmlMotionScissorMode()) return;
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const selected = currentHtmlMotionSelectedChunkIndexes();
      if (!selected.length) {
        setHtmlMotionTimelineStatus('请先点击或框选要删除的动效片段', 'error');
        return;
      }
      if (selected.length === chunks.length) {
        setHtmlMotionTimelineStatus('正在删除 HTML 动效轴...', 'working');
        try {
          await deleteHtmlMotionTimelineTrack();
        } catch (error) {
          setHtmlMotionTimelineStatus(error?.message || '删除 HTML 动效轴失败', 'error');
        }
        return;
      }
      const historyBefore = captureTimelineHistorySnapshot();
      const selectedSet = new Set(selected);
      const removed = selected.map((index) => chunks[index]).filter(Boolean);
      state.videoPreviewModal.htmlMotionTimelineChunks = chunks.filter((_, position) => !selectedSet.has(position));
      setHtmlMotionSelectedChunkIndex(null);
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) video.currentTime = Math.max(0, Number(removed[0]?.startSeconds || 0));
      recordTimelineHistory('html', `删除 ${removed.length} 个动效片段`, historyBefore);
      commitLocalHtmlMotionTimeline(`已删除 ${removed.length} 个动效片段`);
    }

    function resetHtmlMotionTimeline() {
      const original = state.videoPreviewModal?.htmlMotionOriginalTimelineChunks || [];
      if (!original.length) return;
      const historyBefore = captureTimelineHistorySnapshot();
      state.videoPreviewModal.htmlMotionTimelineChunks = cloneHtmlMotionTimelineChunks(original);
      setHtmlMotionSelectedChunkIndex(null);
      const changed = recordTimelineHistory('html', '恢复完整动效', historyBefore);
      if (!changed) return setHtmlMotionTimelineStatus('当前已经是完整动效');
      commitLocalHtmlMotionTimeline('已恢复完整动效', false);
    }
