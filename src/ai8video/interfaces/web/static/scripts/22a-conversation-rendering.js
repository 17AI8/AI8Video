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
      if (Array.isArray(payload.videos) && payload.videos.length) return true;
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
      const resultGroups = isGeneratedResult ? buildVideoGroups(payload.result, payload.meta, state.assets) : [];
      const summary = isGeneratedResult ? summarizeResult(payload.result, resultGroups) : null;
      const renderedPendingStatus = payload.pendingStatus?.generationProgress
        ? payload.pendingStatus
        : buildTerminalAgentPendingStatus(payload, resultGroups, summary, context.sessionId);
      const hasAgentProgress = !!renderedPendingStatus?.generationProgress;
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
      if (payload.meta?.operation === 'pending' || hasAgentProgress) {
        const pending = normalizePendingStatusProgress(renderedPendingStatus || payload.pendingStatus || {});
        const historicalPending = Number(context.messageIndex) < Number(context.messageCount) - 1;
        const displayedPending = historicalPending ? buildHistoricalPendingSnapshot(pending) : pending;
        const pendingProgress = buildPendingProgressFromRecentResults(displayedPending);
        const pendingOverview = buildProgressOverview({ videos: pendingProgress.videos, isActive: !historicalPending });
        const pendingActive = !historicalPending && isPendingStatusActive(pending);
        const pendingTitle = historicalPending
          ? '历史任务进度快照'
          : (pendingActive ? '后台继续执行中' : getPendingStatusLabel(pending));
        const elapsed = pending.elapsedSeconds > 0 ? `已等待 ${pending.elapsedSeconds} 秒` : '已进入后台继续执行';
        const pendingLine = historicalPending
          ? '这是较早消息的进度记录，不再显示为执行中。'
          : (pendingActive
          ? `${elapsed}，结果会自动回填到当前对话。`
          : buildTerminalPendingLine(pendingProgress, pending));
        const pendingCancel = pendingActive
          ? renderForceCancelButton(pending.sessionId || context.sessionId || state.activeId, {
              messageIndex: context.messageIndex,
            })
          : '';
        blocks.push(`
          <div class="mini-card pending-card${historicalPending ? ' is-history' : ''}">
            <div class="pending-card-head">
              <div class="pending-card-title">
                <strong>${escapeHtml(pendingTitle)}</strong>
                <span class="pending-card-status">${escapeHtml(pendingLine)}</span>
              </div>
              ${pendingCancel}
            </div>
            ${renderProgressOverview(pendingOverview)}
            ${renderAgentStepChain(displayedPending, { messageIndex: context.messageIndex })}
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
        const videoIndex = payload.meta.rewrittenVideoIndex;
        const instruction = payload.meta.rewriteInstruction;
        blocks.push(`
          <div class="mini-card">
            <strong>已只重做第 ${escapeHtml(String(videoIndex || '-'))} 条视频</strong>
            <div>${escapeHtml(instruction || '其他视频保持不动。')}</div>
          </div>
        `);
      }
      if (payload.meta?.guide) {
        blocks.push(renderCompletionGuide(payload.meta.guide));
      }
      if (isGeneratedResult && summary && !hasAgentProgress) {
        blocks.push(renderAssistantResultCards(getActiveSession(), payload, resultGroups, summary));
      }
      return blocks.join('');
    }

    function buildHistoricalPendingSnapshot(pending = {}) {
      const progress = pending.generationProgress || {};
      const items = Array.isArray(progress.items) ? progress.items.map((item) => {
        const status = String(item?.status || '').trim();
        return isTerminalProgressStatus(status) ? item : {
          ...item,
          status: 'snapshot',
          historicalSnapshot: true,
          statusLabel: '历史进度快照',
        };
      }) : [];
      return { ...pending, generationProgress: { ...progress, items } };
    }

    function buildTerminalAgentPendingStatus(payload, resultGroups, summary, sessionId) {
      if (!payload?.result || !summary || !Array.isArray(resultGroups) || !resultGroups.length) return null;
      const items = resultGroups.map((group, index) => {
        const succeeded = isGeneratedResult(group);
        const failedStatusLabel = getGenerationFailureStageLabel(group);
        return {
          videoIndex: Number(group?.index || 0) || index + 1,
          title: group?.title || `视频 ${index + 1}`,
          status: succeeded ? 'succeeded' : 'failed',
          statusLabel: succeeded ? '已生成' : failedStatusLabel,
          jobId: group?.jobId || null,
          archiveStatus: group?.archiveStatus || '',
          archiveBackend: group?.archiveBackend || '',
          archiveKey: group?.archiveKey || '',
          error: group?.error || group?.generationReasons || '',
          hasLocalAsset: succeeded,
        };
      });
      const succeededCount = items.filter((item) => item.status === 'succeeded').length;
      const failedCount = items.filter((item) => item.status === 'failed').length;
      const status = failedCount ? (succeededCount ? 'completed_with_error' : 'failed') : 'completed';
      return {
        status,
        sessionId: sessionId || state.activeId || '',
        videoCount: items.length,
        generationProgress: {
          status,
          totalRequested: items.length,
          items,
          submittedCount: items.length,
          runningCount: 0,
          postProcessingCount: 0,
          waitingCount: 0,
          succeededCount,
          failedCount,
          skippedCount: 0,
          events: [{
            kind: 'terminal_result',
            status: failedCount ? 'failed' : 'succeeded',
            message: failedCount ? '本轮任务已结束，失败原因已回填' : '视频已生成并回填',
          }],
        },
      };
    }

    function buildAgentStepChainModel(pending = {}) {
      const progress = pending.generationProgress || {};
      const items = Array.isArray(progress.items) ? progress.items : [];
      const phase = String(pending.phase || '').trim();
      const status = String(pending.status || '').trim();
      const total = Number(progress.totalRequested || pending.videoCount || 0) || 0;
      const submitted = Number(progress.submittedCount || 0) || 0;
      const generatingStatuses = new Set(['submitting', 'preparing_first_frame', 'submitted', 'polling']);
      const generating = items.filter((item) => generatingStatuses.has(String(item?.status || '').trim())).length;
      const finished = Number(progress.succeededCount || 0) || 0;
      const failed = Number(progress.failedCount || 0) || 0;
      const archiving = items.filter((item) => String(item?.status || '').trim() === 'archiving').length;
      const archiveStarted = archiving > 0 || (Array.isArray(progress.events) && progress.events.some(
        (event) => String(event?.status || '').trim() === 'archiving'
      ));
      const terminal = ['cancelled', 'canceled'].includes(status)
        || (total > 0 && finished + failed >= total);
      const planning = phase === 'planning' || String(progress.status || '').trim() === 'planning';
      const planningState = planning ? 'active' : (submitted || generating || archiveStarted || terminal ? 'done' : 'waiting');
      const understandingState = planningState === 'waiting' ? 'active' : (planning ? 'active' : 'done');
      const generationState = generating ? 'active' : (archiveStarted ? 'done' : (terminal ? (failed ? 'error' : 'done') : 'waiting'));
      const archiveState = archiving ? 'active' : (terminal ? (failed ? 'error' : 'done') : 'waiting');
      return [
        { label: '理解需求', state: understandingState, detail: understandingState === 'active' ? '正在整理你的目标、数量和已附带素材。' : '已识别本次任务的核心要求。' },
        { label: '规划任务', state: planningState, detail: planningState === 'active' ? '正在拆分可执行的视频任务并核对生成条件。' : planningState === 'done' ? '已形成生成任务和执行顺序。' : '等待需求理解完成后开始规划。' },
        { label: '提交生成', state: submitted || generating || archiveStarted || terminal ? 'done' : 'waiting', detail: submitted ? `已提交 ${submitted}/${total || submitted} 个生成任务。` : '等待任务规划完成后提交。' },
        { label: '生成视频', state: generationState, detail: generating ? `正在生成 ${generating} 个视频任务。` : archiveStarted ? '视频生成已完成，正在处理本地结果。' : terminal ? `已生成 ${finished} 个${failed ? `，${failed} 个失败` : ''}。` : '等待上游视频服务开始处理。' },
        { label: '归档结果', state: archiveState, detail: archiving ? `正在整理 ${archiving} 个已生成结果。` : terminal ? '本轮任务已结束，结果会保留在当前对话和结果库。' : '视频完成后会自动整理到结果库。' },
      ];
    }

    function renderAgentStepChain(pending = {}, options = {}) {
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
        ${renderAgentExecutionEvents(pending, options)}
      `;
    }

    function buildAgentStepDetailsKey(sessionId, messageIndex) {
      const sessionKey = String(sessionId || state.activeId || '').trim() || 'session';
      const index = Number(messageIndex);
      return `${sessionKey}#${Number.isFinite(index) ? index : 'live'}`;
    }

    function isAgentStepDetailsExpanded(detailsKey) {
      const key = String(detailsKey || '').trim();
      if (!key) return false;
      return !!state.agentStepDetailsExpanded?.[key];
    }

    function toggleAgentStepDetailsExpanded(detailsKey) {
      const key = String(detailsKey || '').trim();
      if (!key) return false;
      if (!state.agentStepDetailsExpanded || typeof state.agentStepDetailsExpanded !== 'object') {
        state.agentStepDetailsExpanded = {};
      }
      state.agentStepDetailsExpanded[key] = !state.agentStepDetailsExpanded[key];
      return !!state.agentStepDetailsExpanded[key];
    }

    function applyAgentStepDetailsExpanded(detailsKey, rootEl = null) {
      const key = String(detailsKey || '').trim();
      if (!key) return false;
      const expanded = isAgentStepDetailsExpanded(key);
      const root = rootEl || els.messages?.querySelector(`[data-agent-step-details="${CSS.escape(key)}"]`);
      if (!root) return false;
      root.classList.toggle('is-expanded', expanded);
      const drawer = root.querySelector('.agent-step-details-drawer');
      if (drawer) drawer.setAttribute('aria-hidden', expanded ? 'false' : 'true');
      const toggle = root.querySelector('[data-agent-step-details-toggle]');
      if (toggle) {
        const count = Number(toggle.getAttribute('data-agent-step-details-count') || 0);
        toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        toggle.textContent = expanded ? '收起' : `展开全部 · ${count}`;
      }
      if (expanded && toggle) {
        window.requestAnimationFrame(() => {
          toggle.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
        });
      }
      return expanded;
    }

    function buildAgentStepDetailMarkup(event, index, { activeFirst = true } = {}) {
      const status = String(event?.status || '').trim();
      const stateClass = status === 'failed'
        ? 'error'
        : (activeFirst && index === 0 && !['succeeded', 'completed'].includes(status) ? 'active' : 'done');
      const segmentPrefix = String(event?.segmentLabel || '').trim();
      const prefix = segmentPrefix
        ? `${segmentPrefix} · `
        : (event?.videoIndex ? `第 ${event.videoIndex} 条 · ` : '');
      const progress = status === 'polling' && Number.isFinite(Number(event?.providerProgress))
        ? ` · ${Number(event.providerProgress)}%`
        : '';
      const title = prefix + (event?.title || '后台任务');
      const message = String(event?.message || '状态已更新') + progress;
      return `<div class="agent-step-detail ${stateClass}"><span class="agent-step-detail-marker" aria-hidden="true"></span><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span></div></div>`;
    }

    function renderAgentExecutionEvents(pending = {}, options = {}) {
      const events = collapseAgentPollingEvents(pending.generationProgress?.events);
      const detailsKey = buildAgentStepDetailsKey(
        pending.sessionId || state.activeId,
        options.messageIndex
      );
      const expanded = isAgentStepDetailsExpanded(detailsKey);
      const readOnlyRecovery = pending.readOnlyRecovery || pending.generationProgress?.readOnlyRecovery;
      const thumbnails = renderAgentVideoThumbnails(pending);
      if (readOnlyRecovery) {
        return `<div class="agent-step-details is-single"><div class="agent-step-details-latest"><div class="agent-step-detail done"><span class="agent-step-detail-marker" aria-hidden="true"></span><div><strong>历史任务已结束</strong><span>服务重启前的进度仅恢复为只读记录，不会继续生成。</span></div></div></div></div>${thumbnails}`;
      }
      if (!events.length) {
        const steps = buildAgentStepChainModel(pending);
        const activeStep = steps.find((step) => step.state === 'active') || steps[0];
        const rawStatus = String(
          pending.statusLabel || pending.generationProgress?.summary || ''
        ).trim();
        const isPlanning = String(pending.phase || '').trim() === 'planning'
          || String(pending.generationProgress?.status || '').trim() === 'planning';
        const currentMessage = rawStatus
          ? (isPlanning ? friendlyPlanningSummary(rawStatus) : humanizePublicExecutionStatus(rawStatus))
          : (activeStep?.label || '后台当前阶段');
        const currentDetail = String(activeStep?.detail || '当前步骤进展会持续更新。').trim();
        return `<div class="agent-step-details is-single"><div class="agent-step-details-latest"><div class="agent-step-detail active"><span class="agent-step-detail-marker" aria-hidden="true"></span><div><strong>${escapeHtml(currentMessage)}</strong><span>${escapeHtml(currentDetail)}</span></div></div></div></div>${thumbnails}`;
      }
      const latestMarkup = buildAgentStepDetailMarkup(events[0], 0, { activeFirst: true });
      const historyEvents = events.slice(1);
      const historyMarkup = historyEvents
        .map((event, index) => buildAgentStepDetailMarkup(event, index + 1, { activeFirst: false }))
        .join('');
      const toggle = historyEvents.length
        ? `<button type="button" class="agent-step-details-toggle" data-agent-step-details-toggle="${escapeHtml(detailsKey)}" data-agent-step-details-count="${events.length}" aria-expanded="${expanded ? 'true' : 'false'}">${expanded ? '收起' : `展开全部 · ${events.length}`}</button>`
        : '';
      const drawer = historyEvents.length
        ? `<div class="agent-step-details-drawer" aria-hidden="${expanded ? 'false' : 'true'}"><div class="agent-step-details-drawer-slot"><div class="agent-step-details-history">${historyMarkup}</div></div></div>`
        : '';
      return `
        <div class="agent-step-details${expanded ? ' is-expanded' : ''}${events.length === 1 ? ' is-single' : ''}" data-agent-step-details="${escapeHtml(detailsKey)}" aria-label="后台真实执行事件">
          <div class="agent-step-details-latest">${latestMarkup}</div>
          ${drawer}
          ${toggle}
        </div>
        ${thumbnails}
      `;
    }

    function collapseAgentPollingEvents(rawEvents) {
      if (!Array.isArray(rawEvents)) return [];
      const events = [];
      const latestStatusIndex = new Map();
      rawEvents.slice(-20).forEach((event) => {
        const status = String(event?.status || '').trim();
        const videoIndex = Number(event?.videoIndex || 0) || 0;
        const segmentIndex = Number(event?.segmentIndex || 0) || 0;
        const eventKind = String(event?.kind || status).trim();
        const eventKey = status ? `${videoIndex}:${segmentIndex}:${status}:${eventKind}` : '';
        if (eventKey && latestStatusIndex.has(eventKey)) {
          events[latestStatusIndex.get(eventKey)] = event;
          return;
        }
        events.push(event);
        if (eventKey) latestStatusIndex.set(eventKey, events.length - 1);
      });
      return events.reverse();
    }

    function renderAgentVideoThumbnails(pending = {}) {
      const progress = pending.generationProgress || {};
      const planning = String(pending.phase || '').trim() === 'planning'
        || String(progress.status || '').trim() === 'planning';
      const submitted = Number(progress.submittedCount || 0) || 0;
      // 理解需求/规划阶段尚无可预览任务，不展示视频占位卡。
      if (planning && !submitted) return '';
      const progressItems = Array.isArray(progress.items)
        ? progress.items
        : [];
      const items = progressItems
        .map((item, index) => {
          if (item?.historicalSnapshot) return buildProgressStatusResultItem(item, index);
          const mirror = findUserGeneratedMirror(item);
          if (mirror?.userGeneratedKey) return mirror;
          return buildProgressStatusResultItem(item, index);
        })
        .filter(Boolean);
      if (!items.length) {
        // 仅在已提交生成后才用数量占位；理解需求阶段即使已知目标条数也不展示小卡片。
        if (!submitted) return '';
        const pendingCount = Math.max(
          0,
          Number(progress.totalRequested || pending.videoCount || 0) || 0
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
      return `${action}：共 ${summary.videoCount} 条，${successCount} 条已生成，${archiveCount} 条已归档${failedPart}。`;
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
