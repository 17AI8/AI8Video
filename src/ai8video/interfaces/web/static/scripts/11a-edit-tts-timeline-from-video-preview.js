    function ttsTimelineDefaultStatus(data = {}) {
      if (data?.pending) return '已修改，等待确认烧录';
      if (data?.waveformStatus === 'failed') return '波形读取失败，仍可切块、删除和拖动';
      return '完整配音，切块、删除或拖动后生成预览';
    }

    function isTtsScissorMode() {
      return state.videoPreviewModal?.ttsScissorMode === true;
    }

    function setTtsScissorMode(active, options = {}) {
      const panel = els.videoPreviewBody?.querySelector('[data-video-preview-tts-timeline]');
      const button = panel?.querySelector('[data-video-preview-action="toggle-tts-scissors"]');
      const enabled = Boolean(active && panel?.classList.contains('is-open'));
      if (state.videoPreviewModal) state.videoPreviewModal.ttsScissorMode = enabled;
      panel?.classList.toggle('is-scissor-mode', enabled);
      if (enabled) setTtsSelectedChunkIndex(null);
      if (button) {
        button.classList.toggle('is-active', enabled);
        button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
        button.title = enabled ? '关闭剪刀工具' : '开启剪刀工具';
      }
      if (options.render !== false) {
        renderTtsTimelineChunks(
          panel?.querySelector('[data-video-preview-tts-chunks]'),
          state.videoPreviewModal?.ttsTimelineChunks || [],
          Number(state.videoPreviewModal?.ttsTimelineDuration || 0),
        );
      }
      if (options.updateStatus !== false) {
        const message = enabled
          ? '剪刀工具已开启：点击波形位置切块'
          : ttsTimelineDefaultStatus(state.videoPreviewModal?.burnReview?.tts || {});
        setTtsTimelineStatus(message);
      }
    }

    function toggleTtsScissorMode() {
      setTtsScissorMode(!isTtsScissorMode());
    }

    function showBurnCandidatePreview(video, previewUrl) {
      if (!video || !previewUrl) return;
      const shouldPlay = !video.paused;
      const currentTime = Math.max(0, Number(video.currentTime || 0));
      const restorePlayback = () => {
        video.currentTime = Math.min(currentTime, Math.max(0, Number(video.duration || currentTime)));
        if (shouldPlay) video.play().catch(() => {});
        else video.pause();
        syncTtsTimelinePlayhead();
        syncVideoTimelinePlayhead();
      };
      video.closest('.video-preview-stage')
        ?.querySelector('[data-video-preview-html-motion-live]')
        ?.remove();
      video.pause();
      video.autoplay = shouldPlay;
      video.addEventListener('loadedmetadata', restorePlayback, { once: true });
      video.src = `${previewUrl}${previewUrl.includes('?') ? '&' : '?'}v=${Date.now()}`;
      video.load();
    }

    function applyBurnReviewToVideoPreview(review = {}, video = null, options = {}) {
      if (!state.videoPreviewModal) return;
      state.videoPreviewModal.burnReview = review;
      const targetVideo = video || els.videoPreviewBody?.querySelector('video');
      configureVideoTimeline(review?.videoTimeline || {});
      configureTtsTimeline(review?.tts || {});
      if (review?.htmlMotion) configureHtmlMotionTimeline(review.htmlMotion);
      syncVideoTimelineDependentEditors(review?.videoTimeline || {});
      syncBurnConfirmButton(review);
      if (options.showPreview === false || !review?.reviewReady) return;
      if (review?.videoTimeline?.pending === true && review.previewUrl) {
        showBurnCandidatePreview(targetVideo, review.previewUrl);
        mountPendingHtmlMotionPreview(targetVideo, review);
        return;
      }
      if (review?.tts?.pending === true && review.previewUrl) {
        showBurnCandidatePreview(targetVideo, review.previewUrl);
        mountPendingHtmlMotionPreview(targetVideo, review);
        return;
      }
      if (review?.htmlMotion?.reviewReady === true) {
        showHtmlMotionPreview(targetVideo, review.previewUrl, {
          ...review.htmlMotion,
          livePreviewUrl: review.livePreviewUrl || review.htmlMotion.livePreviewUrl,
        });
      }
    }

    function mountPendingHtmlMotionPreview(video, review = {}) {
      const livePreviewUrl = String(review.livePreviewUrl || review.htmlMotion?.livePreviewUrl || '').trim();
      if (review.htmlMotion?.reviewReady !== true || !livePreviewUrl) return;
      if (state.videoPreviewModal) state.videoPreviewModal.htmlMotionLivePreviewUrl = livePreviewUrl;
      mountLiveHtmlMotionPreview(
        video,
        livePreviewUrl,
        state.videoPreviewModal?.htmlMotionTimelineChunks || review.htmlMotion.timelineChunks || [],
        { preserveVideoSource: true },
      );
    }

    async function syncBurnReviewFromVideoPreview(userGeneratedKey, video, options = {}) {
      const key = String(userGeneratedKey || '').trim();
      if (!key) return null;
      try {
        const res = await fetch('/api/user-generated-results/burn-review', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        applyBurnReviewToVideoPreview(data, video, options);
        return data;
      } catch (error) {
        syncBurnConfirmButton({ reviewReady: false });
        if (options.silent !== true) window.alert(error?.message || '读取待烧录预览失败');
        return null;
      }
    }

    async function toggleTtsTimelineEditor(userGeneratedKey, button) {
      const panel = els.videoPreviewBody?.querySelector('[data-video-preview-tts-timeline]');
      if (!panel) return;
      let tts = state.videoPreviewModal?.burnReview?.tts;
      if (!tts) {
        const review = await syncBurnReviewFromVideoPreview(
          userGeneratedKey,
          els.videoPreviewBody?.querySelector('video'),
          { showPreview: false },
        );
        tts = review?.tts;
      }
      if (tts?.available !== true) {
        window.alert(tts?.reason || '当前视频没有可编辑的独立 TTS 音频');
        return;
      }
      const open = !panel.classList.contains('is-open');
      panel.hidden = false;
      panel.classList.toggle('is-open', open);
      panel.setAttribute('aria-hidden', open ? 'false' : 'true');
      button?.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) syncTtsTimelinePlayhead();
      else {
        setTtsScissorMode(false, { render: false, updateStatus: false });
        setTtsSelectedChunkIndex(null);
      }
    }

    function configureTtsTimeline(data = {}) {
      const panel = els.videoPreviewBody?.querySelector('[data-video-preview-tts-timeline]');
      const durationLabel = panel?.querySelector('[data-video-preview-tts-duration]');
      const status = panel?.querySelector('[data-video-preview-tts-status]');
      const track = panel?.querySelector('[data-video-preview-tts-chunks]');
      const chunks = Array.isArray(data?.timelineChunks)
        ? data.timelineChunks.map((item) => timelineChunkWithRestoreBounds(item))
        : [];
      const waveformPeaks = Array.isArray(data?.waveformPeaks)
        ? data.waveformPeaks.map((value) => Math.min(1, Math.max(0, Number(value) || 0)))
        : [];
      const available = data?.available === true && chunks.length > 0;
      if (!available) {
        setTtsScissorMode(false, { render: false, updateStatus: false });
        setTtsSelectedChunkIndex(null);
        if (state.videoPreviewModal) state.videoPreviewModal.ttsWaveformPeaks = [];
        if (state.videoPreviewModal) state.videoPreviewModal.ttsTimelineRevision = 0;
        if (panel) {
          panel.classList.remove('is-open');
          panel.setAttribute('aria-hidden', 'true');
          panel.hidden = true;
        }
        return;
      }
      const duration = Math.max(0, Number(data?.durationSeconds || 0));
      state.videoPreviewModal.ttsTimelineChunks = chunks;
      state.videoPreviewModal.ttsTimelineRevision = Number(data?.revision || 0);
      state.videoPreviewModal.ttsTimelineDuration = duration;
      state.videoPreviewModal.ttsAudioDuration = Math.max(0, Number(data?.audioDurationSeconds || 0));
      state.videoPreviewModal.ttsWaveformPeaks = waveformPeaks;
      const selectedIndexes = currentTtsSelectedChunkIndexes()
        .filter((index) => index < chunks.length);
      state.videoPreviewModal.ttsSelectedChunkIndexes = selectedIndexes;
      state.videoPreviewModal.ttsSelectedChunkIndex = selectedIndexes[0] ?? null;
      if (durationLabel) durationLabel.textContent = `${duration.toFixed(1)} 秒`;
      if (status) {
        setTtsTimelineStatus(ttsTimelineDefaultStatus(data));
        status.title = data?.waveformReason || '';
      }
      renderTtsTimelineChunks(track, chunks, duration);
    }

    function buildTtsWaveformPath(peaks, chunk, audioDuration) {
      if (!Array.isArray(peaks) || peaks.length < 2 || audioDuration <= 0) return '';
      const sourceStart = Math.max(0, Number(chunk.originalSourceStartSeconds ?? chunk.sourceStartSeconds ?? 0));
      const sourceEnd = Math.max(
        sourceStart,
        Number(chunk.originalSourceEndSeconds ?? chunk.sourceEndSeconds ?? sourceStart),
      );
      const startIndex = Math.min(peaks.length - 1, Math.floor(sourceStart / audioDuration * peaks.length));
      const endIndex = Math.max(startIndex + 1, Math.min(peaks.length, Math.ceil(sourceEnd / audioDuration * peaks.length)));
      const section = peaks.slice(startIndex, endIndex);
      if (section.length < 2) return '';
      const center = 20;
      const halfHeight = 16;
      const points = section.map((peak, index) => {
        const x = index / (section.length - 1) * 1000;
        const amplitude = Math.max(0.06, Math.min(1, Number(peak) || 0));
        return { x: x.toFixed(1), offset: (amplitude * halfHeight).toFixed(1) };
      });
      const top = points.map((point) => `${point.x},${(center - Number(point.offset)).toFixed(1)}`).join(' ');
      const bottom = points.slice().reverse()
        .map((point) => `${point.x},${(center + Number(point.offset)).toFixed(1)}`).join(' ');
      return `M ${top} L ${bottom} Z`;
    }

    function renderTtsTimelineChunks(track, chunks, duration) {
      if (!track || duration <= 0) return;
      const waveformPeaks = state.videoPreviewModal?.ttsWaveformPeaks || [];
      const audioDuration = Number(state.videoPreviewModal?.ttsAudioDuration || 0);
      const scissorMode = isTtsScissorMode();
      const selectedIndexes = new Set(currentTtsSelectedChunkIndexes());
      const markup = chunks.map((chunk, index) => {
        const start = Math.max(0, Number(chunk.startSeconds || 0));
        const geometry = timelineFixedContentGeometry(chunk, duration);
        const label = chunk.label || `配音 ${index + 1}`;
        const actionLabel = scissorMode
          ? `剪刀工具：点击${label}中的位置切块`
          : `选择并跳转到${label}，${start.toFixed(1)}秒；拖动整体可移动，拖动左右边缘可裁剪或恢复`;
        const selected = selectedIndexes.has(index);
        const waveformPath = buildTtsWaveformPath(waveformPeaks, chunk, audioDuration);
        const waveform = waveformPath
          ? `<svg class="video-preview-tts-waveform" viewBox="0 0 1000 40" preserveAspectRatio="none" aria-hidden="true" focusable="false"><line x1="0" y1="20" x2="1000" y2="20"></line><path d="${waveformPath}"></path></svg>`
          : '<span class="video-preview-tts-waveform-empty" aria-hidden="true"></span>';
        return `<button type="button" class="video-preview-tts-chunk${selected ? ' is-selected' : ''}" data-video-preview-tts-chunk data-chunk-index="${index}" data-boundary-base-title="${escapeHtml(actionLabel)}" aria-label="${escapeHtml(actionLabel)}" aria-pressed="${selected ? 'true' : 'false'}" title="${escapeHtml(actionLabel)}" style="left:${geometry.left}%;width:${geometry.width}%;--video-preview-tts-waveform-scale:${geometry.contentScale}%;--video-preview-tts-waveform-offset:${geometry.contentOffset}%">${waveform}<span class="video-preview-tts-chunk-meta"><span>${escapeHtml(label)}</span><small>${start.toFixed(1)}s</small></span>${timelineTrimHandleMarkup(label)}</button>`;
      }).join('');
      const boundary = timelineBoundaryDetails();
      track.innerHTML = `${timelineRulerMarkup(duration)}<div class="video-preview-tts-chunk-lane" tabindex="0">${timelineOverflowZoneMarkup(duration, boundary.ttsOverflowIndexes, boundary)}<span class="video-preview-tts-marquee" data-video-preview-tts-marquee hidden></span><span class="video-preview-timeline-cut-guide" data-video-preview-cut-guide hidden></span>${timelineSnapGuideMarkup()}<span class="video-preview-tts-playhead" data-video-preview-tts-playhead aria-label="配音时间轴播放头" title="拖动播放头；按住 Shift 临时关闭吸附"></span>${markup}</div>`;
      const lane = track.querySelector('.video-preview-tts-chunk-lane');
      bindTimelineScissorGuide(lane, duration, scissorMode);
      bindTtsMarqueeSelection(lane);
      bindTimelinePlayheadDrag(track.querySelector('[data-video-preview-tts-playhead]'), lane, duration, 'tts');
      track.querySelectorAll('[data-video-preview-tts-chunk]').forEach((element) => {
        bindTtsChunkTrimHandles(element, duration);
        element.addEventListener('pointerdown', (event) => {
          if ((event.shiftKey || event.metaKey || event.ctrlKey) && !isTtsScissorMode()) {
            event.preventDefault();
            toggleTtsChunkSelection(Number(element.dataset.chunkIndex));
            return;
          }
          if (!isTtsScissorMode()) beginTtsChunkDrag(event, element, duration);
        });
        element.addEventListener('click', (event) => {
          if (isTtsScissorMode()) {
            if (event.detail === 0) splitTtsTimelineAtPlayhead(currentVideoPreviewUserGeneratedKey());
            else splitTtsTimelineAtPointer(event, element, duration);
            return;
          }
          if (element.dataset.suppressTtsClick === 'true') {
            delete element.dataset.suppressTtsClick;
            return;
          }
          seekVideoPreviewToTtsChunk(Number(element.dataset.chunkIndex));
        });
      });
      syncTtsDeleteButton();
      syncTtsTimelinePlayhead();
      syncTimelineBoundaryUi();
    }

    function seekVideoPreviewToTtsChunk(index) {
      const chunk = state.videoPreviewModal?.ttsTimelineChunks?.[index];
      const video = els.videoPreviewBody?.querySelector('video');
      if (!chunk || !video) return;
      setTtsSelectedChunkIndex(index);
      video.currentTime = Math.max(0, Number(chunk.startSeconds || 0));
      syncTtsTimelinePlayhead();
    }

    function syncTtsTimelinePlayhead() {
      const video = els.videoPreviewBody?.querySelector('video');
      const playhead = els.videoPreviewBody?.querySelector('[data-video-preview-tts-playhead]');
      const duration = Number(state.videoPreviewModal?.ttsTimelineDuration || video?.duration || 0);
      if (!video || !playhead || duration <= 0) return;
      const current = Math.min(duration, Math.max(0, Number(video.currentTime || 0)));
      playhead.style.left = `${current / duration * 100}%`;
      playhead.setAttribute('aria-valuenow', current.toFixed(3));
    }

    function splitTtsTimelineAtTime(userGeneratedKey, currentTime) {
      if (state.videoPreviewModal?.ttsTimelineBusy) return;
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const current = Math.max(0, Number(currentTime || 0));
      const index = chunks.findIndex((chunk) => {
        const start = Number(chunk.startSeconds || 0);
        const end = start + Number(chunk.durationSeconds || 0);
        return current > start + 0.12 && current < end - 0.12;
      });
      if (index < 0) {
        setTtsTimelineStatus('请把播放头放在某个配音块内部，且距边缘至少 0.12 秒', 'error');
        return;
      }
      const chunk = chunks[index];
      const historyBefore = captureTimelineHistorySnapshot();
      const offset = current - Number(chunk.startSeconds || 0);
      const sourceSplit = Number(chunk.sourceStartSeconds || 0) + offset;
      const bounds = splitTimelineRestoreBounds(chunk, sourceSplit);
      const first = {
        ...bounds.first,
        sourceEndSeconds: sourceSplit,
        durationSeconds: offset,
        endSeconds: current,
      };
      const second = {
        ...bounds.second,
        sourceStartSeconds: sourceSplit,
        startSeconds: current,
        durationSeconds: Number(chunk.sourceEndSeconds || 0) - sourceSplit,
        endSeconds: current + Number(chunk.sourceEndSeconds || 0) - sourceSplit,
      };
      setTtsSelectedChunkIndex(null);
      state.videoPreviewModal.ttsTimelineChunks = [...chunks.slice(0, index), first, second, ...chunks.slice(index + 1)];
      recordTimelineHistory('tts', `切分配音 ${index + 1}`, historyBefore);
      renderCurrentTtsTimeline();
      void previewTtsTimeline(userGeneratedKey, `已在 ${current.toFixed(1)} 秒切块`);
    }

    function splitTtsTimelineAtPlayhead(userGeneratedKey) {
      const video = els.videoPreviewBody?.querySelector('video');
      splitTtsTimelineAtTime(userGeneratedKey, Number(video?.currentTime || 0));
    }

    function splitTtsTimelineAtPointer(event, element, duration) {
      if (state.videoPreviewModal?.ttsTimelineBusy) return;
      const lane = element.closest('.video-preview-tts-chunk-lane');
      if (!lane) return;
      event.preventDefault();
      const current = timelineSecondsAtPointer(event, lane, duration);
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) video.currentTime = current;
      syncTtsTimelinePlayhead();
      splitTtsTimelineAtTime(currentVideoPreviewUserGeneratedKey(), current);
    }

    function resetTtsTimeline(userGeneratedKey) {
      const audioDuration = Number(state.videoPreviewModal?.ttsAudioDuration || 0);
      const visibleDuration = Math.min(
        audioDuration,
        Number(state.videoPreviewModal?.ttsTimelineDuration || audioDuration),
      );
      if (audioDuration <= 0) return;
      const historyBefore = captureTimelineHistorySnapshot();
      setTtsSelectedChunkIndex(null);
      state.videoPreviewModal.ttsTimelineChunks = [{
        index: 0,
        label: '完整配音',
        sourceStartSeconds: 0,
        sourceEndSeconds: visibleDuration,
        originalSourceStartSeconds: 0,
        originalSourceEndSeconds: audioDuration,
        startSeconds: 0,
        durationSeconds: visibleDuration,
        endSeconds: visibleDuration,
      }];
      const changed = recordTimelineHistory('tts', '恢复完整配音', historyBefore);
      renderCurrentTtsTimeline();
      if (!changed) return setTtsTimelineStatus('当前已经是完整配音');
      void previewTtsTimeline(userGeneratedKey, '已恢复为完整配音');
    }

    async function exportTtsMp3FromVideoPreview(userGeneratedKey, button) {
      const key = String(userGeneratedKey || '').trim();
      if (!key || button?.dataset.exportBusy === 'true') return;
      const previousLabel = button?.textContent || '导出 MP3';
      if (button) {
        button.dataset.exportBusy = 'true';
        button.disabled = true;
        button.textContent = '保存为…';
      }
      setTtsTimelineStatus('请设置 MP3 文件名和保存位置', 'working');
      try {
        const res = await fetch('/api/user-generated-results/export-tts-mp3', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        if (data?.canceled) {
          setTtsTimelineStatus('已取消导出');
          return;
        }
        if (button && data?.outputPath) button.title = `已导出到 ${data.outputPath}`;
        setTtsTimelineStatus(`已导出 ${data?.fileName || 'MP3'}`, 'success');
      } catch (error) {
        setTtsTimelineStatus(error?.message || 'MP3 导出失败', 'error');
      } finally {
        if (button) {
          delete button.dataset.exportBusy;
          button.disabled = false;
          button.textContent = previousLabel;
        }
      }
    }

    function setTtsTimelineStatus(message, tone = '') {
      const status = els.videoPreviewBody?.querySelector('[data-video-preview-tts-status]');
      if (!status) return;
      status.textContent = message;
      status.classList.remove('is-working', 'is-success', 'is-error');
      if (tone) status.classList.add(`is-${tone}`);
    }

    async function previewTtsTimeline(userGeneratedKey, successMessage) {
      const key = String(userGeneratedKey || '').trim();
      if (!key || state.videoPreviewModal?.ttsTimelineBusy) return false;
      if (state.videoPreviewModal) state.videoPreviewModal.ttsTimelineBusy = true;
      syncTimelineHistoryButtons();
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const controls = els.videoPreviewBody?.querySelectorAll('[data-video-preview-tts-editor-action]') || [];
      controls.forEach((button) => { button.disabled = true; });
      setTtsTimelineStatus('正在生成配音与动效合并预览', 'working');
      try {
        const res = await fetch('/api/user-generated-results/tts-timeline-preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            userGeneratedKey: key,
            chunks,
            expectedRevision: Number(state.videoPreviewModal?.ttsTimelineRevision || 0),
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        const video = els.videoPreviewBody?.querySelector('video');
        applyBurnReviewToVideoPreview(data.burnReview || {}, video);
        setTtsTimelineStatus(`${successMessage}，等待确认烧录`, 'success');
        return true;
      } catch (error) {
        setTtsTimelineStatus(error?.message || '配音时间轴预览生成失败', 'error');
        return false;
      } finally {
        if (state.videoPreviewModal) state.videoPreviewModal.ttsTimelineBusy = false;
        controls.forEach((button) => { button.disabled = false; });
        syncTtsDeleteButton();
        syncTimelineHistoryButtons();
      }
    }
