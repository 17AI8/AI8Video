    const TIMELINE_SNAP_TOLERANCE_PX = 8;
    const TIMELINE_SNAP_EPSILON_SECONDS = 0.002;

    function timelineCurrentPlayheadSeconds() {
      return Math.max(0, Number(els.videoPreviewBody?.querySelector('video')?.currentTime || 0));
    }

    function timelineBeginPointerInteraction() {
      const modal = state.videoPreviewModal;
      if (!modal) return;
      modal.timelineInteractionCount = Math.max(0, Number(modal.timelineInteractionCount || 0)) + 1;
      syncTimelineHistoryButtons();
    }

    function timelineEndPointerInteraction() {
      const modal = state.videoPreviewModal;
      if (!modal) return;
      modal.timelineInteractionCount = Math.max(0, Number(modal.timelineInteractionCount || 0) - 1);
      syncTimelineHistoryButtons();
    }

    function timelineFormatRulerTime(seconds) {
      const value = Math.max(0, Number(seconds || 0));
      if (value < 1) return `${value.toFixed(1)}s`;
      const whole = Math.round(value);
      const hours = Math.floor(whole / 3600);
      const minutes = Math.floor(whole % 3600 / 60);
      const rest = whole % 60;
      if (hours) return `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
      return `${minutes}:${String(rest).padStart(2, '0')}`;
    }

    function timelineNiceRulerStep(duration) {
      const target = Math.max(0.001, Number(duration || 0) / 6);
      const magnitude = 10 ** Math.floor(Math.log10(target));
      const normalized = target / magnitude;
      const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
      return factor * magnitude;
    }

    function timelineRulerTicks(duration) {
      const total = Math.max(0.001, Number(duration || 0));
      const majorStep = timelineNiceRulerStep(total);
      const minorStep = majorStep / 5;
      const ticks = [];
      const count = Math.min(120, Math.ceil(total / minorStep));
      for (let index = 0; index <= count; index += 1) {
        const seconds = Math.min(total, index * minorStep);
        const quotient = seconds / majorStep;
        const major = Math.abs(quotient - Math.round(quotient)) < 0.001;
        ticks.push({ seconds: timelineRoundSeconds(seconds), major });
      }
      if (Math.abs(Number(ticks.at(-1)?.seconds || 0) - total) > TIMELINE_SNAP_EPSILON_SECONDS) {
        ticks.push({ seconds: timelineRoundSeconds(total), major: true });
      } else if (ticks.length) {
        ticks[ticks.length - 1].major = true;
      }
      return ticks;
    }

    function timelineRulerMarkup(duration) {
      const total = Math.max(0.001, Number(duration || 0));
      const ticks = timelineRulerTicks(total).map((tick) => {
        const left = Math.min(100, Math.max(0, tick.seconds / total * 100));
        const edgeClass = left <= 0.01 ? ' is-start' : left >= 99.99 ? ' is-end' : '';
        const label = tick.major ? `<span>${timelineFormatRulerTime(tick.seconds)}</span>` : '';
        return `<i class="video-preview-timeline-ruler-tick${tick.major ? ' is-major' : ''}${edgeClass}" style="left:${left}%">${label}</i>`;
      }).join('');
      return `<div class="video-preview-timeline-ruler" data-video-preview-timeline-ruler aria-hidden="true">${ticks}</div>`;
    }

    function timelineSnapGuideMarkup() {
      return '<span class="video-preview-timeline-snap-guide" data-video-preview-timeline-snap-guide hidden></span>';
    }

    function timelineSnapPointPriority(kind) {
      if (kind === 'boundary') return 0;
      if (kind === 'playhead') return 1;
      return 2;
    }

    function timelineBuildSnapPoints(duration, options = {}) {
      const total = Math.max(0, Number(duration || 0));
      const maxSeconds = Math.min(total, Math.max(0, Number(options.maxSeconds ?? total)));
      const points = new Map();
      const excludedIndexes = new Set(Array.isArray(options.excludeIndexes) ? options.excludeIndexes : []);
      const add = (seconds, kind) => {
        const value = timelineRoundSeconds(seconds);
        if (!Number.isFinite(value) || value < 0 || value > maxSeconds + TIMELINE_SNAP_EPSILON_SECONDS) return;
        const key = String(Math.round(value * 1000));
        const point = { seconds: value, kind, priority: timelineSnapPointPriority(kind) };
        const current = points.get(key);
        if (!current || point.priority < current.priority) points.set(key, point);
      };
      add(0, 'boundary');
      add(maxSeconds, 'boundary');
      const outputDuration = Number(state.videoPreviewModal?.videoTimelineOutputDuration || 0);
      if (outputDuration > 0 && outputDuration < maxSeconds) add(outputDuration, 'boundary');
      if (options.includePlayhead !== false) {
        add(Number(options.playheadSeconds ?? timelineCurrentPlayheadSeconds()), 'playhead');
      }
      const modal = state.videoPreviewModal;
      [
        ['video', modal?.videoTimelineChunks],
        ['tts', modal?.ttsTimelineChunks],
        ['html', modal?.htmlMotionTimelineChunks],
      ].forEach(([track, chunks]) => {
        (Array.isArray(chunks) ? chunks : []).forEach((chunk, index) => {
          if (track === options.excludeTrack && (index === options.excludeIndex || excludedIndexes.has(index))) return;
          const start = Number(chunk.startSeconds || 0);
          const end = Number(chunk.endSeconds ?? start + Number(chunk.durationSeconds || 0));
          add(start, 'chunk-edge');
          add(end, 'chunk-edge');
        });
      });
      return [...points.values()].sort((left, right) => left.seconds - right.seconds || left.priority - right.priority);
    }

    function timelineResolveSnap(seconds, lane, duration, event, options = {}) {
      const total = Math.max(0, Number(duration || 0));
      const width = Math.max(1, Number(lane?.clientWidth || lane?.getBoundingClientRect?.().width || 0));
      const raw = timelineRoundSeconds(Math.min(total, Math.max(0, Number(seconds || 0))));
      if (event?.shiftKey || total <= 0 || width <= 1) return { seconds: raw, snap: null };
      const threshold = TIMELINE_SNAP_TOLERANCE_PX / width * total;
      const points = options.points || timelineBuildSnapPoints(total, options);
      let best = null;
      points.forEach((point) => {
        const distance = Math.abs(point.seconds - raw);
        if (distance > threshold + TIMELINE_SNAP_EPSILON_SECONDS) return;
        if (!best
          || distance < best.distance - TIMELINE_SNAP_EPSILON_SECONDS
          || (Math.abs(distance - best.distance) <= TIMELINE_SNAP_EPSILON_SECONDS
            && (point.priority < best.point.priority
              || (point.priority === best.point.priority && point.seconds < best.point.seconds)))) {
          best = { point, distance };
        }
      });
      return best ? { seconds: best.point.seconds, snap: best.point } : { seconds: raw, snap: null };
    }

    function timelineSyncSnapGuide(lane, duration, snap) {
      const guide = lane?.querySelector('[data-video-preview-timeline-snap-guide]');
      const total = Math.max(0, Number(duration || 0));
      if (!guide) return;
      if (!snap || total <= 0) {
        guide.hidden = true;
        guide.classList.remove('is-near-end');
        return;
      }
      guide.style.left = `${Math.min(100, Math.max(0, Number(snap.seconds || 0) / total * 100))}%`;
      guide.dataset.timeLabel = `${Number(snap.seconds || 0).toFixed(2)}s`;
      guide.classList.toggle('is-near-end', Number(snap.seconds || 0) / total > 0.88);
      guide.hidden = false;
    }

    function timelineClearSnapGuide(lane) {
      const guide = lane?.querySelector('[data-video-preview-timeline-snap-guide]');
      if (guide) {
        guide.hidden = true;
        guide.classList.remove('is-near-end');
      }
    }

    function timelineChunkBoundaryTime(chunk, edge, sourceSeconds) {
      if (edge === 'start') {
        return timelineRoundSeconds(
          Number(chunk.endSeconds || 0) - (Number(chunk.sourceEndSeconds || 0) - sourceSeconds),
        );
      }
      return timelineRoundSeconds(
        Number(chunk.startSeconds || 0) + sourceSeconds - Number(chunk.sourceStartSeconds || 0),
      );
    }

    function timelineChunkSourceAtBoundary(chunk, edge, seconds) {
      if (edge === 'start') {
        return timelineRoundSeconds(
          Number(chunk.sourceEndSeconds || 0) - (Number(chunk.endSeconds || 0) - seconds),
        );
      }
      return timelineRoundSeconds(
        Number(chunk.sourceStartSeconds || 0) + seconds - Number(chunk.startSeconds || 0),
      );
    }

    function timelineResolveTrimSnap(sourceSeconds, lane, duration, event, options = {}) {
      const minimum = Number(options.minimum || 0);
      const maximum = Math.max(minimum, Number(options.maximum || minimum));
      const bounded = timelineRoundSeconds(Math.min(maximum, Math.max(minimum, sourceSeconds)));
      const chunk = options.chunk;
      if (!chunk || !options.track) return { sourceSeconds: bounded, snap: null };
      const timelineSeconds = timelineChunkBoundaryTime(chunk, options.edge, bounded);
      const resolved = timelineResolveSnap(timelineSeconds, lane, duration, event, { points: options.points });
      if (!resolved.snap) return { sourceSeconds: bounded, snap: null };
      const snappedSource = timelineRoundSeconds(Math.min(
        maximum,
        Math.max(minimum, timelineChunkSourceAtBoundary(chunk, options.edge, resolved.seconds)),
      ));
      const actual = timelineChunkBoundaryTime(chunk, options.edge, snappedSource);
      if (Math.abs(actual - resolved.seconds) > TIMELINE_SNAP_EPSILON_SECONDS) {
        return { sourceSeconds: snappedSource, snap: null };
      }
      return { sourceSeconds: snappedSource, snap: resolved.snap };
    }

    function timelineResolveChunkMoveSnap(startSeconds, chunkDuration, lane, duration, event, options = {}) {
      const minimum = Number(options.minimum || 0);
      const maximum = Math.max(minimum, Number(options.maximum || minimum));
      const visibleDuration = Math.max(0, Number(chunkDuration || 0));
      const raw = timelineRoundSeconds(Math.min(maximum, Math.max(minimum, startSeconds)));
      if (event?.shiftKey) return { startSeconds: raw, snap: null };
      const candidates = [
        { seconds: raw, offset: 0 },
        { seconds: raw + visibleDuration, offset: visibleDuration },
      ];
      let best = null;
      candidates.forEach((candidate) => {
        const resolved = timelineResolveSnap(candidate.seconds, lane, duration, event, { points: options.points });
        if (!resolved.snap) return;
        const nextStart = timelineRoundSeconds(resolved.seconds - candidate.offset);
        if (nextStart < minimum - TIMELINE_SNAP_EPSILON_SECONDS
          || nextStart > maximum + TIMELINE_SNAP_EPSILON_SECONDS) return;
        const distance = Math.abs(nextStart - raw);
        if (!best || distance < best.distance - TIMELINE_SNAP_EPSILON_SECONDS
          || (Math.abs(distance - best.distance) <= TIMELINE_SNAP_EPSILON_SECONDS
            && resolved.snap.priority < best.snap.priority)) {
          best = { startSeconds: nextStart, snap: resolved.snap, distance };
        }
      });
      return best || { startSeconds: raw, snap: null };
    }

    function syncHtmlMotionTimelinePlayhead() {
      const video = els.videoPreviewBody?.querySelector('video');
      const playhead = htmlMotionTimelinePanel()?.querySelector('[data-video-preview-html-motion-playhead]');
      const duration = Number(state.videoPreviewModal?.htmlMotionTimelineDuration || video?.duration || 0);
      if (!video || !playhead || duration <= 0) return;
      const current = Math.min(duration, Math.max(0, Number(video.currentTime || 0)));
      playhead.style.left = `${current / duration * 100}%`;
      playhead.setAttribute('aria-valuenow', current.toFixed(3));
    }

    function syncAllTimelinePlayheads() {
      syncTtsTimelinePlayhead();
      syncVideoTimelinePlayhead();
      syncHtmlMotionTimelinePlayhead();
    }
