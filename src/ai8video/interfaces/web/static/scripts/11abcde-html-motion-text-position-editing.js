    let htmlMotionLiveMessageBridgeBound = false;

    function htmlMotionChunkId(chunk, index = 0) {
      const raw = String(chunk?.chunkId || '').trim();
      const normalized = raw.replace(/[^A-Za-z0-9._:-]+/g, '-').replace(/^[-._:]+|[-._:]+$/g, '').slice(0, 96);
      return normalized || `html-motion-chunk-${Number(index) + 1}`;
    }

    function createHtmlMotionChunkId(base = 'html-motion-chunk') {
      const modal = state.videoPreviewModal || {};
      modal.htmlMotionChunkIdSequence = Number(modal.htmlMotionChunkIdSequence || 0) + 1;
      const prefix = String(base || 'html-motion-chunk').replace(/[^A-Za-z0-9._:-]+/g, '-').slice(0, 64);
      return `${prefix}-split-${Date.now().toString(36)}-${modal.htmlMotionChunkIdSequence}`;
    }

    function normalizeHtmlMotionTextPosition(value) {
      const x = Number(value?.x);
      const y = Number(value?.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      return {
        x: Math.round(Math.min(100, Math.max(0, x)) * 1000) / 1000,
        y: Math.round(Math.min(100, Math.max(0, y)) * 1000) / 1000,
      };
    }

    function htmlMotionTextPositionEquals(left, right) {
      const first = normalizeHtmlMotionTextPosition(left);
      const second = normalizeHtmlMotionTextPosition(right);
      if (!first || !second) return first === second;
      return Math.abs(first.x - second.x) < 0.001 && Math.abs(first.y - second.y) < 0.001;
    }

    function currentHtmlMotionVisibleSelectedChunks(video) {
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const current = Math.max(0, Number(video?.currentTime || 0));
      return currentHtmlMotionSelectedChunkIndexes().map((index) => ({
        index,
        chunk: chunks[index],
      })).filter(({ chunk }) => {
        if (!chunk) return false;
        const start = Math.max(0, Number(chunk.startSeconds || 0));
        const end = Math.max(start, Number(chunk.endSeconds ?? start + Number(chunk.durationSeconds || 0)));
        return current >= start && current < end;
      });
    }

    function htmlMotionTextPositionPreviewState(video, frame) {
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const selectedIndexes = currentHtmlMotionSelectedChunkIndexes();
      const selectedChunkIds = selectedIndexes.map((index) => htmlMotionChunkId(chunks[index], index));
      const editableChunkIds = currentHtmlMotionVisibleSelectedChunks(video)
        .map(({ index, chunk }) => htmlMotionChunkId(chunk, index));
      if (!editableChunkIds.length) frame?.classList.remove('is-text-position-editable');
      return { selectedChunkIds, editableChunkIds };
    }

    function bindLiveHtmlMotionPreviewMessages(_frame, _video) {
      if (htmlMotionLiveMessageBridgeBound) return;
      htmlMotionLiveMessageBridgeBound = true;
      window.addEventListener('message', (event) => {
        const video = els.videoPreviewBody?.querySelector('video');
        const frame = video?.closest('.video-preview-stage')?.querySelector('[data-video-preview-html-motion-live]');
        if (!frame || event.source !== frame.contentWindow) return;
        if (event.data?.type === 'ai8-motion-editability') {
          const previewState = htmlMotionTextPositionPreviewState(video, frame);
          const editable = event.data.editable === true && previewState.editableChunkIds.length > 0;
          frame.classList.toggle('is-text-position-editable', editable);
          frame.title = editable
            ? '拖动黄色虚线框内文字；位置会同步到全部所选动效片段'
            : previewState.selectedChunkIds.length
            ? '播放到所选动效片段后可编辑文字位置'
            : '先在 HTML 动效时间轴选择片段';
          return;
        }
        if (event.data?.type === 'ai8-motion-text-drag-start') {
          video?.pause();
          return;
        }
        if (event.data?.type === 'ai8-motion-text-position-change') {
          applyHtmlMotionTextPositionChange(event.data.position, video);
        }
      });
    }

    function applyHtmlMotionTextPositionChange(rawPosition, video) {
      const position = normalizeHtmlMotionTextPosition(rawPosition);
      const modal = state.videoPreviewModal;
      const selectedIndexes = currentHtmlMotionSelectedChunkIndexes();
      if (!position || !modal || !selectedIndexes.length) return;
      if (!currentHtmlMotionVisibleSelectedChunks(video).length) {
        syncLiveHtmlMotionPreview(video);
        return;
      }
      const historyBefore = captureTimelineHistorySnapshot();
      let changed = 0;
      selectedIndexes.forEach((index) => {
        const chunk = modal.htmlMotionTimelineChunks?.[index];
        if (!chunk || htmlMotionTextPositionEquals(chunk.textPosition, position)) return;
        chunk.textPosition = { ...position };
        changed += 1;
      });
      if (!changed) return setHtmlMotionTimelineStatus('文字位置未变化');
      recordTimelineHistory('html', `调整 ${selectedIndexes.length} 个动效片段文字位置`, historyBefore);
      syncLiveHtmlMotionPreview(video);
      void commitLocalHtmlMotionTimeline(`已同步 ${selectedIndexes.length} 个动效片段文字位置`);
    }
