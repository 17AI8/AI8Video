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

    function ensureAgentResultBatchMergeState() {
      if (!state.agentResultBatchMerge || typeof state.agentResultBatchMerge !== 'object') {
        state.agentResultBatchMerge = {
          active: false,
          animateOpening: false,
          closing: false,
          submitting: false,
          selectedKeys: [],
        };
      }
      if (!Array.isArray(state.agentResultBatchMerge.selectedKeys)) {
        state.agentResultBatchMerge.selectedKeys = [];
      }
      return state.agentResultBatchMerge;
    }

    function toggleAgentResultBatchMergeMode() {
      const batchState = ensureAgentResultBatchMergeState();
      if (batchState.submitting || batchState.closing) return;
      if (batchState.active) {
        batchState.closing = true;
        document.querySelectorAll('.agent-video-batch-merge-actions, .agent-video-results').forEach((element) => {
          element.classList.add('is-batch-merge-closing');
        });
        window.setTimeout(() => {
          batchState.active = false;
          batchState.closing = false;
          batchState.selectedKeys = [];
          renderMessages();
        }, 180);
        return;
      }
      batchState.active = true;
      batchState.animateOpening = true;
      batchState.selectedKeys = [];
      renderMessages();
      batchState.animateOpening = false;
    }

    function toggleAgentResultBatchMergeSelection(rawKey) {
      const batchState = ensureAgentResultBatchMergeState();
      const key = String(rawKey || '').trim();
      if (!batchState.active || batchState.submitting || !key) return;
      const index = batchState.selectedKeys.indexOf(key);
      if (index >= 0) batchState.selectedKeys.splice(index, 1);
      else batchState.selectedKeys.push(key);
      renderMessages();
    }

    async function confirmAgentResultBatchMerge() {
      const batchState = ensureAgentResultBatchMergeState();
      const keys = [...batchState.selectedKeys];
      if (batchState.submitting || keys.length < 2) return;
      batchState.submitting = true;
      renderMessages();
      try {
        await requestResultBatchMerge(keys);
        await animateAgentResultBatchMerge(keys);
        batchState.active = false;
        batchState.selectedKeys = [];
      } catch (error) {
        window.alert(error?.message || '批量合并失败');
      } finally {
        batchState.submitting = false;
        renderMessages();
      }
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
        await requestResultBatchMerge(keys);
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

    async function requestResultBatchMerge(keys) {
      const res = await fetch('/api/user-generated-results/batch-merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userGeneratedKeys: keys }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw buildRequestError(data);
      await refreshUserGeneratedResults();
      return data;
    }

    function findAgentBatchMergeCard(rawKey) {
      const key = String(rawKey || '').trim();
      if (!key) return null;
      return Array.from(document.querySelectorAll('[data-result-batch-merge-select]'))
        .find((button) => button.getAttribute('data-result-batch-merge-select') === key)
        ?.closest('.result-notify-card') || null;
    }

    function animateAgentResultBatchMerge(keys) {
      const cards = keys.map(findAgentBatchMergeCard).filter(Boolean);
      const anchor = cards[0];
      if (!anchor || cards.length < 2) return Promise.resolve();
      const anchorRect = anchor.getBoundingClientRect();
      anchor.classList.add('is-batch-merge-anchor');
      cards.slice(1).forEach((card) => {
        const rect = card.getBoundingClientRect();
        card.style.setProperty('--batch-merge-x', `${anchorRect.left - rect.left}px`);
        card.style.setProperty('--batch-merge-y', `${anchorRect.top - rect.top}px`);
        card.classList.add('is-batch-merge-folding');
      });
      return new Promise((resolve) => window.setTimeout(resolve, 360));
    }
