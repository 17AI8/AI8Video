    function buildBatchReportCardMarkup(item) {
      const passed = Number(item.successCount ?? item.passCount ?? 0);
      const target = Number(item.targetGenerationCount ?? item.targetPassCount ?? 0);
      const attempts = Number(item.totalVideoAttempts || 0);
      const failed = Number(item.failedCount ?? item.rejectCount ?? 0);
      const expansionRounds = Number(item.expansionRoundCount || 0);
      const expandedSeeds = Number(item.expandedSeedCount || 0);
      const badge = item.goalMet
        ? { label: '达标', className: 'ok' }
        : { label: '未达标', className: 'warn' };
      const metaLine = [
        item.reportSource ? `来源：${item.reportSource}` : '',
        item.styleHint ? `风格：${item.styleHint}` : '',
        item.dryRun ? '模式：dry-run' : '模式：real',
      ].filter(Boolean).join(' · ');
      const topUpMeta = expansionRounds > 0
        ? `自动补量 ${expansionRounds} 轮，共补入 ${expandedSeeds} 条候选`
        : '';
      const failureSummary = Array.isArray(item.topFailureReasons) && item.topFailureReasons.length
        ? `主要问题：${item.topFailureReasons.slice(0, 2).map((entry) => `${entry.reason} × ${entry.count}`).join('；')}`
        : '';
      return `
        <div class="report-card">
          <div class="report-card-head">
            <div>
              <div class="report-card-title">${escapeHtml(`生成 ${passed}/${target || passed}`)}</div>
              <div class="report-card-sub">${escapeHtml(formatReportTime(item.generatedAt || item.reportSavedAt))}</div>
            </div>
            <span class="report-badge ${badge.className}">${badge.label}</span>
          </div>
          <div class="report-stats">
            <div class="report-stat"><strong>${escapeHtml(String(attempts))}</strong><span>尝试</span></div>
            <div class="report-stat"><strong>${escapeHtml(String(failed))}</strong><span>失败</span></div>
            <div class="report-stat"><strong>${escapeHtml(String(item.seedMessages || 0))}</strong><span>候选</span></div>
            ${expansionRounds > 0 ? `<div class="report-stat"><strong>${escapeHtml(String(expandedSeeds))}</strong><span>自动补量</span></div>` : ''}
          </div>
          ${metaLine ? `<div class="report-meta">${escapeHtml(metaLine)}</div>` : ''}
          ${topUpMeta ? `<div class="report-meta">${escapeHtml(topUpMeta)}</div>` : ''}
          <div class="report-actions">
            <button type="button" class="report-link-button" data-open-report="${escapeHtml(item.reportPath || '')}">打开日报</button>
            ${failureSummary ? `<span class="report-meta">${escapeHtml(failureSummary)}</span>` : ''}
          </div>
        </div>
      `;
    }

    function buildSupervisorCardMarkup(health) {
      const supervisorState = health.batchSupervisorState || null;
      const deployment = health.batchSupervisorDeployment || null;
      const seedFile = health.batchSeedFileStatus || null;
      const scheduleTimes = getSupervisorScheduleTimes(health);
      const nextScheduledSlot = String(health.batchNextScheduledSlot || '').trim();
      const supervisorSuggestions = getSupervisorSuggestions(health);
      const latestAlert = health.batchLatestAlert || null;
      const latestFailureReason = String(health.batchLatestFailureReason || '').trim();
      const adminResult = state.supervisorAdminResult || null;
      const adminResultSummary = summarizeSupervisorAdminResult(adminResult);
      const deploymentPath = String(adminResult?.path || deployment?.plistPath || '').trim();
      const lastRunAt = supervisorState?.lastRunAt || '';
      const lastGoalMet = supervisorState?.lastGoalMet;
      const lastStatus = String(supervisorState?.lastStatus || '').trim();
      const consecutiveLowPassRuns = Number(supervisorState?.consecutiveLowPassRuns || 0);
      const lockExists = !!health.batchSupervisorLockExists;
      let badge = { label: '待启动', className: 'warn' };
      if (lockExists) {
        badge = { label: '执行中', className: 'info' };
      } else if (lastStatus === 'error') {
        badge = { label: '异常', className: 'bad' };
      } else if (lastRunAt) {
        badge = { label: '已值守', className: lastGoalMet === false ? 'warn' : 'ok' };
      }
      const metaLines = [
        scheduleTimes.length ? `排期：${scheduleTimes.join(' / ')}` : '排期：尚未配置',
        nextScheduledSlot ? `下次执行：${formatPendingTime(nextScheduledSlot)}` : (scheduleTimes.length ? '下次执行：等待下一时段' : '下次执行：尚未配置'),
        lastRunAt ? `上次执行：${formatPendingTime(lastRunAt)}` : '上次执行：尚未开始',
        supervisorState?.lastReportId ? `最近日报：${supervisorState.lastReportId}` : '',
        `锁状态：${lockExists ? '当前有任务在跑' : '空闲'}`,
        supervisorState?.lastError ? `最近异常：${supervisorState.lastError}` : '',
      ].filter(Boolean).join(' · ');
      const latestAlertSummary = latestAlert?.message
        ? `${latestAlert.message}${latestAlert.createdAt ? ` · ${formatReportTime(latestAlert.createdAt)}` : ''}`
        : '当前没有新的异常告警';
      const deploymentSummary = formatSupervisorDeployment(deployment);
      const scheduleHint = scheduleTimes.length
        ? ''
        : '自动排期还没开启；可直接点“配置值守”补排期。';
      const deploymentHint = deployment?.platformSupported === false
        ? '当前系统不是 macOS，不能直接使用 launchd 值守。'
        : (!deployment?.exists ? '长期运行部署文件还没写入；可直接点“配置值守”生成。' : '');
      const seedFileSummary = formatSeedFileStatus(seedFile);
      const seedFileHint = seedFile?.exists
        ? `当前值守会逐行读取 ${seedFile.lineCount || 0} 条候选内容。`
        : '当前还没有值守种子文件；配置或安装值守时会优先自动补一份。';
      const suggestionLine = supervisorSuggestions.length ? `恢复建议：${supervisorSuggestions.join('；')}` : '';
      const preflightSummary = summarizePreflightReport(state.preflight.report);
      const preflightError = String(state.preflight.error || '').trim();
      const reportAction = supervisorState?.lastReportPath
        ? `<button type="button" class="report-link-button" data-open-report="${escapeHtml(supervisorState.lastReportPath)}">打开最近日报</button>`
        : '';
      const alertAction = latestAlert?.alertPath
        ? `<button type="button" class="report-link-button" data-open-alert="${escapeHtml(latestAlert.alertPath)}">打开最近告警</button>`
        : '';
      const preflightAction = `<button type="button" class="report-link-button" data-run-live-preflight="1">${state.preflight.running ? '自检中...' : '环境自检'}</button>`;
      const stateAction = '<button type="button" class="report-link-button" data-open-supervisor-state="1">打开状态文件</button>';
      const adminStateAction = '<button type="button" class="report-link-button" data-open-supervisor-admin-state="1">打开最近操作记录</button>';
      const lockAction = `<button type="button" class="report-link-button" data-open-supervisor-lock="1">${lockExists ? '打开锁文件' : '打开锁目录'}</button>`;
      const deploymentAction = deployment?.plistPath
        ? `<button type="button" class="report-link-button" data-open-supervisor-deployment="1">${deployment?.exists ? '打开部署文件' : '打开部署目录'}</button>`
        : '';
      const seedFileAction = `<button type="button" class="report-link-button" data-build-seed-file="1">${seedFile?.exists ? '重建种子文件' : '生成种子文件'}</button>`;
      const openSeedFileAction = seedFile?.path
        ? `<button type="button" class="report-link-button" data-open-seed-file="1">${seedFile?.exists ? '打开种子文件' : '打开种子目录'}</button>`
        : '';
      const writeDeploymentAction = deployment?.platformSupported === false
        ? ''
        : `<button type="button" class="report-link-button" data-write-supervisor-deployment="1">配置值守</button>`;
      const installDeploymentAction = deployment?.platformSupported === false
        ? ''
        : (deployment?.loaded
            ? `<button type="button" class="report-link-button" data-uninstall-supervisor-deployment="1">卸载值守</button>`
            : `<button type="button" class="report-link-button" data-install-supervisor-deployment="1">${deployment?.exists ? '安装值守' : '配置并安装'}</button>`);
      return `
        <div class="report-card">
          <div class="report-card-head">
            <div>
              <div class="report-card-title">主管调度</div>
              <div class="report-card-sub">${escapeHtml(lastRunAt ? formatReportTime(lastRunAt) : '按排期自动执行批量生产')}</div>
            </div>
            <span class="report-badge ${badge.className}">${badge.label}</span>
          </div>
          <div class="report-stats">
            <div class="report-stat"><strong>${escapeHtml(String(scheduleTimes.length || 0))}</strong><span>排期</span></div>
            <div class="report-stat"><strong>${escapeHtml(String(consecutiveLowPassRuns || 0))}</strong><span>低成功</span></div>
            <div class="report-stat"><strong>${escapeHtml(lastGoalMet == null ? '待跑' : (lastGoalMet ? '达标' : '未达标'))}</strong><span>最近结果</span></div>
          </div>
          <div class="report-meta">${escapeHtml(metaLines)}</div>
          ${scheduleHint ? `<div class="report-meta">${escapeHtml(scheduleHint)}</div>` : ''}
          ${seedFileSummary ? `<div class="report-meta">${escapeHtml(`种子文件：${seedFileSummary}`)}</div>` : ''}
          ${seedFileHint ? `<div class="report-meta">${escapeHtml(seedFileHint)}</div>` : ''}
          ${deploymentSummary ? `<div class="report-meta">${escapeHtml(`长期运行：${deploymentSummary}`)}</div>` : ''}
          ${deploymentHint ? `<div class="report-meta">${escapeHtml(deploymentHint)}</div>` : ''}
          ${adminResultSummary ? `<div class="report-meta full">${escapeHtml(`最近操作：${adminResultSummary}`)}</div>` : ''}
          ${deploymentPath ? `<div class="report-meta full">${escapeHtml(`部署路径：${deploymentPath}`)}</div>` : ''}
          ${latestFailureReason ? `<div class="report-meta">${escapeHtml(`最近失败原因：${latestFailureReason}`)}</div>` : ''}
          ${suggestionLine ? `<div class="report-meta">${escapeHtml(suggestionLine)}</div>` : ''}
          ${preflightSummary ? `<div class="report-meta full">${escapeHtml(`环境自检：${preflightSummary}`)}</div>` : ''}
          ${preflightError ? `<div class="report-meta full">${escapeHtml(`自检失败：${preflightError}`)}</div>` : ''}
          <div class="report-meta">${escapeHtml(`最近告警：${latestAlertSummary}`)}</div>
          <div class="report-actions">${reportAction}${alertAction}${preflightAction}${stateAction}${adminStateAction}${lockAction}${seedFileAction}${openSeedFileAction}${writeDeploymentAction}${installDeploymentAction}${deploymentAction}</div>
        </div>
      `;
    }

    function getSupervisorScheduleTimes(health) {
      const healthSchedules = Array.isArray(health?.batchScheduleTimes) ? health.batchScheduleTimes.filter(Boolean) : [];
      if (healthSchedules.length) return healthSchedules;
      const deployment = health?.batchSupervisorDeployment || null;
      return Array.isArray(deployment?.scheduleTimes) ? deployment.scheduleTimes.filter(Boolean) : [];
    }

    function getSupervisorSuggestions(health) {
      const suggestions = Array.isArray(health?.batchSupervisorSuggestions) ? health.batchSupervisorSuggestions : [];
      return suggestions
        .map((item) => String(item || '').trim().replace(/[；。]+$/g, ''))
        .filter(Boolean);
    }

    function summarizePreflightReport(report) {
      const checks = report && typeof report === 'object' ? report.checks : null;
      if (!checks || typeof checks !== 'object') return '';
      const labels = {
        llm: '对话',
        video_model: '视频模型',
        archive_config: '归档',
        archive_probe: '探针',
      };
      const statuses = {
        ok: '通过',
        skipped: '跳过',
        error: '异常',
      };
      const parts = Object.entries(labels)
        .filter(([key]) => checks[key])
        .map(([key, label]) => `${label}${statuses[checks[key].status] || checks[key].status || '未知'}`);
      if (!parts.length) return '';
      const stamp = report.timestamp ? formatReportTime(report.timestamp) : '';
      return [stamp, ...parts].filter(Boolean).join(' · ');
    }

    function formatSupervisorDeployment(deployment) {
      if (!deployment) return '';
      if (deployment.platformSupported === false) return '当前系统不支持 launchd';
      if (deployment.loaded) return 'launchd 已加载';
      if (deployment.exists) return 'launchd 已写入，待加载';
      return 'launchd 未写入';
    }

    function formatSeedFileStatus(seedFile) {
      if (!seedFile) return '';
      const sourceLabel = seedFile.source === 'config' ? '自定义路径' : '默认路径';
      if (seedFile.exists) return `${sourceLabel}已就绪，${Number(seedFile.lineCount || 0)} 条候选`;
      return `${sourceLabel}未生成`;
    }

    function loadSupervisorAdminResult() {
      try {
        const raw = localStorage.getItem(SUPERVISOR_ACTION_STORAGE_KEY);
        const parsed = raw ? JSON.parse(raw) : null;
        return parsed && typeof parsed === 'object' ? parsed : null;
      } catch {
        return null;
      }
    }

    function persistSupervisorAdminResult(payload) {
      state.supervisorAdminResult = payload && typeof payload === 'object' ? payload : null;
      if (!state.supervisorAdminResult) {
        localStorage.removeItem(SUPERVISOR_ACTION_STORAGE_KEY);
        return;
      }
      localStorage.setItem(SUPERVISOR_ACTION_STORAGE_KEY, JSON.stringify(state.supervisorAdminResult));
    }

    function buildSupervisorAdminResult(apiResult, action) {
      const deployment = apiResult?.deployment || {};
      return {
        action: String(action || apiResult?.action || '').trim() || 'write',
        savedAt: new Date().toISOString(),
        path: String(apiResult?.path || deployment?.plistPath || '').trim(),
        seedFile: String(apiResult?.seedFile || '').trim(),
        loaded: !!deployment?.loaded,
        exists: !!deployment?.exists,
        keepPlist: apiResult?.keepPlist === true,
      };
    }

    function formatSupervisorAdminAction(action) {
      if (action === 'install') return '已安装值守';
      if (action === 'uninstall') return '已卸载值守';
      return '已写入配置';
    }

    function summarizeSupervisorAdminResult(result) {
      if (!result) return '';
      const parts = [];
      if (result.savedAt) {
        parts.push(formatReportTime(result.savedAt));
      }
      parts.push(formatSupervisorAdminAction(result.action));
      if (result.action === 'uninstall' && result.keepPlist) {
        parts.push('保留部署文件');
      } else if (result.loaded) {
        parts.push('launchd 已加载');
      } else if (result.exists) {
        parts.push('部署文件已写入');
      }
      return parts.filter(Boolean).join(' · ');
    }

    function detectSupervisorDraftSource(health, draft) {
      const draftKeys = [
        'scheduleTimes',
        'targetPassCount',
        'styleHint',
        'pollSeconds',
        'minPassRate',
        'consecutiveLowPassRuns',
      ];
      if (draftKeys.some((key) => Object.prototype.hasOwnProperty.call(draft || {}, key))) {
        return '本机暂存草稿';
      }
      const deployment = health?.batchSupervisorDeployment || {};
      if (
        Array.isArray(deployment.scheduleTimes) && deployment.scheduleTimes.length
        || deployment.targetPassCount
        || deployment.styleHint
        || deployment.pollSeconds
        || deployment.minPassRate != null
        || deployment.consecutiveLowPassRuns
        || deployment.exists
      ) {
        return '当前部署配置';
      }
      return '运行默认值';
    }

    function buildBatchAlertCardMarkup(item) {
      const badge = buildAlertBadge(item);
      const passRateText = formatAlertPassRate(item.passRate);
      const metaLine = [
        item.reportId ? `日报：${item.reportId}` : '',
        passRateText ? `成功率：${passRateText}` : '',
        item.consecutiveLowPassRuns ? `连续低成功：${item.consecutiveLowPassRuns}` : '',
      ].filter(Boolean).join(' · ');
      const reportAction = item.reportPath
        ? `<button type="button" class="report-link-button" data-open-report="${escapeHtml(item.reportPath || '')}">打开日报</button>`
        : '';
      return `
        <div class="report-card">
          <div class="report-card-head">
            <div>
              <div class="report-card-title">${escapeHtml(item.message || describeAlertKind(item.kind))}</div>
              <div class="report-card-sub">${escapeHtml(formatReportTime(item.createdAt || item.alertSavedAt))}</div>
            </div>
            <span class="report-badge ${badge.className}">${badge.label}</span>
          </div>
          ${metaLine ? `<div class="report-meta">${escapeHtml(metaLine)}</div>` : ''}
          <div class="report-actions">
            <button type="button" class="report-link-button" data-open-alert="${escapeHtml(item.alertPath || '')}">打开告警</button>
            ${reportAction}
          </div>
        </div>
      `;
    }

    function buildAlertBadge(item) {
      if (String(item?.level || '') === 'error') {
        return { label: '异常', className: 'bad' };
      }
      if (String(item?.kind || '') === 'consecutive_low_pass') {
        return { label: '连续低成功', className: 'warn' };
      }
      return { label: '告警', className: 'warn' };
    }

    function describeAlertKind(kind) {
      if (kind === 'goal_not_met') return '日报未达标';
      if (kind === 'consecutive_low_pass') return '连续低成功率';
      if (kind === 'batch_run_failed') return '批量执行失败';
      return '批量异常';
    }

    function formatAlertPassRate(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return '';
      return `${Math.round(number * 100)}%`;
    }

    function getActiveSession() {
      return state.sessions.find((item) => item.id === state.activeId);
    }

    function createSession(title) {
      const session = {
        id: 's-' + Math.random().toString(36).slice(2, 10),
        title,
        messages: [{ role: 'assistant', payload: { ...WELCOME_PAYLOAD } }],
      };
      state.sessions.unshift(session);
      persistSessions();
      return session;
    }

    function buildAssistantPayload(data, sessionId) {
      const payload = {
        ...(data.reply || {}),
        summary: data.summary || null,
        result: data.result || data.reply?.result || null,
      };
      const pendingStatus = extractPendingStatus(data, sessionId);
      if (pendingStatus) {
        payload.pendingStatus = pendingStatus;
      }
      if (data.chatBackend) {
        payload.chatBackend = data.chatBackend;
      }
      return payload;
    }

    function extractPendingStatus(data, sessionId) {
      const explicitStatus = String(data?.status || '').trim();
      const isPendingReply = data?.reply?.meta?.operation === 'pending';
      if (!explicitStatus && !isPendingReply) return null;
      const pendingStatus = {
        status: explicitStatus || 'pending',
        sessionId: data?.sessionId || sessionId || '',
        elapsedSeconds: Number(data?.elapsedSeconds || 0) || 0,
        phase: data?.phase || data?.reply?.meta?.phase || '',
        statelessProgress: !!data?.statelessProgress,
        readOnlyRecovery: !!data?.readOnlyRecovery,
        willResumeGeneration: data?.willResumeGeneration !== false,
      };
      if (data?.pendingSince) {
        pendingStatus.pendingSince = data.pendingSince;
      }
      if (data?.completedAt) {
        pendingStatus.completedAt = data.completedAt;
      }
      if (data?.statusLabel) {
        pendingStatus.statusLabel = data.statusLabel;
      }
      if (Object.prototype.hasOwnProperty.call(data || {}, 'generationProgress')) {
        pendingStatus.generationProgress = scrubDeletedGenerationProgress(
          data?.generationProgress || null,
          currentDeletedUserGeneratedIdentity(),
        );
      }
      return pendingStatus;
    }

    function isPendingPayload(payload) {
      return ['pending', 'planning'].includes(String(payload?.meta?.operation || '').trim());
    }

    function isCancelledPendingPayload(payload) {
      const pending = payload?.pendingStatus || {};
      const status = String(pending.status || '').trim();
      const phase = String(pending.phase || '').trim();
      return status === 'cancelled' || status === 'canceled' || phase === 'cancelled' || phase === 'canceled';
    }

    function isTerminalTaskStatus(status) {
      return ['completed', 'completed_with_error', 'failed', 'idle', 'cancelled', 'canceled', 'recovered'].includes(
        String(status || '').trim()
      );
    }

    function isBackendGenerationProgressActive(progress) {
      if (!progress) return false;
      if (progress.readOnlyRecovery) return false;
      const running = Number(progress.runningCount || 0) || 0;
      const waiting = Number(progress.waitingCount || 0) || 0;
      if (running > 0 || waiting > 0) return true;
      if (isTerminalTaskStatus(progress.status)) return false;
      const activeItem = Array.isArray(progress.items)
        ? progress.items.some((item) => !isTerminalProgressStatus(item?.status))
        : false;
      if (activeItem) return true;
      const total = Number(progress.totalRequested || 0) || 0;
      const succeeded = Number(progress.succeededCount || 0) || 0;
      const failed = Number(progress.failedCount || 0) || 0;
      const skipped = Number(progress.skippedCount || 0) || 0;
      const deleted = Number(progress.deletedCount || 0) || 0;
      return total > 0 && succeeded + failed + skipped + deleted < total;
    }

    function getGenerationProgressTerminalLabel(progress) {
      if (!progress) return '';
      if (String(progress.status || '').trim() === 'cancelled') return '已强行终止';
      if (progress.readOnlyRecovery) return '历史进度已恢复';
      if (isBackendGenerationProgressActive(progress)) return '';
      const total = Number(progress.totalRequested || 0) || 0;
      const succeeded = Number(progress.succeededCount || 0) || 0;
      const failed = Number(progress.failedCount || 0) || 0;
      const deleted = Number(progress.deletedCount || 0) || 0;
      const hasLocalPostprocessFailure = failed > 0 && Array.isArray(progress.items)
        && progress.items.some((item) => item?.status === 'failed' && getGenerationFailureStageLabel(item) === '本地后处理失败');
      if (failed > 0 && succeeded > 0) return '部分视频已生成';
      if (deleted > 0 && !succeeded && !failed) return '文件已删除';
      if (deleted > 0 && (succeeded > 0 || failed > 0)) return '部分视频已生成';
      if (failed > 0) return hasLocalPostprocessFailure ? '本地后处理失败' : '视频生成失败';
      if (total > 0 && succeeded >= total) return '视频已生成';
      if (succeeded > 0) return '视频已生成';
      return '生成已结束';
    }

    function getPendingStatusLabel(pending = {}, fallback = '后台继续执行中') {
      const terminalLabel = getGenerationProgressTerminalLabel(pending.generationProgress);
      if (terminalLabel) return terminalLabel;
      if (!pending.generationProgress && String(pending.phase || '').trim() === 'planning') {
        return String(pending.statusLabel || '').trim() || '正在理解请求并规划任务';
      }
      return String(pending.statusLabel || '').trim() || fallback;
    }

    function buildTerminalPendingLine(progress = {}, pending = {}) {
      if (String(pending.status || '').trim() === 'cancelled') {
        return '已强行终止，本地已停止等待结果回填。';
      }
      if (pending.readOnlyRecovery || pending.generationProgress?.readOnlyRecovery) {
        return '服务重启前的任务进度已从账本恢复，仅供查看，不会自动继续生成。';
      }
      const done = Number(progress.doneCount || pending.generationProgress?.succeededCount || 0) || 0;
      const failed = Number(pending.generationProgress?.failedCount || 0) || 0;
      const deleted = Number(pending.generationProgress?.deletedCount || 0) || 0;
      const total = Number(progress.expectedCount || pending.generationProgress?.totalRequested || 0) || 0;
      if (total > 0 && deleted >= total && done === 0 && failed === 0) {
        return `已删除 ${deleted}/${total}，不会恢复为已生成。`;
      }
      if (pending.statelessProgress && total > 0 && failed > 0) {
        return `已完成 ${done}/${total}，失败 ${failed} 条，可在“查看结果”里查看已落盘视频。`;
      }
      if (pending.statelessProgress && total > 0) {
        return `已生成 ${done}/${total}，可在“查看结果”里查看。`;
      }
      if (total > 0 && failed > 0) {
        return `本轮已结束：已生成 ${done}/${total}，失败 ${failed} 条。`;
      }
      if (total > 0) {
        return `本轮已结束：已生成 ${done}/${total}。`;
      }
      return '本轮任务已结束。';
    }
