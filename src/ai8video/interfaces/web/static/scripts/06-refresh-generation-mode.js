    async function refreshGenerationMode() {
      const res = await fetch('/api/generation-mode');
      const data = await res.json().catch(() => ({}));
      state.generationMode = {
        ...(state.generationMode || {}),
        concurrentGeneration: !!data?.concurrentGeneration,
        smartSplit: !!data?.smartSplit,
        splitMode: data?.splitMode === 'manual' ? 'manual' : 'smart',
        manualVideoCount: Math.max(1, Math.min(12, Number(data?.manualVideoCount || 2))),
        confirmSmartSplit: !!data?.confirmSmartSplit,
        tailFrameChaining: !!data?.tailFrameChaining,
        tailFrameChainingMode: data?.tailFrameChainingMode === 'manual' ? 'manual' : 'auto',
        saving: false,
        error: data?.error || '',
      };
    }

    async function refreshHtmlMotionOverlay() {
      const res = await fetch('/api/html-motion-overlay');
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) {
        throw new Error(data?.error || 'HTML 动效配置读取失败');
      }
      state.htmlMotionOverlay = {
        ...(state.htmlMotionOverlay || {}),
        enabled: !!data?.enabled,
        runtime: data?.runtime || null,
        safeZones: data?.safeZones && typeof data.safeZones === 'object' ? data.safeZones : {},
        qualityRetryCount: normalizeHtmlMotionQualityRetryCount(data?.qualityRetryCount),
        beatIntervalSeconds: normalizeHtmlMotionBeatIntervalSeconds(data?.beatIntervalSeconds),
        smartBeatInterval: !!data?.smartBeatInterval,
        saving: false,
        error: data?.error || '',
      };
    }





    async function refreshBatchReports() {
      const res = await fetch('/api/batch-reports?limit=8');
      const data = await res.json();
      state.batchReports = data.items || [];
    }

    async function refreshBatchAlerts() {
      const res = await fetch('/api/batch-alerts?limit=8');
      const data = await res.json();
      state.batchAlerts = data.items || [];
    }

    els.composer.addEventListener('submit', async (event) => {
      event.preventDefault();
      syncMessageInputFromEditor();
      const value = els.messageInput.value.trim();
      if (!value || state.busy) return;
      if (isRealGenerationUnavailable()) {
        renderStatus();
        return;
      }

      const session = getActiveSession();
      if (!session) {
        showConversationNotice(state.conversationError || '对话尚未准备完成，请稍后重试。', 'error');
        return;
      }
      if (conversationIsBusy(session)) {
        showConversationNotice('当前对话还有任务正在运行，请等待关键结果返回。', 'error');
        return;
      }
      const expectedExecutionMode = session.executionMode === 'agent' ? 'agent' : 'workflow';
      const expectedRevision = Number(session.revision || 0) || 0;
      const clientMessageId = newClientMessageId();
      const previousMessages = [...(session.messages || [])];
      const previousTitle = session.title;
      const confirmedSmartSplitVideos = getConfirmedSmartSplitVideos(session, value);
      const temporaryKnowledge = buildTemporaryScriptKnowledgeChatPayload();
      const useDefaultKnowledgeReference = !!temporaryKnowledge
        && !!state.scriptReference?.enabled
        && !!state.scriptReference?.item;
      const pendingPayload = buildLocalPendingPayload(session.id, value);
      const welcomeNode = takeWelcomeMessageNode(session);
      session.messages.push({ role: 'user', text: value });
      session.messages.push({ role: 'assistant', payload: pendingPayload });
      session.title = summarizeTitle(value);
      persistSessions();
      playWelcomeLeaveOverlay(welcomeNode);
      render();

      clearMessageEditor();
      hideMaterialMentionPicker();
      state.busy = true;
      if (expectedExecutionMode === 'workflow') startGenerationProgress(session.id, value);
      render();

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sessionId: session.id,
            message: value,
            confirmedSmartSplitVideos,
            temporaryKnowledge,
            useDefaultKnowledgeReference,
            expectedExecutionMode,
            expectedRevision,
            clientMessageId,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          const requestError = buildRequestError(data);
          if (isConversationConflict(requestError)) {
            session.messages = previousMessages;
            session.title = previousTitle;
            await refreshConversationInventory({ hydrateActive: true, renderAfter: false }).catch(() => null);
            showConversationNotice(requestError.message, 'error', 0);
            return;
          }
          const recovered = await tryRecoverTimedOutChat(session, value, data);
          if (recovered) {
            clearGenerationProgress();
            persistSessions();
            await refreshHealth();
            await refreshAuthSettings();
            await refreshVideoModelSettings();
            await refreshAssets();
            await refreshUserGeneratedResults();
            await refreshUserMaterials();
            await refreshBatchAlerts();
            await refreshBatchReports();
            render();
            return;
          }
          throw requestError;
        }
        replaceLocalPendingPayload(session, buildAssistantPayload(data, session.id));
        syncConversationFromResponse(data, session);
        if (expectedExecutionMode === 'agent') {
          session.agentLatestResponseSignature = agentResponseSignature(data);
        }
        clearGenerationProgress();
        persistSessions();
        await refreshHealth();
        await refreshAuthSettings();
        await refreshVideoModelSettings();
        await refreshAssets();
        await refreshUserGeneratedResults();
        await refreshUserMaterials();
        await refreshBatchAlerts();
        await refreshBatchReports();
        render();
      } catch (error) {
        clearGenerationProgress();
        const last = session?.messages?.at?.(-1);
        const keepPending = isTransientChatTransportError(error)
          && last?.role === 'assistant'
          && isPendingPayload(last.payload);
        if (!keepPending) {
          replaceLocalPendingPayload(session, { error: formatNetworkError(error) });
        }
        persistSessions();
        render();
      } finally {
        state.busy = false;
        clearGenerationProgress();
        renderStatus();
      }
    });

    function getConfirmedSmartSplitVideos(session, message) {
      const compactMessage = String(message || '').replace(/\s+/g, '');
      const confirmationMessages = new Set(['确认分集', '确认并继续', '确认', '继续生成', '开始生成']);
      const isReplanMessage = /^(?:重新分集|重分|重新规划)(?:[：:].*)?$/.test(compactMessage);
      if (!confirmationMessages.has(compactMessage) && !isReplanMessage) return null;
      const messages = Array.isArray(session?.messages) ? session.messages : [];
      for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
        const plannedVideos = messages[messageIndex]?.payload?.meta?.guide?.plannedVideos;
        if (Array.isArray(plannedVideos) && plannedVideos.length) return plannedVideos;
      }
      return null;
    }

    function buildLocalPendingPayload(sessionId, text) {
      return {
        text: '已收到请求，正在由AI8video 检查信息是否齐全，并决定下一步生成或追问。',
        stage: 'pending',
        meta: {
          operation: 'pending',
          source: 'local-submit',
        },
        pendingStatus: {
          status: 'pending',
          sessionId,
          pendingSince: new Date().toISOString(),
          elapsedSeconds: 0,
          videoCount: state.generationMode?.splitMode === 'manual'
            ? Number(state.generationMode.manualVideoCount || 2)
            : 0,
        },
      };
    }

    function extractGenerationBatchId(payload) {
      return String(
        payload?.pendingStatus?.generationBatchId
        || payload?.pendingStatus?.generationProgress?.generationBatchId
        || payload?.generationProgress?.generationBatchId
        || payload?.generationBatchId
        || ''
      ).trim();
    }

    function mergePendingGenerationBatchId(previousPayload, nextPayload) {
      if (!nextPayload || typeof nextPayload !== 'object') return nextPayload;
      const generationBatchId = extractGenerationBatchId(nextPayload) || extractGenerationBatchId(previousPayload);
      if (!generationBatchId) return nextPayload;
      if (!nextPayload.pendingStatus || typeof nextPayload.pendingStatus !== 'object') {
        nextPayload.pendingStatus = {};
      }
      nextPayload.generationBatchId = generationBatchId;
      nextPayload.pendingStatus.generationBatchId = generationBatchId;
      if (nextPayload.pendingStatus.generationProgress && typeof nextPayload.pendingStatus.generationProgress === 'object') {
        nextPayload.pendingStatus.generationProgress.generationBatchId = generationBatchId;
      }
      return nextPayload;
    }

    function replaceLocalPendingPayload(session, payload) {
      const last = session?.messages?.at?.(-1);
      if (last && last.role === 'assistant' && isPendingPayload(last.payload)) {
        if (payload?.error) {
          last.error = payload.error;
          delete last.payload;
        } else {
          mergePendingGenerationBatchId(last.payload, payload);
          preservePendingVideoCount(last.payload, payload);
          last.payload = payload;
        }
        return;
      }
      session.messages.push(payload?.error ? { role: 'assistant', error: payload.error } : { role: 'assistant', payload });
    }

    function preservePendingVideoCount(previousPayload, nextPayload) {
      if (!previousPayload?.pendingStatus || !nextPayload?.pendingStatus) return;
      if (!nextPayload.pendingStatus.pendingSince && previousPayload.pendingStatus.pendingSince) {
        nextPayload.pendingStatus.pendingSince = previousPayload.pendingStatus.pendingSince;
      }
      if (!nextPayload.pendingStatus.taskStartedAt) {
        nextPayload.pendingStatus.taskStartedAt =
          previousPayload.pendingStatus.taskStartedAt
          || previousPayload.pendingStatus.pendingSince
          || nextPayload.pendingStatus.pendingSince
          || null;
      }
      const previousCount = Number(previousPayload.pendingStatus.videoCount || 0) || 0;
      const nextCount = Number(nextPayload.pendingStatus.videoCount || 0) || 0;
      const backendCount = Number(nextPayload.pendingStatus.generationProgress?.totalRequested || 0) || 0;
      const itemCount = Array.isArray(nextPayload.pendingStatus.generationProgress?.items)
        ? nextPayload.pendingStatus.generationProgress.items.length
        : 0;
      const preservedCount = Math.max(previousCount, nextCount, backendCount, itemCount);
      if (preservedCount > 0) {
        nextPayload.pendingStatus.videoCount = preservedCount;
      }
      nextPayload.pendingStatus = normalizePendingStatusProgress(nextPayload.pendingStatus);
    }

    function normalizePendingStatusProgress(pendingStatus = {}) {
      if (!pendingStatus || typeof pendingStatus !== 'object') return pendingStatus;
      const progress = pendingStatus.generationProgress;
      if (!progress || typeof progress !== 'object') return pendingStatus;
      const progressStatus = String(progress.status || '').trim();
      const receivedItems = Array.isArray(progress.items) ? progress.items : [];
      // 规划状态由当前请求主导；丢弃同一会话残留的上一批终态结果。
      const originalItems = progressStatus === 'planning'
        ? receivedItems.filter((item) => ['planning', 'pending_submission'].includes(String(item?.status || '').trim()))
        : receivedItems;
      const preserveSparseVideoIndexes = progress.preserveSparseVideoIndexes === true;
      const maxVideoIndex = originalItems.reduce((max, item, index) => (
        Math.max(max, Number(item?.videoIndex || 0) || index + 1)
      ), 0);
      const requested = Math.max(
        Number(pendingStatus.videoCount || 0) || 0,
        progressStatus === 'planning' ? 0 : (Number(progress.totalRequested || 0) || 0),
        originalItems.length,
        maxVideoIndex
      );
      if (requested <= 0) return pendingStatus;
      const terminalStateless = !!(pendingStatus.statelessProgress && !isBackendGenerationProgressActive(progress));
      const byVideo = new Map();
      originalItems.forEach((item, index) => {
        const videoIndex = Number(item?.videoIndex || 0) || index + 1;
        if (!byVideo.has(videoIndex)) {
          byVideo.set(videoIndex, item);
        }
      });
      const items = preserveSparseVideoIndexes ? [...originalItems] : [];
      if (!preserveSparseVideoIndexes) {
        for (let index = 1; index <= requested; index += 1) {
          const existing = byVideo.get(index);
          if (existing) {
            items.push(existing);
          } else {
            items.push({
              videoIndex: index,
              title: `视频 ${index}`,
              status: terminalStateless ? 'skipped' : 'pending_submission',
              statusLabel: terminalStateless ? '未提交' : '正在生成视频方案',
              jobId: null,
            });
          }
        }
      }
      const submittedStatuses = new Set(['submitted', 'polling', 'archiving', 'succeeded', 'failed']);
      const runningStatuses = new Set(['submitting', 'preparing_first_frame', 'preparing_tail_frame', 'submitted', 'polling', 'archiving', 'awaiting_tail_frame_continue']);
      const countStatus = (statuses) => items.filter((item) => statuses.has(String(item?.status || '').trim())).length;
      return {
        ...pendingStatus,
        generationProgress: {
          ...progress,
          totalRequested: preserveSparseVideoIndexes ? items.length : requested,
          items,
          submittedCount: countStatus(submittedStatuses),
          runningCount: countStatus(runningStatuses),
          postProcessingCount: countStatus(new Set(['archiving'])),
          waitingCount: countStatus(new Set(['pending_submission', 'awaiting_tail_frame_continue'])),
          succeededCount: countStatus(new Set(['succeeded'])),
          failedCount: countStatus(new Set(['failed'])),
          skippedCount: countStatus(new Set(['skipped', 'cancelled', 'canceled'])),
          deletedCount: countStatus(new Set(['deleted'])),
        },
      };
    }

    function mergeGenerationProgressSnapshot(previousProgress = {}, nextProgress = {}, options = {}) {
      if (!nextProgress || typeof nextProgress !== 'object') return nextProgress;
      const previousBatchId = String(previousProgress?.generationBatchId || '').trim();
      const nextBatchId = String(nextProgress?.generationBatchId || '').trim();
      const differentBatch = previousBatchId && nextBatchId && previousBatchId !== nextBatchId;
      const authoritativeRecovery = !!(
        options.authoritative === true
        ||
        nextProgress.readOnlyRecovery
        || nextProgress.statelessProgress
        || ['cancelled', 'canceled'].includes(String(nextProgress.status || '').trim())
      );
      if (differentBatch || authoritativeRecovery) return nextProgress;
      const previousItems = Array.isArray(previousProgress?.items) ? previousProgress.items : [];
      const nextItems = Array.isArray(nextProgress.items) ? nextProgress.items : [];
      if (!previousItems.length || previousItems.length <= nextItems.length) return nextProgress;
      const byVideo = new Map(previousItems.map((item, index) => [
        Number(item?.videoIndex || 0) || index + 1,
        item,
      ]));
      nextItems.forEach((item, index) => {
        byVideo.set(Number(item?.videoIndex || 0) || index + 1, item);
      });
      const items = [...byVideo.entries()]
        .sort(([left], [right]) => left - right)
        .map(([, item]) => item);
      return {
        ...previousProgress,
        ...nextProgress,
        totalRequested: Math.max(
          Number(previousProgress?.totalRequested || 0) || 0,
          Number(nextProgress.totalRequested || 0) || 0,
          items.length,
        ),
        items,
      };
    }

    function mergeGenerationStatusPayload(payload = {}, data = {}, sessionId = '', options = {}) {
      const previousPending = payload?.pendingStatus || {};
      const incomingPending = extractPendingStatus(data, sessionId) || {};
      const incomingProgress = incomingPending.generationProgress;
      const authoritativeRecovery = !!(
        options.authoritative === true
        ||
        incomingPending.readOnlyRecovery
        || incomingPending.statelessProgress
        || incomingProgress?.readOnlyRecovery
        || incomingProgress?.statelessProgress
      );
      const generationProgress = incomingProgress
        ? mergeGenerationProgressSnapshot(previousPending.generationProgress, incomingProgress, options)
        : previousPending.generationProgress;
      const pendingBase = authoritativeRecovery ? {} : previousPending;
      const nextPayload = {
        ...payload,
        pendingStatus: normalizePendingStatusProgress({
          ...pendingBase,
          ...incomingPending,
          ...(generationProgress ? { generationProgress } : {}),
        }),
      };
      mergePendingGenerationBatchId(payload, nextPayload);
      const isErrorPayload = String(payload?.meta?.operation || '').trim() === 'error';
      const familyStillActive = String(generationProgress?.status || '').trim() === 'active'
        || (generationProgress?.items || []).some((item) => (
          String(item?.status || '').trim() === 'awaiting_tail_frame_continue'
        ));
      if (
        !isErrorPayload
        && data.status !== 'pending'
        && incomingProgress
        && isTerminalTaskStatus(data.status)
        && !familyStillActive
      ) {
        nextPayload.stage = 'completed';
        nextPayload.meta = {
          ...(nextPayload.meta || {}),
          operation: 'pending',
          continuationClosed: true,
        };
      }
      return nextPayload;
    }

    function replaceLocalAssistantError(session, message) {
      const last = session?.messages?.at?.(-1);
      if (last && last.role === 'assistant') {
        last.error = message;
        delete last.payload;
        return;
      }
      session?.messages?.push?.({ role: 'assistant', error: message });
    }

    function buildRequestError(data) {
      const error = new Error(data?.error || '请求失败');
      if (data && typeof data === 'object') {
        error.code = String(data.code || '').trim();
        error.payload = data;
      }
      return error;
    }

    function formatNetworkError(error) {
      const raw = String(error?.message || error || '').trim();
      const lower = raw.toLowerCase();
      if (!raw || lower === 'failed to fetch' || lower.includes('networkerror') || lower.includes('load failed')) {
        return '无法连接本地服务（127.0.0.1:18720）。请确认工作台服务仍在运行后重试。';
      }
      if (lower.includes('abort')) return '请求已中断，请重试。';
      return raw;
    }

    function isTransientChatTransportError(error) {
      const name = String(error?.name || '').trim().toLowerCase();
      if (name === 'aborterror') return true;
      const raw = String(error?.message || error || '').trim().toLowerCase();
      return !raw
        || raw === 'failed to fetch'
        || raw.includes('networkerror')
        || raw.includes('load failed')
        || raw.includes('abort')
        || raw.includes('network request failed');
    }

    function isStoredTransportFailureMessage(value) {
      const text = String(value || '').trim();
      if (!text) return false;
      if (text.includes('无法连接本地服务') || text.includes('请求已中断')) return true;
      const lower = text.toLowerCase();
      return lower === 'failed to fetch'
        || lower.includes('networkerror')
        || lower.includes('load failed');
    }

    async function recoverSessionsAfterReload() {
      const sessions = Array.isArray(state.sessions) ? state.sessions : [];
      let changed = false;
      for (const session of sessions) {
        if (session?.executionMode === 'agent') continue;
        const last = session?.messages?.at?.(-1);
        const pendingMessage = [...(session?.messages || [])].reverse().find((message) => (
          message?.role === 'assistant'
          && (message?.payload?.pendingStatus || extractGenerationBatchId(message?.payload))
        ));
        if (pendingMessage) {
          const recovered = await reconcilePendingSessionAfterReload(session, pendingMessage, pendingMessage);
          if (recovered) changed = true;
          continue;
        }
        if (last?.role === 'user') {
          const recovered = await recoverReplyAfterOrphanedUserMessage(session);
          if (recovered) changed = true;
          continue;
        }
        if (!last || last.role !== 'assistant') continue;
        if (!isStoredTransportFailureMessage(last.error)) continue;
        const recovered = await tryRecoverSessionAfterTransportFailure(
          session,
          getLatestUserRequestText(session),
        );
        if (recovered) changed = true;
      }
      return changed;
    }

    async function recoverReplyAfterOrphanedUserMessage(session) {
      const sessionId = String(session?.id || '').trim();
      if (!sessionId) return false;
      try {
        const { res, data } = await fetchChatStatusWithBatchFallback(sessionId, session, {
          preferLatestBatch: true,
        });
        if (!res.ok || !data || typeof data !== 'object') return false;
        if (data.status !== 'pending' && data.reply) {
          session.messages.push({ role: 'assistant', payload: buildAssistantPayload(data, sessionId) });
          return true;
        }
        if (data.status === 'pending' || data.generationProgress) {
          const pendingPayload = buildLocalPendingPayload(
            sessionId,
            getLatestUserRequestText(session),
          );
          session.messages.push({
            role: 'assistant',
            payload: mergeGenerationStatusPayload(pendingPayload, data, sessionId),
          });
          return true;
        }
      } catch (error) {
        console.error(error);
      }
      return false;
    }

    async function reconcilePendingSessionAfterReload(session, targetMessage = null, statusMessage = null) {
      const sessionId = String(session?.id || '').trim();
      const messageToUpdate = targetMessage || session?.messages?.at?.(-1);
      const messageForStatus = statusMessage || messageToUpdate;
      if (!sessionId || !messageToUpdate?.payload) return false;
      try {
        const statusSession = {
          ...session,
          messages: [messageForStatus],
        };
        const { res, data } = await fetchChatStatusWithBatchFallback(sessionId, statusSession, {
          preferLatestBatch: true,
        });
        if (!res.ok) return false;
        if (data.status !== 'pending' && data.reply) {
          const terminalPayload = buildAssistantPayload(data, sessionId);
          messageToUpdate.payload = data.generationProgress
            ? mergeGenerationStatusPayload(terminalPayload, data, sessionId)
            : terminalPayload;
          return true;
        }
        if (!data?.generationProgress) return false;
        messageToUpdate.payload = mergeGenerationStatusPayload(
          messageToUpdate.payload,
          data,
          sessionId,
          { authoritative: true },
        );
        return true;
      } catch (error) {
        console.error(error);
        return false;
      }
    }

    async function tryRecoverSessionAfterTransportFailure(session, requestText) {
      const sessionId = String(session?.id || '').trim();
      const last = session?.messages?.at?.(-1);
      if (!sessionId || !last || last.role !== 'assistant') return false;
      try {
        const { res, data } = await fetchChatStatusWithBatchFallback(sessionId, session);
        if (!res.ok || !data || typeof data !== 'object') return false;
        if (data.status !== 'pending' && data.reply) {
          delete last.error;
          last.payload = buildAssistantPayload(data, sessionId);
          return true;
        }
        if (data.status === 'pending' || data.generationProgress) {
          const pendingPayload = buildLocalPendingPayload(sessionId, requestText);
          const recoveredPayload = mergeGenerationStatusPayload(pendingPayload, data, sessionId);
          delete last.error;
          last.payload = recoveredPayload;
          return true;
        }
      } catch (error) {
        console.error(error);
      }
      return false;
    }

    function wait(ms) {
      return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    async function tryRecoverTimedOutChat(session, requestText, failureData) {
      if (String(failureData?.code || '').trim() !== 'AI8VIDEO_CHAT_TIMEOUT_NO_GENERATION') {
        return false;
      }
      const sessionId = String(failureData?.sessionId || session?.id || '').trim();
      if (!sessionId) {
        return false;
      }
      const retryDelays = [0, 400, 1200, 2400];
      for (const delay of retryDelays) {
        if (delay > 0) {
          await wait(delay);
        }
        let res;
        let data;
        try {
          ({ res, data } = await fetchChatStatusWithBatchFallback(sessionId, session));
        } catch (error) {
          console.error(error);
          continue;
        }
        if (!res.ok || !data || typeof data !== 'object') {
          continue;
        }
        if (data.status !== 'pending' && data.reply) {
          replaceLocalPendingPayload(session, buildAssistantPayload(data, sessionId));
          return true;
        }
        if (data.status !== 'pending' && data.generationProgress) {
          const pendingPayload = buildLocalPendingPayload(sessionId, requestText);
          replaceLocalPendingPayload(
            session,
            mergeGenerationStatusPayload(pendingPayload, data, sessionId),
          );
          if (!data.statelessProgress && isTerminalTaskStatus(data.status)) {
            schedulePendingPoll(sessionId, 3000);
          }
          return true;
        }
        if (data.status === 'pending') {
          const pendingPayload = buildLocalPendingPayload(sessionId, requestText);
          replaceLocalPendingPayload(
            session,
            mergeGenerationStatusPayload(pendingPayload, data, sessionId),
          );
          return true;
        }
      }
      return false;
    }

    els.messageEditor.addEventListener('input', () => {
      syncMessageInputFromEditor();
      renderMaterialMentionPicker();
    });

    els.messageEditor.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        hideMaterialMentionPicker();
      }
    });

    els.messageEditor.addEventListener('paste', (event) => {
      event.preventDefault();
      const text = event.clipboardData?.getData('text/plain') || '';
      document.execCommand('insertText', false, text);
    });

    els.messageEditor.addEventListener('copy', (event) => {
      const selection = window.getSelection();
      if (!selection || !selection.rangeCount) return;
      const range = selection.getRangeAt(0);
      if (!els.messageEditor.contains(range.commonAncestorContainer)) return;
      const text = rangeFragmentToEditorText(range.cloneContents()).trim();
      if (!text) return;
      event.preventDefault();
      event.clipboardData?.setData('text/plain', text);
    });
