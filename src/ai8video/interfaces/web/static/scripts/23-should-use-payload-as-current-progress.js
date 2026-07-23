
    function shouldUsePayloadAsCurrentProgress(payload) {
      if (!payload || typeof payload !== 'object') return false;
      if (!isPendingPayload(payload)) return false;
      const pending = payload.pendingStatus || {};
      return isPendingStatusActive(pending);
    }

    function startGenerationProgress(sessionId, text, options = {}) {
      clearGenerationProgress();
      state.generationProgress = {
        sessionId,
        text,
        startedAt: Date.now(),
        count: Number(options.count || 0) || inferVideoCountFromText(text),
        kind: String(options.kind || ''),
        backendProgress: null,
        statusPollInflight: false,
        lastAssetRefreshAt: 0,
      };
      scheduleGenerationProgressTick();
    }

    function clearGenerationProgress() {
      if (state.generationProgressTimer) {
        window.clearTimeout(state.generationProgressTimer);
      }
      state.generationProgressTimer = null;
      state.generationProgress = null;
    }

    function scheduleGenerationProgressTick() {
      if (state.generationProgressTimer) {
        window.clearTimeout(state.generationProgressTimer);
      }
      state.generationProgressTimer = window.setTimeout(() => {
        if (!state.generationProgress) return;
        if (state.generationProgress.kind === 'extension') {
          void refreshExtensionGenerationProgress(state.generationProgress);
        }
        syncLocalPendingProgress();
        renderMessages();
        renderProgress();
        renderProgressModal();
        renderStatus();
        refreshRecentGenerationProgressAssets();
        scheduleGenerationProgressTick();
      }, 1000);
    }

    async function refreshExtensionGenerationProgress(progress) {
      if (!progress || progress.statusPollInflight || progress.kind !== 'extension') return;
      progress.statusPollInflight = true;
      try {
        const params = new URLSearchParams({
          sessionId: progress.sessionId,
          videoCount: '1',
          pendingSince: new Date(progress.startedAt).toISOString(),
        });
        const res = await fetch(`/api/chat-status?${params.toString()}`);
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.generationProgress) {
          progress.backendProgress = data.generationProgress;
          renderProgress();
          renderProgressModal();
        }
        const leftKey = String(els.videoPreviewBody?.querySelector('.video-preview-stage-grid')?.dataset.leftVideoKey || '').trim();
        if (leftKey) await reconcileVideoPreviewExtensionGeneration(leftKey);
      } catch (error) {
        console.warn('读取延长视频进度失败', error);
      } finally {
        progress.statusPollInflight = false;
      }
    }

    function refreshRecentGenerationProgressAssets() {
      const progress = state.generationProgress;
      if (!progress) return;
      const now = Date.now();
      if (now - Number(progress.lastAssetRefreshAt || 0) < 3000) return;
      progress.lastAssetRefreshAt = now;
      Promise.allSettled([
        refreshAssets(),
        refreshUserGeneratedResults(),
      ]).then(() => {
        if (!state.generationProgress || state.generationProgress.sessionId !== progress.sessionId) return;
        renderMessages();
        renderProgress();
        renderProgressModal();
      });
    }

    function syncLocalPendingProgress() {
      const progress = state.generationProgress;
      if (!progress) return;
      const session = state.sessions.find((item) => item.id === progress.sessionId);
      const last = session?.messages?.at?.(-1);
      if (!last || last.role !== 'assistant' || !isPendingPayload(last.payload)) return;
      const elapsedSeconds = Math.max(0, Math.floor((Date.now() - progress.startedAt) / 1000));
      last.payload.pendingStatus = normalizePendingStatusProgress({
        ...(last.payload.pendingStatus || {}),
        status: 'pending',
        sessionId: progress.sessionId,
        pendingSince: last.payload.pendingStatus?.pendingSince || new Date(progress.startedAt).toISOString(),
        taskStartedAt: last.payload.pendingStatus?.taskStartedAt || new Date(progress.startedAt).toISOString(),
        elapsedSeconds,
        videoCount: progress.count,
      });
    }

    function buildGenerationProgressModel(session) {
      const progress = state.generationProgress;
      if (!progress || progress.sessionId !== session?.id) return null;
      const elapsedSeconds = Math.max(0, Math.floor((Date.now() - progress.startedAt) / 1000));
      const last = session?.messages?.at?.(-1);
      if (last?.role === 'assistant' && !isPendingPayload(last.payload) && progress.kind !== 'extension') {
        return null;
      }
      const pendingStatus = normalizePendingStatusProgress(last?.payload?.pendingStatus || {});
      const backendProgress = progress.backendProgress || pendingStatus.generationProgress || null;
      const backendCount = Number(backendProgress?.totalRequested || 0) || 0;
      const pendingCount = Number(pendingStatus.videoCount || 0) || 0;
      const expectedCount = Math.max(backendCount, pendingCount, progress.count);
      const recentProgress = buildPendingProgressFromRecentResults({
        videoCount: expectedCount || progress.count,
        elapsedSeconds,
        pendingSince: new Date(progress.startedAt).toISOString(),
        taskStartedAt: new Date(progress.startedAt).toISOString(),
        generationProgress: backendProgress,
      });
      const hasRecentGenerated = recentProgress.doneCount > 0;
      const hasBackendProgress = !!(backendProgress?.items?.length);
      const videos = hasRecentGenerated || hasBackendProgress
        ? recentProgress.videos
        : Array.from({ length: progress.count }, (_, index) => buildGenerationVideoTile(index, elapsedSeconds));
      return {
        title: '当前进度',
        sessionId: session.id,
        cancelable: true,
        isActive: isBackendGenerationProgressActive(backendProgress) || (!hasBackendProgress && !hasRecentGenerated),
        summary: hasBackendProgress
          ? buildBackendProgressSummary(backendProgress, elapsedSeconds)
          : hasRecentGenerated
          ? `生成进行中，已检测到 ${recentProgress.doneCount}/${recentProgress.expectedCount} 条成片`
          : '',
        requestText: progress.text || '',
        cards: hasRecentGenerated ? recentProgress.cards : [],
        pendingCount: hasRecentGenerated ? recentProgress.pendingCount : 0,
        videos: hasRecentGenerated ? [] : videos,
        details: hasRecentGenerated ? recentProgress.details : [],
      };
    }

    function buildGenerationVideoTile(index, elapsedSeconds = 0) {
      return {
        title: `视频 ${index + 1}`,
        stage: elapsedSeconds > 0 ? '正在生成视频方案' : '准备开始',
        percent: 0,
        pending: true,
      };
    }

    function isTerminalProgressStatus(status) {
      return ['succeeded', 'failed', 'skipped', 'cancelled', 'canceled', 'deleted'].includes(
        String(status || '').trim().toLowerCase()
      );
    }

    function isPostProcessingProgressStatus(status) {
      return ['archiving', 'postprocessing', 'post_processing'].includes(
        String(status || '').trim().toLowerCase()
      );
    }

    function hasUsableProgressAssetRecord(record) {
      if (!record || typeof record !== 'object') return false;
      const archiveStatus = String(record.archiveStatus || record.archive?.status || '').trim().toLowerCase();
      const archiveKey = String(record.archiveKey || record.local_video_path || record.archiveLocalPath || '').trim();
      return ['archived', 'stored'].includes(archiveStatus) && !!archiveKey;
    }

    function isPostProcessingProgressItem(item) {
      const status = String(item?.status || '').trim().toLowerCase();
      if (isPostProcessingProgressStatus(status)) return true;
      if (status !== 'succeeded') return false;
      const providerStatus = String(item?.providerStatus || '').trim().toLowerCase();
      const label = String(item?.statusLabel || '').trim();
      if (providerStatus === 'deleted' || label.includes('删除')) return false;
      const hasVideo = !!String(item?.videoUrl || '').trim();
      const hasAsset = item?.hasLocalAsset === true || hasUsableProgressAssetRecord(item?.assetRecord);
      return hasVideo && !hasAsset && item?.hasLocalAsset !== false;
    }

    function isTerminalProgressStage(stage) {
      return ['已生成', '生成失败', '已取消', '已强行终止'].includes(String(stage || '').trim());
    }

    function buildPendingProgressFromRecentResults(pending = {}) {
      pending = normalizePendingStatusProgress(pending);
      const backendProgress = pending.generationProgress || null;
      const backendItems = Array.isArray(backendProgress?.items) ? backendProgress.items : [];
      const statelessTerminal = !!(pending.statelessProgress && backendProgress && !isBackendGenerationProgressActive(backendProgress));
      const expectedCount = Math.max(
        Number(backendProgress?.totalRequested || 0) || 0,
        statelessTerminal ? 0 : Number(pending.videoCount || 0) || 0,
        statelessTerminal ? 0 : Number(state.generationProgress?.count || 0) || 0,
        backendItems.length
      );
      const boundedExpected = backendItems.length
        ? Math.max(1, expectedCount || backendItems.length)
        : Math.max(1, Math.min(5, expectedCount || 2));
      const backendJobIds = new Set(backendItems.map((item) => String(item.jobId || '').trim()).filter(Boolean));
      const preSubmitPlanning = isPreSubmitPlanningProgress(backendProgress);
      const recentItems = backendJobIds.size
        ? dedupeAssets(sortAssetsNewest(state.userGeneratedResults || []))
          .filter((item) => {
            const jobId = String(item?.jobId || '').trim();
            return jobId && backendJobIds.has(jobId);
          })
          .slice(0, boundedExpected)
        : [];
      const recentByJobId = new Map(recentItems.map((item) => [String(item.jobId || '').trim(), item]));
      const recentByVideo = new Map(recentItems.map((item) => [Number(item.videoIndex || 0), item]));
      const videos = backendItems.length
        ? backendItems.map((item, index) => {
            const jobId = String(item.jobId || '').trim();
            const asset = (jobId && recentByJobId.get(jobId)) || recentByVideo.get(Number(item.videoIndex || 0));
            const progressTitle = `视频 ${Number(item.videoIndex || 0) || index + 1}`;
            if (asset) {
              const stage = resolveBatchStageLabel(asset);
              return {
                title: progressTitle,
                stage,
                percent: isTerminalProgressStage(stage) ? 100 : 0,
                pending: !isTerminalProgressStage(stage),
              };
            }
            const status = String(item.status || '').trim();
            return {
              title: progressTitle,
              stage: formatGenerationProgressStatus(item, { preSubmitPlanning }),
              percent: generationProgressPercent(item, { preSubmitPlanning }),
              pending: !isTerminalProgressStatus(status),
            };
          })
        : recentItems.map((item) => {
            const stage = resolveBatchStageLabel(item);
            return {
              title: item.videoTitle || item.title || '已生成',
              stage,
              percent: isTerminalProgressStage(stage) ? 100 : 0,
              pending: !isTerminalProgressStage(stage),
            };
          });
      while (!statelessTerminal && videos.length < boundedExpected) {
        videos.push(buildGenerationVideoTile(videos.length, Number(pending.elapsedSeconds || 0) || 0));
      }
      const progressCards = backendItems.length
        ? backendItems.map((item, index) => {
            const jobId = String(item.jobId || '').trim();
            const asset = (jobId && recentByJobId.get(jobId)) || recentByVideo.get(Number(item.videoIndex || 0));
            if (asset) return asset;
            return buildProgressStatusResultItem(item, index);
          })
        : recentItems;
      const doneCount = videos.filter((item) => item.stage === '已生成').length;
      const failedCount = videos.filter((item) => item.stage === '生成失败').length;
      const deletedCount = videos.filter((item) => String(item.stage || '').includes('删除')).length;
      const runningCount = videos.filter((item) => item.stage === '生成中').length;
      const planningCount = videos.filter((item) => item.stage === '正在生成视频方案').length;
      const waitingCount = videos.filter((item) => item.stage === '待生成').length;
      const terminalCount = doneCount + failedCount + deletedCount;
      const isTerminal = boundedExpected > 0 && terminalCount >= boundedExpected && runningCount === 0 && waitingCount === 0 && planningCount === 0;
      const latestNames = recentItems
        .map((item) => cleanDisplayText(item.videoTitle || item.title || item.fileName || item.jobId || ''))
        .filter(Boolean)
        .slice(0, 3);
      const detailTitle = isTerminal
        ? '本轮已结束'
        : doneCount > 0
          ? '后台已有成片写入'
          : '';
      const detailBody = isTerminal
        ? `本轮已结束：已生成 ${doneCount}/${boundedExpected}，失败 ${failedCount} 条，已删除 ${deletedCount} 条。${latestNames.length ? `最近结果：${latestNames.join('；')}` : ''}`
        : doneCount > 0
          ? `已检测到 ${doneCount} 条最近成片，后台仍有 ${runningCount + waitingCount + planningCount} 条未终态。${latestNames.length ? `最近结果：${latestNames.join('；')}` : ''}`
          : '';
      const details = detailBody ? [{
        title: detailTitle,
        body: detailBody,
      }] : [];
      return {
        expectedCount: boundedExpected,
        doneCount,
        failedCount,
        runningCount,
        waitingCount,
        isTerminal,
        cards: progressCards,
        pendingCount: backendItems.length ? 0 : Math.max(0, boundedExpected - recentItems.length),
        videos,
        details,
      };
    }

    function buildBackendProgressSummary(progress, elapsedSeconds = 0) {
      const total = Number(progress?.totalRequested || 0) || 0;
      const submitted = Number(progress?.submittedCount || 0) || 0;
      const running = Number(progress?.runningCount || 0) || 0;
      const succeeded = Number(progress?.succeededCount || 0) || 0;
      const failed = Number(progress?.failedCount || 0) || 0;
      const waiting = Number(progress?.waitingCount || 0) || 0;
      const skipped = Number(progress?.skippedCount || 0) || 0;
      const deleted = Number(progress?.deletedCount || 0) || 0;
      if (String(progress?.status || '').trim() === 'planning') {
        const summary = String(progress?.summary || '').trim() || '正在分析文档并规划剧本';
        return `${friendlyPlanningSummary(summary)}（${elapsedSeconds}s）`;
      }
      if (isPreSubmitPlanningProgress(progress)) {
        return `正在生成视频方案：已生成 ${total} 条标题，正在完善每条视频脚本，完成后会自动进入首帧图和视频生成（${elapsedSeconds}s）`;
      }
      if (!isBackendGenerationProgressActive(progress) && total > 0) {
        const skippedLabel = String(progress?.status || '').trim() === 'cancelled' ? '取消' : '未提交';
        const failedTotal = failed + (skippedLabel === '未提交' ? skipped : 0);
        const skippedPart = skippedLabel === '取消' ? `，取消 ${skipped}` : '';
        return `本轮已结束：已生成 ${succeeded}/${total}，失败 ${failedTotal}${skippedPart}，已删除 ${deleted}`;
      }
      return `后台真实进度：已提交视频 ${submitted}/${total}，生成中 ${running}，已完成 ${succeeded}，失败 ${failed}，已删除 ${deleted}，方案生成中 ${waiting}（${elapsedSeconds}s）`;
    }

    function friendlyPlanningSummary(summary) {
      const text = String(summary || '').trim();
      const labels = {
        '正在理解全文关键词': '正在读取剧本重点',
        '关键词理解完成，准备规划视频': '已读懂重点，正在规划批量视频',
        '正在智能规划视频': '正在规划多条独立视频',
        '视频规划完成，正在整理提示词': '已规划多条视频，正在写每条视频方案',
        '正在整理视频提示词': '正在生成每条视频方案',
        '正在逐条审校视频提示词': '正在完善每条视频脚本',
        '视频提示词已规划，准备提交生成': '视频方案已完成，正在进入生成',
        '正在分析文档并规划剧本': '正在生成视频方案：把剧本拆成可生成的视频脚本',
      };
      return labels[text] || text || '正在生成视频方案';
    }

    function isPreSubmitPlanningProgress(progress) {
      const items = Array.isArray(progress?.items) ? progress.items : [];
      if (!items.length) return false;
      const hasJob = items.some((item) => String(item?.jobId || '').trim());
      if (hasJob) return false;
      const submitted = Number(progress?.submittedCount || 0) || 0;
      const running = Number(progress?.runningCount || 0) || 0;
      const succeeded = Number(progress?.succeededCount || 0) || 0;
      const failed = Number(progress?.failedCount || 0) || 0;
      if (submitted || running || succeeded || failed) return false;
      return items.every((item) => String(item?.status || '').trim() === 'pending_submission');
    }

	    function formatGenerationProgressStatus(item, options = {}) {
      const status = String(item?.status || '').trim();
	      const label = String(item?.statusLabel || '').trim();
      if (options.preSubmitPlanning && status === 'pending_submission') {
        return '正在生成视频方案';
      }
      if (isPostProcessingProgressItem(item)) {
        return '后台处理中';
      }
      if (label) {
        const friendlyLabels = {
          '准备提交': '正在生成视频方案',
          '剧本规划中': '正在生成视频方案',
          '图生图处理中': '正在生成首帧图',
          '首帧图接口已请求，等待结果': '正在等待首帧图返回',
          '首帧图已生成，待提交视频': '正在提交视频生成',
          '等待真实接口返回': '等待生成服务返回',
        };
        return friendlyLabels[label] || label;
      }
      const labels = {
	        pending_submission: '正在生成视频方案',
	        preparing_first_frame: '正在生成首帧图',
	        submitting: '正在提交视频生成',
	        submitted: '已提交，等待生成',
	        polling: '等待视频生成结果',
	        archiving: '后台处理中',
        succeeded: '已归档',
        failed: '生成失败',
        skipped: '生成失败',
        cancelled: '已强行终止',
        canceled: '已强行终止',
        deleted: '已生成，文件已删除',
      };
      return labels[status] || '等待生成服务返回';
    }

    function stageWeightedProviderProgress(value, start, end) {
      const providerProgress = Number(value);
      if (!Number.isFinite(providerProgress) || providerProgress <= 0) return start;
      const bounded = Math.max(0, Math.min(100, providerProgress));
      return Math.round(start + ((end - start) * bounded / 100));
    }

    function generationProgressPercent(item, options = {}) {
      const status = String(item?.status || '').trim();
      if (isPostProcessingProgressItem(item)) {
        const providerProgress = Number(item?.providerProgress);
        if (Number.isFinite(providerProgress) && providerProgress > 0) {
          return Math.max(82, Math.min(99, stageWeightedProviderProgress(providerProgress, 82, 96)));
        }
        return 99;
      }
      if (status === 'succeeded' || status === 'failed' || status === 'skipped' || status === 'cancelled' || status === 'canceled' || status === 'deleted') {
        return 100;
      }
      if (options.preSubmitPlanning && status === 'pending_submission') {
        return stageWeightedProviderProgress(item?.providerProgress, 5, 28);
      }
      const providerProgress = Number(item?.providerProgress);
      if (status === 'planning') {
        return stageWeightedProviderProgress(providerProgress, 5, 30);
      }
      if (status === 'preparing_first_frame') {
        return stageWeightedProviderProgress(providerProgress, 30, 38);
      }
      if (status === 'submitting') {
        return stageWeightedProviderProgress(providerProgress, 38, 44);
      }
      if (status === 'submitted') {
        return stageWeightedProviderProgress(providerProgress, 44, 50);
      }
      if (status === 'polling') {
        return stageWeightedProviderProgress(providerProgress, 50, 82);
      }
      if (Number.isFinite(providerProgress) && providerProgress > 0) {
        return Math.max(1, Math.min(99, Math.floor(providerProgress)));
      }
	      const values = {
	        pending_submission: 5,
        planning: 12,
	        preparing_first_frame: 32,
	        submitting: 40,
	        submitted: 46,
	        polling: 50,
        archiving: 88,
        succeeded: 100,
        failed: 100,
        skipped: 100,
        cancelled: 100,
        canceled: 100,
      };
      return values[status] || 12;
    }

    function resolvePendingStartedAtMs(pending = {}) {
      const candidates = [
        pending.taskStartedAt,
        pending.pendingSince,
        state.generationProgress?.startedAt,
      ];
      for (const candidate of candidates) {
        if (!candidate) continue;
        if (typeof candidate === 'number' && Number.isFinite(candidate)) return candidate;
        const parsed = Date.parse(String(candidate));
        if (Number.isFinite(parsed)) return parsed;
      }
      return 0;
    }

    function isAssetFromPendingWindow(item, startedAtMs) {
      if (!startedAtMs) return false;
      const timeFields = [
        item?.userGeneratedUpdatedAt,
        item?.createdAt,
        item?.archivedAt,
        item?.updatedAt,
      ];
      const itemTime = timeFields
        .map((value) => Date.parse(String(value || '')))
        .find((value) => Number.isFinite(value));
      if (!Number.isFinite(itemTime)) return false;
      return itemTime >= startedAtMs - 5000;
    }

    function inferVideoCountFromText(text) {
      const value = String(text || '');
      const match =
        value.match(/(?:生成|做|跑|来|出|要)\s*([0-9一二两三四五六七八九十]+)\s*(?:个|条|集|段|支)?/) ||
        value.match(/^\s*([0-9一二两三四五六七八九十]+)\s*(?:个|条|集|段|支)\s*[,，、\s]+/) ||
        value.match(/(?:^|[\s，。！？、；;,.!?])([0-9一二两三四五六七八九十]+)\s*(?:个|条|段|支)(?:视频|短视频)?(?:$|[\s，。！？、；;,.!?])/);
      if (!match) return 1;
      const raw = match[1];
      const parsed = parseShortChineseNumber(raw);
      return Math.max(1, Math.min(5, parsed || 1));
    }
