    function bindTimelineStartTrimHandle(element, options = {}) {
      const handle = element?.querySelector('[data-video-preview-timeline-trim-handle="start"]');
      const lane = element?.parentElement;
      const duration = Math.max(0, Number(options.duration || 0));
      if (!handle || !lane || duration <= 0) return;
      handle.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
      });
      handle.addEventListener('pointerdown', (event) => beginTimelineStartTrim(
        handle, lane, duration, options, event,
      ));
    }

    function beginTimelineStartTrim(handle, lane, duration, options, event) {
      if (event.button !== 0 || options.disabled?.()) return;
      event.preventDefault();
      event.stopPropagation();
      const originX = event.clientX;
      const originStart = Number(options.currentStart?.() || 0);
      const historyBefore = options.historyTrack ? captureTimelineHistorySnapshot() : null;
      const snapPoints = timelineBuildSnapPoints(duration, {
        excludeTrack: options.snapTrack,
        excludeIndex: options.snapIndex,
        playheadSeconds: timelineCurrentPlayheadSeconds(),
      });
      let moved = false;
      handle.setPointerCapture(event.pointerId);
      timelineBeginPointerInteraction();
      const move = (moveEvent) => {
        const deltaX = moveEvent.clientX - originX;
        if (!moved && Math.abs(deltaX) < 2) return;
        moved = true;
        const delta = deltaX / Math.max(1, lane.clientWidth) * duration;
        const minimum = Number(options.minimumStart?.() || 0);
        const maximum = Math.max(minimum, Number(options.maximumStart?.() || minimum));
        const resolved = timelineResolveTrimSnap(originStart + delta, lane, duration, moveEvent, {
          minimum,
          maximum,
          track: options.snapTrack,
          edge: 'start',
          chunk: options.snapChunk?.(),
          points: snapPoints,
        });
        timelineSyncSnapGuide(lane, duration, resolved.snap);
        options.preview?.(resolved.sourceSeconds, moveEvent, originStart);
      };
      const finish = (finishEvent) => {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', finish);
        handle.removeEventListener('pointercancel', finish);
        handle.removeEventListener('lostpointercapture', finish);
        timelineClearSnapGuide(lane);
        timelineEndPointerInteraction();
        if (!moved) return;
        if (finishEvent.type !== 'pointerup') {
          options.preview?.(originStart, finishEvent, originStart);
          options.cancel?.();
          return;
        }
        options.finalize?.();
        if (historyBefore && !recordTimelineHistory(
          options.historyTrack,
          options.historyLabel?.(),
          historyBefore,
        )) return options.cancel?.();
        options.commit?.();
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', finish);
      handle.addEventListener('pointercancel', finish);
      handle.addEventListener('lostpointercapture', finish);
    }

    function bindTtsChunkTrimHandles(element, duration) {
      bindTtsChunkStartTrim(element, duration);
      bindTtsChunkEndTrim(element, duration);
    }

    function bindTtsChunkStartTrim(element, duration) {
      const index = Number(element.dataset.chunkIndex);
      bindTimelineStartTrimHandle(element, {
        duration,
        disabled: () => isTtsScissorMode() || timelineHistoryBusy(),
        currentStart: () => Number(state.videoPreviewModal?.ttsTimelineChunks?.[index]?.sourceStartSeconds || 0),
        minimumStart: () => ttsChunkMinimumTrimStart(index),
        maximumStart: () => Number(state.videoPreviewModal?.ttsTimelineChunks?.[index]?.sourceEndSeconds || 0)
          - TIMELINE_TRIM_MIN_SECONDS,
        snapTrack: 'tts',
        snapIndex: index,
        snapChunk: () => state.videoPreviewModal?.ttsTimelineChunks?.[index],
        historyTrack: 'tts',
        historyLabel: () => `裁剪配音 ${index + 1} 开头`,
        preview: (nextStart) => previewTtsChunkStartTrim(element, index, duration, nextStart),
        cancel: () => {
          renderCurrentTtsTimeline();
          setTtsTimelineStatus('已取消配音裁剪');
        },
        commit: () => commitTtsChunkStartTrim(index),
      });
    }

    function ttsChunkMinimumTrimStart(index) {
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const item = chunks[index];
      if (!item) return 0;
      const previous = chunks[index - 1];
      const timelineFloor = previous ? Number(previous.endSeconds || 0) : 0;
      const sourceFloor = previous ? Number(previous.sourceEndSeconds || 0) : 0;
      const fixedEnd = Number(item.endSeconds || 0);
      const sourceEnd = Number(item.sourceEndSeconds || 0);
      return Math.max(
        Number(item.originalSourceStartSeconds ?? item.sourceStartSeconds ?? 0),
        sourceFloor,
        sourceEnd - Math.max(0, fixedEnd - timelineFloor),
      );
    }

    function previewTtsChunkStartTrim(element, index, duration, nextStart) {
      const item = state.videoPreviewModal?.ttsTimelineChunks?.[index];
      if (!item) return;
      const fixedEnd = Number(item.endSeconds || 0);
      item.sourceStartSeconds = nextStart;
      item.durationSeconds = timelineRoundSeconds(Number(item.sourceEndSeconds || 0) - nextStart);
      item.startSeconds = timelineRoundSeconds(fixedEnd - item.durationSeconds);
      const geometry = timelineFixedContentGeometry(item, duration);
      element.style.left = `${geometry.left}%`;
      element.style.width = `${geometry.width}%`;
      element.style.setProperty('--video-preview-tts-waveform-scale', `${geometry.contentScale}%`);
      element.style.setProperty('--video-preview-tts-waveform-offset', `${geometry.contentOffset}%`);
      const meta = element.querySelector('small');
      if (meta) meta.textContent = `${item.startSeconds.toFixed(1)}s`;
      element.title = `配音开头 ${item.startSeconds.toFixed(2)} 秒，释放后生成预览`;
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) {
        video.pause();
        video.currentTime = Math.min(item.startSeconds, Number(video.duration || item.startSeconds));
      }
      syncTtsTimelinePlayhead();
      syncLiveHtmlMotionPreview(video);
      syncHtmlMotionTimelinePlayhead();
      syncTimelineBoundaryUi();
      setTtsTimelineStatus(`配音开头 ${item.startSeconds.toFixed(2)} 秒，释放后应用`, 'working');
    }

    function commitTtsChunkStartTrim(index) {
      const item = state.videoPreviewModal?.ttsTimelineChunks?.[index];
      renderCurrentTtsTimeline();
      void previewTtsTimeline(
        currentVideoPreviewUserGeneratedKey(),
        `配音 ${index + 1} 已从 ${Number(item?.startSeconds || 0).toFixed(2)} 秒开始`,
      );
    }

    function bindVideoChunkTrimHandles(element, duration) {
      bindVideoChunkStartTrim(element, duration);
      bindVideoChunkEndTrim(element, duration);
    }

    function bindVideoChunkStartTrim(element, duration) {
      const index = Number(element.dataset.chunkIndex);
      const originTimelineStart = Number(
        state.videoPreviewModal?.videoTimelineChunks?.[index]?.startSeconds || 0,
      );
      bindTimelineStartTrimHandle(element, {
        duration,
        disabled: () => isVideoScissorMode() || isVideoSeekMode() || timelineHistoryBusy(),
        currentStart: () => Number(state.videoPreviewModal?.videoTimelineChunks?.[index]?.sourceStartSeconds || 0),
        minimumStart: () => Number(
          state.videoPreviewModal?.videoTimelineChunks?.[index]?.originalSourceStartSeconds
          ?? state.videoPreviewModal?.videoTimelineChunks?.[index]?.sourceStartSeconds
          ?? 0,
        ),
        maximumStart: () => Number(state.videoPreviewModal?.videoTimelineChunks?.[index]?.sourceEndSeconds || 0)
          - TIMELINE_TRIM_MIN_SECONDS,
        snapTrack: 'video',
        snapIndex: index,
        snapChunk: () => state.videoPreviewModal?.videoTimelineChunks?.[index],
        historyTrack: 'video',
        historyLabel: () => `裁剪视频片段 ${index + 1} 开头`,
        preview: (nextStart, _event, originSourceStart) => previewVideoChunkStartTrim(
          index,
          nextStart,
          originSourceStart,
          originTimelineStart,
        ),
        finalize: finalizeVideoChunkStartTrim,
        cancel: () => {
          finalizeVideoChunkStartTrim();
          renderVideoTimelineChunks();
          setVideoTimelineStatus('已取消视频裁剪');
        },
        commit: () => commitVideoChunkStartTrim(index),
      });
    }

    function previewVideoChunkStartTrim(index, nextStart, originSourceStart, originTimelineStart) {
      const chunks = state.videoPreviewModal?.videoTimelineChunks || [];
      const item = chunks[index];
      if (!item) return;
      const fixedEnd = Number(item.endSeconds || 0);
      item.sourceStartSeconds = nextStart;
      item.durationSeconds = timelineRoundSeconds(Number(item.sourceEndSeconds || 0) - nextStart);
      item.startSeconds = timelineRoundSeconds(fixedEnd - item.durationSeconds);
      state.videoPreviewModal.videoTimelineOutputDuration = timelineRoundSeconds(
        chunks.reduce((sum, chunk) => sum + timelineChunkVisibleDuration(chunk), 0),
      );
      syncVideoTimelineChunkGeometry();
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) {
        video.pause();
        const previewTime = nextStart >= originSourceStart
          ? originTimelineStart + nextStart - originSourceStart
          : originTimelineStart;
        video.currentTime = Math.min(previewTime, Number(video.duration || previewTime));
      }
      syncLiveHtmlMotionPreview(video);
      syncTimelineBoundaryUi();
      setVideoTimelineStatus(
        `视频开头已调整，预计总长 ${state.videoPreviewModal.videoTimelineOutputDuration.toFixed(2)} 秒，释放后应用`,
        'working',
      );
    }

    function finalizeVideoChunkStartTrim() {
      const packed = packVideoTimelineChunks(state.videoPreviewModal?.videoTimelineChunks || []);
      state.videoPreviewModal.videoTimelineChunks = packed;
      state.videoPreviewModal.videoTimelineOutputDuration = timelineRoundSeconds(
        packed.reduce((sum, chunk) => sum + Number(chunk.durationSeconds || 0), 0),
      );
    }

    function commitVideoChunkStartTrim(index) {
      const item = state.videoPreviewModal?.videoTimelineChunks?.[index];
      renderVideoTimelineChunks();
      void previewVideoTimeline(
        currentVideoPreviewUserGeneratedKey(),
        `片段 ${index + 1} 开头已裁剪，当前片段 ${Number(item?.durationSeconds || 0).toFixed(2)} 秒`,
      );
    }

    function bindHtmlMotionChunkTrimHandles(element, duration) {
      bindHtmlMotionChunkStartTrim(element, duration);
      bindHtmlMotionChunkEndTrim(element, duration);
    }

    function bindHtmlMotionChunkStartTrim(element, duration) {
      const index = Number(element.dataset.chunkIndex);
      bindTimelineStartTrimHandle(element, {
        duration,
        disabled: () => isHtmlMotionScissorMode() || timelineHistoryBusy(),
        currentStart: () => Number(state.videoPreviewModal?.htmlMotionTimelineChunks?.[index]?.sourceStartSeconds || 0),
        minimumStart: () => {
          const item = state.videoPreviewModal?.htmlMotionTimelineChunks?.[index];
          if (!item) return 0;
          return Math.max(
            Number(item.originalSourceStartSeconds ?? item.sourceStartSeconds ?? 0),
            Number(item.sourceEndSeconds || 0) - Number(item.endSeconds || 0),
          );
        },
        maximumStart: () => Number(state.videoPreviewModal?.htmlMotionTimelineChunks?.[index]?.sourceEndSeconds || 0)
          - TIMELINE_TRIM_MIN_SECONDS,
        snapTrack: 'html',
        snapIndex: index,
        snapChunk: () => state.videoPreviewModal?.htmlMotionTimelineChunks?.[index],
        historyTrack: 'html',
        historyLabel: () => `裁剪动效 ${index + 1} 开头`,
        preview: (nextStart) => previewHtmlMotionChunkStartTrim(element, index, duration, nextStart),
        cancel: () => {
          renderCurrentHtmlMotionTimeline();
          setHtmlMotionTimelineStatus('已取消动效裁剪');
        },
        commit: () => commitHtmlMotionChunkStartTrim(index),
      });
    }

    function previewHtmlMotionChunkStartTrim(element, index, duration, nextStart) {
      const item = state.videoPreviewModal?.htmlMotionTimelineChunks?.[index];
      if (!item) return;
      const fixedEnd = Number(item.endSeconds || 0);
      item.sourceStartSeconds = nextStart;
      item.durationSeconds = timelineRoundSeconds(Number(item.sourceEndSeconds || 0) - nextStart);
      item.startSeconds = timelineRoundSeconds(fixedEnd - item.durationSeconds);
      const left = item.startSeconds / duration * 100;
      const width = Math.max(0.1, item.durationSeconds) / duration * 100;
      element.style.left = `${left}%`;
      element.style.width = `${Math.min(width, 100 - left)}%`;
      const meta = element.querySelector('small');
      if (meta) meta.textContent = `${item.startSeconds.toFixed(1)}s`;
      element.title = `动效开头 ${item.startSeconds.toFixed(2)} 秒，释放后保存`;
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) {
        video.pause();
        video.currentTime = Math.min(item.startSeconds, Number(video.duration || item.startSeconds));
      }
      syncLiveHtmlMotionPreview(video);
      syncHtmlMotionTimelinePlayhead();
      syncTimelineBoundaryUi();
      setHtmlMotionTimelineStatus(`动效开头 ${item.startSeconds.toFixed(2)} 秒，释放后保存`, 'working');
    }

    function commitHtmlMotionChunkStartTrim(index) {
      const item = state.videoPreviewModal?.htmlMotionTimelineChunks?.[index];
      commitLocalHtmlMotionTimeline(
        `动效 ${index + 1} 已从 ${Number(item?.startSeconds || 0).toFixed(2)} 秒开始`,
      );
    }
