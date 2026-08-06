    let conversationNoticeTimer = null;

    function conversationApiError(data, fallback = '会话请求失败') {
      const error = new Error(String(data?.error || fallback));
      error.code = String(data?.code || '').trim().toUpperCase();
      error.payload = data && typeof data === 'object' ? data : {};
      return error;
    }

    async function conversationRequest(url, options = {}) {
      const res = await fetch(url, options);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw conversationApiError(data);
      return data;
    }

    function conversationWelcomeMessage() {
      return {
        role: 'assistant',
        welcome: true,
        payload: { ...WELCOME_PAYLOAD, meta: { ...(WELCOME_PAYLOAD.meta || {}) } },
      };
    }

    function localConversationFromMetadata(metadata = {}, previous = null) {
      const messageCount = Number(metadata.messageCount || 0) || 0;
      const previousMessages = Array.isArray(previous?.messages) ? previous.messages : [];
      const messages = previousMessages.length
        ? previousMessages
        : (messageCount > 0 ? [] : [conversationWelcomeMessage()]);
      return {
        ...(previous || {}),
        ...metadata,
        id: String(metadata.id || previous?.id || ''),
        title: String(metadata.title || previous?.title || NEW_SESSION_TITLE),
        executionMode: metadata.executionMode === 'agent' ? 'agent' : 'workflow',
        modeLocked: !!metadata.modeLocked,
        modeSwitchAllowed: metadata.modeSwitchAllowed !== false,
        lifecycleState: String(metadata.lifecycleState || (messageCount ? 'idle' : 'empty')),
        revision: Number(metadata.revision || 0) || 0,
        messageCount,
        messages,
        temporaryScriptKnowledge: previous?.temporaryScriptKnowledge || null,
      };
    }

    function applyConversationCapacity(data = {}) {
      const limit = Number(data.conversationLimit || data.maxConversations || state.conversationLimit || 3);
      const count = Number(data.conversationCount ?? state.sessions.length);
      state.conversationLimit = Math.max(1, limit || 3);
      state.overLimit = data.overLimit === true || count > state.conversationLimit;
      state.canCreateConversation = data.canCreateConversation !== undefined
        ? !!data.canCreateConversation
        : count < state.conversationLimit;
      if (Object.prototype.hasOwnProperty.call(data, 'agentModeEnabled')) {
        state.agentModeEnabled = data.agentModeEnabled !== false;
        if (!state.agentModeEnabled && state.newConversationMode === 'agent') {
          state.newConversationMode = 'workflow';
        }
      }
    }

    function mergeConversationInventory(items, previousSessions = state.sessions) {
      const previousById = new Map((previousSessions || []).map((item) => [String(item?.id || ''), item]));
      const activeId = String(state.activeId || '');
      state.sessions = (Array.isArray(items) ? items : [])
        .filter((item) => item && typeof item === 'object' && String(item.id || '').trim())
        .map((item) => ({
          ...localConversationFromMetadata(item, previousById.get(String(item.id))),
          // The inventory is newer than any local pending snapshot retained for offline recovery.
          serverLifecycleAuthoritative: true,
        }));
      state.activeId = state.sessions.some((item) => item.id === activeId)
        ? activeId
        : (state.sessions[0]?.id || null);
      persistSessions();
      return state.sessions;
    }

    function legacyConversationPayload(session) {
      const messages = (Array.isArray(session?.messages) ? session.messages : [])
        .filter((message) => !isWelcomeMessage(message));
      return {
        id: String(session?.id || ''),
        title: String(session?.title || NEW_SESSION_TITLE),
        executionMode: session?.executionMode === 'agent' ? 'agent' : 'workflow',
        messages,
      };
    }

    async function reconcileMissingLegacyConversations(legacySessions, inventory) {
      const serverItems = Array.isArray(inventory?.items)
        ? inventory.items
        : (Array.isArray(inventory?.conversations) ? inventory.conversations : []);
      const serverIds = new Set(serverItems.map((item) => String(item?.id || '').trim()).filter(Boolean));
      const missing = legacySessions
        .filter((session) => !serverIds.has(String(session?.id || '').trim()))
        .map(legacyConversationPayload)
        .filter((session) => session.id);
      if (!missing.length) return inventory;
      return conversationRequest('/api/conversations/reconcile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversations: missing }),
      });
    }

    async function initializeConversations() {
      const legacySessions = Array.isArray(state.sessions) ? [...state.sessions] : [];
      state.conversationSyncing = true;
      state.conversationError = '';
      try {
        const inventory = await conversationRequest('/api/conversations');
        let data = inventory;
        if (legacySessions.length) {
          data = await reconcileMissingLegacyConversations(legacySessions, inventory);
        }
        applyConversationCapacity(data);
        mergeConversationInventory(data.items || data.conversations || [], legacySessions);
        if (!state.sessions.length) {
          const created = await conversationRequest('/api/conversations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: NEW_SESSION_TITLE, executionMode: 'workflow' }),
          });
          applyConversationCapacity(created);
          mergeConversationInventory([created.conversation], []);
        }
        await hydrateConversationMessages(getActiveSession());
      } catch (error) {
        state.conversationError = error?.message || '无法读取服务端会话';
        state.sessions = legacySessions;
        state.activeId = legacySessions[0]?.id || null;
        showConversationNotice(`会话服务不可用：${state.conversationError}`, 'error', 0);
      } finally {
        state.conversationSyncing = false;
        persistSessions();
      }
    }

    async function refreshConversationInventory({ hydrateActive = false, renderAfter = true } = {}) {
      const data = await conversationRequest('/api/conversations');
      applyConversationCapacity(data);
      mergeConversationInventory(data.items || data.conversations || []);
      if (hydrateActive) await hydrateConversationMessages(getActiveSession());
      if (renderAfter) render();
      return data;
    }

    function localMessageFromServer(item) {
      const role = String(item?.role || 'assistant');
      const content = String(item?.content || '');
      const metadata = item?.metadata && typeof item.metadata === 'object' ? item.metadata : {};
      if (role === 'user') return { role: 'user', text: content };
      const legacyPayload = metadata.legacyPayload;
      if (legacyPayload && typeof legacyPayload === 'object' && !Array.isArray(legacyPayload)) {
        return { role: 'assistant', payload: legacyPayload };
      }
      return {
        role: 'assistant',
        payload: {
          text: content,
          stage: String(metadata.stage || 'completed'),
          awaiting: null,
          draft: null,
          result: null,
          meta: { operation: String(metadata.operation || metadata.source || 'server_history') },
        },
      };
    }

    async function hydrateConversationMessages(session) {
      if (!session || session.serverMessagesHydrated) return session;
      const localMessages = Array.isArray(session.messages) ? session.messages : [];
      const hasLocalHistory = localMessages.some((message) => !isWelcomeMessage(message));
      if (hasLocalHistory || Number(session.messageCount || 0) <= 0) {
        session.serverMessagesHydrated = true;
        return session;
      }
      const data = await conversationRequest(
        `/api/conversations/${encodeURIComponent(session.id)}/messages`,
      );
      session.messages = (data.messages || []).map(localMessageFromServer);
      if (!session.messages.length) session.messages = [conversationWelcomeMessage()];
      Object.assign(session, localConversationFromMetadata(data.conversation, session));
      session.serverMessagesHydrated = true;
      persistSessions();
      return session;
    }

    async function createConversation(title = NEW_SESSION_TITLE, executionMode = state.newConversationMode) {
      if (state.conversationSyncing) return null;
      if (state.sessions.length >= state.conversationLimit || !state.canCreateConversation) {
        showConversationLimitNotice();
        return null;
      }
      const mode = executionMode === 'agent' ? 'agent' : 'workflow';
      if (mode === 'agent' && !state.agentModeEnabled) {
        showConversationNotice('Agent 模式当前已关闭，暂时只能新建标准模式对话。', 'error');
        state.newConversationMode = 'workflow';
        renderConversationShell();
        return null;
      }
      state.conversationSyncing = true;
      renderConversationShell();
      try {
        const data = await conversationRequest('/api/conversations', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title, executionMode: mode }),
        });
        applyConversationCapacity(data);
        const session = localConversationFromMetadata(data.conversation);
        state.sessions.unshift(session);
        state.activeId = session.id;
        if (Array.isArray(data.items)) mergeConversationInventory(data.items, state.sessions);
        persistSessions();
        showConversationNotice(
          mode === 'agent' ? '已新建 Agent 模式 Beta 对话。' : '已新建标准模式对话。',
          'success',
        );
        render();
        return getActiveSession();
      } catch (error) {
        if (error?.code === 'CONVERSATION_LIMIT_REACHED') {
          await refreshConversationInventory({ renderAfter: false }).catch(() => null);
          showConversationLimitNotice();
        } else {
          showConversationNotice(error?.message || '新建对话失败', 'error');
        }
        return null;
      } finally {
        state.conversationSyncing = false;
        renderConversationShell();
      }
    }

    async function createSession(title = NEW_SESSION_TITLE) {
      return createConversation(title, 'workflow');
    }

    async function setActiveConversation(conversationId) {
      const session = state.sessions.find((item) => item.id === conversationId);
      if (!session) return;
      state.activeId = session.id;
      await hydrateConversationMessages(session).catch((error) => {
        showConversationNotice(error?.message || '对话消息读取失败', 'error');
      });
      persistSessions();
      render();
    }

    async function deleteConversation(conversationId) {
      const session = state.sessions.find((item) => item.id === conversationId);
      if (!session || state.conversationSyncing) return;
      if (state.sessions.length <= 1) {
        showConversationNotice('至少保留一个可用对话；最后一个对话不能删除。', 'error');
        return;
      }
      if (conversationIsBusy(session) || session.canDelete === false) {
        showConversationNotice('当前对话还有任务正在运行，暂时不能删除。', 'error');
        return;
      }
      const confirmed = window.confirm(
        '删除后将移除这个对话及其上下文，但不会删除已经生成的视频、素材和任务记录。确定继续吗？',
      );
      if (!confirmed) return;
      state.conversationSyncing = true;
      renderConversationShell();
      try {
        const data = await conversationRequest(
          `/api/conversations/${encodeURIComponent(session.id)}`,
          { method: 'DELETE' },
        );
        applyConversationCapacity(data);
        const remaining = state.sessions.filter((item) => item.id !== session.id);
        state.sessions = remaining;
        if (state.activeId === session.id) state.activeId = remaining[0]?.id || null;
        if (Array.isArray(data.items)) mergeConversationInventory(data.items, remaining);
        await hydrateConversationMessages(getActiveSession());
        persistSessions();
        showConversationNotice('对话已删除；生成视频、素材和任务审计记录均未删除。', 'success');
      } catch (error) {
        await refreshConversationInventory({ renderAfter: false }).catch(() => null);
        showConversationNotice(error?.message || '删除对话失败', 'error');
      } finally {
        state.conversationSyncing = false;
        render();
      }
    }

    function syncConversationFromResponse(data, session = getActiveSession()) {
      if (!session || !data || typeof data !== 'object') return session;
      if (data.conversation && String(data.conversation.id || '') === session.id) {
        Object.assign(session, localConversationFromMetadata(data.conversation, session));
      }
      if (data.agentRun && typeof data.agentRun === 'object') session.agentRun = data.agentRun;
      if (data.approval && typeof data.approval === 'object') session.agentApproval = data.approval;
      persistSessions();
      return session;
    }

    function isConversationConflict(error) {
      return [
        'CONVERSATION_MODE_CONFLICT',
        'CONVERSATION_MODE_LOCKED',
        'CONVERSATION_REVISION_CONFLICT',
        'CONVERSATION_BUSY',
        'CONVERSATION_NOT_FOUND',
      ].includes(String(error?.code || '').toUpperCase());
    }

    function newClientMessageId() {
      if (globalThis.crypto?.randomUUID) return `client-${globalThis.crypto.randomUUID()}`;
      return `client-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
    }

    function showConversationLimitNotice() {
      const lines = state.sessions.map((item, index) => (
        `${index + 1}. ${item.title || NEW_SESSION_TITLE}　${conversationModeLabel(item)} · ${conversationLifecycleLabel(item)}`
      ));
      showConversationNotice(
        `最多同时保留 ${state.conversationLimit} 个对话。请先删除一个不再需要的对话，再创建新对话。${lines.length ? `\n${lines.join('\n')}` : ''}`,
        'error',
        0,
      );
    }

    function showConversationNotice(text, tone = 'info', duration = 5200) {
      state.conversationNotice = { text: String(text || ''), tone };
      renderConversationNotice();
      if (conversationNoticeTimer) clearTimeout(conversationNoticeTimer);
      conversationNoticeTimer = null;
      if (duration > 0) {
        conversationNoticeTimer = window.setTimeout(() => {
          state.conversationNotice = { text: '', tone: 'info' };
          renderConversationNotice();
        }, duration);
      }
    }

    function renderConversationNotice() {
      if (!els.conversationNotice) return;
      const notice = state.conversationNotice || {};
      els.conversationNotice.textContent = String(notice.text || '');
      els.conversationNotice.dataset.tone = String(notice.tone || 'info');
      els.conversationNotice.hidden = !notice.text;
    }

    function selectNewConversationMode(executionMode) {
      const nextMode = executionMode === 'agent' ? 'agent' : 'workflow';
      if (state.conversationSyncing) return;
      if (nextMode === 'agent' && !state.agentModeEnabled) {
        showConversationNotice('Agent 模式当前已关闭，暂时只能新建标准模式对话。', 'error');
        return;
      }
      state.newConversationMode = nextMode;
      renderConversationShell();
    }

    function bindConversationControls() {
      els.newConversationButtons.forEach((button) => {
        if (button.dataset.boundConversation === '1') return;
        button.dataset.boundConversation = '1';
        button.addEventListener('click', () => void createConversation());
      });
      els.newConversationModeButtons.forEach((button) => {
        if (button.dataset.boundConversation === '1') return;
        button.dataset.boundConversation = '1';
        button.addEventListener('click', () => selectNewConversationMode(button.dataset.newConversationMode));
      });
      els.conversationMobileSelect?.addEventListener('change', (event) => {
        void setActiveConversation(String(event.target.value || ''));
      });
      els.sessionList?.addEventListener('click', (event) => {
        const deleteButton = event.target.closest('[data-delete-conversation]');
        if (deleteButton) {
          event.preventDefault();
          event.stopPropagation();
          void deleteConversation(deleteButton.dataset.deleteConversation || '');
          return;
        }
        const selectButton = event.target.closest('[data-select-conversation]');
        if (selectButton) void setActiveConversation(selectButton.dataset.selectConversation || '');
      });
    }
