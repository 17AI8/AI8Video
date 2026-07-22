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
            { label: '集数', value: 0 },
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
          currentResult.expectedCount || currentResult.summary?.episodeCount || 0
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
        const groups = buildEpisodeGroups(result, last.payload.meta, state.assets);
        const summary = summarizeResult(result, groups);
        return {
          title: '当前进度',
          isActive: false,
          summary: buildProgressSummary(summary, groups, last.payload.meta),
          metrics: [
            { label: '集数', value: summary.episodeCount },
            { label: '已生成', value: summary.successCount ?? summary.passCount },
            { label: '归档', value: groups.filter((item) => item.archiveStatus && item.archiveStatus !== 'disabled').length },
          ],
          details: groups.map((item) => ({
            title: `第 ${item.index} 集 · ${item.title}${item.updated ? ' · 已重做' : ''}`,
            body: [
              item.updated ? '本次最新动作：已按修改意见重做这一集' : '',
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
            { label: '集数', value: draft.episode_count || draft.episodeCount || 1 },
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
            { label: '模式', value: draft.mode === 'multi_episode_script' ? '多集' : (draft.mode === 'single_prompt' ? '单条' : '待识别') },
            { label: '集数', value: draft.episodeCount || 1 },
            { label: '参考图', value: draft.referenceImage ? '已给' : (draft.referenceImageEnabled === false ? '不用' : '待定') },
          ],
          details: [{
            title: '已识别信息',
            body: [
              `风格：${draft.styleHint || '未指定'}`,
              `集数：${draft.episodeCount || '待确认'}`,
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
            { label: '集数', value: '-' },
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
          { label: '集数', value: '-' },
          { label: '通过', value: '-' },
          { label: '归档', value: '-' },
        ],
        details: [{ title: '状态', body: '等待下一步输入。' }],
      };
    }
