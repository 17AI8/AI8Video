    function sanitizeConversationMessageForTextClear(message) {
      if (isTextOnlyConversationMessage(message)) return null;
      if (!message || typeof message !== 'object') return null;
      const next = {
        ...message,
        textCleared: true,
        payload: message.payload && typeof message.payload === 'object'
          ? sanitizeConversationPayloadForTextClear(message.payload)
          : message.payload,
      };
      if (next.text) delete next.text;
      if (next.error && !next.payload) return null;
      if (next.payload && typeof next.payload === 'object' && !hasNonTextConversationPayload(next.payload)) {
        return null;
      }
      return next;
    }

    function sanitizeConversationPayloadForTextClear(payload) {
      const next = { ...payload };
      delete next.text;
      delete next.draft;
      delete next.awaiting;
      if (next.meta && typeof next.meta === 'object') {
        next.meta = { ...next.meta };
        delete next.meta.guide;
        if (!Object.keys(next.meta).length) delete next.meta;
      }
      delete next.guide;
      return next;
    }

    function hasNonTextConversationPayload(payload) {
      if (!payload || typeof payload !== 'object') return false;
      if (payload.result || payload.summary || payload.pendingStatus) return true;
      if (['pending', 'planning', 'batch_run', 'rewrite', 'error'].includes(String(payload.meta?.operation || ''))) return true;
      if (Array.isArray(payload.results) && payload.results.length) return true;
      if (Array.isArray(payload.episodes) && payload.episodes.length) return true;
      if (Array.isArray(payload.files) && payload.files.length) return true;
      return false;
    }

    function buildPinnedProgressModel(session) {
      const liveProgress = buildGenerationProgressModel(session);
      if (liveProgress) {
        return {
          ...liveProgress,
          requestText: liveProgress.requestText || getLatestUserRequestText(session),
        };
      }
      const last = session?.messages?.at?.(-1);
      if (last?.role === 'assistant' && shouldUsePayloadAsCurrentProgress(last.payload)) {
        return {
          ...buildProgressModel(session),
          requestText: getLatestUserRequestText(session),
        };
      }
      return null;
    }

    function getLatestUserRequestText(session) {
      const messages = Array.isArray(session?.messages) ? session.messages : [];
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const item = messages[index];
        if (item?.role === 'user' && item?.text) {
          return item.text;
        }
      }
      return '';
    }

    function buildPinnedProgressMessage(model) {
      const wrap = document.createElement('div');
      wrap.className = 'message pinned-progress';
      const noteMarkup = '<button type="button" class="pinned-progress-note" data-show-progress-modal="1">查看进度</button>';
      const summary = String(model.summary || '').trim();
      wrap.innerHTML = `
        <div class="bubble">
          <div class="pinned-progress-banner-head">
            <div class="pinned-progress-title">
              <strong>${escapeHtml(model.title || '当前进度')}</strong>
              ${noteMarkup}
            </div>
            ${summary ? `<div class="pinned-progress-summary-chip">${escapeHtml(summary)}</div>` : ''}
          </div>
        </div>
      `;
      return wrap;
    }

    function renderAssistantPayload(payload, context = {}) {
      const blocks = [];
      const isBatchRun = payload.meta?.operation === 'batch_run';
      const isGeneratedResult = !!(payload.result && !isBatchRun);
      const isStaleDryRunResult = !!(isGeneratedResult && payload.result?.dryRun && state.health && !state.health.dryRun);
      if (isStaleDryRunResult) {
        return `
          <div class="mini-card">
            <strong>旧演示记录已隐藏</strong>
            <div>这条消息来自之前的 dry-run 测试，不代表当前真实接口结果。真实结果请看左侧“查看结果”。</div>
          </div>
        `;
      }
      if (payload.meta?.operation === 'error') {
        blocks.push(`
          <div class="mini-card">
            <strong>本轮真实任务未完成</strong>
            <div>${escapeHtml(humanizeAssistantError(payload.text))}</div>
          </div>
        `);
      } else if (payload.text && !isGeneratedResult && payload.meta?.operation !== 'pending') {
        blocks.push(renderParagraphs(payload.text));
      }
      if (payload.meta?.operation === 'pending' || (payload.pendingStatus?.generationProgress && !isGeneratedResult)) {
        const pending = normalizePendingStatusProgress(payload.pendingStatus || {});
        const pendingProgress = buildPendingProgressFromRecentResults(pending);
        const pendingOverview = buildProgressOverview({ videos: pendingProgress.videos });
        const pendingActive = isPendingStatusActive(pending);
        const planningProgress = pending.generationProgress && String(pending.generationProgress.status || '').trim() === 'planning';
        const pendingTitle = pendingActive && !planningProgress ? '后台继续执行中' : getPendingStatusLabel(pending);
        const elapsed = pending.elapsedSeconds > 0 ? `已等待 ${pending.elapsedSeconds} 秒` : '已进入后台继续执行';
        const pendingLine = pendingActive
          ? `${planningProgress && pending.generationProgress?.summary ? `${pending.generationProgress.summary}，` : ''}${elapsed}，结果会自动回填到当前对话。`
          : buildTerminalPendingLine(pendingProgress, pending);
        const pendingCancel = pendingActive
          ? renderForceCancelButton(pending.sessionId || context.sessionId || state.activeId, {
              messageIndex: context.messageIndex,
            })
          : '';
        blocks.push(`
          <div class="mini-card pending-card">
            <div class="pending-card-head">
              <div class="pending-card-title">
                <strong>${escapeHtml(pendingTitle)}</strong>
                <span class="pending-card-status">${escapeHtml(pendingLine)}</span>
              </div>
              ${pendingCancel}
            </div>
            ${renderProgressOverview(pendingOverview)}
            ${renderAgentStepChain(pending)}
          </div>
        `);
      }
      if (payload.meta?.operation === 'batch_run') {
        const summary = payload.summary || summarizeBatchReport(payload.result);
        const failures = (payload.result?.topFailureReasons || []).slice(0, 3);
        blocks.push(`
          <div class="summary-grid">
            <div class="summary-card"><strong>${summary.successCount ?? summary.passCount}</strong><span>已生成</span></div>
            <div class="summary-card"><strong>${summary.failedCount ?? summary.rejectCount}</strong><span>失败</span></div>
            <div class="summary-card"><strong>${summary.totalVideoAttempts}</strong><span>尝试</span></div>
            <div class="summary-card"><strong>${summary.goalMet ? '达标' : '未达标'}</strong><span>${summary.targetGenerationCount ?? summary.targetPassCount} 条目标</span></div>
          </div>
          <div class="mini-card">
            <strong>批量结果</strong>
            <div>共尝试 ${escapeHtml(String(summary.totalVideoAttempts || 0))} 条，已生成 ${escapeHtml(String(summary.successCount ?? summary.passCount ?? 0))} 条。</div>
            ${failures.length ? `<div class="job-meta">主要问题：${escapeHtml(failures.map((item) => `${item.reason} × ${item.count}`).join('；'))}</div>` : ''}
          </div>
        `);
      }
      if (payload.meta?.operation === 'rewrite') {
        const episodeIndex = payload.meta.rewrittenEpisodeIndex;
        const instruction = payload.meta.rewriteInstruction;
        blocks.push(`
          <div class="mini-card">
            <strong>已只重做第 ${escapeHtml(String(episodeIndex || '-'))} 集</strong>
            <div>${escapeHtml(instruction || '其他集数保持不动。')}</div>
          </div>
        `);
      }
      if (payload.meta?.guide) {
        blocks.push(renderCompletionGuide(payload.meta.guide));
      }
      const resultGroups = isGeneratedResult ? buildEpisodeGroups(payload.result, payload.meta, state.assets) : [];
      const summary = isGeneratedResult ? summarizeResult(payload.result, resultGroups) : null;
      if (isGeneratedResult && summary) {
        blocks.push(renderAssistantResultCards(getActiveSession(), payload, resultGroups, summary));
      }
      return blocks.join('');
    }

    function buildAgentStepChainModel(pending = {}) {
      const progress = pending.generationProgress || {};
      const items = Array.isArray(progress.items) ? progress.items : [];
      const phase = String(pending.phase || '').trim();
      const status = String(pending.status || '').trim();
      const total = Number(progress.totalRequested || pending.videoCount || 0) || 0;
      const submitted = Number(progress.submittedCount || 0) || 0;
      const running = Number(progress.runningCount || 0) || 0;
      const finished = Number(progress.succeededCount || 0) || 0;
      const failed = Number(progress.failedCount || 0) || 0;
      const archiving = items.filter((item) => String(item?.status || '').trim() === 'archiving').length;
      const terminal = ['cancelled', 'canceled'].includes(status)
        || (total > 0 && finished + failed >= total);
      const planning = phase === 'planning' || String(progress.status || '').trim() === 'planning';
      const planningState = planning ? 'active' : (submitted || running || terminal ? 'done' : 'waiting');
      const understandingState = planningState === 'waiting' ? 'active' : (planning ? 'active' : 'done');
      const generationState = running ? 'active' : (terminal ? (failed ? 'error' : 'done') : 'waiting');
      const archiveState = archiving ? 'active' : (terminal ? (failed ? 'error' : 'done') : 'waiting');
      return [
        { label: '理解需求', state: understandingState, detail: understandingState === 'active' ? '正在整理你的目标、数量和已附带素材。' : '已识别本次任务的核心要求。' },
        { label: '规划任务', state: planningState, detail: planningState === 'active' ? '正在拆分可执行的视频任务并核对生成条件。' : planningState === 'done' ? '已形成生成任务和执行顺序。' : '等待需求理解完成后开始规划。' },
        { label: '提交生成', state: submitted || running || terminal ? 'done' : 'waiting', detail: submitted ? `已提交 ${submitted}/${total || submitted} 个生成任务。` : '等待任务规划完成后提交。' },
        { label: '生成视频', state: generationState, detail: running ? `正在生成 ${running} 个视频任务。` : terminal ? `已生成 ${finished} 个${failed ? `，${failed} 个失败` : ''}。` : '等待上游视频服务开始处理。' },
        { label: '归档结果', state: archiveState, detail: archiving ? `正在整理 ${archiving} 个已生成结果。` : terminal ? '本轮任务已结束，结果会保留在当前对话和结果库。' : '视频完成后会自动整理到结果库。' },
      ];
    }

    function renderAgentStepChain(pending = {}) {
      const steps = buildAgentStepChainModel(pending);
      return `
        <div class="agent-step-chain-wrap">
          <div class="agent-step-chain" aria-label="任务步骤链">
            ${steps.map((step, index) => `
              ${index ? '<span class="agent-step-connector" aria-hidden="true"></span>' : ''}
              <span class="agent-step ${step.state}">
                <span class="agent-step-index">${index + 1}</span>
                <span>${escapeHtml(step.label)}</span>
              </span>
            `).join('')}
          </div>
        </div>
        ${renderAgentExecutionEvents(pending)}
      `;
    }

    function renderAgentExecutionEvents(pending = {}) {
      const events = collapseAgentPollingEvents(pending.generationProgress?.events);
      const readOnlyRecovery = pending.readOnlyRecovery || pending.generationProgress?.readOnlyRecovery;
      if (readOnlyRecovery) {
        return `<div class="agent-step-details"><div class="agent-step-detail done"><span class="agent-step-detail-marker" aria-hidden="true"></span><div><strong>历史任务已结束</strong><span>服务重启前的进度仅恢复为只读记录，不会继续生成。</span></div></div></div>${renderAgentVideoThumbnails(pending)}`;
      }
      if (!events.length) {
        const currentMessage = humanizePublicExecutionStatus(
          pending.statusLabel || pending.generationProgress?.summary
        );
        return `<div class="agent-step-details"><div class="agent-step-detail active"><span class="agent-step-detail-marker" aria-hidden="true"></span><div><strong>后台当前阶段</strong><span>${escapeHtml(currentMessage)}</span></div></div></div>${renderAgentVideoThumbnails(pending)}`;
      }
      return `
        <div class="agent-step-details" aria-label="后台真实执行事件">
          ${events.map((event) => {
            const status = String(event.status || '').trim();
            const state = status === 'failed' ? 'error' : ['succeeded', 'completed'].includes(status) ? 'done' : 'active';
            const prefix = event.episodeIndex ? `第 ${event.episodeIndex} 条 · ` : '';
            const progress = Number.isFinite(Number(event.providerProgress)) ? ` · ${Number(event.providerProgress)}%` : '';
            return `<div class="agent-step-detail ${state}"><span class="agent-step-detail-marker" aria-hidden="true"></span><div><strong>${escapeHtml(prefix + (event.title || '后台任务'))}</strong><span>${escapeHtml(String(event.message || '状态已更新') + progress)}</span></div></div>`;
          }).join('')}
        </div>
        ${renderAgentVideoThumbnails(pending)}
      `;
    }

    function collapseAgentPollingEvents(rawEvents) {
      if (!Array.isArray(rawEvents)) return [];
      const events = [];
      const latestStatusIndex = new Map();
      rawEvents.slice(-20).forEach((event) => {
        const status = String(event?.status || '').trim();
        if (status && latestStatusIndex.has(status)) {
          events[latestStatusIndex.get(status)] = { ...event, episodeIndex: null, title: '后台任务' };
          return;
        }
        events.push(event);
        if (status) latestStatusIndex.set(status, events.length - 1);
      });
      return events.reverse();
    }

    function renderAgentVideoThumbnails(pending = {}) {
      const progressItems = Array.isArray(pending.generationProgress?.items)
        ? pending.generationProgress.items
        : [];
      const items = progressItems
        .map((item, index) => {
          const mirror = findUserGeneratedMirror(item);
          if (mirror?.userGeneratedKey) return mirror;
          const status = String(item?.status || '').trim();
          if (['succeeded', 'completed'].includes(status)) return null;
          return buildProgressStatusResultItem(item, index);
        })
        .filter(Boolean);
      if (!items.length) {
        const pendingCount = Math.max(
          0,
          Number(pending.generationProgress?.totalRequested || pending.videoCount || 0) || 0
        );
        if (!pendingCount) return '';
        return `<div class="agent-video-results" aria-label="待生成视频">${renderProgressResultStrip([], pendingCount)}</div>`;
      }
      return `
        <div class="agent-video-results" aria-label="已生成视频">
          ${renderResultNotifyStrip(items, { compact: true })}
        </div>
      `;
    }

    function humanizePublicExecutionStatus(value) {
      const message = String(value || '').trim();
      if (/首帧/u.test(message)) return '正在准备首帧图。';
      if (/提交/u.test(message)) return '正在提交生成任务。';
      if (/归档|处理结果/u.test(message)) return '正在整理生成结果。';
      if (/规划|理解/u.test(message)) return '正在整理本次任务。';
      if (/生成|轮询|上游/u.test(message)) return '视频生成处理中。';
      return '后台正在准备任务。';
    }

    function renderCompletionGuide(guide) {
      if (!guide || typeof guide !== 'object') return '';
      const missingFields = Array.isArray(guide.missingFields) ? guide.missingFields : [];
      const actions = Array.isArray(guide.actions) ? guide.actions : [];
      return `
        <div class="mini-card guide-card">
          <strong>${escapeHtml(guide.title || '补充信息')}</strong>
          <div>${escapeHtml(guide.summary || '生成前还需要补充一点信息。')}</div>
          ${missingFields.length ? `
            <div class="guide-missing-list">
              ${missingFields.map((item) => `
                <div class="guide-missing-item">
                  <strong>${escapeHtml(item.label || item.key || '缺失信息')}</strong>
                  <span>${escapeHtml(item.reason || '请先补充后再继续。')}</span>
                </div>
              `).join('')}
            </div>
          ` : ''}
          ${actions.length ? `
            <div class="guide-actions">
              ${actions.map((action, index) => `
                <button
                  type="button"
                  class="guide-action-button${index === 0 ? ' primary' : ''}"
                  data-guide-action-kind="${escapeHtml(action.kind || 'fill')}"
                  data-guide-action-value="${escapeHtml(action.value || '')}"
                >${escapeHtml(action.label || '继续')}</button>
              `).join('')}
            </div>
          ` : ''}
        </div>
      `;
    }

    function buildAssistantResultText(summary, archiveCount, meta) {
      const action = meta?.operation === 'rewrite' ? '重做结果已返回' : '真实结果已返回';
      const successCount = Number(summary.successCount ?? summary.passCount ?? 0);
      const failedCount = Number(summary.failedCount ?? summary.rejectCount ?? 0);
      const failedPart = failedCount ? `，${failedCount} 条生成失败` : '';
      return `${action}：共 ${summary.episodeCount} 条，${successCount} 条已生成，${archiveCount} 条已归档${failedPart}。`;
    }

    function renderStatus() {
      const parts = [];
      const activeSession = getActiveSession();
      const activePending = isSessionPending(activeSession);
      const unavailableReason = getGenerationBlockingReason();
      const unavailable = !!unavailableReason;
      if (state.health) {
        parts.push(pill(state.health.hasLLM ? '文本鉴权已配置' : '文本鉴权缺失', state.health.hasLLM ? 'ok' : 'bad'));
        parts.push(pill(state.health.hasVideoModel ? '视频鉴权已配置' : '视频鉴权缺失', state.health.hasVideoModel ? 'ok' : 'bad'));
        if (unavailableReason) {
          parts.push(pill(unavailableReason, 'bad'));
        }
        const archiveBackendLabel = state.health.archiveResolvedBackend || state.health.archiveBackend;
        parts.push(pill(state.health.archiveEnabled ? `归档：${archiveBackendLabel}` : '归档未开启', state.health.archiveEnabled ? 'ok' : 'info'));
        const guard = state.health.realGenerationGuard;
        if (guard?.enabled && !state.health.dryRun) {
          parts.push(
            pill(
              `生成额度：本窗口剩余 ${guard.remainingInWindow}/${guard.maxJobsPerWindow} 条`,
              guard.remainingInWindow > 0 ? 'warn' : 'bad'
            )
          );
        }
      }
      if (state.busy) {
        parts.push(pill('正在生成中', 'warn'));
      }
      if (activePending) {
        parts.push(pill('后台继续执行中', 'info'));
      }
      els.statusBar.innerHTML = parts.join('');
      els.sendButton.disabled = state.busy || activePending || unavailable;
      const locked = state.busy || activePending || unavailable;
      els.composer.classList.toggle('locked', locked);
      els.messageEditor.contentEditable = locked ? 'false' : 'true';
      els.messageEditor.setAttribute('aria-disabled', String(locked));
      if (locked) {
        hideMaterialMentionPicker();
      }
    }

    function getGenerationBlockingReason() {
      if (!state.health) return '';
      if (!state.health.hasLLM) {
        return '未配置文本鉴权，禁止发送';
      }
      if (!state.health.dryRun && !state.health.hasVideoModel) {
        return '未配置视频鉴权，禁止发送';
      }
      const flowerTextRuntime = state.health.videoTextOverlayRuntime || {};
      if (flowerTextRuntime.enabled && flowerTextRuntime.textPresent && !flowerTextRuntime.ready) {
        return `花字烧录不可用：${flowerTextRuntime.blockingReason || '运行环境异常'}`;
      }
      return '';
    }

    function isRealGenerationUnavailable() {
      return !!getGenerationBlockingReason();
    }
