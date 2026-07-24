    async function refreshGenerationMode() {
      const res = await fetch('/api/generation-mode');
      const data = await res.json().catch(() => ({}));
      state.generationMode = {
        ...(state.generationMode || {}),
        concurrentGeneration: !!data?.concurrentGeneration,
        smartSplit: !!data?.smartSplit,
        confirmSmartSplit: !!data?.confirmSmartSplit,
        tailFrameChaining: !!data?.tailFrameChaining,
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
      startGenerationProgress(session.id, value);
      render();

      try {
        const refresh = shouldRefreshChatSession(session, value);
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sessionId: session.id,
            message: value,
            refresh,
            temporaryKnowledge,
            useDefaultKnowledgeReference,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          const recovered = await tryRecoverTimedOutChat(session, value, data);
          if (recovered) {
            clearTemporaryScriptKnowledgeReference();
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
          throw buildRequestError(data);
        }
        clearTemporaryScriptKnowledgeReference();
        replaceLocalPendingPayload(session, buildAssistantPayload(data, session.id));
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
          videoCount: inferVideoCountFromText(text),
        },
      };
    }

    function extractGenerationBatchId(payload) {
      return String(
        payload?.generationBatchId
        || payload?.pendingStatus?.generationBatchId
        || payload?.pendingStatus?.generationProgress?.generationBatchId
        || payload?.generationProgress?.generationBatchId
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
      const originalItems = Array.isArray(progress.items) ? progress.items : [];
      const maxVideoIndex = originalItems.reduce((max, item, index) => (
        Math.max(max, Number(item?.videoIndex || 0) || index + 1)
      ), 0);
      const requested = Math.max(
        Number(pendingStatus.videoCount || 0) || 0,
        Number(progress.totalRequested || 0) || 0,
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
      const items = [];
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
      const submittedStatuses = new Set(['submitted', 'polling', 'archiving', 'succeeded', 'failed']);
      const runningStatuses = new Set(['submitting', 'preparing_first_frame', 'submitted', 'polling', 'archiving']);
      const countStatus = (statuses) => items.filter((item) => statuses.has(String(item?.status || '').trim())).length;
      return {
        ...pendingStatus,
        generationProgress: {
          ...progress,
          totalRequested: requested,
          items,
          submittedCount: countStatus(submittedStatuses),
          runningCount: countStatus(runningStatuses),
          postProcessingCount: countStatus(new Set(['archiving'])),
          waitingCount: countStatus(new Set(['pending_submission'])),
          succeededCount: countStatus(new Set(['succeeded'])),
          failedCount: countStatus(new Set(['failed'])),
          skippedCount: countStatus(new Set(['skipped', 'cancelled', 'canceled'])),
          deletedCount: countStatus(new Set(['deleted'])),
        },
      };
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
        const last = session?.messages?.at?.(-1);
        if (!last || last.role !== 'assistant' || !isStoredTransportFailureMessage(last.error)) continue;
        const recovered = await tryRecoverSessionAfterTransportFailure(
          session,
          getLatestUserRequestText(session),
        );
        if (recovered) changed = true;
      }
      return changed;
    }

    async function tryRecoverSessionAfterTransportFailure(session, requestText) {
      const sessionId = String(session?.id || '').trim();
      const last = session?.messages?.at?.(-1);
      if (!sessionId || !last || last.role !== 'assistant') return false;
      try {
        const res = await fetch(buildChatStatusUrl(sessionId, session));
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data || typeof data !== 'object') return false;
        if (data.status !== 'pending' && data.reply) {
          delete last.error;
          last.payload = buildAssistantPayload(data, sessionId);
          return true;
        }
        if (data.status === 'pending' || data.generationProgress) {
          const pendingPayload = buildLocalPendingPayload(sessionId, requestText);
          pendingPayload.pendingStatus = {
            ...(pendingPayload.pendingStatus || {}),
            ...extractPendingStatus(data, sessionId),
          };
          delete last.error;
          last.payload = pendingPayload;
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

    function isSimpleFollowupMessage(text) {
      const value = String(text || '').trim();
      if (!value) return false;
      if (/^\d{1,3}$/.test(value)) return true;
      if (/^(并发模式|普通模式|不用参考图|需要参考图|有参考图|跳过关键词|不用关键词|无关键词|确认分集|确认并继续|重新分集)$/u.test(value)) {
        return true;
      }
      return false;
    }

    function looksLikeFreshBaseRequest(text) {
      const value = String(text || '').trim();
      if (!value || isSimpleFollowupMessage(value)) return false;
      if (/@/.test(value)) return true;
      if (/\.(docx|doc|pdf|txt|jpg|jpeg|png|webp|mp4)\b/i.test(value)) return true;
      if (/(剧本|提示词|生成|短视频|视频|文案|产品|教程|探店|脚本)/u.test(value)) return true;
      return value.length >= 18;
    }

    function shouldRefreshChatSession(session, text) {
      if (!session || !looksLikeFreshBaseRequest(text)) {
        return false;
      }
      const last = session.messages?.at?.(-1);
      if (!last) return false;
      if (last.error) return true;
      if (last.role !== 'assistant') return false;
      if (isPendingPayload(last.payload) || isCollectingPayload(last.payload)) return true;
      if (last.payload?.stage === 'completed') return true;
      if (last.payload?.meta?.operation === 'generate') return true;
      return false;
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
          res = await fetch(buildChatStatusUrl(sessionId, session));
          data = await res.json();
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
          pendingPayload.pendingStatus = {
            ...(pendingPayload.pendingStatus || {}),
            ...extractPendingStatus(data, sessionId),
          };
          replaceLocalPendingPayload(session, pendingPayload);
          if (!data.statelessProgress && isTerminalTaskStatus(data.status)) {
            schedulePendingPoll(sessionId, 3000);
          }
          return true;
        }
        if (data.status === 'pending') {
          const pendingPayload = buildLocalPendingPayload(sessionId, requestText);
          pendingPayload.pendingStatus = {
            ...(pendingPayload.pendingStatus || {}),
            ...extractPendingStatus(data, sessionId),
          };
          replaceLocalPendingPayload(session, pendingPayload);
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
