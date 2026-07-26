    function currentTtsSelectedChunkIndex() {
      const index = state.videoPreviewModal?.ttsSelectedChunkIndex;
      return Number.isInteger(index) ? index : null;
    }

    function syncTtsDeleteButton() {
      const button = els.videoPreviewBody?.querySelector('[data-video-preview-action="delete-selected-tts-chunk"]');
      if (!button) return;
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const selectedIndex = currentTtsSelectedChunkIndex();
      const selected = selectedIndex !== null && selectedIndex >= 0 && selectedIndex < chunks.length;
      const busy = state.videoPreviewModal?.ttsTimelineBusy === true;
      button.disabled = busy || isTtsScissorMode() || chunks.length <= 1 || !selected;
      button.title = busy
        ? '正在生成配音预览'
        : isTtsScissorMode()
        ? '关闭剪刀工具后再选择要删除的配音块'
        : chunks.length <= 1
        ? '至少保留一个配音块，请先使用剪刀切块'
        : selected
        ? `删除${chunks[selectedIndex]?.label || `配音 ${selectedIndex + 1}`}`
        : '请先点击选择一个配音块';
    }

    function setTtsSelectedChunkIndex(index) {
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const selected = Number.isInteger(index) && index >= 0 && index < chunks.length ? index : null;
      if (state.videoPreviewModal) state.videoPreviewModal.ttsSelectedChunkIndex = selected;
      els.videoPreviewBody?.querySelectorAll('[data-video-preview-tts-chunk]').forEach((element) => {
        const active = Number(element.dataset.chunkIndex) === selected;
        element.classList.toggle('is-selected', active);
        element.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      syncTtsDeleteButton();
    }

    function deleteSelectedTtsChunk(userGeneratedKey) {
      if (state.videoPreviewModal?.ttsTimelineBusy || isTtsScissorMode()) return;
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const index = currentTtsSelectedChunkIndex();
      if (index === null || !chunks[index]) {
        setTtsTimelineStatus('请先点击选择要删除的配音块', 'error');
        return;
      }
      if (chunks.length <= 1) {
        setTtsTimelineStatus('至少保留一个配音块，请先使用剪刀切块', 'error');
        return;
      }
      const removed = chunks[index];
      const remaining = chunks.filter((_, chunkIndex) => chunkIndex !== index);
      state.videoPreviewModal.ttsTimelineChunks = remaining;
      setTtsSelectedChunkIndex(null);
      renderTtsTimelineChunks(
        els.videoPreviewBody?.querySelector('[data-video-preview-tts-chunks]'),
        remaining,
        Number(state.videoPreviewModal.ttsTimelineDuration || 0),
      );
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) video.currentTime = Math.max(0, Number(removed.startSeconds || 0));
      syncTtsTimelinePlayhead();
      const label = removed.label || `配音 ${index + 1}`;
      void previewTtsTimeline(userGeneratedKey, `${label}已删除，原位置保留静音`);
    }
