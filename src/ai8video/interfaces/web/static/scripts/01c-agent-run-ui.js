    const AGENT_BUSY_STATES = new Set(['queued', 'deciding', 'running', 'waiting_runtime', 'cancelling']);
    const AGENT_TERMINAL_STATES = new Set(['succeeded', 'failed', 'cancelled']);
    const AGENT_MONITORED_STATES = new Set([...AGENT_BUSY_STATES, 'waiting_user']);
    let agentRunPollTimer = null;
    const agentRunPollInflight = new Set();

    function conversationModeLabel(session) {
      if (session?.executionMode !== 'agent') return '标准';
      return state.agentModeEnabled ? 'Agent' : '标准回退';
    }

    function conversationLifecycleLabel(session) {
      if (!session) return '不可用';
      if (session.serverLifecycleAuthoritative && session.lifecycleState === 'idle' && session.canDelete === true) {
        return Number(session.messageCount || 0) > 0 ? '空闲' : '空对话';
      }
      const runState = String(session.agentRun?.state || session.agentRunState || '');
      if (runState === 'deciding') return '正在决策';
      if (runState === 'queued') return '等待执行';
      if (runState === 'running') return '正在执行';
      if (runState === 'waiting_runtime') return '正在生成';
      if (runState === 'waiting_user') return '等待确认';
      if (runState === 'succeeded') return '已完成';
      if (runState === 'failed') return '失败';
      if (runState === 'cancelled') return '已取消';
      if (isSessionPending(session)) return '正在生成';
      const lifecycle = String(session.lifecycleState || '');
      const labels = {
        empty: '空对话',
        idle: Number(session.messageCount || 0) > 0 ? '空闲' : '空对话',
        busy: '正在执行',
        waiting_user: '等待确认',
        completed: '已完成',
        failed: '失败',
        cancelled: '已取消',
      };
      return labels[lifecycle] || summarizeSessionSub(session) || '空闲';
    }

    function conversationIsBusy(session) {
      if (session?.serverLifecycleAuthoritative && session.lifecycleState === 'idle' && session.canDelete === true) {
        return false;
      }
      const runState = String(session?.agentRun?.state || session?.agentRunState || '');
      return session?.lifecycleState === 'busy' || AGENT_BUSY_STATES.has(runState) || isSessionPending(session);
    }

    function renderConversationShell() {
      const session = getActiveSession();
      const count = state.sessions.length;
      const limit = state.conversationLimit || 3;
      renderSessions();
      if (els.sidebarConversationCount) els.sidebarConversationCount.textContent = `${count}/${limit}`;
      const newConversationMode = state.newConversationMode === 'agent' && state.agentModeEnabled
        ? 'agent'
        : 'workflow';
      state.newConversationMode = newConversationMode;
      els.newConversationModeButtons.forEach((button) => {
        const mode = button.dataset.newConversationMode === 'agent' ? 'agent' : 'workflow';
        const active = mode === newConversationMode;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', String(active));
        button.disabled = state.conversationSyncing || (mode === 'agent' && !state.agentModeEnabled);
        button.title = mode === 'agent'
          ? (state.agentModeEnabled
            ? '下一条新对话使用 Agent 模式 Beta；不会修改当前对话'
            : 'Agent 模式当前已关闭')
          : '下一条新对话使用标准模式；不会修改当前对话';
      });
      const limitReached = count >= limit || !state.canCreateConversation;
      els.newConversationButtons.forEach((button) => {
        const buttonLabel = button.querySelector('[data-new-conversation-label]');
        const createLabel = newConversationMode === 'agent' ? '新建 Agent 对话' : '新建标准对话';
        const visibleLabel = state.conversationSyncing
          ? '处理中…'
          : (limitReached ? `对话已满（${count}/${limit}）` : createLabel);
        button.classList.toggle('is-limit', limitReached);
        button.setAttribute('aria-disabled', String(state.conversationSyncing));
        button.disabled = state.conversationSyncing;
        if (buttonLabel) buttonLabel.textContent = visibleLabel;
        button.title = state.conversationSyncing
          ? '正在处理对话，请稍候。'
          : (limitReached
          ? `最多同时保留 ${limit} 个对话；点击查看需要删除的对话。`
          : `${createLabel}；不会修改当前对话。`);
      });
      renderConversationMobileSelect();
      renderConversationNotice();
      renderAgentRunPanel();
      syncConversationComposerLock();
      ensureAgentRunMonitor();
    }

    function renderConversationMobileSelect() {
      const select = els.conversationMobileSelect;
      if (!select) return;
      const signature = state.sessions.map((item) => `${item.id}:${item.title}:${item.executionMode}`).join('|');
      if (select.dataset.signature !== signature) {
        select.innerHTML = state.sessions.map((item) => (
          `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title || NEW_SESSION_TITLE)} · ${escapeHtml(conversationModeLabel(item))}</option>`
        )).join('');
        select.dataset.signature = signature;
      }
      select.value = String(state.activeId || '');
      select.disabled = state.conversationSyncing || !state.sessions.length;
    }

    function syncConversationComposerLock() {
      const session = getActiveSession();
      const locked = !session
        || !!state.conversationError
        || state.conversationSyncing
        || conversationIsBusy(session);
      if (!locked) {
        renderStatus();
        return;
      }
      els.sendButton.disabled = true;
      els.composer.classList.add('locked');
      els.messageEditor.contentEditable = 'false';
      els.messageEditor.setAttribute('aria-disabled', 'true');
      hideMaterialMentionPicker();
    }

    function agentRunStateLabel(stateName) {
      const labels = {
        queued: '等待决策',
        deciding: '主 Agent 决策中',
        running: '复合动作执行中',
        waiting_runtime: 'Runtime 执行中',
        waiting_user: '等待你的确认',
        succeeded: '已完成',
        failed: '已停止',
        cancelled: '已取消',
      };
      return labels[String(stateName || '')] || '尚未开始';
    }

    function agentToolLabel(toolName) {
      const labels = {
        prepare_video_plan: '准备视频方案',
        review_video_plan: '审核视频方案',
        generate_video_batch: '生成视频批次',
        inspect_generation_result: '检查生成结果',
        archive_and_deliver: '归档并交付',
        task_user: '询问用户',
      };
      return labels[String(toolName || '')] || String(toolName || '复合动作');
    }

    function agentActionStateLabel(stateName) {
      const labels = {
        requested: '待执行',
        running: '执行中',
        waiting_runtime: '后台执行中',
        waiting_approval: '等待批准',
        succeeded: '已完成',
        failed: '失败',
        cancelled: '已拒绝',
      };
      return labels[String(stateName || '')] || String(stateName || '已记录');
    }

    function renderAgentRunPanel() {
      const panel = els.agentRunPanel;
      const session = getActiveSession();
      if (!panel || session?.executionMode !== 'agent') {
        panel?.classList.add('hidden');
        if (panel) panel.innerHTML = '';
        return;
      }
      panel.classList.remove('hidden');
      const run = session.agentRun || {
        id: session.activeRunId,
        state: session.agentRunState,
        decisionCount: 0,
        maxDecisions: 8,
        costUnits: 0,
        costLimit: 8,
      };
      const context = session.agentContext || {};
      const actions = Array.isArray(session.agentActions) ? session.agentActions : [];
      const observations = Array.isArray(session.agentObservations) ? session.agentObservations : [];
      const approval = context.pendingApproval || session.agentApproval || null;
      const question = context.pendingUserQuestion || null;
      const actionById = new Map(actions.map((item) => [String(item.id || ''), item]));
      const recentActions = actions.slice(-3).reverse().map((item) => (
        `<div class="agent-run-observation">${escapeHtml(agentToolLabel(item.toolName || item.tool))} · ${escapeHtml(agentActionStateLabel(item.state))}</div>`
      )).join('');
      const recentObservations = observations.slice(-2).reverse().map((item) => {
        const action = actionById.get(String(item.actionId || ''));
        const title = action ? agentToolLabel(action.toolName || action.tool) : 'Runtime 观察';
        return `<div class="agent-run-observation">${escapeHtml(title)} · ${escapeHtml(agentActionStateLabel(item.state))}</div>`;
      }).join('');
      const fallbackCopy = !state.agentModeEnabled
        ? '<div class="agent-run-copy">Agent Feature Flag 已关闭；这个对话仍保留原模式记录，但聊天入口会使用标准流程。</div>'
        : '';
      const approvalMarkup = approval?.actionId ? `
        <div class="agent-approval-card">
          <strong>${escapeHtml(approval.question || '本次操作需要你的明确批准。')}</strong>
          <div class="agent-approval-actions">
            <button type="button" class="agent-approval-button" data-agent-approval="${escapeHtml(approval.actionId)}" data-approved="true" ${session.agentApprovalBusy ? 'disabled' : ''}>批准并继续</button>
            <button type="button" class="agent-approval-button" data-agent-approval="${escapeHtml(approval.actionId)}" data-approved="false" ${session.agentApprovalBusy ? 'disabled' : ''}>拒绝</button>
          </div>
        </div>
      ` : (question?.question ? `<div class="agent-approval-card"><strong>${escapeHtml(question.question)}</strong><span>请直接在下方输入你的决定。</span></div>` : '');
      panel.innerHTML = `
        <div class="agent-run-head">
          <span class="agent-run-title">Agent 模式 Beta · 关键节点决策</span>
          <span class="agent-run-state">${escapeHtml(agentRunStateLabel(run.state))}</span>
        </div>
        ${fallbackCopy}
        <div class="agent-run-metrics">
          <span class="agent-run-metric">主决策 ${Number(run.decisionCount || 0)}/${Number(run.maxDecisions || 8)}</span>
          <span class="agent-run-metric">成本 ${Number(run.costUnits || 0).toFixed(2)}/${Number(run.costLimit || 8).toFixed(2)}</span>
          ${context.generationBatchId ? '<span class="agent-run-metric">Runtime 已接管生成</span>' : ''}
        </div>
        ${approvalMarkup}
        ${(recentActions || recentObservations) ? `<div class="agent-run-observations">${recentActions}${recentObservations}</div>` : '<div class="agent-run-copy">发送目标后，主 Agent 只会在规划、审核、终态结果和交付等关键节点重新决策。</div>'}
      `;
    }

    function bindAgentRunControls() {
      if (!els.agentRunPanel || els.agentRunPanel.dataset.boundAgentApproval === '1') return;
      els.agentRunPanel.dataset.boundAgentApproval = '1';
      els.agentRunPanel.addEventListener('click', (event) => {
        const button = event.target.closest('[data-agent-approval]');
        if (!button) return;
        void submitAgentApproval(
          String(button.dataset.agentApproval || ''),
          button.dataset.approved === 'true',
        );
      });
    }

    async function submitAgentApproval(actionId, approved) {
      const session = getActiveSession();
      if (!session || !actionId || session.agentApprovalBusy) return;
      session.agentApprovalBusy = true;
      renderAgentRunPanel();
      try {
        const data = await conversationRequest(
          `/api/agent-actions/${encodeURIComponent(actionId)}/approval`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approved: !!approved }),
          },
        );
        const result = data.result || {};
        syncConversationFromResponse(result, session);
        session.agentApproval = null;
        session.agentContext = { ...(session.agentContext || {}), pendingApproval: null };
        if (result.reply) session.messages.push({ role: 'assistant', payload: buildAssistantPayload(result, session.id) });
        showConversationNotice(approved ? '已批准本次额外操作。' : '已拒绝本次额外操作。', approved ? 'success' : 'info');
        await refreshConversationInventory({ renderAfter: false }).catch(() => null);
        persistSessions();
      } catch (error) {
        showConversationNotice(error?.message || '提交确认失败', 'error');
      } finally {
        session.agentApprovalBusy = false;
        render();
      }
    }

    function shouldPollAgentRun(session) {
      if (session?.executionMode !== 'agent' || !session.activeRunId) return false;
      const stateName = String(session.agentRun?.state || session.agentRunState || '');
      return AGENT_MONITORED_STATES.has(stateName);
    }

    function agentResponseSignature(response) {
      return JSON.stringify({
        status: response?.status,
        reply: response?.reply,
        run: response?.agentRun,
      });
    }

    function agentResponseAlreadyRendered(session, response) {
      const reply = response?.reply;
      if (!reply || typeof reply !== 'object') return true;
      const text = String(reply.text || '').trim();
      const operation = String(reply.meta?.operation || '').trim();
      const agentRunId = String(reply.meta?.agentRunId || response?.agentRun?.id || '').trim();
      return [...(session?.messages || [])].reverse().some((message) => {
        if (message?.role !== 'assistant') return false;
        const payload = message.payload || {};
        if (String(payload.text || '').trim() !== text) return false;
        const payloadOperation = String(payload.meta?.operation || '').trim();
        const payloadRunId = String(payload.meta?.agentRunId || '').trim();
        return (agentRunId && payloadRunId === agentRunId) || (operation && payloadOperation === operation);
      });
    }

    function ensureAgentRunMonitor() {
      const shouldPoll = state.sessions.some(shouldPollAgentRun);
      if (!shouldPoll) {
        if (agentRunPollTimer) clearTimeout(agentRunPollTimer);
        agentRunPollTimer = null;
        return;
      }
      if (agentRunPollTimer || agentRunPollInflight.size) return;
      agentRunPollTimer = window.setTimeout(() => {
        agentRunPollTimer = null;
        void pollAgentRuns();
      }, 1800);
    }

    async function pollAgentRuns() {
      const sessions = state.sessions.filter(shouldPollAgentRun);
      let inventoryRefreshNeeded = false;
      await Promise.all(sessions.map(async (session) => {
        const runId = String(session.activeRunId || '');
        if (!runId || agentRunPollInflight.has(runId)) return;
        agentRunPollInflight.add(runId);
        try {
          const data = await conversationRequest(`/api/agent-runs/${encodeURIComponent(runId)}`);
          const previousState = String(session.agentRun?.state || session.agentRunState || '');
          session.agentRun = data.run || session.agentRun;
          session.agentRunState = data.run?.state || session.agentRunState;
          session.agentContext = data.context || {};
          session.agentActions = Array.isArray(data.actions) ? data.actions : [];
          session.agentObservations = Array.isArray(data.observations) ? data.observations : [];
          session.agentApproval = session.agentContext.pendingApproval || null;
          const latest = data.latestResponse;
          if (latest && typeof latest === 'object') {
            const signature = agentResponseSignature(latest);
            if (signature !== session.agentLatestResponseSignature) {
              session.agentLatestResponseSignature = signature;
              const alreadyRendered = agentResponseAlreadyRendered(session, latest);
              syncConversationFromResponse(latest, session);
              if (latest.reply && !alreadyRendered) {
                session.messages.push({ role: 'assistant', payload: buildAssistantPayload(latest, session.id) });
              }
              inventoryRefreshNeeded = true;
            }
          }
          if (previousState !== String(session.agentRun?.state || '')) inventoryRefreshNeeded = true;
          session.agentMonitorError = '';
        } catch (error) {
          session.agentMonitorError = error?.message || 'Agent 状态读取失败';
        } finally {
          agentRunPollInflight.delete(runId);
        }
      }));
      if (inventoryRefreshNeeded) {
        await refreshConversationInventory({ renderAfter: false }).catch(() => null);
      }
      persistSessions();
      render();
    }
