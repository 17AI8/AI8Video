    function currentTtsSelectedChunkIndex() {
      return currentTtsSelectedChunkIndexes()[0] ?? null;
    }

    function currentTtsSelectedChunkIndexes() {
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const stored = state.videoPreviewModal?.ttsSelectedChunkIndexes;
      const fallback = state.videoPreviewModal?.ttsSelectedChunkIndex;
      const values = Array.isArray(stored) ? stored : (Number.isInteger(fallback) ? [fallback] : []);
      return [...new Set(values)]
        .filter((index) => Number.isInteger(index) && index >= 0 && index < chunks.length)
        .sort((left, right) => left - right);
    }

    function syncTtsDeleteButton() {
      const button = els.videoPreviewBody?.querySelector('[data-video-preview-action="delete-selected-tts-chunk"]');
      if (!button) return;
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const selectedIndexes = currentTtsSelectedChunkIndexes();
      const selected = selectedIndexes.length > 0;
      const busy = state.videoPreviewModal?.ttsTimelineBusy === true;
      button.disabled = busy || isTtsScissorMode() || chunks.length <= 1 || !selected;
      button.title = busy
        ? '正在生成配音预览'
        : isTtsScissorMode()
        ? '关闭剪刀工具后再选择要删除的配音块'
        : selectedIndexes.length >= chunks.length
        ? '至少保留一个配音块'
        : selected
        ? `删除所选 ${selectedIndexes.length} 个配音块`
        : '请先点击选择一个配音块';
    }

    function setTtsSelectedChunkIndex(index, exclusive = true) {
      setTtsSelectedChunkIndexes(Number.isInteger(index) ? [index] : [], exclusive);
    }

    function setTtsSelectedChunkIndexes(indexes, exclusive = true) {
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const selected = [...new Set(Array.isArray(indexes) ? indexes : [])]
        .filter((index) => Number.isInteger(index) && index >= 0 && index < chunks.length)
        .sort((left, right) => left - right);
      if (selected.length && exclusive) {
        setVideoSelectedChunkIndex(null, false);
        setHtmlMotionSelectedChunkIndex(null, false);
      }
      if (state.videoPreviewModal) {
        state.videoPreviewModal.ttsSelectedChunkIndexes = selected;
        state.videoPreviewModal.ttsSelectedChunkIndex = selected[0] ?? null;
      }
      const selectedSet = new Set(selected);
      els.videoPreviewBody?.querySelectorAll('[data-video-preview-tts-chunk]').forEach((element) => {
        const active = selectedSet.has(Number(element.dataset.chunkIndex));
        element.classList.toggle('is-selected', active);
        element.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      syncTtsDeleteButton();
    }

    function toggleTtsChunkSelection(index) {
      const selected = new Set(currentTtsSelectedChunkIndexes());
      if (selected.has(index)) selected.delete(index); else selected.add(index);
      setTtsSelectedChunkIndexes([...selected]);
    }

    function deleteSelectedTtsChunk(userGeneratedKey) {
      if (state.videoPreviewModal?.ttsTimelineBusy || isTtsScissorMode()) return;
      const chunks = state.videoPreviewModal?.ttsTimelineChunks || [];
      const selectedIndexes = currentTtsSelectedChunkIndexes();
      if (!selectedIndexes.length) {
        setTtsTimelineStatus('请先点击选择要删除的配音块', 'error');
        return;
      }
      if (selectedIndexes.length >= chunks.length) {
        setTtsTimelineStatus('至少保留一个配音块', 'error');
        return;
      }
      const historyBefore = captureTimelineHistorySnapshot();
      const selectedSet = new Set(selectedIndexes);
      const removed = chunks[selectedIndexes[0]];
      const remaining = chunks.filter((_, chunkIndex) => !selectedSet.has(chunkIndex));
      state.videoPreviewModal.ttsTimelineChunks = remaining;
      setTtsSelectedChunkIndex(null);
      recordTimelineHistory('tts', `删除 ${selectedIndexes.length} 个配音块`, historyBefore);
      renderCurrentTtsTimeline();
      const video = els.videoPreviewBody?.querySelector('video');
      if (video) video.currentTime = Math.max(0, Number(removed.startSeconds || 0));
      syncTtsTimelinePlayhead();
      void previewTtsTimeline(userGeneratedKey, `已删除 ${selectedIndexes.length} 个配音块，原位置保留静音`);
    }
