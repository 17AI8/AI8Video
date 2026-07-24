    function parseShortChineseNumber(raw) {
      const text = String(raw || '').trim();
      if (/^\d+$/.test(text)) return Number(text);
      const numberMap = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
      if (text === '十') return 10;
      if (text.startsWith('十')) return 10 + (numberMap[text.slice(1)] || 0);
      if (text.includes('十')) {
        const [tens, ones] = text.split('十');
        return (numberMap[tens] || 1) * 10 + (numberMap[ones] || 0);
      }
      return numberMap[text] || 0;
    }

    function buildVideoGroups(result, meta, assets) {
      const videos = result.videos || [];
      const jobs = new Map((result.jobs || []).map((item) => [String(item.video_index), item]));
      const archives = new Map((result.archives || []).map((item) => [String(item.video_index), item]));
      const payloadAssets = new Map((result.assetRecords || [])
        .filter((item) => {
          const videoKey = String(item.videoIndex || item.video_index || '').trim();
          const jobId = String(item.jobId || item.job_id || '').trim();
          const job = jobs.get(videoKey) || {};
          const expectedJobId = String(job.job_id || '').trim();
          return jobId && expectedJobId && jobId === expectedJobId;
        })
        .map((item) => [String(item.videoIndex || item.video_index || ''), item]));
      const assetByJobId = new Map((assets || []).map((item) => [String(item.jobId || ''), item]));
      const rewrittenVideoIndex = Number(meta?.rewrittenVideoIndex || 0);
      return videos.map((video) => {
        const key = String(video.index);
        const job = jobs.get(key) || {};
        const archive = archives.get(key) || {};
        const payloadAsset = payloadAssets.get(key) || {};
        const asset = assetByJobId.get(String(job.job_id || '')) || payloadAsset || {};
        const status = asset.status || job.status || '';
        const archiveStatus = asset.archiveStatus || archive.status || '';
        const generated = isGeneratedResult({ ...asset, jobStatus: status, archiveStatus });
        const failed = isFailedResult({ ...asset, jobStatus: status, archiveStatus, error: job.error || archive.error || '' });
        return {
          index: video.index,
          title: video.title || `视频 ${video.index}`,
          jobId: job.job_id || '',
          jobStatus: status,
          error: job.error || archive.error || '',
          generationStatus: generated ? 'generated' : (failed ? 'failed' : ''),
          generationReasons: Array.isArray(asset.generationReasons) && asset.generationReasons.length
            ? asset.generationReasons.join('；')
            : '',
          archiveStatus,
          archiveBackend: asset.archiveBackend || archive.backend || '',
          archiveKey: asset.archiveKey || archive.archive_key || asset.archiveLocalPath || archive.local_path || '',
          updated: rewrittenVideoIndex > 0 && Number(video.index) === rewrittenVideoIndex,
        };
      });
    }

    function summarizeResult(result, groups) {
      if (Array.isArray(groups) && groups.length) {
        const successCount = groups.filter((item) => isGeneratedResult(item)).length;
        const failedCount = groups.filter((item) => isFailedResult(item)).length;
        return {
          videoCount: groups.length,
          successCount,
          failedCount,
          passCount: successCount,
          retryCount: 0,
          rejectCount: failedCount,
          dryRun: !!result?.dryRun,
        };
      }
      const jobs = result?.jobs || [];
      const successCount = jobs.filter((item) => isGeneratedResult({ jobStatus: item.status, videoUrl: item.video_url, localVideoPath: item.local_video_path })).length;
      const failedCount = Math.max(0, (result?.videos || []).length - successCount);
      return {
        videoCount: (result?.videos || []).length,
        successCount,
        failedCount,
        passCount: successCount,
        retryCount: 0,
        rejectCount: failedCount,
        dryRun: !!result?.dryRun,
      };
    }

    function summarizeBatchReport(report) {
      const successCount = Number(report?.successCount ?? report?.passCount ?? 0);
      const failedCount = Number(report?.failedCount ?? report?.rejectCount ?? 0);
      const targetCount = Number(report?.targetGenerationCount ?? report?.targetPassCount ?? 0);
      return {
        targetGenerationCount: targetCount,
        targetPassCount: targetCount,
        seedMessages: Number(report?.seedMessages || 0),
        totalVideoAttempts: Number(report?.totalVideoAttempts || 0),
        successCount,
        failedCount,
        passCount: successCount,
        retryCount: 0,
        rejectCount: failedCount,
        retryScheduledCount: 0,
        expansionRoundCount: Number(report?.expansionRoundCount || 0),
        expandedSeedCount: Number(report?.expandedSeedCount || 0),
        topUpStrategies: Array.isArray(report?.topUpStrategies) ? report.topUpStrategies : [],
        expansionError: String(report?.expansionError || '').trim(),
        goalMet: !!report?.goalMet,
        dryRun: !!report?.dryRun,
      };
    }

    function buildProgressSummary(summary, groups, meta) {
      const archiveCount = groups.filter((item) => item.archiveStatus && item.archiveStatus !== 'disabled').length;
      const successCount = Number(summary.successCount ?? summary.passCount ?? 0);
      const failedCount = Number(summary.failedCount ?? summary.rejectCount ?? 0);
      const failedPart = failedCount ? `，${failedCount} 条生成失败` : '';
      if (meta?.operation === 'rewrite' && meta?.rewrittenVideoIndex) {
        return `已重做第 ${meta.rewrittenVideoIndex} 条视频，其他视频保持不动；当前共 ${summary.videoCount} 条，${successCount} 条已生成，${archiveCount} 条已归档${failedPart}`;
      }
      return `${summary.videoCount} 条视频已完成，${successCount} 条已生成，${archiveCount} 条已归档${failedPart}`;
    }

    function summarizeAwaiting(awaiting) {
      if (awaiting === 'video_count') return '等待补充视频数量';
      if (awaiting === 'reference_image') return '等待确认参考图';
      if (awaiting === 'content_completion') return '等待补充台词 / 文案';
      if (awaiting === 'core_keywords') return '等待确认核心主题';
      if (awaiting === 'concurrent_generation') return '等待选择生成模式';
      if (awaiting === 'smart_split_confirmation') return '等待确认智能分集';
      if (awaiting === 'batch_seed_messages') return '等待补充批量候选';
      return '信息已齐';
    }

    function buildBatchProgressSummary(summary) {
      const seedPart = summary.seedMessages > 0 ? `，初始候选 ${summary.seedMessages} 条` : '';
      const topUpPart = summary.expansionRoundCount > 0
        ? `，自动补量 ${summary.expansionRoundCount} 轮，补入 ${summary.expandedSeedCount} 条候选`
        : '';
      const successCount = Number(summary.successCount ?? summary.passCount ?? 0);
      const failedCount = Number(summary.failedCount ?? summary.rejectCount ?? 0);
      const targetCount = Number(summary.targetGenerationCount ?? summary.targetPassCount ?? 0);
      const failedPart = failedCount ? `，${failedCount} 条失败` : '';
      if (summary.goalMet) {
        return `本轮批量生产已达标；目标 ${targetCount} 条，当前已生成 ${successCount} 条${failedPart}${seedPart}${topUpPart}`;
      }
      return `本轮批量生产未达标；目标 ${targetCount} 条，当前已生成 ${successCount} 条，已尝试 ${summary.totalVideoAttempts} 条${failedPart}${seedPart}${topUpPart}`;
    }

    function formatTopUpStrategy(strategy) {
      const code = String(strategy || '').trim();
      if (!code) return '';
      if (code === 'queue_exhausted_goal_gap_top_up') return '候选耗尽后按缺口补量';
      if (code === 'initial_pool_exhausted_low_pass_rate_top_up') return '初始候选耗尽后按成功率补量';
      if (code === 'expansion_failed') return '补量扩写失败';
      if (code === 'goal_met_or_budget_exhausted') return '目标达成或预算耗尽';
      if (code === 'no_seed_messages') return '没有可扩写候选';
      return code;
    }

    function getLatestGeneratedResultPayload(session) {
      const messages = session?.messages || [];
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        const payload = message?.payload;
        if (message?.role !== 'assistant' || !payload?.result) continue;
        if (payload.meta?.operation === 'batch_run') continue;
        return payload;
      }
      return null;
    }

    function buildCurrentResultGalleryModel(session) {
      const payload = getLatestGeneratedResultPayload(session);
      return buildResultGalleryFromPayload(payload, {
        expectedCount: getActiveExpectedResultCount(),
      });
    }

    function buildResultGalleryFromPayload(payload, options = {}) {
      if (!payload?.result) {
        return {
          payload: null,
          items: [],
          groups: [],
          summary: null,
          expectedCount: Number(options.expectedCount || 0) || 0,
          emptyText: '当前没有生成结果。',
          folderTarget: null,
        };
      }
      const groups = buildVideoGroups(payload.result, payload.meta, state.assets);
      const summary = summarizeResult(payload.result, groups);
      const groupByJobId = new Map(groups.filter((item) => item.jobId).map((item) => [String(item.jobId), item]));
      const payloadAssetItems = (payload.result.assetRecords || [])
        .filter((item) => {
          const jobId = String(item.jobId || item.job_id || '').trim();
          return jobId && groupByJobId.has(jobId);
        })
        .map((item) => ({
          ...item,
          __payloadBound: true,
        }));
      const exactJobAssets = dedupeAssets(sortAssetsNewest(state.assets || []))
        .filter((item) => {
          const jobId = String(item.jobId || '').trim();
          return jobId && groupByJobId.has(jobId);
        });
      const items = dedupeAssets(sortAssetsNewest([...payloadAssetItems, ...exactJobAssets]))
        .map((item) => {
          const jobId = String(item.jobId || '').trim();
          const group = groupByJobId.get(jobId) || {};
          const { __payloadBound: _payloadBound, ...cleanItem } = item;
          return {
            ...cleanItem,
            videoIndex: group.index || item.videoIndex,
            videoTitle: group.title || item.videoTitle || `第 ${item.videoIndex || '-'} 条`,
            generationStatus: item.generationStatus || group.generationStatus || '',
            generationReasons: item.generationReasons || group.generationReasons || '',
            archiveStatus: item.archiveStatus || group.archiveStatus || '',
            archiveBackend: item.archiveBackend || group.archiveBackend || '',
            archiveKey: item.archiveKey || group.archiveKey || '',
          };
        })
        .sort((left, right) => Number(left.videoIndex || 0) - Number(right.videoIndex || 0));
      const folderItem = items.find((item) => item.archiveBackend === 'local' && (item.archiveKey || item.archiveLocalPath)) || null;
      return {
        payload,
        items,
        groups,
        summary,
        expectedCount: Number(options.expectedCount || 0) || groups.length || Number(payload.pendingStatus?.videoCount || 0) || items.length,
        emptyText: '当前没有生成结果。',
        folderTarget: folderItem ? {
          archiveKey: folderItem.archiveKey || '',
          localPath: buildMediaFsPath(folderItem.archiveBackend, folderItem.archiveKey, folderItem.archiveLocalPath),
        } : null,
      };
    }

    function buildCurrentResultProgressSummary(gallery, meta) {
      if (!gallery?.summary) return '';
      const summary = gallery.summary;
      const expectedCount = gallery.expectedCount || summary.videoCount || 0;
      const returnedCount = getPlayableResultItems(gallery).length;
      const archiveCount = (gallery.groups || []).filter((item) => item.archiveStatus && item.archiveStatus !== 'disabled').length;
      const successCount = Number(summary.successCount ?? summary.passCount ?? 0);
      const failedCount = Number(summary.failedCount ?? summary.rejectCount ?? 0);
      const failedPart = failedCount ? `，${failedCount} 条生成失败` : '';
      const prefix = meta?.operation === 'rewrite' && meta?.rewrittenVideoIndex
        ? `已重做第 ${meta.rewrittenVideoIndex} 条视频；`
        : '';
      return `${prefix}当前任务已返回 ${returnedCount}/${expectedCount} 条，${successCount} 条已生成，${archiveCount} 条已归档${failedPart}`;
    }

    function buildResultFolderGalleryModel(session) {
      const currentGallery = buildCurrentResultGalleryModel(session);
      const items = dedupeAssets(sortAssetsNewest(state.userGeneratedResults || []));
      return {
        ...currentGallery,
        items,
        expectedCount: items.length,
        source: 'folder',
        emptyText: '当前视频文件夹里还没有可预览结果。',
        folderTarget: null,
      };
    }

    function isGeneratedResult(item) {
      const jobStatus = String(item?.jobStatus || item?.status || '').trim().toLowerCase();
      const archiveStatus = String(item?.archiveStatus || '').trim().toLowerCase();
      return Boolean(
        item?.generationStatus === 'generated'
        || jobStatus === 'succeeded'
        || jobStatus === 'completed'
        || item?.videoUrl
        || item?.storageKey
        || item?.localVideoPath
        || item?.userGeneratedLocalPath
        || (
          (archiveStatus === 'archived' || archiveStatus === 'stored')
          && (item?.archiveKey || item?.archiveLocalPath)
        )
      );
    }

    function isFailedResult(item) {
      const jobStatus = String(item?.jobStatus || item?.status || '').trim().toLowerCase();
      const archiveStatus = String(item?.archiveStatus || '').trim().toLowerCase();
      return Boolean(
        item?.generationStatus === 'failed'
        || ['failed', 'error', 'cancelled'].includes(jobStatus)
        || archiveStatus === 'failed'
        || archiveStatus === 'error'
        || item?.error
      );
    }

    function resolveBatchStageLabel(item) {
      if (isFailedResult(item)) {
        return '生成失败';
      }
      if (isGeneratedResult(item)) {
        return '已生成';
      }
      const jobStatus = String(item?.jobStatus || item?.status || '').trim().toLowerCase();
      if (
        ['queued', 'pending', 'running', 'processing', 'submitted', 'polling', 'archiving'].includes(jobStatus)
      ) {
        return '生成中';
      }
      return '待生成';
    }

    function buildBatchStageCards(groups, expectedCount = 0) {
      const cards = (groups || []).map((item, index) => {
        const stage = resolveBatchStageLabel(item);
        const rawReason = getGenerationFailureRawReason(item);
        return {
          title: `视频 ${item.index || index + 1}`,
          stage,
          error: rawReason,
          generationReasons: item?.generationReasons || '',
          statusLabel: item?.statusLabel || '',
          jobStatus: item?.jobStatus || item?.status || '',
          percent: stage === '已生成' ? 100 : 0,
          pending: stage === '生成中' || stage === '待生成',
        };
      });
      const total = Math.max(cards.length, Number(expectedCount || 0));
      while (cards.length < total) {
        cards.push({
          title: `视频 ${cards.length + 1}`,
          stage: '待生成',
          percent: 0,
          pending: true,
        });
      }
      return cards;
    }

    function buildProgressResultCards(playableItems = [], stageCards = []) {
      const used = new Set();
      const byVideo = new Map();
      (playableItems || []).forEach((item, index) => {
        const video = Number(item?.videoIndex || 0);
        if (video > 0 && !byVideo.has(video)) {
          byVideo.set(video, { item, index });
        }
      });
      return (stageCards || []).map((card, index) => {
        const video = index + 1;
        const matched = byVideo.get(video);
        if (matched && !used.has(matched.index)) {
          used.add(matched.index);
          return matched.item;
        }
        const stage = String(card?.stage || '').trim();
        const status = stage === '生成失败'
          ? 'failed'
          : stage === '已生成'
            ? 'succeeded'
            : stage === '待生成'
              ? 'pending_submission'
              : 'polling';
        return {
          __progressStatus: true,
          title: cleanDisplayText(card?.title, `视频 ${video}`),
          stage: stage || '生成中',
          status,
          error: card?.error || card?.generationReasons || '',
          generationReasons: card?.generationReasons || '',
          statusLabel: card?.statusLabel || '',
          jobStatus: card?.jobStatus || '',
          percent: Number(card?.percent || 0) || (status === 'succeeded' || status === 'failed' ? 100 : 0),
          pending: status === 'pending_submission' || status === 'polling',
        };
      });
    }

    function summarizeBatchStageCards(cards) {
      const doneCount = cards.filter((item) => item.stage === '已生成').length;
      const runningCount = cards.filter((item) => item.stage === '生成中').length;
      const waitingCount = cards.filter((item) => item.stage === '待生成').length;
      const failedCount = cards.filter((item) => item.stage === '生成失败').length;
      return {
        doneCount,
        runningCount,
        waitingCount,
        failedCount,
        text: `最近一批 ${cards.length} 条里，${doneCount} 条已生成，${runningCount} 条生成中，${waitingCount} 条待生成，${failedCount} 条生成失败`,
      };
    }

    function buildAssetProgressSummary(items, meta) {
      const gallery = buildAssetGalleryModel(items || []);
      const visibleItems = gallery.items || [];
      if (!visibleItems.length) return '';
      const successCount = visibleItems.filter((item) => isGeneratedResult(item)).length;
      const failedCount = visibleItems.filter((item) => isFailedResult(item)).length;
      const archiveCount = visibleItems.filter((item) => item.archiveStatus === 'archived').length;
      const failedPart = failedCount ? `，${failedCount} 条生成失败` : '';
      if (meta?.operation === 'rewrite' && meta?.rewrittenVideoIndex) {
        return `已重做第 ${meta.rewrittenVideoIndex} 条视频；当前展示 ${visibleItems.length} 条结果里，${successCount} 条已生成，${archiveCount} 条已归档${failedPart}`;
      }
      return `当前展示 ${visibleItems.length} 条结果里，${successCount} 条已生成，${archiveCount} 条已归档${failedPart}`;
    }

    function renderAssets() {
      const gallery = buildAssetGalleryModel(state.assets);
      const generatedCount = gallery.items.filter((item) => isGeneratedResult(item)).length;
      els.metrics.innerHTML = `
        <div class="metric"><div class="num">${gallery.items.length}</div><div class="label">${escapeHtml(gallery.metricLabel)}</div></div>
        <div class="metric"><div class="num">${generatedCount}</div><div class="label">当前展示已生成</div></div>
      `;
      els.assetList.innerHTML = '';
      if (!gallery.items.length) {
        els.assetList.innerHTML = `<div class="empty">${escapeHtml(gallery.emptyText)}</div>`;
        return;
      }
      gallery.items.forEach((item) => {
        const card = document.createElement('div');
        card.className = 'asset-card';
        card.innerHTML = buildAssetCardMarkup(item);
        els.assetList.appendChild(card);
      });
    }

    function buildAssetCardMarkup(item) {
      const videoSrc = resolvePlayableVideoSrc(item);
      const coverSrc = resolvePlayableCoverSrc(item);
      const localPath = buildMediaFsPath(item.archiveBackend, item.archiveKey, item.archiveLocalPath);
      const remoteArchiveUrl = resolveRemoteArchiveUrl(item.archiveUrl);
      const remoteArchiveLocation = String(item.archiveUrl || item.archiveKey || '').trim();
      const archiveName = extractFileName(localPath || remoteArchiveUrl || item.archiveKey || item.storageKey || '');
      const archiveState = item.archiveStatus === 'archived'
        ? { label: '已归档', className: 'archived' }
        : item.archiveStatus === 'simulated'
          ? { label: '演示', className: 'simulated' }
          : isFailedResult(item)
            ? { label: '生成失败', className: 'bad' }
            : isGeneratedResult(item)
              ? { label: '已生成', className: 'archived' }
              : { label: '待处理', className: 'pending' };
      const motionOverlay = htmlMotionOverlayDisplay(item);
      const links = [
        videoSrc ? `<a href="${escapeHtml(videoSrc)}" target="_blank" rel="noreferrer">打开视频</a>` : '',
        coverSrc ? `<a href="${escapeHtml(coverSrc)}" target="_blank" rel="noreferrer">打开封面</a>` : '',
        item.archiveStatus === 'archived' && item.archiveBackend === 'local'
          ? `<button type="button" class="asset-link-button" data-open-folder="${escapeHtml(item.archiveKey || '')}" data-local-path="${escapeHtml(localPath || '')}">打开文件夹</button>`
          : '',
        item.archiveStatus === 'archived' && item.archiveBackend !== 'local' && remoteArchiveUrl
          ? `<a href="${escapeHtml(remoteArchiveUrl)}" target="_blank" rel="noreferrer">打开OSS</a>`
          : '',
      ].filter(Boolean).join('');
      return `
        <div class="asset-card-head">
          <h4>第 ${item.videoIndex} 条 · ${escapeHtml(item.videoTitle || '短视频')}</h4>
          <div class="asset-chip-stack">
            <span class="asset-chip ${archiveState.className}">${archiveState.label}</span>
          </div>
        </div>
        ${videoSrc ? `<video class="asset-preview" controls preload="none" ${coverSrc ? `poster="${escapeHtml(coverSrc)}"` : ''} src="${escapeHtml(videoSrc)}"></video>` : ''}
        ${links ? `<div class="asset-links">${links}</div>` : ''}
        <div class="asset-meta">
          <span>${escapeHtml(formatAssetTime(item.createdAt))}</span>
          <span>${escapeHtml(isFailedResult(item) ? '生成失败' : isGeneratedResult(item) ? '已生成' : '待生成')}</span>
        </div>
        ${archiveName ? `<div class="asset-note">文件：${escapeHtml(archiveName)}</div>` : ''}
        ${item.archiveStatus === 'archived' && item.archiveBackend === 'local' ? '<div class="asset-note">已存入用户生成结果文件夹</div>' : ''}
        ${motionOverlay.label ? `<div class="asset-note">HTML 动效：${escapeHtml(motionOverlay.label)}${motionOverlay.reason ? `（${escapeHtml(motionOverlay.reason)}）` : ''}</div>` : ''}
        ${item.archiveStatus === 'archived' && item.archiveBackend !== 'local' && remoteArchiveUrl ? `<div class="asset-note">远端归档：${escapeHtml(item.archiveBackend || 'OSS')}</div>` : ''}
        ${item.archiveStatus === 'archived' && item.archiveBackend !== 'local' && !remoteArchiveUrl && remoteArchiveLocation ? `<div class="asset-note">归档地址：${escapeHtml(remoteArchiveLocation)}</div>` : ''}
      `;
    }

    function htmlMotionOverlayDisplay(item) {
      const overlay = item?.htmlMotionOverlay || item?.archiveMeta?.htmlMotionOverlay || item?.assetRecord?.htmlMotionOverlay || null;
      const status = String(overlay?.status || '').trim().toLowerCase();
      if (status === 'applied') return { label: '已叠加', reason: '' };
      if (status === 'degraded') return { label: '已降级，基础视频已保留', reason: String(overlay?.reason || '').trim() };
      if (status === 'skipped') return { label: '未开启', reason: '' };
      return { label: '', reason: '' };
    }
