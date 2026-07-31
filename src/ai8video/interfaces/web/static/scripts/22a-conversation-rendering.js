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

    function syncAssistantBubbleLayoutClasses(bubble) {
      const directChildren = Array.from(bubble?.children || []);
      const hasPendingCard = directChildren.some((element) => element.classList.contains('pending-card'));
      const hasAgentVideoResults = directChildren.some((element) => element.classList.contains('agent-video-results'));
      bubble?.classList.toggle(
        'agent-run-with-results',
        directChildren.length === 2 && hasPendingCard && hasAgentVideoResults,
      );
      bubble?.classList.toggle(
        'pending-only',
        directChildren.length === 1 && hasPendingCard,
      );
    }

    function renderAssistantPayload(payload, context = {}) {
      const blocks = [];
      const isHistoricalMessage = Number(context.messageIndex) < Number(context.messageCount) - 1;
      const guideAwaiting = String(payload.awaiting || '').trim();
      const activeAwaiting = String(context.activeAwaiting || '').trim();
      const isActiveGuide = !isHistoricalMessage
        && !!guideAwaiting
        && guideAwaiting === activeAwaiting;
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
          <div class="assistant-error-message">
            <strong>本轮真实任务未完成</strong>
            <div>${escapeHtml(humanizeAssistantError(payload.text))}</div>
          </div>
        `);
      } else if (payload.text && !isGeneratedResult && payload.meta?.operation !== 'pending') {
        blocks.push(renderParagraphs(payload.text));
      }
      if (payload.meta?.operation === 'pending' || hasAgentProgress) {
        const pending = normalizePendingStatusProgress(renderedPendingStatus || payload.pendingStatus || {});
        const historicalPending = isHistoricalMessage;
        const readOnlyRecovery = pending.readOnlyRecovery || pending.generationProgress?.readOnlyRecovery;
        const staticPending = historicalPending || readOnlyRecovery;
        const displayedPending = staticPending
          ? buildHistoricalPendingSnapshot(pending, readOnlyRecovery ? '已中断，待重试' : '历史进度快照')
          : pending;
        const pendingProgress = buildPendingProgressFromRecentResults(displayedPending);
        const pendingOverview = buildProgressOverview({ videos: pendingProgress.videos, isActive: !staticPending });
        const pendingActive = !historicalPending && isPendingStatusActive(pending);
        const manualTailFrameWait = pending.phase === 'awaiting_tail_frame_continue';
        const pendingTitle = historicalPending
          ? '历史任务进度快照'
          : (manualTailFrameWait
            ? '等待手动继续'
            : (pendingActive ? '后台继续执行中' : getPendingStatusLabel(pending)));
        const elapsed = pending.elapsedSeconds > 0 ? `已等待 ${pending.elapsedSeconds} 秒` : '已进入后台继续执行';
        const pendingLine = historicalPending
          ? '这是较早消息的进度记录，不再显示为执行中。'
          : (pendingActive
          ? (manualTailFrameWait
            ? '上一条尾帧已准备完成，点击“继续”后才会提交下一条视频。'
            : `${elapsed}，结果会自动回填到当前对话。`)
          : buildTerminalPendingLine(pendingProgress, pending));
        const pendingCancel = pendingActive
          ? renderForceCancelButton(pending.sessionId || context.sessionId || state.activeId, {
              messageIndex: context.messageIndex,
            })
          : '';
        const pendingStepChain = `${renderAgentStepChain(displayedPending, { messageIndex: context.messageIndex })}`;
        const pendingExecutionEvents = renderAgentExecutionEvents(displayedPending, {
          messageIndex: context.messageIndex,
        });
        const pendingThumbnails = renderAgentVideoThumbnails(displayedPending);
        blocks.push(`
          <div class="mini-card pending-card${staticPending ? ' is-history' : ''}">
            <div class="pending-card-head">
              <div class="pending-card-title">
                <strong>${escapeHtml(pendingTitle)}</strong>
                <span class="pending-card-status">${escapeHtml(pendingLine)}</span>
              </div>
              ${pendingCancel}
            </div>
            ${renderProgressOverview(pendingOverview, pendingStepChain)}
            ${pendingExecutionEvents}
          </div>
          ${pendingThumbnails}
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
      if (payload.meta?.guide && isActiveGuide && String(payload.meta.guide.kind || '') === 'smart_split_confirmation') {
        blocks.push(renderSmartSplitPlanOverview(payload.meta.guide));
      }
      if (payload.meta?.guide && isActiveGuide) {
        blocks.push(renderCompletionGuide(payload.meta.guide));
      }
      if (isGeneratedResult && summary && !hasAgentProgress) {
        blocks.push(renderAssistantResultCards(getActiveSession(), payload, resultGroups, summary));
      }
      return blocks.join('');
    }

    function buildHistoricalPendingSnapshot(pending = {}, statusLabel = '历史进度快照') {
      const progress = pending.generationProgress || {};
      const items = Array.isArray(progress.items) ? progress.items.map((item) => {
        const status = String(item?.status || '').trim();
        return isTerminalProgressStatus(status) ? item : {
          ...item,
          status: 'snapshot',
          historicalSnapshot: true,
          statusLabel,
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
      const message = humanizeAgentEventMessage(event?.message || '状态已更新') + progress;
      return `<div class="agent-step-detail ${stateClass}"><span class="agent-step-detail-marker" aria-hidden="true"></span><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span></div></div>`;
    }

    function humanizeAgentEventMessage(value) {
      const statusLabels = {
        queued: '排队中',
        pending: '等待中',
        submitted: '已提交',
        running: '执行中',
        processing: '处理中',
        polling: '查询结果中',
        archiving: '归档中',
        succeeded: '已完成',
        completed: '已完成',
        failed: '失败',
        cancelled: '已取消',
      };
      return String(value || '').replace(
        /\b(queued|pending|submitted|running|processing|polling|archiving|succeeded|completed|failed|cancelled)\b/gi,
        (status) => statusLabels[status.toLowerCase()] || status
      );
    }

    function renderAgentExecutionEvents(pending = {}, options = {}) {
      const events = collapseAgentPollingEvents(pending.generationProgress?.events);
      const detailsKey = buildAgentStepDetailsKey(
        pending.sessionId || state.activeId,
        options.messageIndex
      );
      const expanded = isAgentStepDetailsExpanded(detailsKey);
      const readOnlyRecovery = pending.readOnlyRecovery || pending.generationProgress?.readOnlyRecovery;
      if (readOnlyRecovery) {
        return '<div class="agent-step-details is-single"><div class="agent-step-details-latest"><div class="agent-step-detail done"><span class="agent-step-detail-marker" aria-hidden="true"></span><div><strong>历史任务已结束</strong><span>服务重启前的进度仅恢复为只读记录，不会继续生成。</span></div></div></div></div>';
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
        return `<div class="agent-step-details is-single"><div class="agent-step-details-latest"><div class="agent-step-detail active"><span class="agent-step-detail-marker" aria-hidden="true"></span><div><strong>${escapeHtml(currentMessage)}</strong><span>${escapeHtml(currentDetail)}</span></div></div></div></div>`;
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
      const progress = reconcileBatchMergedProgress(pending.generationProgress || {});
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
          const itemWithBatch = {
            ...item,
            generationBatchId: item?.generationBatchId || progress.generationBatchId || pending.generationBatchId || '',
          };
          if (itemWithBatch.historicalSnapshot) return buildProgressStatusResultItem(itemWithBatch, index, progressItems);
          const mirror = findUserGeneratedMirror(item);
          if (mirror?.userGeneratedKey) return mirror;
          return buildProgressStatusResultItem(itemWithBatch, index, progressItems);
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
      const firstRowItems = items.slice(0, 6);
      const secondRowItems = items.slice(6);
      const batchState = ensureAgentResultBatchMergeState();
      const batchOptions = {
        compact: true,
        batchMerge: batchState.active,
        batchSubmitting: batchState.submitting,
        selectedKeys: batchState.selectedKeys,
      };
      const batchOpeningClass = batchState.animateOpening ? ' is-batch-merge-opening' : '';
      const batchSubmittingClass = batchState.submitting ? ' is-batch-merge-submitting' : '';
      return `
        <div class="agent-video-results${batchOpeningClass}${batchSubmittingClass}" aria-label="已生成视频">
          <div class="agent-video-results-primary">
            ${renderResultNotifyStrip(firstRowItems, batchOptions)}
            ${renderAgentVideoResultActionButton(items)}
          </div>
          ${secondRowItems.length ? `<div class="agent-video-results-secondary">${renderResultNotifyStrip(secondRowItems, batchOptions)}</div>` : ''}
        </div>
      `;
    }

    function renderAgentVideoResultActionButton(items = []) {
      const mergeableCount = new Set(
        items.map((item) => resolveResultBatchMergeKey(item)).filter(Boolean)
      ).size;
      if (mergeableCount < 2) return '';
      const batchState = ensureAgentResultBatchMergeState();
      if (!batchState.active) {
        return '<button type="button" class="agent-video-batch-merge-button" data-toggle-agent-batch-merge>批量合并</button>';
      }
      const selectedCount = batchState.selectedKeys.length;
      const openingClass = batchState.animateOpening ? ' is-opening' : '';
      return `
        <div class="agent-video-batch-merge-actions${openingClass}">
          <button type="button" class="agent-video-batch-merge-button" data-confirm-agent-batch-merge
            ${selectedCount < 2 || batchState.submitting ? 'disabled' : ''}>
            ${batchState.submitting ? '合并中' : '确认合并'}
          </button>
          <button type="button" class="agent-video-batch-merge-cancel" data-toggle-agent-batch-merge>取消</button>
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

    function renderSmartSplitFeedbackDrawer() {
      return `
        <div class="smart-split-feedback-drawer" data-smart-split-feedback-drawer hidden>
          <label class="smart-split-feedback-field">
            <span>重新分集意见（可选）</span>
            <textarea
              rows="2"
              data-smart-split-feedback
              placeholder="例如：合并前两集，每集只讲一个主题"
            ></textarea>
          </label>
          <button
            type="button"
            class="guide-action-button smart-split-feedback-submit"
            data-guide-action-kind="send"
            data-guide-action-value="重新分集"
            data-smart-split-feedback-submit
          >提交重新分集</button>
        </div>
      `;
    }

    function renderSmartSplitPlanOverview(guide) {
      const videos = Array.isArray(guide?.plannedVideos) ? guide.plannedVideos : [];
      if (!videos.length) {
        return '<div class="smart-split-plan-recovering">正在恢复每集完整规划…</div>';
      }
      return `
        <div class="smart-split-plan-tree" role="tree">
          ${videos.map((video, index) => `
            <article class="smart-split-plan-node" role="treeitem" aria-expanded="false">
              <button type="button" class="smart-split-plan-summary" data-smart-split-plan-toggle>
                <i class="smart-split-plan-chevron" aria-hidden="true"></i>
                <span class="smart-split-plan-title">${escapeHtml(video.index || index + 1)}. ${escapeHtml(video.title || '未命名视频')}</span>
                <span class="smart-split-plan-meta">详情</span>
              </button>
              <div class="smart-split-plan-drawer">
                <div class="smart-split-plan-drawer-slot">
                  <div class="smart-split-plan-source">${escapeHtml(formatSmartSplitSourceSummary(video.sourceSummary))}</div>
                  <div class="smart-split-plan-prompt" data-smart-split-plan-prompt>${escapeHtml(video.prompt || '暂无完整提示词')}</div>
                  <textarea class="smart-split-plan-editor" data-smart-split-plan-editor hidden>${escapeHtml(video.prompt || '')}</textarea>
                  <div class="smart-split-plan-edit-actions" role="group" aria-label="提示词编辑操作">
                    <button type="button" data-smart-split-plan-edit>编辑</button>
                    <button type="button" data-smart-split-plan-save data-video-index="${escapeHtml(video.index || index + 1)}" disabled>保存</button>
                  </div>
                </div>
              </div>
            </article>
          `).join('')}
        </div>
      `;
    }

    function formatSmartSplitSourceSummary(sourceSummary) {
      const value = String(sourceSummary || '').trim();
      if (!value) return '已生成独立内容方案';
      if (value.startsWith('来自')) return `参考了${value.slice(2)}`;
      if (value.startsWith('基于')) return `参考了${value.slice(2)}`;
      return value.startsWith('参考') ? value : `参考了${value}`;
    }

    const smartSplitPlanRecoveryInflight = new Set();
    const smartSplitPlanRecoveryReady = new Set();

    async function recoverLegacySmartSplitPlans(session) {
      if (!session?.id || smartSplitPlanRecoveryInflight.has(session.id) || smartSplitPlanRecoveryReady.has(session.id)) return;
      const targets = (session.messages || []).filter((message) => {
        const guide = message?.payload?.meta?.guide;
        return guide?.kind === 'smart_split_confirmation';
      });
      if (!targets.length) return;
      smartSplitPlanRecoveryInflight.add(session.id);
      try {
        const target = targets.at(-1);
        let plannedVideos = target.payload.meta.guide.plannedVideos;
        let fetched = false;
        if (!Array.isArray(plannedVideos) || !plannedVideos.length) {
          const res = await fetch(`/api/smart-split-plan?sessionId=${encodeURIComponent(session.id)}`);
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !Array.isArray(data.plannedVideos) || !data.plannedVideos.length) return;
          plannedVideos = data.plannedVideos;
          target.payload.meta.guide.plannedVideos = plannedVideos;
          target.payload.text = compactLegacySmartSplitText(target.payload.text);
          fetched = true;
        }
        const restoreRes = await fetch('/api/smart-split-plan/restore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionId: session.id, plannedVideos }),
        });
        if (!restoreRes.ok) return;
        smartSplitPlanRecoveryReady.add(session.id);
        if (fetched) {
          persistSessions();
          if (session.id === state.activeId) renderMessages();
        }
      } finally {
        smartSplitPlanRecoveryInflight.delete(session.id);
      }
    }

    function repairRecoveredSmartSplitFailure(session) {
      const messages = session?.messages;
      const latest = messages?.at?.(-1);
      const errorText = String(latest?.payload?.text || latest?.text || '');
      if (latest?.role !== 'assistant' || !errorText.includes('draft.raw_text is required')) return false;
      messages.pop();
      if (messages.at(-1)?.role === 'user' && /确认分集|确认并继续/.test(messages.at(-1).text || '')) {
        messages.pop();
      }
      persistSessions();
      return true;
    }

    function compactLegacySmartSplitText(text) {
      const value = String(text || '');
      const firstItem = value.search(/\n\s*1[.、]/);
      return firstItem > 0 ? value.slice(0, firstItem).trim() : value;
    }

    function renderGuideActionButton(action, index, isSmartSplitConfirmation) {
      const value = String(action.value || '');
      const isReplanToggle = isSmartSplitConfirmation && value.trim() === '重新分集';
      const isSmartSplitConfirm = isSmartSplitConfirmation && value.trim() === '确认分集';
      const isSmartSplitCancel = isSmartSplitConfirmation && action.kind === 'dismiss-plan';
      return `
        <button
          type="button"
          class="guide-action-button${index === 0 && !isSmartSplitConfirmation ? ' primary' : ''}"
          data-guide-action-kind="${escapeHtml(action.kind || 'fill')}"
          data-guide-action-value="${escapeHtml(value)}"
          ${isSmartSplitConfirm ? 'data-smart-split-confirm-action data-smart-split-hide-on-feedback' : ''}
          ${isSmartSplitCancel ? 'data-smart-split-cancel-action data-smart-split-hide-on-feedback' : ''}
          ${isReplanToggle ? 'data-smart-split-feedback-toggle aria-expanded="false"' : ''}
        >${escapeHtml(action.label || '继续')}</button>
      `;
    }

    function smartSplitActionRank(action) {
      const value = String(action?.value || '').trim();
      if (value === '重新分集') return 0;
      if (value === '确认分集') return 1;
      if (String(action?.kind || '').trim() === 'dismiss-plan') return 2;
      return 3;
    }

    function renderCompletionGuide(guide) {
      if (!guide || typeof guide !== 'object') return '';
      const missingFields = Array.isArray(guide.missingFields) ? guide.missingFields : [];
      const actions = Array.isArray(guide.actions) ? guide.actions : [];
      const isSmartSplitConfirmation = String(guide.kind || '') === 'smart_split_confirmation';
      const summaryMarkup = isSmartSplitConfirmation
        ? ''
        : `<div>${escapeHtml(guide.summary || '生成前还需要补充一点信息。')}</div>`;
      const feedbackDrawerMarkup = isSmartSplitConfirmation
        ? renderSmartSplitFeedbackDrawer()
        : '';
      const displayActions = isSmartSplitConfirmation
        ? [...actions].sort((left, right) => smartSplitActionRank(left) - smartSplitActionRank(right))
        : actions;
      return `
        <div class="mini-card guide-card${isSmartSplitConfirmation ? ' smart-split-confirmation-card' : ''}">
          <strong>${escapeHtml(isSmartSplitConfirmation
            ? '确认后进入视频生成，也可重新分集调整。'
            : (guide.title || '补充信息'))}</strong>
          ${summaryMarkup}
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
          ${displayActions.length ? `
            <div class="guide-actions">
              ${displayActions.map((action, index) => (
                renderGuideActionButton(action, index, isSmartSplitConfirmation)
              )).join('')}
            </div>
          ` : ''}
          ${feedbackDrawerMarkup}
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
