    function beginTtsChunkDrag(event, element, duration) {
      const index = Number(element.dataset.chunkIndex);
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const item = chunks[index];
      const lane = element.closest('.video-preview-tts-chunk-lane');
      if (event.button !== 0 || !item || !lane || element.disabled || isTtsScissorMode() || timelineHistoryBusy()) return;
      delete element.dataset.suppressTtsClick;
      const currentSelection = currentTtsSelectedChunkIndexes();
      if (!currentSelection.includes(index)) setTtsSelectedChunkIndex(index);
      const selectedIndexes = currentTtsSelectedChunkIndexes();
      const selectedSet = new Set(selectedIndexes);
      const selectedItems = selectedIndexes.map((selectedIndex) => ({
        index: selectedIndex,
        item: chunks[selectedIndex],
        originStart: Number(chunks[selectedIndex]?.startSeconds || 0),
      })).filter((entry) => entry.item);
      event.preventDefault();
      element.setPointerCapture(event.pointerId);
      timelineBeginPointerInteraction();
      const originX = event.clientX;
      const originStart = Number(item.startSeconds || 0);
      const historyBefore = captureTimelineHistorySnapshot();
      let minimumDelta = -Math.min(...selectedItems.map((entry) => entry.originStart));
      let maximumDelta = duration - Math.max(...selectedItems.map((entry) => (
        entry.originStart + Number(entry.item.durationSeconds || 0)
      )));
      selectedItems.forEach((entry) => {
        const originEnd = entry.originStart + Number(entry.item.durationSeconds || 0);
        const previousEnd = chunks.reduce((latest, chunk, chunkIndex) => {
          if (selectedSet.has(chunkIndex)) return latest;
          const end = Number(chunk.startSeconds || 0) + Number(chunk.durationSeconds || 0);
          return end <= entry.originStart ? Math.max(latest, end) : latest;
        }, 0);
        const nextStart = chunks.reduce((earliest, chunk, chunkIndex) => {
          if (selectedSet.has(chunkIndex)) return earliest;
          const start = Number(chunk.startSeconds || 0);
          return start >= originEnd ? Math.min(earliest, start) : earliest;
        }, duration);
        minimumDelta = Math.max(minimumDelta, previousEnd - entry.originStart);
        maximumDelta = Math.min(maximumDelta, nextStart - originEnd);
      });
      const snapPoints = timelineBuildSnapPoints(duration, {
        excludeTrack: 'tts',
        excludeIndexes: selectedIndexes,
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
          { minimum: originStart + minimumDelta, maximum: originStart + maximumDelta, points: snapPoints },
        );
        const resolvedDelta = resolved.startSeconds - originStart;
        selectedItems.forEach((entry) => {
          entry.item.startSeconds = timelineRoundSeconds(entry.originStart + resolvedDelta);
          entry.item.endSeconds = timelineRoundSeconds(entry.item.startSeconds + Number(entry.item.durationSeconds || 0));
          const chunkElement = lane.querySelector(`[data-video-preview-tts-chunk][data-chunk-index="${entry.index}"]`);
          if (!chunkElement) return;
          chunkElement.style.left = `${entry.item.startSeconds / duration * 100}%`;
          chunkElement.querySelector('small').textContent = `${entry.item.startSeconds.toFixed(1)}s`;
        });
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
      let ended = false;
      const end = (endEvent) => {
        if (ended) return;
        ended = true;
        element.removeEventListener('pointermove', move);
        element.removeEventListener('pointerup', end);
        element.removeEventListener('pointercancel', end);
        element.removeEventListener('lostpointercapture', end);
        if (endEvent.type !== 'lostpointercapture' && element.hasPointerCapture?.(event.pointerId)) {
          element.releasePointerCapture(event.pointerId);
        }
        timelineClearSnapGuide(lane);
        timelineEndPointerInteraction();
        if (endEvent.type !== 'pointerup') {
          selectedItems.forEach((entry) => {
            entry.item.startSeconds = entry.originStart;
            entry.item.endSeconds = timelineRoundSeconds(entry.originStart + Number(entry.item.durationSeconds || 0));
          });
          renderCurrentTtsTimeline();
          const video = els.videoPreviewBody?.querySelector('video');
          if (video) video.currentTime = originStart;
          syncTtsTimelinePlayhead();
          syncHtmlMotionTimelinePlayhead();
          syncTimelineBoundaryUi();
          setTtsTimelineStatus('已取消配音移动');
          return;
        }
        if (!dragged) {
          return;
        }
        element.dataset.suppressTtsClick = 'true';
        const changed = recordTimelineHistory('tts', `移动 ${selectedItems.length} 个配音块`, historyBefore);
        if (!changed) return setTtsTimelineStatus('配音位置未变化');
        void previewTtsTimeline(
          currentVideoPreviewUserGeneratedKey(),
          `已移动 ${selectedItems.length} 个配音块`,
        );
      };
      element.addEventListener('pointermove', move);
      element.addEventListener('pointerup', end);
      element.addEventListener('pointercancel', end);
      element.addEventListener('lostpointercapture', end);
    }

    function beginHtmlMotionChunkDrag(event, element, duration) {
      const index = Number(element.dataset.chunkIndex);
      const chunks = state.videoPreviewModal?.htmlMotionTimelineChunks || [];
      const item = chunks[index];
      const lane = element.closest('.video-preview-html-motion-chunk-lane');
      if (event.button !== 0 || !item || !lane || element.disabled || isHtmlMotionScissorMode() || timelineHistoryBusy()) return;
      const currentSelection = currentHtmlMotionSelectedChunkIndexes();
      if (!currentSelection.includes(index)) setHtmlMotionSelectedChunkIndex(index);
      const selectedIndexes = currentHtmlMotionSelectedChunkIndexes();
      const selectedItems = selectedIndexes.map((selectedIndex) => ({
        index: selectedIndex,
        item: chunks[selectedIndex],
        originStart: Number(chunks[selectedIndex]?.startSeconds || 0),
      })).filter((entry) => entry.item);
      event.preventDefault();
      element.setPointerCapture(event.pointerId);
      timelineBeginPointerInteraction();
      const originX = event.clientX;
      const originStart = Number(item.startSeconds || 0);
      const historyBefore = captureTimelineHistorySnapshot();
      const minimumDelta = -Math.min(...selectedItems.map((entry) => entry.originStart));
      const maximumDelta = Math.max(0, duration - Math.max(...selectedItems.map((entry) => (
        entry.originStart + Number(entry.item.durationSeconds || 0.1)
      ))));
      const snapPoints = timelineBuildSnapPoints(duration, {
        excludeTrack: 'html',
        excludeIndexes: selectedIndexes,
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
          { minimum: originStart + minimumDelta, maximum: originStart + maximumDelta, points: snapPoints },
        );
        const resolvedDelta = resolved.startSeconds - originStart;
        selectedItems.forEach((entry) => {
          entry.item.startSeconds = timelineRoundSeconds(entry.originStart + resolvedDelta);
          entry.item.endSeconds = timelineRoundSeconds(entry.item.startSeconds + Number(entry.item.durationSeconds || 0.1));
          const chunkElement = lane.querySelector(`[data-video-preview-html-motion-chunk][data-chunk-index="${entry.index}"]`);
          if (!chunkElement) return;
          chunkElement.style.left = `${entry.item.startSeconds / duration * 100}%`;
          chunkElement.querySelector('small').textContent = `${entry.item.startSeconds.toFixed(1)}s`;
        });
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
          selectedItems.forEach((entry) => {
            entry.item.startSeconds = entry.originStart;
            entry.item.endSeconds = timelineRoundSeconds(entry.originStart + Number(entry.item.durationSeconds || 0.1));
          });
          renderCurrentHtmlMotionTimeline();
          const video = els.videoPreviewBody?.querySelector('video');
          if (video) video.currentTime = originStart;
          syncLiveHtmlMotionPreview(video);
          syncHtmlMotionTimelinePlayhead();
          syncTimelineBoundaryUi();
          setHtmlMotionTimelineStatus('已取消动效移动');
          return;
        }
        if (dragged) {
          const changed = recordTimelineHistory('html', `移动 ${selectedItems.length} 个动效片段`, historyBefore);
          if (!changed) return setHtmlMotionTimelineStatus('动效位置未变化');
          commitLocalHtmlMotionTimeline(`已移动 ${selectedItems.length} 个动效片段`);
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
          const width = Math.max(0.1, item.durationSeconds) / duration * 100;
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
