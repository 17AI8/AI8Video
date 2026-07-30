    const TIMELINE_HISTORY_LIMIT = 50;

    function cloneTimelineHistoryChunks(chunks) {
      return (Array.isArray(chunks) ? chunks : []).map((chunk) => ({ ...chunk }));
    }

    function timelineHistoryState() {
      const modal = state.videoPreviewModal;
      if (!modal) return null;
      if (!Array.isArray(modal.timelineHistory)) modal.timelineHistory = [];
      if (!Array.isArray(modal.timelineFuture)) modal.timelineFuture = [];
      return modal;
    }

    function initializeTimelineHistory(userGeneratedKey) {
      const modal = timelineHistoryState();
      if (!modal) return;
      modal.timelineHistoryKey = String(userGeneratedKey || '');
      modal.timelineHistory = [];
      modal.timelineFuture = [];
      modal.timelineHistoryApplying = false;
      modal.timelineInteractionCount = 0;
      syncTimelineHistoryButtons();
    }

    function clearTimelineHistory() {
      const modal = timelineHistoryState();
      if (!modal) return;
      modal.timelineHistory = [];
      modal.timelineFuture = [];
      modal.timelineHistoryApplying = false;
      syncTimelineHistoryButtons();
    }

    function applyRegeneratedBurnReview(burnReview, video) {
      clearTimelineHistory();
      applyBurnReviewToVideoPreview(burnReview, video);
    }

    function configureRegeneratedHtmlMotionTimeline(overlay) {
      clearTimelineHistory();
      configureHtmlMotionTimeline(overlay);
    }

    function captureTimelineHistorySnapshot() {
      const modal = timelineHistoryState();
      if (!modal) return null;
      return {
        videoChunks: cloneTimelineHistoryChunks(modal.videoTimelineChunks),
        videoOutputDuration: Number(modal.videoTimelineOutputDuration || 0),
        ttsChunks: cloneTimelineHistoryChunks(modal.ttsTimelineChunks),
        htmlChunks: cloneTimelineHistoryChunks(modal.htmlMotionTimelineChunks),
        htmlDirty: modal.htmlMotionTimelineDirty === true,
      };
    }

    function timelineHistorySnapshotSignature(snapshot) {
      return snapshot ? JSON.stringify(snapshot) : '';
    }

    function recordTimelineHistory(track, label, before) {
      const modal = timelineHistoryState();
      const after = captureTimelineHistorySnapshot();
      if (!modal || modal.timelineHistoryApplying || !before || !after) return false;
      if (timelineHistorySnapshotSignature(before) === timelineHistorySnapshotSignature(after)) return false;
      modal.timelineHistory.push({ track, label: String(label || '时间轴编辑'), before, after });
      if (modal.timelineHistory.length > TIMELINE_HISTORY_LIMIT) modal.timelineHistory.shift();
      modal.timelineFuture = [];
      syncTimelineHistoryButtons();
      return true;
    }

    function timelineHistoryBusy() {
      const modal = state.videoPreviewModal;
      return Boolean(
        modal?.timelineHistoryApplying
        || Number(modal?.timelineInteractionCount || 0) > 0
        || modal?.videoTimelineBusy
        || modal?.ttsTimelineBusy
        || modal?.htmlMotionSubmitting
        || modal?.htmlMotionTaskId,
      );
    }

    function syncTimelineHistoryButton(action, entry, busy) {
      const button = els.videoPreviewBody?.querySelector(`[data-video-preview-action="${action}-timeline"]`);
      if (!button) return;
      const verb = action === 'undo' ? '撤销' : '重做';
      button.disabled = busy || !entry;
      button.title = entry ? `${verb}：${entry.label}` : `没有可${verb}的时间轴编辑`;
      button.setAttribute('aria-label', button.title);
    }

    function syncTimelineHistoryButtons() {
      const modal = timelineHistoryState();
      const busy = timelineHistoryBusy();
      syncTimelineHistoryButton('undo', modal?.timelineHistory?.at(-1), busy);
      syncTimelineHistoryButton('redo', modal?.timelineFuture?.at(-1), busy);
    }

    function renderCurrentTtsTimeline() {
      renderTtsTimelineChunks(
        els.videoPreviewBody?.querySelector('[data-video-preview-tts-chunks]'),
        state.videoPreviewModal?.ttsTimelineChunks || [],
        Number(state.videoPreviewModal?.ttsTimelineDuration || 0),
      );
    }

    function restoreTimelineHistorySnapshot(snapshot) {
      const modal = timelineHistoryState();
      if (!modal || !snapshot) return;
      modal.videoTimelineChunks = cloneTimelineHistoryChunks(snapshot.videoChunks);
      modal.videoTimelineOutputDuration = Number(snapshot.videoOutputDuration || 0);
      modal.ttsTimelineChunks = cloneTimelineHistoryChunks(snapshot.ttsChunks);
      modal.htmlMotionTimelineChunks = cloneTimelineHistoryChunks(snapshot.htmlChunks);
      modal.htmlMotionTimelineDirty = snapshot.htmlDirty === true;
      modal.videoTimelineSelectedChunkIndex = null;
      modal.ttsSelectedChunkIndex = null;
      modal.htmlMotionSelectedChunkIndex = null;
    }

    function renderTimelineHistorySnapshot() {
      setVideoSeekMode(false, { updateStatus: false });
      setVideoScissorMode(false, { render: false, updateStatus: false });
      setTtsScissorMode(false, { render: false, updateStatus: false });
      setHtmlMotionScissorMode(false, { render: false, updateStatus: false });
      renderVideoTimelineChunks();
      renderCurrentTtsTimeline();
      renderCurrentHtmlMotionTimeline();
      const video = els.videoPreviewBody?.querySelector('video');
      video?.pause();
      syncLiveHtmlMotionPreview(video);
      syncTimelineBoundaryUi();
    }

    function setTimelineHistoryFailureStatus(track, verb) {
      const message = `${verb}失败，已恢复编辑前状态`;
      if (track === 'video') setVideoTimelineStatus(message, 'error');
      else if (track === 'tts') setTtsTimelineStatus(message, 'error');
      else setHtmlMotionTimelineStatus(message, 'error');
    }

    async function persistTimelineHistorySnapshot(entry, direction) {
      const key = currentVideoPreviewUserGeneratedKey();
      if (!key) return false;
      const verb = direction === 'undo' ? '已撤销' : '已重做';
      const message = `${verb}：${entry.label}`;
      if (entry.track === 'video') return previewVideoTimeline(key, message);
      if (entry.track === 'tts') return previewTtsTimeline(key, message);
      if (entry.track === 'html') {
        return (await commitLocalHtmlMotionTimeline(
          message,
          state.videoPreviewModal?.htmlMotionTimelineDirty === true,
        )) !== false;
      }
      return true;
    }

    async function applyTimelineHistory(direction) {
      const modal = timelineHistoryState();
      if (!modal || timelineHistoryBusy()) return;
      const source = direction === 'undo' ? modal.timelineHistory : modal.timelineFuture;
      const target = direction === 'undo' ? modal.timelineFuture : modal.timelineHistory;
      const entry = source.pop();
      if (!entry) return syncTimelineHistoryButtons();
      target.push(entry);
      modal.timelineHistoryApplying = true;
      syncTimelineHistoryButtons();
      const snapshot = direction === 'undo' ? entry.before : entry.after;
      const rollback = direction === 'undo' ? entry.after : entry.before;
      restoreTimelineHistorySnapshot(snapshot);
      renderTimelineHistorySnapshot();
      let success = false;
      try {
        success = await persistTimelineHistorySnapshot(entry, direction);
      } catch {
        success = false;
      }
      if (!success) {
        target.pop();
        source.push(entry);
        restoreTimelineHistorySnapshot(rollback);
        renderTimelineHistorySnapshot();
        setTimelineHistoryFailureStatus(entry.track, direction === 'undo' ? '撤销' : '重做');
      }
      modal.timelineHistoryApplying = false;
      syncTimelineHistoryButtons();
    }

    function undoTimelineHistory() {
      void applyTimelineHistory('undo');
    }

    function redoTimelineHistory() {
      void applyTimelineHistory('redo');
    }

    function timelineEditorsOpen() {
      return Boolean(els.videoPreviewBody?.querySelector(
        '[data-video-preview-video-timeline].is-open, [data-video-preview-tts-timeline].is-open, [data-video-preview-html-motion-timeline].is-open',
      ));
    }

    document.addEventListener('click', (event) => {
      const action = event.target?.closest?.('[data-video-preview-action]')?.dataset.videoPreviewAction;
      if (action === 'undo-timeline') undoTimelineHistory();
      if (action === 'redo-timeline') redoTimelineHistory();
    });

    document.addEventListener('keydown', (event) => {
      if (!timelineEditorsOpen() || els.videoPreviewModal?.classList.contains('hidden')) return;
      const editing = event.target?.matches?.('input, textarea, select, [contenteditable="true"]');
      if (editing || !(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key !== 'z' && key !== 'y') return;
      event.preventDefault();
      if (key === 'y' || event.shiftKey) redoTimelineHistory();
      else undoTimelineHistory();
    });
