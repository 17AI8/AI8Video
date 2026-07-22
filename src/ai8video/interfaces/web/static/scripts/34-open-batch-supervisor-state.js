    async function openBatchSupervisorState(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-batch-supervisor-state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open batch supervisor state failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openBatchSupervisorAdminState(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-batch-supervisor-admin-state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open batch supervisor admin state failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openBatchSupervisorLock(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-batch-supervisor-lock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open batch supervisor lock failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openBatchSupervisorDeployment(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-batch-supervisor-deployment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open batch supervisor deployment failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function runLivePreflight() {
      if (state.preflight.running) return;
      state.preflight.running = true;
      state.preflight.error = '';
      renderSupervisorStatus();
      try {
        const res = await fetch('/api/live-preflight', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const result = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(result.error || '环境自检失败');
        }
        state.preflight.report = result;
      } catch (error) {
        state.preflight.report = null;
        state.preflight.error = error.message || String(error);
      } finally {
        state.preflight.running = false;
        renderSupervisorStatus();
      }
    }

    async function openBatchSeedFile(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-batch-seed-file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open batch seed file failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function buildBatchSeedFile(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '生成中...';
      const res = await fetch('/api/build-batch-seed-file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        trigger.textContent = '生成失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error(payload.error || 'build batch seed file failed');
      }
      await refreshHealth();
      renderSupervisorStatus();
    }

    function readSupervisorConfigDraft() {
      try {
        const raw = localStorage.getItem(SUPERVISOR_CONFIG_STORAGE_KEY);
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === 'object' ? parsed : {};
      } catch {
        return {};
      }
    }

    function saveSupervisorConfigDraft(payload) {
      const next = {
        scheduleTimes: String(payload?.scheduleTimes || '').trim(),
        targetPassCount: String(payload?.targetPassCount || '').trim(),
        styleHint: String(payload?.styleHint || '').trim(),
        pollSeconds: String(payload?.pollSeconds || '').trim(),
        minPassRate: String(payload?.minPassRate || '').trim(),
        consecutiveLowPassRuns: String(payload?.consecutiveLowPassRuns || '').trim(),
        autoBuildSeedFile: payload?.autoBuildSeedFile !== false,
      };
      localStorage.setItem(SUPERVISOR_CONFIG_STORAGE_KEY, JSON.stringify(next));
    }

    function pickSupervisorConfigValue(source, key, fallback) {
      if (source && Object.prototype.hasOwnProperty.call(source, key)) {
        return source[key];
      }
      return fallback;
    }

    function buildSupervisorConfigDefaults() {
      const health = state.health || {};
      const deployment = health.batchSupervisorDeployment || {};
      const draft = readSupervisorConfigDraft();
      return {
        source: detectSupervisorDraftSource(health, draft),
        scheduleTimes: String(
          pickSupervisorConfigValue(
            draft,
            'scheduleTimes',
            getSupervisorScheduleTimes(health).join(',') || '09:00,13:15',
          ),
        ).trim(),
        targetPassCount: String(
          pickSupervisorConfigValue(
            draft,
            'targetPassCount',
            deployment.targetPassCount || health.batchTargetPassCount || 30,
          ),
        ).trim(),
        styleHint: String(
          pickSupervisorConfigValue(
            draft,
            'styleHint',
            deployment.styleHint || health.batchStyleHint || '商务',
          ),
        ).trim(),
        pollSeconds: String(
          pickSupervisorConfigValue(
            draft,
            'pollSeconds',
            deployment.pollSeconds || 30,
          ),
        ).trim(),
        minPassRate: String(
          pickSupervisorConfigValue(
            draft,
            'minPassRate',
            deployment.minPassRate ?? health.batchAlertMinPassRate ?? 0.7,
          ),
        ).trim(),
        consecutiveLowPassRuns: String(
          pickSupervisorConfigValue(
            draft,
            'consecutiveLowPassRuns',
            deployment.consecutiveLowPassRuns || health.batchAlertConsecutiveLowPassRuns || 2,
          ),
        ).trim(),
        autoBuildSeedFile: draft.autoBuildSeedFile !== false,
      };
    }

    function fillSupervisorConfigForm(payload) {
      els.supervisorScheduleTimesInput.value = String(payload?.scheduleTimes || '');
      els.supervisorTargetPassCountInput.value = String(payload?.targetPassCount || '');
      els.supervisorStyleHintInput.value = String(payload?.styleHint || '');
      els.supervisorPollSecondsInput.value = String(payload?.pollSeconds || '');
      els.supervisorMinPassRateInput.value = String(payload?.minPassRate || '');
      els.supervisorLowPassRunsInput.value = String(payload?.consecutiveLowPassRuns || '');
      els.supervisorAutoBuildSeedInput.checked = payload?.autoBuildSeedFile !== false;
    }

    function readSupervisorConfigFormValue() {
      return {
        scheduleTimes: els.supervisorScheduleTimesInput.value.trim(),
        targetPassCount: els.supervisorTargetPassCountInput.value.trim(),
        styleHint: els.supervisorStyleHintInput.value.trim(),
        pollSeconds: els.supervisorPollSecondsInput.value.trim(),
        minPassRate: els.supervisorMinPassRateInput.value.trim(),
        consecutiveLowPassRuns: els.supervisorLowPassRunsInput.value.trim(),
        autoBuildSeedFile: !!els.supervisorAutoBuildSeedInput.checked,
      };
    }

    function buildSupervisorConfigSummaryLines() {
      const health = state.health || {};
      const deployment = health.batchSupervisorDeployment || null;
      const seedFile = health.batchSeedFileStatus || null;
      const suggestions = getSupervisorSuggestions(health);
      const scheduleTimes = getSupervisorScheduleTimes(health);
      const adminResult = state.supervisorAdminResult || null;
      const actionSummary = summarizeSupervisorAdminResult(adminResult);
      const deploymentPath = String(adminResult?.path || deployment?.plistPath || '').trim();
      return [
        `当前草稿：${state.supervisorModal.draftSource || '运行默认值'}`,
        `当前排期：${scheduleTimes.length ? scheduleTimes.join(' / ') : '尚未配置'}`,
        `长期运行：${formatSupervisorDeployment(deployment) || '未写入'}`,
        `种子文件：${formatSeedFileStatus(seedFile) || '暂未生成'}`,
        actionSummary ? `最近操作：${actionSummary}` : '最近操作：还没有新的值守写入记录',
        deploymentPath ? `部署路径：${deploymentPath}` : '部署路径：当前还没有可用的部署文件路径',
        suggestions.length ? `恢复建议：${suggestions.join('；')}` : '恢复建议：当前没有新的恢复建议',
      ];
    }

    function renderSupervisorConfigNote() {
      const lines = buildSupervisorConfigSummaryLines();
      els.supervisorConfigNote.innerHTML = lines.map((line) => (
        `<div class="modal-note-line">${escapeHtml(line)}</div>`
      )).join('');
    }

    function renderSupervisorConfigModal() {
      const visible = !!state.supervisorModal.visible;
      const mode = state.supervisorModal.mode === 'install' ? 'install' : 'write';
      const submitting = !!state.supervisorModal.submitting;
      els.supervisorConfigModal.classList.toggle('hidden', !visible);
      els.supervisorConfigTitle.textContent = mode === 'install' ? '配置并安装值守' : '配置值守';
      els.supervisorConfigSub.textContent = mode === 'install'
        ? '写入 launchd 配置后，直接安装到当前机器。'
        : '先生成或更新 launchd 配置文件，暂不安装。';
      els.supervisorConfigSubmitButton.textContent = submitting
        ? (mode === 'install' ? '安装中...' : '写入中...')
        : (mode === 'install' ? '确认安装' : '保存配置');
      [
        els.supervisorConfigCloseButton,
        els.supervisorConfigCancelButton,
        els.supervisorScheduleTimesInput,
        els.supervisorTargetPassCountInput,
        els.supervisorStyleHintInput,
        els.supervisorPollSecondsInput,
        els.supervisorMinPassRateInput,
        els.supervisorLowPassRunsInput,
        els.supervisorAutoBuildSeedInput,
      ].forEach((element) => {
        element.disabled = submitting;
      });
      els.supervisorConfigSubmitButton.disabled = submitting;
      els.supervisorConfigError.textContent = state.supervisorModal.error || '';
      renderSupervisorConfigNote();
    }

    function openSupervisorConfigModal(mode) {
      state.supervisorModal.visible = true;
      state.supervisorModal.mode = mode === 'install' ? 'install' : 'write';
      state.supervisorModal.submitting = false;
      state.supervisorModal.error = '';
      const defaults = buildSupervisorConfigDefaults();
      state.supervisorModal.draftSource = defaults.source || '运行默认值';
      fillSupervisorConfigForm(defaults);
      renderSupervisorConfigModal();
      window.requestAnimationFrame(() => {
        els.supervisorScheduleTimesInput.focus();
        els.supervisorScheduleTimesInput.select();
      });
    }

    function closeSupervisorConfigModal() {
      if (state.supervisorModal.submitting) return;
      state.supervisorModal.visible = false;
      state.supervisorModal.error = '';
      renderSupervisorConfigModal();
    }

    function validateSupervisorConfigPayload(payload) {
      if (!payload.scheduleTimes) {
        throw new Error('请先填写自动排期，例如 09:00,13:15。');
      }
      const targetPassCount = Number(payload.targetPassCount);
      if (!Number.isFinite(targetPassCount) || targetPassCount < 1) {
        throw new Error('目标生成数至少为 1。');
      }
      const pollSeconds = Number(payload.pollSeconds);
      if (!Number.isFinite(pollSeconds) || pollSeconds < 5) {
        throw new Error('轮询秒数至少为 5。');
      }
      const minPassRate = Number(payload.minPassRate);
      if (!Number.isFinite(minPassRate) || minPassRate < 0 || minPassRate > 1) {
        throw new Error('最低成功率必须填 0 到 1 之间的小数。');
      }
      const consecutiveLowPassRuns = Number(payload.consecutiveLowPassRuns);
      if (!Number.isFinite(consecutiveLowPassRuns) || consecutiveLowPassRuns < 1) {
        throw new Error('连续低成功告警阈值至少为 1。');
      }
    }

    async function submitSupervisorConfigModal() {
      if (state.supervisorModal.submitting) return;
      const payload = readSupervisorConfigFormValue();
      try {
        validateSupervisorConfigPayload(payload);
      } catch (error) {
        state.supervisorModal.error = error.message || String(error);
        renderSupervisorConfigModal();
        return;
      }
      saveSupervisorConfigDraft(payload);
      state.supervisorModal.submitting = true;
      state.supervisorModal.error = '';
      renderSupervisorConfigModal();
      const endpoint = state.supervisorModal.mode === 'install'
        ? '/api/install-batch-supervisor-deployment'
        : '/api/write-batch-supervisor-deployment';
      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const result = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(result.error || '值守配置提交失败');
        }
        persistSupervisorAdminResult(
          result.adminResult || buildSupervisorAdminResult(result, state.supervisorModal.mode === 'install' ? 'install' : 'write')
        );
        await refreshHealth();
        state.supervisorModal.submitting = false;
        state.supervisorModal.visible = false;
        state.supervisorModal.error = '';
        render();
      } catch (error) {
        state.supervisorModal.submitting = false;
        state.supervisorModal.error = error.message || String(error);
        renderSupervisorConfigModal();
      }
    }

    async function submitBatchSupervisorDeployment(trigger, endpoint, pendingText, doneText, payloadBuilder) {
      const previous = trigger.textContent;
      const payload = await payloadBuilder();
      if (!payload) return;
      trigger.textContent = pendingText;
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) {
        trigger.textContent = '处理失败';
        setTimeout(() => { trigger.textContent = previous; }, 1800);
        throw new Error(result.error || 'batch supervisor deployment failed');
      }
      await refreshHealth();
      renderSupervisorStatus();
      trigger.textContent = doneText;
      setTimeout(() => { trigger.textContent = previous; }, 1400);
    }

    async function writeBatchSupervisorDeployment() {
      openSupervisorConfigModal('write');
    }

    async function installBatchSupervisorDeployment() {
      openSupervisorConfigModal('install');
    }

    async function uninstallBatchSupervisorDeployment(trigger) {
      const confirmed = window.confirm('卸载后会停止当前机器上的自动值守。确定继续吗？');
      if (!confirmed) return;
      await submitBatchSupervisorDeployment(
        trigger,
        '/api/uninstall-batch-supervisor-deployment',
        '卸载中...',
        '已卸载',
        async () => ({ keepPlist: false }),
      );
      persistSupervisorAdminResult(
        state.health?.batchSupervisorAdminResult
        || buildSupervisorAdminResult({
          action: 'uninstall',
          deployment: state.health?.batchSupervisorDeployment || {},
        }, 'uninstall')
      );
      render();
    }

    function buildAssetGalleryModel(items) {
      const deduped = dedupeAssets(sortAssetsNewest(items || []));
      const realArchived = deduped.filter((item) => (
        item.archiveStatus === 'archived'
        && !item.dryRun
        && (resolvePlayableVideoSrc(item) || resolvePlayablePreviewSrc(item) || resolvePlayableCoverSrc(item))
      ));
      if (realArchived.length) {
        return {
          items: realArchived,
          metricLabel: '真实成片',
          emptyText: '还没有真实成片。',
          summaryText: '当前只展示真实已归档成片。',
        };
      }
      return {
        items: [],
        metricLabel: '真实成片',
        emptyText: '还没有真实成片。',
        summaryText: '还没有真实成片。',
      };
    }

    function sortAssetsNewest(items) {
      return [...items].sort((left, right) => {
        const leftTime = Date.parse(left?.createdAt || '') || 0;
        const rightTime = Date.parse(right?.createdAt || '') || 0;
        return rightTime - leftTime;
      });
    }

    function dedupeAssets(items) {
      const seen = new Set();
      return items.filter((item) => {
        const key = item.archiveKey || item.jobId || `${item.storageKey || ''}::${item.createdAt || ''}`;
        if (!key) return true;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    }

    function groupAssetsByDay(items) {
      const groups = new Map();
      (items || []).forEach((item) => {
        const date = formatAssetDay(item.createdAt);
        if (!groups.has(date)) groups.set(date, []);
        groups.get(date).push(item);
      });
      return Array.from(groups.entries()).map(([date, groupItems]) => ({ date, items: groupItems }));
    }

    function groupReportsByDay(items) {
      const groups = new Map();
      (items || []).forEach((item) => {
        const date = formatReportDay(item.reportDate || item.generatedAt || item.reportSavedAt);
        if (!groups.has(date)) groups.set(date, []);
        groups.get(date).push(item);
      });
      return Array.from(groups.entries()).map(([date, groupItems]) => ({ date, items: groupItems }));
    }

    function groupAlertsByDay(items) {
      const groups = new Map();
      (items || []).forEach((item) => {
        const date = formatReportDay(item.alertDate || item.createdAt || item.alertSavedAt);
        if (!groups.has(date)) groups.set(date, []);
        groups.get(date).push(item);
      });
      return Array.from(groups.entries()).map(([date, groupItems]) => ({ date, items: groupItems }));
    }

