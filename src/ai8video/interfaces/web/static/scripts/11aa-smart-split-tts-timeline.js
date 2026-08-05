    function ttsSmartSplitThreshold(peaks) {
      const sorted = peaks.slice().sort((left, right) => left - right);
      const percentile = (ratio) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * ratio))] || 0;
      return Math.min(0.14, Math.max(0.025, percentile(0.25) * 1.8, percentile(0.5) * 0.22));
    }

    function detectTtsPauseRanges(peaks, audioDuration) {
      if (!Array.isArray(peaks) || peaks.length < 2 || audioDuration <= 0) return [];
      const threshold = ttsSmartSplitThreshold(peaks);
      const secondsPerPeak = audioDuration / peaks.length;
      // TTS syntheses often leave natural word/phrase gaps below 0.32s. Keep
      // short fragments out, but recognize a usable 0.16s valley.
      const minimumPausePeaks = Math.max(2, Math.ceil(0.16 / secondsPerPeak));
      const ranges = [];
      let runStart = null;
      for (let index = 0; index <= peaks.length; index += 1) {
        const silent = index < peaks.length && Number(peaks[index] || 0) <= threshold;
        if (silent && runStart === null) runStart = index;
        if (silent || runStart === null) continue;
        if (index - runStart >= minimumPausePeaks) {
          ranges.push({
            startSeconds: runStart * secondsPerPeak,
            endSeconds: index * secondsPerPeak,
          });
        }
        runStart = null;
      }
      return ranges;
    }

    function trimTtsChunkPauseGaps(chunk, pauseRanges) {
      const sourceStart = Number(chunk.sourceStartSeconds || 0);
      const sourceEnd = Number(chunk.sourceEndSeconds || sourceStart);
      const sourceDuration = sourceEnd - sourceStart;
      const timelineStart = Number(chunk.startSeconds || 0);
      const timelineDuration = Number(chunk.durationSeconds || sourceDuration);
      if (sourceDuration <= 0 || timelineDuration <= 0) return [{ ...chunk }];
      const gaps = [];
      let previousEnd = sourceStart;
      pauseRanges.forEach((range) => {
        const gapStart = Math.max(sourceStart, Number(range?.startSeconds || 0));
        const gapEnd = Math.min(sourceEnd, Number(range?.endSeconds || gapStart));
        if (gapEnd - gapStart < 0.16) return;
        // Keep only enough spoken material for a stable TTS chunk. This is
        // deliberately much smaller than the old 0.8s split guard so normal
        // word/phrase pauses are actually removed.
        if (gapStart - previousEnd < 0.16 || sourceEnd - gapEnd < 0.16) return;
        gaps.push({ startSeconds: gapStart, endSeconds: gapEnd });
        previousEnd = gapEnd;
      });
      if (!gaps.length) return [{ ...chunk }];
      const segments = [];
      let cursor = sourceStart;
      gaps.forEach((gap) => {
        segments.push({ startSeconds: cursor, endSeconds: gap.startSeconds });
        cursor = gap.endSeconds;
      });
      segments.push({ startSeconds: cursor, endSeconds: sourceEnd });
      return segments.map(({ startSeconds: rawStart, endSeconds: rawEnd }) => {
        const start = timelineRoundSeconds(rawStart);
        const end = timelineRoundSeconds(rawEnd);
        const startRatio = (start - sourceStart) / sourceDuration;
        const duration = timelineRoundSeconds((end - start) / sourceDuration * timelineDuration);
        const startSeconds = timelineRoundSeconds(timelineStart + startRatio * timelineDuration);
        return {
          ...chunk,
          sourceStartSeconds: start,
          sourceEndSeconds: end,
          // A smart-cut gap is deliberately not restorable by a trim handle:
          // restore is the explicit "恢复完整配音" action. This preserves the
          // original video timecode instead of ripple-moving later narration.
          originalSourceStartSeconds: start,
          originalSourceEndSeconds: end,
          startSeconds,
          durationSeconds: duration,
          endSeconds: timelineRoundSeconds(startSeconds + duration),
        };
      });
    }

    function buildSmartSplitTtsChunks(chunks, pauseRanges) {
      const splitChunks = chunks.flatMap((chunk) => trimTtsChunkPauseGaps(chunk, pauseRanges));
      return splitChunks.map((chunk, index) => ({
        ...chunk,
        index,
        label: `配音 ${index + 1}`,
      }));
    }

    function smartSplitTtsTimeline(userGeneratedKey) {
      if (state.videoPreviewModal?.ttsTimelineBusy) return;
      const peaks = state.videoPreviewModal?.ttsWaveformPeaks || [];
      const audioDuration = Number(state.videoPreviewModal?.ttsAudioDuration || 0);
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      if (peaks.length < 2 || audioDuration <= 0) {
        setTtsTimelineStatus('当前配音没有可用音波，无法智能切块', 'error');
        return;
      }
      const pauseRanges = detectTtsPauseRanges(peaks, audioDuration);
      const nextChunks = buildSmartSplitTtsChunks(chunks, pauseRanges);
      if (nextChunks.length <= chunks.length) {
        setTtsTimelineStatus('没有检测到可剪除的音波停顿', 'error');
        return;
      }
      const historyBefore = captureTimelineHistorySnapshot();
      setTtsScissorMode(false, { render: false, updateStatus: false });
      setTtsSelectedChunkIndex(null);
      state.videoPreviewModal.ttsTimelineChunks = nextChunks;
      recordTimelineHistory('tts', '智能剪除配音气口', historyBefore);
      renderCurrentTtsTimeline();
      const removed = nextChunks.length - chunks.length;
      void previewTtsTimeline(userGeneratedKey, `已剪除 ${removed} 处气口，后续配音保持原时间码`);
    }
