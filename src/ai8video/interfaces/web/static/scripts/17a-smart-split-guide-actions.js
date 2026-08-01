    function setSmartSplitFeedbackMode(card, shouldOpen) {
      const drawer = card?.querySelector?.('[data-smart-split-feedback-drawer]');
      const toggle = card?.querySelector?.('[data-smart-split-feedback-toggle]');
      if (!drawer || !toggle) return;
      drawer.hidden = !shouldOpen;
      toggle.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
      card.querySelectorAll('[data-smart-split-hide-on-feedback]').forEach((action) => {
        action.disabled = shouldOpen;
        action.setAttribute('aria-hidden', shouldOpen ? 'true' : 'false');
      });
      if (!shouldOpen) return;
      requestAnimationFrame(() => {
        drawer.querySelector('[data-smart-split-feedback]')?.focus({ preventScroll: true });
        drawer.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    }

    function setSmartSplitPromptEditing(node, editing) {
      const prompt = node?.querySelector?.('[data-smart-split-plan-prompt]');
      const editor = node?.querySelector?.('[data-smart-split-plan-editor]');
      const editButton = node?.querySelector?.('[data-smart-split-plan-edit]');
      const saveButton = node?.querySelector?.('[data-smart-split-plan-save]');
      if (!prompt || !editor || !editButton || !saveButton) return;
      prompt.hidden = editing;
      editor.hidden = !editing;
      editButton.disabled = editing;
      saveButton.disabled = !editing;
      if (editing) {
        editor.value = prompt.textContent || '';
        requestAnimationFrame(() => editor.focus({ preventScroll: true }));
      }
    }

    async function saveSmartSplitPrompt(trigger) {
      const node = trigger?.closest?.('.smart-split-plan-node');
      const editor = node?.querySelector?.('[data-smart-split-plan-editor]');
      const prompt = String(editor?.value || '').trim();
      const videoIndex = Number(trigger?.dataset?.videoIndex || 0);
      const session = getActiveSession();
      if (!node || !session?.id || videoIndex < 1 || !prompt) return;
      trigger.disabled = true;
      trigger.textContent = '保存中';
      try {
        const res = await fetch('/api/smart-split-plan/prompt', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionId: session.id, videoIndex, prompt }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        updateLocalSmartSplitPrompt(session, videoIndex, prompt);
        node.querySelector('[data-smart-split-plan-prompt]').textContent = prompt;
        persistSessions();
        setSmartSplitPromptEditing(node, false);
        trigger.textContent = '已保存';
        window.setTimeout(() => { if (trigger.isConnected) trigger.textContent = '保存'; }, 1000);
      } catch (error) {
        trigger.disabled = false;
        trigger.textContent = '重试保存';
        trigger.title = formatNetworkError(error);
      }
    }

    function updateLocalSmartSplitPrompt(session, videoIndex, prompt) {
      for (const message of session.messages || []) {
        const videos = message?.payload?.meta?.guide?.plannedVideos;
        if (!Array.isArray(videos)) continue;
        const video = videos.find((item) => Number(item?.index) === videoIndex);
        if (video) video.prompt = prompt;
      }
    }

    function smartSplitDismissDuration() {
      return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 100 : 220;
    }

    function getSmartSplitDismissRange(session, targetMessage) {
      const currentIndex = session?.messages?.indexOf?.(targetMessage) ?? -1;
      if (currentIndex < 0) return null;
      let startIndex = currentIndex;
      while (startIndex > 0 && session.messages[startIndex - 1]?.role === 'user') {
        startIndex -= 1;
      }
      return { startIndex, currentIndex };
    }

    function fadeSmartSplitMessages(session, targetMessage) {
      const range = getSmartSplitDismissRange(session, targetMessage);
      if (!range) return;
      for (let index = range.startIndex; index <= range.currentIndex; index += 1) {
        document.querySelector(`.message[data-message-index="${index}"]`)
          ?.classList.add('is-smart-split-dismissing');
      }
    }

    function removeSmartSplitMessages(session, targetMessage) {
      const range = getSmartSplitDismissRange(session, targetMessage);
      if (!range) return;
      const rollbackBoundary = session.messages[range.startIndex - 1];
      if (rollbackBoundary?.role === 'assistant' && rollbackBoundary.payload) {
        rollbackBoundary.payload = {
          ...rollbackBoundary.payload,
          meta: {
            ...(rollbackBoundary.payload.meta || {}),
            continuationClosed: true,
          },
        };
      }
      clearPendingPoll(session.id);
      clearCollectingSync(session.id);
      collectingSyncSeen.delete(session.id);
      session.messages.splice(range.startIndex, range.currentIndex - range.startIndex + 1);
      persistSessions();
      if (session.id === state.activeId) render();
    }

    function restoreSmartSplitDismissActions(messageNode, trigger, originalLabel, error) {
      messageNode?.classList.remove('is-smart-split-dismiss-pending');
      const card = trigger?.closest?.('.guide-card');
      card?.removeAttribute('aria-busy');
      card?.querySelectorAll?.('.guide-action-button').forEach((action) => {
        action.disabled = false;
      });
      trigger.textContent = '取消失败，重试';
      trigger.title = formatNetworkError(error);
      trigger.focus({ preventScroll: true });
      window.setTimeout(() => {
        if (!trigger.isConnected) return;
        trigger.textContent = originalLabel;
        trigger.removeAttribute('title');
      }, 1800);
    }

    async function dismissSmartSplitMessage(trigger, options = {}) {
      const messageNode = trigger?.closest?.('.message');
      const session = getActiveSession();
      const messageIndex = Number(messageNode?.dataset?.messageIndex);
      const targetMessage = session?.messages?.[messageIndex];
      if (!messageNode || !targetMessage || messageNode.classList.contains('is-smart-split-dismiss-pending')) return;
      const originalLabel = trigger.textContent;
      messageNode.classList.add('is-smart-split-dismiss-pending');
      trigger.closest('.guide-card')?.setAttribute('aria-busy', 'true');
      messageNode.querySelectorAll('.guide-action-button').forEach((action) => { action.disabled = true; });
      try {
        const res = await fetch('/api/chat-plan-cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionId: session.id }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        if (data.cancelled !== true && !options.allowResetSession) {
          throw new Error('当前分集确认已失效，未执行取消');
        }
        const duration = smartSplitDismissDuration();
        messageNode.classList.remove('is-smart-split-dismiss-pending');
        fadeSmartSplitMessages(session, targetMessage);
        await new Promise((resolve) => window.setTimeout(resolve, duration));
        removeSmartSplitMessages(session, targetMessage);
      } catch (error) {
        restoreSmartSplitDismissActions(messageNode, trigger, originalLabel, error);
      }
    }

    async function dismissAssistantErrorMessage(trigger) {
      const messageNode = trigger?.closest?.('.message');
      const session = getActiveSession();
      const messageIndex = Number(messageNode?.dataset?.messageIndex);
      const targetMessage = session?.messages?.[messageIndex];
      if (!messageNode || !targetMessage) return;
      fadeSmartSplitMessages(session, targetMessage);
      await new Promise((resolve) => window.setTimeout(resolve, smartSplitDismissDuration()));
      removeSmartSplitMessages(session, targetMessage);
    }

    async function confirmSmartSplitPlan(trigger) {
      const session = getActiveSession();
      const plannedVideos = getConfirmedSmartSplitVideos(session, '确认分集');
      if (!session?.id || !Array.isArray(plannedVideos) || !plannedVideos.length) return;
      const pendingPayload = buildLocalPendingPayload(session.id, '确认分集');
      session.messages.push({ role: 'user', text: '确认分集' });
      session.messages.push({ role: 'assistant', payload: pendingPayload });
      persistSessions();
      state.busy = true;
      startGenerationProgress(session.id, '确认分集');
      render();
      try {
        const response = await fetch('/api/smart-split-plan/confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionId: session.id, plannedVideos }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw buildRequestError(data);
        replaceLocalPendingPayload(session, buildAssistantPayload(data, session.id));
        persistSessions();
        await Promise.allSettled([refreshAssets(), refreshUserGeneratedResults()]);
      } catch (error) {
        replaceLocalPendingPayload(session, { error: formatNetworkError(error) });
        persistSessions();
      } finally {
        state.busy = false;
        clearGenerationProgress();
        render();
        renderStatus();
      }
    }

    async function handleGuideAction(kind, value, trigger = null) {
      const actionKind = String(kind || '').trim();
      const text = String(value || '').trim();
      if (state.busy || !text) return;
      if (actionKind === 'dismiss-error') {
        await dismissAssistantErrorMessage(trigger);
        return;
      }
      if (actionKind === 'confirm-smart-split') {
        await confirmSmartSplitPlan(trigger);
        return;
      }
      if (actionKind === 'dismiss-plan') {
        await dismissSmartSplitMessage(trigger);
        return;
      }
      if (isRealGenerationUnavailable()) return;
      if (actionKind !== 'send') {
        setComposerDraft(text, { submit: false });
        return;
      }
      if (text !== '重新分集') {
        setComposerDraft(text, { submit: true });
        return;
      }
      const card = trigger?.closest?.('.guide-card');
      const drawer = card?.querySelector?.('[data-smart-split-feedback-drawer]');
      if (trigger?.hasAttribute?.('data-smart-split-feedback-toggle') && drawer) {
        setSmartSplitFeedbackMode(card, drawer.hidden);
        return;
      }
      const feedback = String(card?.querySelector?.('[data-smart-split-feedback]')?.value || '').trim();
      setComposerDraft(feedback ? `重新分集：${feedback}` : text, { submit: true });
    }
