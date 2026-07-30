    const TIMELINE_TRIM_MIN_SECONDS = 0.12;

    function timelineRoundSeconds(value) {
      return Math.round(Math.max(0, Number(value) || 0) * 1000) / 1000;
    }

    function timelineChunkVisibleDuration(chunk = {}) {
      const explicit = Number(chunk.durationSeconds);
      if (Number.isFinite(explicit)) return Math.max(0, explicit);
      return Math.max(
        0,
        Number(chunk.sourceEndSeconds || 0) - Number(chunk.sourceStartSeconds || 0),
      );
    }

    function timelineChunkRestoreDuration(chunk = {}) {
      const start = Number(chunk.originalSourceStartSeconds ?? chunk.sourceStartSeconds ?? 0);
      const end = Number(chunk.originalSourceEndSeconds ?? chunk.sourceEndSeconds ?? start);
      return Math.max(timelineChunkVisibleDuration(chunk), end - start);
    }

    function timelineFixedContentGeometry(chunk, timelineDuration, minimumWidthPercent = 1.5) {
      const duration = Math.max(0.001, Number(timelineDuration || 0));
      const start = Math.max(0, Number(chunk?.startSeconds || 0));
      const left = Math.min(100, start / duration * 100);
      const visibleDuration = Math.max(0.001, timelineChunkVisibleDuration(chunk));
      const visibleWidth = visibleDuration / duration * 100;
      const width = Math.min(Math.max(minimumWidthPercent, visibleWidth), Math.max(0, 100 - left));
      const restoreWidth = timelineChunkRestoreDuration(chunk) / duration * 100;
      const sourceStart = Number(chunk?.sourceStartSeconds || 0);
      const originalStart = Number(chunk?.originalSourceStartSeconds ?? sourceStart);
      return {
        left,
        width,
        contentScale: Math.max(100, restoreWidth / Math.max(0.001, width) * 100),
        contentOffset: Math.max(0, sourceStart - originalStart) / visibleDuration * 100,
      };
    }

    function videoTimelineEditScaleDuration(chunks = state.videoPreviewModal?.videoTimelineChunks || []) {
      const restorable = chunks.reduce((sum, chunk) => sum + timelineChunkRestoreDuration(chunk), 0);
      return Math.max(
        0.001,
        restorable,
        Number(state.videoPreviewModal?.videoTimelineOutputDuration || 0),
      );
    }

    function timelineChunkWithRestoreBounds(chunk = {}) {
      const sourceStart = timelineRoundSeconds(chunk.sourceStartSeconds);
      const sourceEnd = Math.max(sourceStart, timelineRoundSeconds(chunk.sourceEndSeconds));
      const originalStart = Math.min(
        sourceStart,
        timelineRoundSeconds(chunk.originalSourceStartSeconds ?? sourceStart),
      );
      const originalEnd = Math.max(
        sourceEnd,
        timelineRoundSeconds(chunk.originalSourceEndSeconds ?? sourceEnd),
      );
      return {
        ...chunk,
        sourceStartSeconds: sourceStart,
        sourceEndSeconds: sourceEnd,
        originalSourceStartSeconds: originalStart,
        originalSourceEndSeconds: originalEnd,
      };
    }

    function timelineTrimHandleMarkup(label) {
      const safeLabel = escapeHtml(label);
      return `<span class="video-preview-timeline-trim-handle is-start" data-video-preview-timeline-trim-handle="start" aria-hidden="true" title="拖动裁剪或恢复${safeLabel}开头"></span><span class="video-preview-timeline-trim-handle is-end" data-video-preview-timeline-trim-handle="end" aria-hidden="true" title="拖动裁剪或恢复${safeLabel}结尾"></span>`;
    }

    function splitTimelineRestoreBounds(chunk, sourceSplit) {
      const current = timelineChunkWithRestoreBounds(chunk);
      const split = timelineRoundSeconds(sourceSplit);
      return {
        first: {
          ...current,
          sourceEndSeconds: split,
          originalSourceEndSeconds: split,
        },
        second: {
          ...current,
          sourceStartSeconds: split,
          originalSourceStartSeconds: split,
        },
      };
    }

    function timelineSecondsAtPointer(event, lane, duration) {
      const bounds = lane?.getBoundingClientRect();
      if (!bounds || bounds.width <= 0 || duration <= 0) return 0;
      const ratio = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
      return Math.round(ratio * duration * 1000) / 1000;
    }

    function bindTimelineScissorGuide(lane, duration, enabled) {
      const guide = lane?.querySelector('[data-video-preview-cut-guide]');
      if (!lane || !guide || !enabled || duration <= 0) return;
      const update = (event) => {
        const seconds = timelineSecondsAtPointer(event, lane, duration);
        guide.style.left = `${seconds / duration * 100}%`;
        guide.dataset.timeLabel = `${seconds.toFixed(2)}s`;
        guide.hidden = false;
      };
      lane.addEventListener('pointermove', update);
      lane.addEventListener('pointerleave', () => { guide.hidden = true; });
    }

    function bindTimelinePlayheadDrag(playhead, lane, duration, kind, maxSeconds = duration) {
      if (!playhead || !lane || duration <= 0) return;
      playhead.setAttribute('role', 'slider');
      playhead.setAttribute('tabindex', '0');
      playhead.setAttribute('aria-valuemin', '0');
      playhead.setAttribute('aria-valuemax', Number(maxSeconds || duration).toFixed(3));
      const seekAtClick = (event) => {
        const interactive = event.target.closest?.(
          '[data-video-preview-html-motion-chunk], [data-video-preview-tts-chunk], '
          + '[data-video-preview-video-chunk], [data-video-preview-html-motion-playhead], '
          + '[data-video-preview-tts-playhead], [data-video-preview-video-playhead], '
          + '[data-video-preview-timeline-trim-handle]',
        );
        if (interactive || lane.dataset.timelineIgnoreClick === 'true') {
          delete lane.dataset.timelineIgnoreClick;
          return;
        }
        const video = els.videoPreviewBody?.querySelector('video');
        if (!video) return;
        const limit = Math.min(
          Number.isFinite(video.duration) ? Number(video.duration) : Number(maxSeconds || duration),
          Math.max(0, Number(maxSeconds || duration)),
        );
        video.pause();
        video.currentTime = Math.min(limit, timelineSecondsAtPointer(event, lane, duration));
        if (kind === 'tts') setTtsSelectedChunkIndex(null);
        if (kind === 'video') setVideoSelectedChunkIndex(null);
        if (kind === 'html') setHtmlMotionSelectedChunkIndex(null);
        syncTtsTimelinePlayhead();
        syncVideoTimelinePlayhead();
        syncHtmlMotionTimelinePlayhead();
      };
      lane.parentElement?.querySelector('[data-video-preview-timeline-ruler]')?.addEventListener('click', seekAtClick);
      playhead.addEventListener('pointerdown', (event) => {
        const video = els.videoPreviewBody?.querySelector('video');
        if (!video || event.button !== 0) return;
        event.preventDefault();
        const resume = !video.paused;
        const originTime = Number(video.currentTime || 0);
        const limit = Math.min(
          Number.isFinite(video.duration) ? Number(video.duration) : Number(maxSeconds || duration),
          Math.max(0, Number(maxSeconds || duration)),
        );
        const snapPoints = timelineBuildSnapPoints(duration, {
          includePlayhead: false,
          maxSeconds: limit,
        });
        video.pause();
        playhead.setPointerCapture(event.pointerId);
        timelineBeginPointerInteraction();
        const seek = (pointerEvent) => {
          const raw = Math.min(limit, timelineSecondsAtPointer(pointerEvent, lane, duration));
          const resolved = timelineResolveSnap(raw, lane, duration, pointerEvent, { points: snapPoints });
          video.currentTime = Math.min(limit, resolved.seconds);
          timelineSyncSnapGuide(lane, duration, resolved.snap);
          if (kind === 'tts') setTtsSelectedChunkIndex(null);
          if (kind === 'video') setVideoSelectedChunkIndex(null);
          if (kind === 'html') setHtmlMotionSelectedChunkIndex(null);
          syncTtsTimelinePlayhead();
          syncVideoTimelinePlayhead();
          syncHtmlMotionTimelinePlayhead();
        };
        const finish = (finishEvent) => {
          playhead.removeEventListener('pointermove', seek);
          playhead.removeEventListener('pointerup', finish);
          playhead.removeEventListener('pointercancel', finish);
          playhead.removeEventListener('lostpointercapture', finish);
          timelineClearSnapGuide(lane);
          timelineEndPointerInteraction();
          if (finishEvent.type !== 'pointerup') {
            video.currentTime = Math.min(limit, originTime);
            syncTtsTimelinePlayhead();
            syncVideoTimelinePlayhead();
            syncHtmlMotionTimelinePlayhead();
          }
          if (resume) video.play().catch(() => {});
        };
        seek(event);
        playhead.addEventListener('pointermove', seek);
        playhead.addEventListener('pointerup', finish);
        playhead.addEventListener('pointercancel', finish);
        playhead.addEventListener('lostpointercapture', finish);
      });
      playhead.addEventListener('keydown', (event) => {
        const video = els.videoPreviewBody?.querySelector('video');
        if (!video || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const limit = Math.min(
          Number.isFinite(video.duration) ? Number(video.duration) : Number(maxSeconds || duration),
          Math.max(0, Number(maxSeconds || duration)),
        );
        const step = event.shiftKey ? 1 : 0.1;
        if (event.key === 'Home') video.currentTime = 0;
        else if (event.key === 'End') video.currentTime = limit;
        else video.currentTime = Math.min(limit, Math.max(0, Number(video.currentTime || 0)
          + (event.key === 'ArrowRight' ? step : -step)));
        syncTtsTimelinePlayhead();
        syncVideoTimelinePlayhead();
        syncHtmlMotionTimelinePlayhead();
      });
    }

    function bindTimelineEndTrimHandle(element, options = {}) {
      const handle = element?.querySelector('[data-video-preview-timeline-trim-handle="end"]');
      const lane = element?.parentElement;
      const duration = Math.max(0, Number(options.duration || 0));
      if (!handle || !lane || duration <= 0) return;
      handle.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
      });
      handle.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || options.disabled?.()) return;
        event.preventDefault();
        event.stopPropagation();
        const originX = event.clientX;
        const originEnd = Number(options.currentEnd?.() || 0);
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
          const minimum = Number(options.minimumEnd?.() || 0);
          const maximum = Math.max(minimum, Number(options.maximumEnd?.() || minimum));
          const resolved = timelineResolveTrimSnap(originEnd + delta, lane, duration, moveEvent, {
            minimum,
            maximum,
            track: options.snapTrack,
            edge: 'end',
            chunk: options.snapChunk?.(),
            points: snapPoints,
          });
          timelineSyncSnapGuide(lane, duration, resolved.snap);
          options.preview?.(resolved.sourceSeconds, moveEvent);
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
            options.preview?.(originEnd, finishEvent);
            options.cancel?.();
            return;
          }
          if (historyBefore) {
            const changed = recordTimelineHistory(
              options.historyTrack,
              options.historyLabel?.(),
              historyBefore,
            );
            if (!changed) {
              options.cancel?.();
              return;
            }
          }
          options.commit?.();
        };
        handle.addEventListener('pointermove', move);
        handle.addEventListener('pointerup', finish);
        handle.addEventListener('pointercancel', finish);
        handle.addEventListener('lostpointercapture', finish);
      });
    }

    function bindTtsChunkEndTrim(element, duration) {
      const index = Number(element.dataset.chunkIndex);
      bindTimelineEndTrimHandle(element, {
        duration,
        disabled: () => isTtsScissorMode() || timelineHistoryBusy(),
        currentEnd: () => Number(state.videoPreviewModal?.ttsTimelineChunks?.[index]?.sourceEndSeconds || 0),
        minimumEnd: () => Number(state.videoPreviewModal?.ttsTimelineChunks?.[index]?.sourceStartSeconds || 0)
          + TIMELINE_TRIM_MIN_SECONDS,
        maximumEnd: () => ttsChunkMaximumTrimEnd(index, duration),
        snapTrack: 'tts',
        snapIndex: index,
        snapChunk: () => state.videoPreviewModal?.ttsTimelineChunks?.[index],
        historyTrack: 'tts',
        historyLabel: () => `裁剪配音 ${index + 1}`,
        preview: (nextEnd) => previewTtsChunkEndTrim(element, index, duration, nextEnd),
        cancel: () => {
          renderTtsTimelineChunks(
            els.videoPreviewBody?.querySelector('[data-video-preview-tts-chunks]'),
            state.videoPreviewModal?.ttsTimelineChunks || [],
            duration,
          );
          setTtsTimelineStatus('已取消配音裁剪');
        },
        commit: () => commitTtsChunkEndTrim(index, duration),
      });
    }

    function ttsChunkMaximumTrimEnd(index, duration) {
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const item = chunks[index];
      const next = chunks[index + 1];
      if (!item) return 0;
      const timelineRoom = (next ? Number(next.startSeconds || 0) : duration)
        - Number(item.startSeconds || 0);
      return Math.min(
        Number(item.originalSourceEndSeconds || item.sourceEndSeconds || 0),
        Number(item.sourceStartSeconds || 0) + Math.max(0, timelineRoom),
      );
    }

    function previewTtsChunkEndTrim(element, index, duration, nextEnd) {
      const item = state.videoPreviewModal?.ttsTimelineChunks?.[index];
      if (!item) return;
      item.sourceEndSeconds = nextEnd;
      item.durationSeconds = timelineRoundSeconds(nextEnd - Number(item.sourceStartSeconds || 0));
      item.endSeconds = timelineRoundSeconds(Number(item.startSeconds || 0) + item.durationSeconds);
      const geometry = timelineFixedContentGeometry(item, duration);
      element.style.width = `${geometry.width}%`;
      element.style.setProperty('--video-preview-tts-waveform-scale', `${geometry.contentScale}%`);
      element.style.setProperty('--video-preview-tts-waveform-offset', `${geometry.contentOffset}%`);
      element.title = `配音结尾 ${item.endSeconds.toFixed(2)} 秒，释放后生成预览`;
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) {
        video.pause();
        video.currentTime = Math.min(item.endSeconds, Number(video.duration || item.endSeconds));
      }
      syncTtsTimelinePlayhead();
      syncLiveHtmlMotionPreview(video);
      syncTimelineBoundaryUi();
      setTtsTimelineStatus(`配音结尾 ${item.endSeconds.toFixed(2)} 秒，释放后应用`, 'working');
    }

    function commitTtsChunkEndTrim(index, duration) {
      const item = state.videoPreviewModal?.ttsTimelineChunks?.[index];
      renderTtsTimelineChunks(
        els.videoPreviewBody?.querySelector('[data-video-preview-tts-chunks]'),
        state.videoPreviewModal?.ttsTimelineChunks || [],
        duration,
      );
      void previewTtsTimeline(
        currentVideoPreviewUserGeneratedKey(),
        `配音 ${index + 1} 已裁剪到 ${Number(item?.endSeconds || 0).toFixed(2)} 秒`,
      );
    }

    function syncVideoTimelineChunkGeometry() {
      const duration = videoTimelineEditScaleDuration();
      const chunks = state.videoPreviewModal?.videoTimelineChunks || [];
      videoTimelinePanel()?.querySelectorAll('[data-video-preview-video-chunk]').forEach((element) => {
        const item = chunks[Number(element.dataset.chunkIndex)];
        if (!item) return;
        const geometry = timelineFixedContentGeometry(item, duration);
        element.style.left = `${geometry.left}%`;
        element.style.width = `${geometry.width}%`;
        element.style.setProperty('--video-preview-video-content-scale', `${geometry.contentScale}%`);
        element.style.setProperty('--video-preview-video-content-offset', `${geometry.contentOffset}%`);
        const meta = element.querySelector('small');
        if (meta) meta.textContent = `${Number(item.startSeconds || 0).toFixed(1)}s`;
      });
      syncVideoTimelineDurationLabels();
      syncVideoTimelinePlayhead();
    }

    function bindVideoChunkEndTrim(element, duration) {
      const index = Number(element.dataset.chunkIndex);
      bindTimelineEndTrimHandle(element, {
        duration,
        disabled: () => isVideoScissorMode() || isVideoSeekMode() || timelineHistoryBusy(),
        currentEnd: () => Number(state.videoPreviewModal?.videoTimelineChunks?.[index]?.sourceEndSeconds || 0),
        minimumEnd: () => Number(state.videoPreviewModal?.videoTimelineChunks?.[index]?.sourceStartSeconds || 0)
          + TIMELINE_TRIM_MIN_SECONDS,
        maximumEnd: () => videoChunkMaximumTrimEnd(index),
        snapTrack: 'video',
        snapIndex: index,
        snapChunk: () => state.videoPreviewModal?.videoTimelineChunks?.[index],
        historyTrack: 'video',
        historyLabel: () => `裁剪视频片段 ${index + 1}`,
        preview: (nextEnd) => previewVideoChunkEndTrim(index, nextEnd),
        cancel: () => {
          renderVideoTimelineChunks();
          setVideoTimelineStatus('已取消视频裁剪');
        },
        commit: () => commitVideoChunkEndTrim(index),
      });
    }

    function videoChunkMaximumTrimEnd(index) {
      const item = state.videoPreviewModal?.videoTimelineChunks?.[index];
      return Number(item?.originalSourceEndSeconds || item?.sourceEndSeconds || 0);
    }

    function previewVideoChunkEndTrim(index, nextEnd) {
      const chunks = state.videoPreviewModal?.videoTimelineChunks || [];
      const item = chunks[index];
      if (!item) return;
      item.sourceEndSeconds = nextEnd;
      const packed = packVideoTimelineChunks(chunks);
      state.videoPreviewModal.videoTimelineChunks = packed;
      state.videoPreviewModal.videoTimelineOutputDuration = packed.reduce(
        (sum, chunk) => sum + Number(chunk.durationSeconds || 0),
        0,
      );
      syncVideoTimelineChunkGeometry();
      const current = packed[index];
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) {
        video.pause();
        video.currentTime = Math.min(
          Number(current.endSeconds || 0),
          Number(video.duration || current.endSeconds || 0),
        );
      }
      syncLiveHtmlMotionPreview(video);
      syncTimelineBoundaryUi();
      setVideoTimelineStatus(
        `视频结尾 ${Number(current.endSeconds || 0).toFixed(2)} 秒，释放后应用`,
        'working',
      );
    }

    function commitVideoChunkEndTrim(index) {
      const item = state.videoPreviewModal?.videoTimelineChunks?.[index];
      renderVideoTimelineChunks();
      void previewVideoTimeline(
        currentVideoPreviewUserGeneratedKey(),
        `片段 ${index + 1} 已裁剪到 ${Number(item?.endSeconds || 0).toFixed(2)} 秒`,
      );
    }
