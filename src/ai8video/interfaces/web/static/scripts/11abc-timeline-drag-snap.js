    function beginTtsChunkDrag(event, element, duration) {
      const index = Number(element.dataset.chunkIndex);
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const item = chunks[index];
      const lane = element.closest('.video-preview-tts-chunk-lane');
      if (event.button !== 0 || !item || !lane || element.disabled || isTtsScissorMode() || timelineHistoryBusy()) return;
      setTtsSelectedChunkIndex(index);
      event.preventDefault();
      element.setPointerCapture(event.pointerId);
      timelineBeginPointerInteraction();
      const originX = event.clientX;
      const originStart = Number(item.startSeconds || 0);
      const historyBefore = captureTimelineHistorySnapshot();
      const previous = chunks[index - 1];
      const next = chunks[index + 1];
      const minStart = previous ? Number(previous.startSeconds) + Number(previous.durationSeconds) : 0;
      const maxStart = (next ? Number(next.startSeconds) : duration) - Number(item.durationSeconds || 0);
      const snapPoints = timelineBuildSnapPoints(duration, {
        excludeTrack: 'tts',
        excludeIndex: index,
        playheadSeconds: timelineCurrentPlayheadSeconds(),
      });
      let dragged = false;
      const move = (moveEvent) => {
        const deltaX = moveEvent.clientX - originX;
        if (!dragged && Math.abs(deltaX) < 3) return;
        dragged = true;
        const delta = deltaX / Math.max(1, lane.clientWidth) * duration;
        const resolved = timelineResolveChunkMoveSnap(
          originStart + delta,
          Number(item.durationSeconds || 0),
          lane,
          duration,
          moveEvent,
          { minimum: minStart, maximum: maxStart, points: snapPoints },
        );
        item.startSeconds = resolved.startSeconds;
        item.endSeconds = timelineRoundSeconds(item.startSeconds + Number(item.durationSeconds || 0));
        element.style.left = `${item.startSeconds / duration * 100}%`;
        element.querySelector('small').textContent = `${item.startSeconds.toFixed(1)}s`;
        timelineSyncSnapGuide(lane, duration, resolved.snap);
        const video = els.videoPreviewBody?.querySelector('video');
        if (video) {
          video.pause();
          video.currentTime = item.startSeconds;
        }
        syncTtsTimelinePlayhead();
        syncHtmlMotionTimelinePlayhead();
        syncTimelineBoundaryUi();
      };
      const end = (endEvent) => {
        element.removeEventListener('pointermove', move);
        element.removeEventListener('pointerup', end);
        element.removeEventListener('pointercancel', end);
        element.removeEventListener('lostpointercapture', end);
        timelineClearSnapGuide(lane);
        timelineEndPointerInteraction();
        if (endEvent.type !== 'pointerup') {
          item.startSeconds = originStart;
          item.endSeconds = timelineRoundSeconds(originStart + Number(item.durationSeconds || 0));
          element.style.left = `${originStart / duration * 100}%`;
          element.querySelector('small').textContent = `${originStart.toFixed(1)}s`;
          const video = els.videoPreviewBody?.querySelector('video');
          if (video) video.currentTime = originStart;
          syncTtsTimelinePlayhead();
          syncHtmlMotionTimelinePlayhead();
          syncTimelineBoundaryUi();
          setTtsTimelineStatus('已取消配音移动');
          return;
        }
        if (!dragged) {
          seekVideoPreviewToTtsChunk(index);
          return;
        }
        const changed = recordTimelineHistory('tts', `移动配音 ${index + 1}`, historyBefore);
        if (!changed) return setTtsTimelineStatus('配音位置未变化');
        void previewTtsTimeline(
          currentVideoPreviewUserGeneratedKey(),
          `配音 ${index + 1} 已移动到 ${item.startSeconds.toFixed(1)} 秒`,
        );
      };
      element.addEventListener('pointermove', move);
      element.addEventListener('pointerup', end);
      element.addEventListener('pointercancel', end);
      element.addEventListener('lostpointercapture', end);
    }

    function beginHtmlMotionChunkDrag(event, element, duration) {
      const index = Number(element.dataset.chunkIndex);
      const item = state.videoPreviewModal?.htmlMotionTimelineChunks?.[index];
      const lane = element.closest('.video-preview-html-motion-chunk-lane');
      if (event.button !== 0 || !item || !lane || element.disabled || isHtmlMotionScissorMode() || timelineHistoryBusy()) return;
      setHtmlMotionSelectedChunkIndex(index);
      event.preventDefault();
      element.setPointerCapture(event.pointerId);
      timelineBeginPointerInteraction();
      const originX = event.clientX;
      const originStart = Number(item.startSeconds || 0);
      const historyBefore = captureTimelineHistorySnapshot();
      const maxStart = Math.max(0, duration - Number(item.durationSeconds || 0.1));
      const snapPoints = timelineBuildSnapPoints(duration, {
        excludeTrack: 'html',
        excludeIndex: index,
        playheadSeconds: timelineCurrentPlayheadSeconds(),
      });
      let dragged = false;
      const move = (moveEvent) => {
        const deltaX = moveEvent.clientX - originX;
        if (!dragged && Math.abs(deltaX) < 3) return;
        dragged = true;
        const delta = deltaX / Math.max(1, lane.clientWidth) * duration;
        const resolved = timelineResolveChunkMoveSnap(
          originStart + delta,
          Number(item.durationSeconds || 0.1),
          lane,
          duration,
          moveEvent,
          { minimum: 0, maximum: maxStart, points: snapPoints },
        );
        item.startSeconds = resolved.startSeconds;
        item.endSeconds = timelineRoundSeconds(item.startSeconds + Number(item.durationSeconds || 0.1));
        element.style.left = `${item.startSeconds / duration * 100}%`;
        element.querySelector('small').textContent = `${item.startSeconds.toFixed(1)}s`;
        timelineSyncSnapGuide(lane, duration, resolved.snap);
        const video = els.videoPreviewBody?.querySelector('video');
        if (video) {
          video.pause();
          video.currentTime = item.startSeconds;
        }
        syncHtmlMotionTimelinePlayhead();
        syncTimelineBoundaryUi();
      };
      const end = (endEvent) => {
        element.removeEventListener('pointermove', move);
        element.removeEventListener('pointerup', end);
        element.removeEventListener('pointercancel', end);
        element.removeEventListener('lostpointercapture', end);
        timelineClearSnapGuide(lane);
        timelineEndPointerInteraction();
        if (endEvent.type !== 'pointerup') {
          item.startSeconds = originStart;
          item.endSeconds = timelineRoundSeconds(originStart + Number(item.durationSeconds || 0.1));
          element.style.left = `${originStart / duration * 100}%`;
          element.querySelector('small').textContent = `${originStart.toFixed(1)}s`;
          const video = els.videoPreviewBody?.querySelector('video');
          if (video) video.currentTime = originStart;
          syncLiveHtmlMotionPreview(video);
          syncHtmlMotionTimelinePlayhead();
          syncTimelineBoundaryUi();
          setHtmlMotionTimelineStatus('已取消动效移动');
          return;
        }
        if (!dragged) seekVideoPreviewToHtmlMotionChunk(index);
        else {
          const changed = recordTimelineHistory('html', `移动动效 ${index + 1}`, historyBefore);
          if (!changed) return setHtmlMotionTimelineStatus('动效位置未变化');
          commitLocalHtmlMotionTimeline(`动效 ${index + 1} 已移动到 ${item.startSeconds.toFixed(1)} 秒`);
        }
      };
      element.addEventListener('pointermove', move);
      element.addEventListener('pointerup', end);
      element.addEventListener('pointercancel', end);
      element.addEventListener('lostpointercapture', end);
    }

    function bindHtmlMotionChunkEndTrim(element, duration) {
      const index = Number(element.dataset.chunkIndex);
      bindTimelineEndTrimHandle(element, {
        duration,
        disabled: () => isHtmlMotionScissorMode() || timelineHistoryBusy(),
        currentEnd: () => Number(state.videoPreviewModal?.htmlMotionTimelineChunks?.[index]?.sourceEndSeconds || 0),
        minimumEnd: () => Number(state.videoPreviewModal?.htmlMotionTimelineChunks?.[index]?.sourceStartSeconds || 0)
          + TIMELINE_TRIM_MIN_SECONDS,
        maximumEnd: () => {
          const item = state.videoPreviewModal?.htmlMotionTimelineChunks?.[index];
          if (!item) return 0;
          const timelineRoom = duration - Number(item.startSeconds || 0);
          return Math.min(
            Number(item.originalSourceEndSeconds || item.sourceEndSeconds || 0),
            Number(item.sourceStartSeconds || 0) + Math.max(0, timelineRoom),
          );
        },
        snapTrack: 'html',
        snapIndex: index,
        snapChunk: () => state.videoPreviewModal?.htmlMotionTimelineChunks?.[index],
        historyTrack: 'html',
        historyLabel: () => `裁剪动效 ${index + 1}`,
        preview: (nextEnd) => {
          const item = state.videoPreviewModal?.htmlMotionTimelineChunks?.[index];
          if (!item) return;
          item.sourceEndSeconds = nextEnd;
          item.durationSeconds = timelineRoundSeconds(nextEnd - Number(item.sourceStartSeconds || 0));
          item.endSeconds = timelineRoundSeconds(Number(item.startSeconds || 0) + item.durationSeconds);
          const left = Number(item.startSeconds || 0) / duration * 100;
          const width = Math.max(2, item.durationSeconds / duration * 100);
          element.style.width = `${Math.min(width, 100 - left)}%`;
          element.title = `动效结尾 ${item.endSeconds.toFixed(2)} 秒，释放后保存`;
          const video = els.videoPreviewBody?.querySelector('video');
          if (video) {
            video.pause();
            video.currentTime = Math.min(item.endSeconds, Number(video.duration || item.endSeconds));
          }
          syncLiveHtmlMotionPreview(video);
          syncHtmlMotionTimelinePlayhead();
          syncTimelineBoundaryUi();
          setHtmlMotionTimelineStatus(`动效结尾 ${item.endSeconds.toFixed(2)} 秒，释放后保存`, 'working');
        },
        cancel: () => {
          renderCurrentHtmlMotionTimeline();
          setHtmlMotionTimelineStatus('已取消动效裁剪');
        },
        commit: () => {
          const item = state.videoPreviewModal?.htmlMotionTimelineChunks?.[index];
          commitLocalHtmlMotionTimeline(
            `动效 ${index + 1} 已裁剪到 ${Number(item?.endSeconds || 0).toFixed(2)} 秒`,
          );
        },
      });
    }
