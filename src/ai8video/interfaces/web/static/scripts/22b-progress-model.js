    const AGENT_STEP_ORBIT_DURATION_MS = 1200;

    function buildAgentStepChainModel(pending = {}) {
      const progress = pending.generationProgress || {};
      const items = Array.isArray(progress.items) ? progress.items : [];
      const itemStatuses = items.map((item) => String(item?.status || '').trim());
      const countStatuses = (statuses) => itemStatuses.filter((value) => statuses.has(value)).length;
      const phase = String(pending.phase || '').trim();
      const status = String(pending.status || '').trim();
      const total = Number(progress.totalRequested || pending.videoCount || 0) || 0;
      const submitted = Number(progress.submittedCount || 0) || 0;
      const finished = Number(progress.succeededCount || 0) || 0;
      const failed = Number(progress.failedCount || 0) || 0;
      const planning = phase === 'planning' || String(progress.status || '').trim() === 'planning';
      const planningCount = countStatuses(new Set(['pending_submission', 'planning']));
      const submittingCount = countStatuses(new Set(['preparing_first_frame', 'preparing_tail_frame', 'submitting']));
      const generatingCount = countStatuses(new Set(['submitted', 'polling']));
      const archivingCount = countStatuses(new Set(['archiving']));
      const manualTailFrameWait = phase === 'awaiting_tail_frame_continue'
        || itemStatuses.includes('awaiting_tail_frame_continue');
      const archiveStarted = archivingCount > 0 || (Array.isArray(progress.events) && progress.events.some(
        (event) => String(event?.status || '').trim() === 'archiving'
      ));
      const terminal = ['cancelled', 'canceled'].includes(status)
        || (total > 0 && finished + failed >= total);
      let activeStepIndex = 0;
      if (!terminal && manualTailFrameWait) activeStepIndex = 2;
      else if (!terminal && (planning || planningCount)) activeStepIndex = 1;
      else if (!terminal && submittingCount) activeStepIndex = 2;
      else if (!terminal && generatingCount) activeStepIndex = 3;
      else if (!terminal && archiveStarted) activeStepIndex = 4;
      else if (!terminal && submitted) activeStepIndex = 3;
      const steps = [
        { label: '理解需求', detail: activeStepIndex === 0 ? '正在整理你的目标、数量和已附带素材。' : '已识别本次任务的核心要求。' },
        { label: '规划任务', detail: activeStepIndex === 1 ? '正在拆分可执行的视频任务并核对生成条件。' : activeStepIndex > 1 || terminal ? '已形成生成任务和执行顺序。' : '等待需求理解完成后开始规划。' },
        {
          label: manualTailFrameWait ? '等待继续' : '提交生成',
          detail: manualTailFrameWait
            ? '尾帧已准备完成，点击继续后才会提交下一条视频。'
            : (activeStepIndex === 2
              ? `正在准备并提交 ${submittingCount || total || 1} 个视频任务。`
              : (submitted ? `已提交 ${submitted}/${total || submitted} 个生成任务。` : '等待任务规划完成后提交。')),
        },
        { label: '生成视频', detail: activeStepIndex === 3 ? `正在生成 ${generatingCount || submitted || 1} 个视频任务。` : terminal ? `已生成 ${finished} 个${failed ? `，${failed} 个失败` : ''}。` : '等待上游视频服务开始处理。' },
        { label: '归档结果', detail: activeStepIndex === 4 ? `正在整理 ${archivingCount || 1} 个已生成结果。` : terminal ? '本轮任务已结束，结果会保留在当前对话和结果库。' : '视频完成后会自动整理到结果库。' },
      ];
      return steps.map((step, index) => ({
        ...step,
        state: terminal
          ? (failed && index >= 3 ? 'error' : 'done')
          : (index < activeStepIndex ? 'done' : index === activeStepIndex ? 'active' : 'waiting'),
      }));
    }

    function renderAgentStepChain(pending = {}, options = {}) {
      void options;
      const steps = buildAgentStepChainModel(pending);
      const orbitDelayMs = -(Date.now() % AGENT_STEP_ORBIT_DURATION_MS);
      return `
        <div class="agent-step-chain-wrap">
          <div class="agent-step-chain" role="list" aria-label="任务步骤链" style="--agent-step-orbit-delay:${orbitDelayMs}ms">
            ${steps.map((step, index) => `
              ${index ? `<span class="agent-step-connector${steps[index - 1].state === 'done' ? ' done' : ''}" aria-hidden="true"></span>` : ''}
              <span class="agent-step ${step.state}" role="listitem"${step.state === 'active' ? ' aria-current="step"' : ''}>
                <span class="agent-step-index">${index + 1}</span>
                <span class="agent-step-label">${escapeHtml(step.label)}</span>
              </span>
            `).join('')}
          </div>
        </div>
      `;
    }

    function buildProgressModel(session) {
      if (!session) return null;
      const liveProgress = buildGenerationProgressModel(session);
      if (liveProgress) return liveProgress;
      const last = session.messages.at(-1);
      if (!last) {
        return {
          title: '当前进度',
          isActive: false,
          summary: '等待输入提示词',
          metrics: [
            { label: '视频数', value: 0 },
            { label: '已生成', value: 0 },
            { label: '归档', value: 0 },
          ],
          details: [{ title: '状态', body: '还没有开始生成。' }],
        };
      }
      if (last.role === 'assistant' && shouldUsePayloadAsCurrentProgress(last.payload)) {
        const pending = normalizePendingStatusProgress(last.payload.pendingStatus || {});
        const pendingProgress = buildPendingProgressFromRecentResults(pending);
        const backendProgress = pending.generationProgress || null;
        const isPlanning = String(pending.phase || '').trim() === 'planning';
        const isActive = isPendingStatusActive(pending);
        const pendingLabel = getPendingStatusLabel(
          pending,
          isPlanning ? 'AI8video 正在分析文档并规划剧本' : '后台继续执行中'
        );
        return {
          title: '当前进度',
          sessionId: session.id,
          cancelable: isActive,
          isActive,
          summary: pending.elapsedSeconds > 0
            ? `${pendingLabel}，已等待 ${pending.elapsedSeconds} 秒`
            : pendingLabel,
          metrics: backendProgress
            ? [
                { label: '已提交', value: `${Number(backendProgress.submittedCount || 0)}/${Number(backendProgress.totalRequested || 0)}` },
                { label: '生成中', value: Number(backendProgress.runningCount || 0) },
                { label: '方案生成中', value: Number(backendProgress.waitingCount || 0) },
                { label: '失败', value: Number(backendProgress.failedCount || 0) },
              ]
            : [
                { label: '状态', value: isPlanning ? '规划中' : '后台' },
                { label: '结果', value: pendingProgress.doneCount > 0 ? `${pendingProgress.doneCount}/${pendingProgress.expectedCount}` : '待回填' },
              ],
          cards: pendingProgress.cards,
          pendingCount: pendingProgress.pendingCount,
          videos: pendingProgress.cards.length ? [] : pendingProgress.videos,
          details: pendingProgress.details,
        };
      }
      if (last.role === 'assistant' && last.payload?.meta?.operation === 'batch_run' && last.payload?.result) {
        const report = last.payload.result;
        const summary = last.payload.summary || summarizeBatchReport(report);
        const failures = (report.topFailureReasons || []).slice(0, 5);
        return {
          title: '当前进度',
          isActive: false,
          summary: buildBatchProgressSummary(summary),
          metrics: [
            { label: '目标', value: summary.targetGenerationCount ?? summary.targetPassCount },
            { label: '已生成', value: summary.successCount ?? summary.passCount },
            { label: '尝试', value: summary.totalVideoAttempts },
            { label: summary.expansionRoundCount > 0 ? '补量' : '候选', value: summary.expansionRoundCount > 0 ? `${summary.expandedSeedCount} 条` : (summary.seedMessages || '-') },
          ],
          details: [
            {
              title: '批量结果',
              body: [
                `本轮目标：${summary.targetGenerationCount ?? summary.targetPassCount} 条`,
                `初始候选：${summary.seedMessages} 条`,
                `已生成：${summary.successCount ?? summary.passCount} 条`,
                `生成失败：${summary.failedCount ?? summary.rejectCount} 条`,
                summary.expansionRoundCount > 0 ? `自动补量：${summary.expansionRoundCount} 轮，共补入 ${summary.expandedSeedCount} 条候选` : '自动补量：本轮未触发',
                summary.topUpStrategies?.length ? `补量策略：${summary.topUpStrategies.map(formatTopUpStrategy).join('；')}` : '',
                summary.expansionError ? `补量异常：${summary.expansionError}` : '',
                `状态：${summary.goalMet ? '已达标' : '未达标'}`,
              ].filter(Boolean).join('\n'),
            },
            ...failures.map((item, index) => ({
              title: `主要失败原因 ${index + 1}`,
              body: `${item.reason} · ${item.count} 次`,
            })),
          ],
        };
      }
      if (last.role === 'assistant' && last.payload?.awaiting === 'batch_seed_messages') {
        const targetPassCount = Number(last.payload?.meta?.targetPassCount || 30);
        return {
          title: '当前进度',
          isActive: false,
          summary: `等待补充批量候选；目标 ${targetPassCount} 条生成`,
          metrics: [
            { label: '目标', value: targetPassCount },
            { label: '候选', value: '待补充' },
            { label: '模式', value: '批量' },
          ],
          details: [{
            title: '下一步',
            body: '请逐行发送候选提示词、候选选题或候选剧本，一行一条。',
          }],
        };
      }
      if (last.role === 'assistant' && last.payload?.result) {
        const currentResult = buildCurrentResultGalleryModel(session);
        const stageCards = buildBatchStageCards(
          currentResult.groups,
          currentResult.expectedCount || currentResult.summary?.videoCount || 0
        );
        if (stageCards.length) {
          const stageSummary = summarizeBatchStageCards(stageCards);
          const playableItems = getPlayableResultItems(currentResult);
          const progressCards = buildProgressResultCards(playableItems, stageCards);
          return {
            title: '当前进度',
            isActive: stageSummary.runningCount > 0 || stageSummary.waitingCount > 0,
            summary: stageSummary.text,
            metrics: [
              { label: '已生成', value: stageSummary.doneCount },
              { label: '生成中', value: stageSummary.runningCount },
              { label: '待生成', value: stageSummary.waitingCount },
              { label: '失败', value: stageSummary.failedCount },
            ],
            cards: progressCards,
            videos: progressCards.length ? [] : stageCards,
            details: [],
          };
        }
        const result = last.payload.result;
        const groups = buildVideoGroups(result, last.payload.meta, state.assets);
        const summary = summarizeResult(result, groups);
        return {
          title: '当前进度',
          isActive: false,
          summary: buildProgressSummary(summary, groups, last.payload.meta),
          metrics: [
            { label: '视频数', value: summary.videoCount },
            { label: '已生成', value: summary.successCount ?? summary.passCount },
            { label: '归档', value: groups.filter((item) => item.archiveStatus && item.archiveStatus !== 'disabled').length },
          ],
          details: groups.map((item) => ({
            title: `第 ${item.index} 条 · ${item.title}${item.updated ? ' · 已重做' : ''}`,
            body: [
              item.updated ? '本次最新动作：已按修改意见重做这条视频' : '',
              `任务：${item.jobStatus || '待生成'}`,
              `归档：${item.archiveStatus || '未归档'}${item.archiveBackend ? ` · ${item.archiveBackend}` : ''}`,
              item.generationReasons ? `原因：${item.generationReasons}` : '',
            ].filter(Boolean).join('\n'),
            updated: item.updated,
          })),
        };
      }
      if (last.role === 'assistant' && last.payload?.draft && !last.payload?.awaiting && ['completed', 'error'].includes(String(last.payload?.stage || '').trim())) {
        const draft = last.payload.draft;
        const text = String(last.payload.text || '').trim();
        const failed = text.includes('失败') || String(last.payload?.stage || '').trim() === 'error';
        return {
          title: '当前进度',
          isActive: false,
          summary: failed ? '视频生成失败' : '任务已结束',
          metrics: [
            { label: '视频数', value: draft.video_count || draft.videoCount || 1 },
            { label: '状态', value: failed ? '失败' : '已结束' },
            { label: '归档', value: '-' },
          ],
          details: [{
            title: '任务结果',
            body: text || '本轮任务已结束，未返回可展示成片。',
          }],
        };
      }
      if (last.role === 'assistant' && last.payload?.draft) {
        const draft = last.payload.draft;
        const awaiting = summarizeAwaiting(last.payload.awaiting);
        return {
          title: '当前进度',
          isActive: false,
          summary: awaiting,
          metrics: [
            { label: '模式', value: draft.mode === 'batch_videos' ? '批量' : (draft.mode === 'single_video' ? '单条' : '待识别') },
            { label: '视频数', value: draft.videoCount || 1 },
            { label: '参考图', value: draft.referenceImage ? '已给' : (draft.referenceImageEnabled === false ? '不用' : '待定') },
          ],
          details: [{
            title: '已识别信息',
            body: [
              `风格：${draft.styleHint || '未指定'}`,
              `视频数：${draft.videoCount || '待确认'}`,
              `参考图：${draft.referenceImage || (draft.referenceImageEnabled === false ? '不用参考图' : '待确认')}`,
              `状态：${awaiting}`,
            ].join('\n'),
          }],
        };
      }
      if (last.role === 'user') {
        return {
          title: '当前进度',
          isActive: false,
          summary: '等待 AI8video 回复',
          metrics: [
            { label: '视频数', value: '-' },
            { label: '通过', value: '-' },
            { label: '归档', value: '-' },
          ],
          details: [{ title: '最新输入', body: last.text }],
        };
      }
      return {
        title: '当前进度',
        isActive: false,
        summary: '继续补充需求',
        metrics: [
          { label: '视频数', value: '-' },
          { label: '通过', value: '-' },
          { label: '归档', value: '-' },
        ],
        details: [{ title: '状态', body: '等待下一步输入。' }],
      };
    }
