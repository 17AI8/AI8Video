    function isPendingStatusActive(pending = {}) {
      if (pending.readOnlyRecovery || pending.generationProgress?.readOnlyRecovery) return false;
      if (pending.generationProgress) {
        return isBackendGenerationProgressActive(pending.generationProgress);
      }
      const status = String(pending.status || '').trim();
      if (isTerminalTaskStatus(status)) return false;
      return status === 'pending' || String(pending.phase || '').trim() === 'planning';
    }

    function isCollectingPayload(payload) {
      return payload?.stage === 'collecting' && payload?.meta?.operation === 'collect';
    }

    function isSessionPending(session) {
      const last = session?.messages?.at?.(-1);
      if (!(last && last.role === 'assistant' && isPendingPayload(last.payload))) return false;
      if (last.payload?.pendingStatus && !isPendingStatusActive(last.payload.pendingStatus)) return false;
      return true;
    }

    function isTerminalPendingPayloadAwaitingReply(payload) {
      if (!isPendingPayload(payload)) return false;
      const pending = payload.pendingStatus || {};
      if (!pending.generationProgress) return false;
      if (pending.statelessProgress) return false;
      if (isPendingStatusActive(pending)) return false;
      return !payload.result;
    }

    function isSessionAwaitingTerminalReply(session) {
      const last = session?.messages?.at?.(-1);
      return !!(last && last.role === 'assistant' && isTerminalPendingPayloadAwaitingReply(last.payload));
    }

    function isSessionCollecting(session) {
      const last = session?.messages?.at?.(-1);
      return !!(last && last.role === 'assistant' && isCollectingPayload(last.payload));
    }

    function clearPendingPoll(sessionId) {
      const timer = pendingPollTimers.get(sessionId);
      if (timer) {
        clearTimeout(timer);
        pendingPollTimers.delete(sessionId);
      }
    }

    function schedulePendingPoll(sessionId, delay = 3000) {
      clearPendingPoll(sessionId);
      pendingPollTimers.set(sessionId, window.setTimeout(() => {
        pollPendingSession(sessionId);
      }, delay));
    }

    function clearCollectingSync(sessionId) {
      const timer = collectingSyncTimers.get(sessionId);
      if (timer) {
        clearTimeout(timer);
        collectingSyncTimers.delete(sessionId);
      }
    }

    function collectingPayloadSignature(session) {
      const payload = session?.messages?.at?.(-1)?.payload || {};
      return [
        payload.awaiting || '',
        payload.draft?.episodeCount || '',
        payload.draft?.concurrentGeneration ?? '',
        payload.text || '',
      ].join('|');
    }

    function scheduleCollectingSync(sessionId, delay = 600) {
      clearCollectingSync(sessionId);
      collectingSyncTimers.set(sessionId, window.setTimeout(() => {
        syncCollectingSession(sessionId);
      }, delay));
    }

    function ensurePendingPolls() {
      const activePendingIds = new Set();
      state.sessions.forEach((session) => {
        if (!isSessionPending(session) && !isSessionAwaitingTerminalReply(session)) return;
        activePendingIds.add(session.id);
        if (!pendingPollTimers.has(session.id) && !pendingPollInflight.has(session.id)) {
          schedulePendingPoll(session.id, 1200);
        }
      });
      [...pendingPollTimers.keys()].forEach((sessionId) => {
        if (!activePendingIds.has(sessionId)) {
          clearPendingPoll(sessionId);
        }
      });
    }

    function ensureCollectingSyncs() {
      const activeCollectingIds = new Set();
      state.sessions.forEach((session) => {
        if (!isSessionCollecting(session)) return;
        activeCollectingIds.add(session.id);
        const signature = collectingPayloadSignature(session);
        if (collectingSyncSeen.get(session.id) === signature) return;
        if (!collectingSyncTimers.has(session.id) && !collectingSyncInflight.has(session.id)) {
          scheduleCollectingSync(session.id);
        }
      });
      [...collectingSyncTimers.keys()].forEach((sessionId) => {
        if (!activeCollectingIds.has(sessionId)) {
          clearCollectingSync(sessionId);
        }
      });
    }

    async function syncCollectingSession(sessionId) {
      if (collectingSyncInflight.has(sessionId)) return;
      const session = state.sessions.find((item) => item.id === sessionId);
      if (!isSessionCollecting(session)) {
        clearCollectingSync(sessionId);
        return;
      }
      const signature = collectingPayloadSignature(session);
      collectingSyncSeen.set(sessionId, signature);
      collectingSyncInflight.add(sessionId);
      try {
        const res = await fetch(buildChatStatusUrl(sessionId, session));
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.error || '状态查询失败');
        }
        const targetSession = state.sessions.find((item) => item.id === sessionId);
        const last = targetSession?.messages?.at?.(-1);
        if (!targetSession || !last || last.role !== 'assistant' || !isCollectingPayload(last.payload)) {
          clearCollectingSync(sessionId);
          return;
        }
        if (data.status === 'completed' && data.reply) {
          const nextPayload = buildAssistantPayload(data, sessionId);
          const before = JSON.stringify(last.payload || {});
          const after = JSON.stringify(nextPayload || {});
          if (before !== after) {
            last.payload = nextPayload;
            collectingSyncSeen.delete(sessionId);
            persistSessions();
            render();
          }
          clearCollectingSync(sessionId);
          return;
        }
        if (data.status === 'pending') {
          last.payload = buildLocalPendingPayload(sessionId, getLatestUserRequestText(targetSession));
          last.payload.pendingStatus = normalizePendingStatusProgress({
            ...(last.payload.pendingStatus || {}),
            ...extractPendingStatus(data, sessionId),
          });
          persistSessions();
          render();
          return;
        }
        if (data.status === 'idle') {
          replaceLocalAssistantError(
            targetSession,
            '这条历史收集状态已经失效；后台当前没有对应会话上下文，请重新发送最近一次需求。',
          );
          collectingSyncSeen.delete(sessionId);
          persistSessions();
          render();
          clearCollectingSync(sessionId);
          return;
        }
        clearCollectingSync(sessionId);
      } catch (error) {
        console.error(error);
        clearCollectingSync(sessionId);
      } finally {
        collectingSyncInflight.delete(sessionId);
      }
    }

    async function pollPendingSession(sessionId) {
      if (pendingPollInflight.has(sessionId)) return;
      const session = state.sessions.find((item) => item.id === sessionId);
      if (!isSessionPending(session) && !isSessionAwaitingTerminalReply(session)) {
        clearPendingPoll(sessionId);
        return;
      }
      pendingPollInflight.add(sessionId);
      try {
        const res = await fetch(buildChatStatusUrl(sessionId, session));
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.error || '状态查询失败');
        }
        const targetSession = state.sessions.find((item) => item.id === sessionId);
        const last = targetSession?.messages?.at?.(-1);
        if (!targetSession || !last || last.role !== 'assistant') {
          clearPendingPoll(sessionId);
          return;
        }
        if (isCancelledPendingPayload(last.payload)) {
          clearPendingPoll(sessionId);
          return;
        }
        if (data.status !== 'pending' && data.reply) {
          last.payload.pendingStatus = normalizePendingStatusProgress({
            ...(last.payload.pendingStatus || {}),
            ...extractPendingStatus(data, sessionId),
            status: data.status,
            phase: data.phase || 'completed',
            completedAt: data.completedAt || new Date().toISOString(),
          });
          if (state.generationProgress?.sessionId === sessionId) {
            clearGenerationProgress();
          }
          persistSessions();
          await refreshHealth();
          await refreshAssets();
          await refreshUserGeneratedResults();
          await refreshBatchAlerts();
          await refreshBatchReports();
          clearPendingPoll(sessionId);
          render();
          return;
        }
        if (data.status !== 'pending' && data.generationProgress) {
          last.payload.pendingStatus = normalizePendingStatusProgress({
            ...(last.payload.pendingStatus || {}),
            ...extractPendingStatus(data, sessionId),
          });
          if (state.generationProgress?.sessionId === sessionId) {
            clearGenerationProgress();
          }
          await Promise.allSettled([
            refreshAssets(),
            refreshUserGeneratedResults(),
          ]);
          persistSessions();
          render();
          if (data.statelessProgress || isTerminalTaskStatus(data.status)) {
            clearPendingPoll(sessionId);
          } else {
            schedulePendingPoll(sessionId, 3000);
          }
          return;
        }
        if (data.status === 'pending') {
          last.payload.pendingStatus = normalizePendingStatusProgress({
            ...(last.payload.pendingStatus || {}),
            ...extractPendingStatus(data, sessionId),
          });
          await Promise.allSettled([
            refreshAssets(),
            refreshUserGeneratedResults(),
          ]);
          persistSessions();
          render();
          schedulePendingPoll(sessionId, 3000);
          return;
        }
        if (data.status === 'idle') {
          if (!data.stalePending) {
            schedulePendingPoll(sessionId, 3000);
            return;
          }
          last.error = '后台没有检测到活跃的视频生成任务；这条等待状态已失效。';
          delete last.payload;
          if (state.generationProgress?.sessionId === sessionId) {
            clearGenerationProgress();
          }
          persistSessions();
          render();
          clearPendingPoll(sessionId);
          return;
        }
        if (state.generationProgress?.sessionId === sessionId) {
          clearGenerationProgress();
        }
        clearPendingPoll(sessionId);
      } catch (error) {
        console.error(error);
        const latestSession = state.sessions.find((item) => item.id === sessionId);
        if (isSessionPending(latestSession)) {
          schedulePendingPoll(sessionId, 5000);
        } else {
          clearPendingPoll(sessionId);
          render();
        }
      } finally {
        pendingPollInflight.delete(sessionId);
      }
    }

    async function forceCancelPendingSession(sessionId, messageIndex = null) {
      const targetSessionId = String(sessionId || '').trim();
      if (!targetSessionId || pendingCancelInflight.has(targetSessionId)) return;
      pendingCancelInflight.add(targetSessionId);
      clearPendingPoll(targetSessionId);
      applyCancelledPendingStatus(targetSessionId, {
        status: 'cancelled',
        phase: 'cancelled',
        statusLabel: '已强行终止',
        cancelledAt: new Date().toISOString(),
      }, messageIndex);
      render();
      try {
        const res = await fetch('/api/chat-cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sessionId: targetSessionId,
            reason: '用户强行终止，本地停止等待结果回填',
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.error || '强行终止失败');
        }
        applyCancelledPendingStatus(targetSessionId, data, messageIndex);
      } catch (error) {
        console.error(error);
      } finally {
        pendingCancelInflight.delete(targetSessionId);
        render();
      }
    }

    function applyCancelledPendingStatus(sessionId, data = {}, messageIndex = null) {
      const session = state.sessions.find((item) => item.id === sessionId);
      const isCancelableMessage = (item) => (
        item?.role === 'assistant'
        && item.payload
        && (isPendingPayload(item.payload) || item.payload?.pendingStatus)
      );
      const hasMessageIndex = messageIndex !== null && messageIndex !== undefined && String(messageIndex).trim() !== '';
      const targetIndex = hasMessageIndex ? Number(messageIndex) : NaN;
      let message = Number.isInteger(targetIndex)
        ? session?.messages?.[targetIndex]
        : session?.messages?.at?.(-1);
      if (session && !isCancelableMessage(message)) {
        message = [...(session.messages || [])].reverse().find(isCancelableMessage);
      }
      if (!session || !isCancelableMessage(message)) {
        return;
      }
      const previousPending = message.payload.pendingStatus || {};
      const nextPending = extractPendingStatus({
        ...data,
        status: 'cancelled',
        phase: 'cancelled',
        statusLabel: data.statusLabel || '已强行终止',
        generationProgress: normalizeCancelledGenerationProgress(
          data.generationProgress || previousPending.generationProgress,
          previousPending,
        ),
      }, sessionId) || {};
      message.payload = {
        ...message.payload,
        text: '已强行终止，本地已停止等待结果回填。',
        pendingStatus: {
          ...previousPending,
          ...nextPending,
          status: 'cancelled',
          phase: 'cancelled',
          statusLabel: '已强行终止',
          completedAt: data.cancelledAt || new Date().toISOString(),
        },
      };
      if (state.generationProgress?.sessionId === sessionId) {
        clearGenerationProgress();
      }
      persistSessions();
      clearPendingPoll(sessionId);
    }

    function normalizeCancelledGenerationProgress(progress, pending = {}) {
      const sourceItems = Array.isArray(progress?.items) ? progress.items : [];
      const total = Math.max(
        Number(progress?.totalRequested || 0) || 0,
        Number(pending.videoCount || 0) || 0,
        sourceItems.length
      );
	      const items = sourceItems.length
	        ? sourceItems.map((item, index) => {
	            const status = String(item?.status || '').trim();
	            if (['succeeded', 'failed', 'skipped', 'deleted'].includes(status)) return { ...item };
            return {
              ...item,
              episodeIndex: Number(item?.episodeIndex || 0) || index + 1,
              title: item?.title || `视频 ${index + 1}`,
              status: 'skipped',
              statusLabel: '已取消',
              error: '用户强行终止，本地停止等待结果回填',
            };
          })
        : Array.from({ length: Math.max(1, total || 1) }, (_, index) => ({
            episodeIndex: index + 1,
            title: `视频 ${index + 1}`,
            status: 'skipped',
            statusLabel: '已取消',
            error: '用户强行终止，本地停止等待结果回填',
          }));
	      const succeededCount = Number(progress?.succeededCount || 0) || items.filter((item) => item.status === 'succeeded').length;
	      const failedCount = Number(progress?.failedCount || 0) || items.filter((item) => item.status === 'failed').length;
	      const skippedCount = items.filter((item) => item.status === 'skipped').length;
	      const deletedCount = items.filter((item) => item.status === 'deleted').length;
	      return {
        ...(progress || {}),
        status: 'cancelled',
        totalRequested: Math.max(total, items.length),
        items,
        submittedCount: Number(progress?.submittedCount || 0) || items.filter((item) => item.jobId).length,
        runningCount: 0,
        waitingCount: 0,
	        succeededCount,
	        failedCount,
	        skippedCount,
	        deletedCount,
	      };
    }

    function buildChatStatusUrl(sessionId, session) {
      const params = new URLSearchParams({ sessionId });
      const pendingPayload = session?.messages?.at?.(-1)?.payload || {};
      const pendingStatus = pendingPayload?.pendingStatus || {};
      const generationBatchId = extractGenerationBatchId(pendingPayload);
      if (generationBatchId) {
        params.set('generationBatchId', generationBatchId);
      }
      const videoCount = Number(
        pendingStatus.videoCount || pendingStatus.generationProgress?.totalRequested || 0
      ) || 0;
      if (videoCount > 0) {
        params.set('videoCount', String(videoCount));
      }
      if (pendingStatus.pendingSince) {
        params.set('pendingSince', String(pendingStatus.pendingSince));
      }
      const jobs = extractStatusFallbackJobs(session);
      if (jobs.length) {
        params.set('jobs', JSON.stringify(jobs));
      }
      return `/api/chat-status?${params.toString()}`;
    }

    function extractStatusFallbackJobs(session) {
      const items = session?.messages?.at?.(-1)?.payload?.pendingStatus?.generationProgress?.items;
      if (!Array.isArray(items)) return [];
      return items
        .map((item, index) => ({
          episodeIndex: Number(item?.episodeIndex || 0) || index + 1,
          jobId: String(item?.jobId || '').trim(),
        }))
        .filter((item) => item.jobId)
        .slice(0, 12);
    }

    function pruneSettledPendingProgressFromSessions() {
      let changed = false;
      state.sessions = (state.sessions || []).map((session) => {
        const messages = Array.isArray(session?.messages) ? session.messages : [];
        const nextMessages = messages.map((message) => {
          const payload = message?.payload;
          if (message?.role !== 'assistant' || !payload?.pendingStatus) return message;
          if (isPendingPayload(payload) || isTerminalPendingPayloadAwaitingReply(payload)) return message;
          changed = true;
          const nextPayload = { ...payload };
          delete nextPayload.pendingStatus;
          return { ...message, payload: nextPayload };
        });
        return nextMessages === messages ? session : { ...session, messages: nextMessages };
      });
      return changed;
    }

    function summarizeTitle(text) {
      return text.replace(/\s+/g, ' ').slice(0, 18) || NEW_SESSION_TITLE;
    }
