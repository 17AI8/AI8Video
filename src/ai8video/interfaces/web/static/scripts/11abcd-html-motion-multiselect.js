    function currentHtmlMotionSelectedChunkIndexes() {
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const stored = state.videoPreviewModal?.htmlMotionSelectedChunkIndexes;
      const fallback = state.videoPreviewModal?.htmlMotionSelectedChunkIndex;
      const values = Array.isArray(stored) ? stored : (Number.isInteger(fallback) ? [fallback] : []);
      return [...new Set(values)]
        .filter((index) => Number.isInteger(index) && index >= 0 && index < chunks.length)
        .sort((left, right) => left - right);
    }

    function setHtmlMotionSelectedChunkIndexes(indexes, exclusive = true) {
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const selected = [...new Set(Array.isArray(indexes) ? indexes : [])]
        .filter((index) => Number.isInteger(index) && index >= 0 && index < chunks.length)
        .sort((left, right) => left - right);
      if (selected.length && exclusive) {
        setVideoSelectedChunkIndex(null, false);
        setTtsSelectedChunkIndex(null, false);
      }
      if (state.videoPreviewModal) {
        state.videoPreviewModal.htmlMotionSelectedChunkIndexes = selected;
        state.videoPreviewModal.htmlMotionSelectedChunkIndex = selected[0] ?? null;
      }
      const selectedSet = new Set(selected);
      htmlMotionTimelinePanel()?.querySelectorAll('[data-video-preview-html-motion-chunk]').forEach((element) => {
        const active = selectedSet.has(Number(element.dataset.chunkIndex));
        element.classList.toggle('is-selected', active);
        element.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      syncHtmlMotionDeleteButton();
      syncLiveHtmlMotionPreview(els.videoPreviewBody?.querySelector('video'));
    }

    function toggleHtmlMotionChunkSelection(index) {
      const selected = new Set(currentHtmlMotionSelectedChunkIndexes());
      if (selected.has(index)) selected.delete(index);
      else selected.add(index);
      setHtmlMotionSelectedChunkIndexes([...selected]);
    }

    function bindHtmlMotionMarqueeSelection(lane) {
      if (!lane) return;
      lane.addEventListener('pointerdown', (event) => {
        const interactive = event.target.closest?.('[data-video-preview-html-motion-chunk], [data-video-preview-html-motion-playhead], [data-timeline-trim-handle]');
        if (event.button !== 0 || interactive || isHtmlMotionScissorMode() || timelineHistoryBusy()) return;
        const bounds = lane.getBoundingClientRect();
        const additive = event.shiftKey || event.metaKey || event.ctrlKey;
        const initial = additive ? currentHtmlMotionSelectedChunkIndexes() : [];
        const marquee = lane.querySelector('[data-video-preview-html-motion-marquee]');
        const originX = Math.min(bounds.width, Math.max(0, event.clientX - bounds.left));
        const originY = Math.min(bounds.height, Math.max(0, event.clientY - bounds.top));
        let moved = false;
        lane.setPointerCapture(event.pointerId);
        const move = (moveEvent) => {
          const currentX = Math.min(bounds.width, Math.max(0, moveEvent.clientX - bounds.left));
          const currentY = Math.min(bounds.height, Math.max(0, moveEvent.clientY - bounds.top));
          if (!moved && Math.hypot(currentX - originX, currentY - originY) < 3) return;
          moved = true;
          const left = Math.min(originX, currentX);
          const top = Math.min(originY, currentY);
          const right = Math.max(originX, currentX);
          const bottom = Math.max(originY, currentY);
          if (marquee) {
            marquee.hidden = false;
            Object.assign(marquee.style, { left: `${left}px`, top: `${top}px`, width: `${right - left}px`, height: `${bottom - top}px` });
          }
          const hits = Array.from(lane.querySelectorAll('[data-video-preview-html-motion-chunk]'))
            .filter((chunk) => {
              const rect = chunk.getBoundingClientRect();
              return rect.right >= bounds.left + left && rect.left <= bounds.left + right
                && rect.bottom >= bounds.top + top && rect.top <= bounds.top + bottom;
            })
            .map((chunk) => Number(chunk.dataset.chunkIndex));
          setHtmlMotionSelectedChunkIndexes([...initial, ...hits]);
        };
        const end = () => {
          lane.removeEventListener('pointermove', move);
          lane.removeEventListener('pointerup', end);
          lane.removeEventListener('pointercancel', end);
          if (marquee) marquee.hidden = true;
          if (moved) lane.dataset.timelineIgnoreClick = 'true';
          if (!moved && !additive) setHtmlMotionSelectedChunkIndexes([]);
          const count = currentHtmlMotionSelectedChunkIndexes().length;
          if (count) setHtmlMotionTimelineStatus(`已选择 ${count} 个动效片段`);
        };
        lane.addEventListener('pointermove', move);
        lane.addEventListener('pointerup', end);
        lane.addEventListener('pointercancel', end);
      });
      lane.addEventListener('keydown', (event) => {
        if (!['Delete', 'Backspace'].includes(event.key) || !currentHtmlMotionSelectedChunkIndexes().length) return;
        event.preventDefault();
        deleteSelectedHtmlMotionChunk();
      });
    }

    function bindTtsMarqueeSelection(lane) {
      if (!lane) return;
      lane.addEventListener('pointerdown', (event) => {
        const interactive = event.target.closest?.('[data-video-preview-tts-chunk], [data-video-preview-tts-playhead], [data-timeline-trim-handle]');
        if (event.button !== 0 || interactive || isTtsScissorMode() || timelineHistoryBusy()) return;
        const bounds = lane.getBoundingClientRect();
        const additive = event.shiftKey || event.metaKey || event.ctrlKey;
        const initial = additive ? currentTtsSelectedChunkIndexes() : [];
        const marquee = lane.querySelector('[data-video-preview-tts-marquee]');
        const originX = Math.min(bounds.width, Math.max(0, event.clientX - bounds.left));
        const originY = Math.min(bounds.height, Math.max(0, event.clientY - bounds.top));
        let moved = false;
        lane.setPointerCapture(event.pointerId);
        const move = (moveEvent) => {
          const currentX = Math.min(bounds.width, Math.max(0, moveEvent.clientX - bounds.left));
          const currentY = Math.min(bounds.height, Math.max(0, moveEvent.clientY - bounds.top));
          if (!moved && Math.hypot(currentX - originX, currentY - originY) < 3) return;
          moved = true;
          const left = Math.min(originX, currentX);
          const top = Math.min(originY, currentY);
          const right = Math.max(originX, currentX);
          const bottom = Math.max(originY, currentY);
          if (marquee) {
            marquee.hidden = false;
            Object.assign(marquee.style, { left: `${left}px`, top: `${top}px`, width: `${right - left}px`, height: `${bottom - top}px` });
          }
          const hits = Array.from(lane.querySelectorAll('[data-video-preview-tts-chunk]'))
            .filter((chunk) => {
              const rect = chunk.getBoundingClientRect();
              return rect.right >= bounds.left + left && rect.left <= bounds.left + right
                && rect.bottom >= bounds.top + top && rect.top <= bounds.top + bottom;
            })
            .map((chunk) => Number(chunk.dataset.chunkIndex));
          setTtsSelectedChunkIndexes([...initial, ...hits]);
        };
        let ended = false;
        const end = (endEvent) => {
          if (ended) return;
          ended = true;
          lane.removeEventListener('pointermove', move);
          lane.removeEventListener('pointerup', end);
          lane.removeEventListener('pointercancel', end);
          lane.removeEventListener('lostpointercapture', end);
          if (endEvent?.type !== 'lostpointercapture' && lane.hasPointerCapture?.(event.pointerId)) {
            lane.releasePointerCapture(event.pointerId);
          }
          if (marquee) marquee.hidden = true;
          if (moved) lane.dataset.timelineIgnoreClick = 'true';
          if (!moved && !additive) setTtsSelectedChunkIndexes([]);
          const count = currentTtsSelectedChunkIndexes().length;
          setTtsTimelineStatus(count
            ? `已选择 ${count} 个配音块`
            : ttsTimelineDefaultStatus(state.videoPreviewModal?.burnReview?.tts || {}));
        };
        lane.addEventListener('pointermove', move);
        lane.addEventListener('pointerup', end);
        lane.addEventListener('pointercancel', end);
        lane.addEventListener('lostpointercapture', end);
      });
      lane.addEventListener('click', (event) => {
        if (lane.dataset.timelineIgnoreClick === 'true') {
          delete lane.dataset.timelineIgnoreClick;
          return;
        }
        const interactive = event.target.closest?.('[data-video-preview-tts-chunk], [data-video-preview-tts-playhead], [data-timeline-trim-handle]');
        if (interactive || event.shiftKey || event.metaKey || event.ctrlKey || isTtsScissorMode()) return;
        setTtsSelectedChunkIndexes([]);
        setTtsTimelineStatus(ttsTimelineDefaultStatus(state.videoPreviewModal?.burnReview?.tts || {}));
      });
      lane.addEventListener('keydown', (event) => {
        if (!['Delete', 'Backspace'].includes(event.key) || !currentTtsSelectedChunkIndexes().length) return;
        event.preventDefault();
        deleteSelectedTtsChunk(currentVideoPreviewUserGeneratedKey());
      });
    }

    async function deleteHtmlMotionTimelineTrack() {
      const key = currentVideoPreviewUserGeneratedKey();
      if (!key) return false;
      await state.videoPreviewModal?.htmlMotionPersistChain?.catch(() => false);
      const response = await fetch('/api/user-generated-results/delete-html-motion-review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userGeneratedKey: key }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data?.ok === false) throw buildRequestError(data);
      const panel = htmlMotionTimelinePanel();
      clearHtmlMotionTimelineState();
      panel?.classList.remove('is-open');
      if (panel) {
        panel.hidden = true;
        panel.setAttribute('aria-hidden', 'true');
      }
      els.videoPreviewBody?.querySelector('[data-video-preview-html-motion-live]')?.remove();
      return true;
    }
