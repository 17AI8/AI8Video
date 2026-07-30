    function ttsSmartSplitThreshold(peaks) {
      const sorted = peaks.slice().sort((left, right) => left - right);
      const percentile = (ratio) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * ratio))] || 0;
      return Math.min(0.14, Math.max(0.025, percentile(0.25) * 1.8, percentile(0.5) * 0.22));
    }

    function detectTtsPauseCenters(peaks, audioDuration) {
      if (!Array.isArray(peaks) || peaks.length < 2 || audioDuration <= 0) return [];
      const threshold = ttsSmartSplitThreshold(peaks);
      const secondsPerPeak = audioDuration / peaks.length;
      const minimumPausePeaks = Math.max(2, Math.ceil(0.32 / secondsPerPeak));
      const centers = [];
      let runStart = null;
      for (let index = 0; index <= peaks.length; index += 1) {
        const silent = index < peaks.length && Number(peaks[index] || 0) <= threshold;
        if (silent && runStart === null) runStart = index;
        if (silent || runStart === null) continue;
        if (index - runStart >= minimumPausePeaks) {
          centers.push((runStart + index - 1) / 2 * secondsPerPeak);
        }
        runStart = null;
      }
      return centers;
    }

    function splitTtsChunkAtPauses(chunk, pauseCenters) {
      const sourceStart = Number(chunk.sourceStartSeconds || 0);
      const sourceEnd = Number(chunk.sourceEndSeconds || sourceStart);
      const sourceDuration = sourceEnd - sourceStart;
      const timelineStart = Number(chunk.startSeconds || 0);
      const timelineDuration = Number(chunk.durationSeconds || sourceDuration);
      if (sourceDuration <= 0 || timelineDuration <= 0) return [{ ...chunk }];
      const cuts = [];
      pauseCenters.forEach((time) => {
        const previous = cuts.length ? cuts[cuts.length - 1] : sourceStart;
        if (time - previous >= 0.8 && sourceEnd - time >= 0.8) cuts.push(time);
      });
      const boundaries = [sourceStart, ...cuts, sourceEnd];
      let remaining = timelineChunkWithRestoreBounds(chunk);
      return boundaries.slice(0, -1).map((start, index) => {
        const end = boundaries[index + 1];
        const startRatio = (start - sourceStart) / sourceDuration;
        const duration = (end - start) / sourceDuration * timelineDuration;
        const startSeconds = timelineStart + startRatio * timelineDuration;
        const last = index === boundaries.length - 2;
        const bounds = last
          ? { first: remaining }
          : splitTimelineRestoreBounds(remaining, end);
        remaining = bounds.second || remaining;
        return {
          ...bounds.first,
          sourceStartSeconds: start,
          sourceEndSeconds: end,
          startSeconds,
          durationSeconds: duration,
          endSeconds: startSeconds + duration,
        };
      });
    }

    function buildSmartSplitTtsChunks(chunks, pauseCenters) {
      const splitChunks = chunks.flatMap((chunk) => splitTtsChunkAtPauses(chunk, pauseCenters));
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
      const pauseCenters = detectTtsPauseCenters(peaks, audioDuration);
      const nextChunks = buildSmartSplitTtsChunks(chunks, pauseCenters);
      if (nextChunks.length <= chunks.length) {
        setTtsTimelineStatus('没有检测到足够明显的音波停顿', 'error');
        return;
      }
      const historyBefore = captureTimelineHistorySnapshot();
      setTtsScissorMode(false, { render: false, updateStatus: false });
      setTtsSelectedChunkIndex(null);
      state.videoPreviewModal.ttsTimelineChunks = nextChunks;
      recordTimelineHistory('tts', '智能切分配音', historyBefore);
      renderCurrentTtsTimeline();
      void previewTtsTimeline(userGeneratedKey, `已根据音波停顿智能切为 ${nextChunks.length} 段`);
    }
