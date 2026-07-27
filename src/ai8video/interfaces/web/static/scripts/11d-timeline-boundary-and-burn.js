    function burnConfirmButton() {
      return els.videoPreviewBody?.querySelector('[data-video-preview-action="confirm-burn"]');
    }

    function activeBurnReview(review = null) {
      return review && typeof review === 'object'
        ? review
        : (state.videoPreviewModal?.burnReview || {});
    }

    function timelineChunkEndSeconds(chunk = {}) {
      const start = Math.max(0, Number(chunk.startSeconds || 0));
      const end = Number(chunk.endSeconds);
      return Number.isFinite(end)
        ? Math.max(start, end)
        : start + Math.max(0, Number(chunk.durationSeconds || 0));
    }

    function timelineBoundaryDetails(review = null) {
      const current = activeBurnReview(review);
      const videoPending = current?.videoTimeline?.pending === true;
      const duration = videoPending
        ? Math.max(0, Number(
            state.videoPreviewModal?.videoTimelineOutputDuration
            || current?.timelineBoundary?.videoDurationSeconds
            || current?.videoTimeline?.outputDurationSeconds
            || 0,
          ))
        : 0;
      const localTtsChunks = state.videoPreviewModal?.ttsTimelineChunks;
      const ttsChunks = current?.tts?.available === true
        ? (Array.isArray(localTtsChunks) && localTtsChunks.length
            ? localTtsChunks
            : (current?.tts?.timelineChunks || []))
        : [];
      const htmlActive = current?.htmlMotion?.reviewReady === true;
      const localHtmlChunks = state.videoPreviewModal?.htmlMotionTimelineChunks;
      const htmlChunks = htmlActive
        ? (Array.isArray(localHtmlChunks) && localHtmlChunks.length
            ? localHtmlChunks
            : (current?.htmlMotion?.timelineChunks || []))
        : [];
      const overflowIndexes = (chunks) => chunks
        .map((chunk, position) => ({ chunk, index: Number(chunk?.index ?? position) }))
        .filter(({ chunk }) => timelineChunkEndSeconds(chunk) > duration + 0.08)
        .map(({ index }) => index);
      const ttsOverflowIndexes = videoPending && duration > 0 ? overflowIndexes(ttsChunks) : [];
      const htmlMotionOverflowIndexes = videoPending && duration > 0 ? overflowIndexes(htmlChunks) : [];
      return {
        active: videoPending && duration > 0,
        valid: !ttsOverflowIndexes.length && !htmlMotionOverflowIndexes.length,
        videoDurationSeconds: duration,
        ttsOverflowIndexes,
        htmlMotionOverflowIndexes,
      };
    }

    function timelineBoundaryReason(boundary) {
      const parts = [];
      if (boundary.ttsOverflowIndexes.length) parts.push(`配音 ${boundary.ttsOverflowIndexes.length} 段`);
      if (boundary.htmlMotionOverflowIndexes.length) parts.push(`动效 ${boundary.htmlMotionOverflowIndexes.length} 段`);
      if (!parts.length) return '';
      return `${parts.join('、')}超出 ${boundary.videoDurationSeconds.toFixed(1)} 秒，请先调整`;
    }

    function timelineChunkBoundaryState(chunk, boundary = timelineBoundaryDetails()) {
      const duration = Math.max(0.001, Number(chunk?.durationSeconds || 0.001));
      const overflow = timelineChunkEndSeconds(chunk) - boundary.videoDurationSeconds;
      const invalid = boundary.active && overflow > 0.08;
      return {
        invalid,
        overflowLeftPercent: invalid
          ? Math.min(100, Math.max(0, (1 - overflow / duration) * 100))
          : 100,
      };
    }

    function timelineOverflowZoneMarkup(duration, overflowIndexes, boundary = timelineBoundaryDetails()) {
      if (!Array.isArray(overflowIndexes) || !overflowIndexes.length) return '';
      if (!boundary.active || duration <= 0 || boundary.videoDurationSeconds >= duration - 0.08) return '';
      const left = Math.min(100, Math.max(0, boundary.videoDurationSeconds / duration * 100));
      return `<span class="video-preview-timeline-overflow-zone" aria-hidden="true" style="left:${left}%"><small>新视频结尾 ${boundary.videoDurationSeconds.toFixed(1)}s</small></span>`;
    }

    function syncTimelineChunkBoundaryElements(selector, chunks, boundary) {
      els.videoPreviewBody?.querySelectorAll(selector).forEach((element) => {
        const index = Number(element.dataset.chunkIndex);
        const chunk = chunks.find((item, position) => Number(item?.index ?? position) === index);
        if (!chunk) return;
        const chunkBoundary = timelineChunkBoundaryState(chunk, boundary);
        const baseTitle = element.dataset.boundaryBaseTitle || element.title || '';
        element.classList.toggle('is-out-of-bounds', chunkBoundary.invalid);
        element.style.setProperty('--timeline-overflow-left', `${chunkBoundary.overflowLeftPercent}%`);
        const title = chunkBoundary.invalid
          ? `${baseTitle}；超出裁剪后视频结尾，暂时不能烧录`
          : baseTitle;
        element.title = title;
        element.setAttribute('aria-label', title);
      });
    }

    function syncTimelineBoundaryUi(review = null) {
      const current = activeBurnReview(review);
      const boundary = timelineBoundaryDetails(current);
      syncTimelineChunkBoundaryElements(
        '[data-video-preview-tts-chunk]',
        state.videoPreviewModal?.ttsTimelineChunks || [],
        boundary,
      );
      syncTimelineChunkBoundaryElements(
        '[data-video-preview-html-motion-chunk]',
        state.videoPreviewModal?.htmlMotionTimelineChunks || [],
        boundary,
      );
      syncBurnConfirmButton(current, boundary);
      return boundary;
    }

    function syncBurnConfirmButton(review = {}, boundary = timelineBoundaryDetails(review)) {
      const button = burnConfirmButton();
      if (!button) return;
      const ready = review?.reviewReady === true;
      const htmlMotionBusy = Boolean(
        state.videoPreviewModal?.htmlMotionTaskId
        || state.videoPreviewModal?.htmlMotionSubmitting,
      );
      const kinds = Array.isArray(review?.pendingKinds) ? review.pendingKinds : [];
      const reason = timelineBoundaryReason(boundary);
      button.disabled = !ready || htmlMotionBusy || !boundary.valid;
      button.dataset.pendingKinds = kinds.join(',');
      button.classList.toggle('has-timeline-blocker', !boundary.valid);
      button.title = htmlMotionBusy
        ? 'HTML 动效仍在生成，完成后再统一烧录'
        : reason
        ? reason
        : ready
        ? `待烧录：${kinds.map((kind) => ({
            videoTimeline: '视频裁剪',
            tts: '配音',
            htmlMotion: 'HTML 动效',
          }[kind] || kind)).join('、')}`
        : '当前没有待烧录预览';
      button.setAttribute('aria-label', reason ? `确认烧录：${reason}` : '确认烧录');
      setVideoPreviewButtonLabel(button, '确认烧录');
    }

    async function requestConfirmedBurn(key) {
      const res = await fetch('/api/user-generated-results/confirm-burn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          userGeneratedKey: key,
          htmlMotionChunks: state.videoPreviewModal?.htmlMotionTimelineChunks || [],
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw buildRequestError(data);
      return data;
    }

    function updateConfirmedBurnVideo(data, shouldResumePlayback) {
      const video = els.videoPreviewBody?.querySelector('video');
      video?.closest('.video-preview-stage')
        ?.querySelector('[data-video-preview-html-motion-live]')
        ?.remove();
      if (!video) return;
      const officialSrc = String(video.dataset.officialSrc || data.videoUrl || '').split('?')[0];
      video.src = `${officialSrc}?burn=${Date.now()}`;
      video.load();
      if (shouldResumePlayback) video.play().catch(() => {});
      else video.pause();
    }

    function closeBurnTimelinePanels() {
      const selectors = [
        '[data-video-preview-video-timeline]',
        '[data-video-preview-tts-timeline]',
        '[data-video-preview-html-motion-timeline]',
      ];
      selectors.forEach((selector) => {
        const panel = els.videoPreviewBody?.querySelector(selector);
        panel?.classList.remove('is-open');
        if (panel) {
          panel.hidden = true;
          panel.setAttribute('aria-hidden', 'true');
        }
      });
      els.videoPreviewBody?.querySelector('[data-video-preview-action="edit-video-timeline"]')
        ?.setAttribute('aria-expanded', 'false');
    }

    async function applyConfirmedBurn(data, shouldResumePlayback, button) {
      setTtsScissorMode(false, { render: false, updateStatus: false });
      setTtsSelectedChunkIndex(null);
      setVideoSeekMode(false, { updateStatus: false });
      setVideoScissorMode(false, { render: false, updateStatus: false });
      setVideoSelectedChunkIndex(null);
      updateConfirmedBurnVideo(data, shouldResumePlayback);
      await refreshUserGeneratedResults();
      renderResultModal();
      renderStatus();
      configureHtmlMotionTimeline({ timelineAdjustable: false });
      configureVideoTimeline(data?.burnReview?.videoTimeline || {});
      configureTtsTimeline(data?.burnReview?.tts || {});
      syncVideoTimelineDependentEditors(data?.burnReview?.videoTimeline || {});
      closeBurnTimelinePanels();
      syncBurnConfirmButton({ reviewReady: false });
      setVideoPreviewButtonLabel(button, '已烧录');
      setTimeout(() => setVideoPreviewButtonLabel(button, '确认烧录'), 1400);
    }

    async function confirmBurnFromVideoPreview(userGeneratedKey, button) {
      const key = String(userGeneratedKey || '').trim();
      if (!key || !button) return;
      const boundary = syncTimelineBoundaryUi();
      if (!boundary.valid) {
        window.alert(timelineBoundaryReason(boundary));
        return;
      }
      const currentVideo = els.videoPreviewBody?.querySelector('video');
      const shouldResumePlayback = Boolean(currentVideo && !currentVideo.paused);
      button.disabled = true;
      button.classList.add('is-spinning');
      button.setAttribute('aria-busy', 'true');
      setVideoPreviewButtonLabel(button, '烧录中');
      try {
        const data = await requestConfirmedBurn(key);
        await applyConfirmedBurn(data, shouldResumePlayback, button);
      } catch (error) {
        syncBurnConfirmButton(state.videoPreviewModal?.burnReview || {});
        window.alert(error?.message || '确认烧录失败');
      } finally {
        button.classList.remove('is-spinning');
        button.setAttribute('aria-busy', 'false');
      }
    }
