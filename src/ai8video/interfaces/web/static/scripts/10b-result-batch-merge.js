    function resolveResultBatchMergeKey(item) {
      const videoSrc = resolvePlayableVideoSrc(item);
      return String(item?.userGeneratedKey || deriveUserGeneratedKeyFromMediaUrl(videoSrc) || '').trim();
    }

    function toggleResultBatchMergeMode() {
      const modalState = ensureResultModalState();
      if (modalState.batchMergeSubmitting) return;
      modalState.batchMerge = !modalState.batchMerge;
      modalState.selectedKeys = [];
      renderResultModal({ preserveScroll: true });
    }

    function toggleResultBatchMergeSelection(rawKey) {
      const modalState = ensureResultModalState();
      if (!modalState.batchMerge || modalState.batchMergeSubmitting) return;
      const key = String(rawKey || '').trim();
      if (!key) return;
      const index = modalState.selectedKeys.indexOf(key);
      if (index >= 0) modalState.selectedKeys.splice(index, 1);
      else modalState.selectedKeys.push(key);
      renderResultModal({ preserveScroll: true });
    }

    async function confirmResultBatchMerge() {
      const modalState = ensureResultModalState();
      const keys = [...modalState.selectedKeys];
      if (modalState.batchMergeSubmitting || keys.length < 2) return;
      modalState.batchMergeSubmitting = true;
      renderResultModal({ preserveScroll: true });
      try {
        const res = await fetch('/api/user-generated-results/batch-merge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKeys: keys }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        await refreshUserGeneratedResults();
        modalState.batchMerge = false;
        modalState.selectedKeys = [];
      } catch (error) {
        window.alert(error?.message || '批量合并失败');
      } finally {
        modalState.batchMergeSubmitting = false;
        renderResultModal();
        renderProgress();
      }
    }
