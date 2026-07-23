    async function regenerateTtsFromVideoPreview(userGeneratedKey, button) {
      const key = String(userGeneratedKey || '').trim();
      if (!key) return;
      const previous = getVideoPreviewButtonLabel(button) || '重新生成TTS配音';
      if (button) {
        button.disabled = true;
        setVideoPreviewButtonLabel(button, '生成中');
      }
      try {
        const res = await fetch('/api/user-generated-results/regenerate-tts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw buildRequestError(data);
        }
        if (data?.deleted) {
          if (button) {
            setVideoPreviewButtonLabel(button, '台词已删除');
            setTimeout(() => {
              setVideoPreviewButtonLabel(button, previous);
              button.disabled = false;
            }, 1400);
          }
          return;
        }
        const video = els.videoPreviewBody?.querySelector('video');
        if (video) {
          const baseSrc = video.currentSrc || video.getAttribute('src') || data.videoUrl || '';
          const cleanSrc = String(baseSrc).split('?')[0];
          video.src = `${cleanSrc}?tts=${Date.now()}`;
          video.load();
          video.play().catch(() => {});
        }
        await refreshUserGeneratedResults();
        renderResultModal();
        renderStatus();
        if (button) {
          setVideoPreviewButtonLabel(button, '已生成');
          setTimeout(() => {
            setVideoPreviewButtonLabel(button, previous);
            button.disabled = false;
          }, 1400);
        }
      } catch (error) {
        const message = error?.message || '重新生成TTS配音失败';
        window.alert(message.includes('台词已删除') ? '台词已删除' : message);
        if (button) {
          setVideoPreviewButtonLabel(button, previous);
          button.disabled = false;
        }
      }
    }

    async function persistOpenTtsEditorBeforeHtmlMotion(userGeneratedKey) {
      const popover = els.videoPreviewBody?.querySelector('[data-video-preview-tts-editor]');
      const textarea = popover?.querySelector('[data-video-preview-tts-textarea]');
      if (!popover || !popover.classList.contains('is-open') || !textarea || textarea.disabled) return;
      const res = await fetch('/api/user-generated-results/tts-narration', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userGeneratedKey, text: textarea.value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw buildRequestError(data);
      const status = popover.querySelector('[data-video-preview-tts-status]');
      if (status) status.textContent = data?.deleted ? '台词已删除' : `已同步，${Number(data?.textChars || 0)} 字`;
    }

    function showHtmlMotionPreview(video, previewUrl) {
      if (!video || !previewUrl) return;
      video.src = `${previewUrl}${previewUrl.includes('?') ? '&' : '?'}v=${Date.now()}`;
      video.load();
      video.play().catch(() => {});
    }

    function invalidateHtmlMotionPreviewRequest() {
      const modalState = state.videoPreviewModal || {};
      if (modalState.htmlMotionPollTimer) {
        clearTimeout(modalState.htmlMotionPollTimer);
      }
      if (modalState.htmlMotionTickTimer) {
        clearInterval(modalState.htmlMotionTickTimer);
      }
      state.videoPreviewModal = {
        ...modalState,
        htmlMotionTaskId: '',
        htmlMotionPollTimer: null,
        htmlMotionTickTimer: null,
        htmlMotionRequestSeq: Number(modalState.htmlMotionRequestSeq || 0) + 1,
      };
      return state.videoPreviewModal.htmlMotionRequestSeq;
    }

    function htmlMotionRequestIsCurrent(requestSeq) {
      return Number(state.videoPreviewModal?.htmlMotionRequestSeq || 0) === Number(requestSeq);
    }

    function rememberHtmlMotionJob(userGeneratedKey, taskId, pollUrl) {
      const key = String(userGeneratedKey || '').trim();
      const id = String(taskId || '').trim();
      if (!key || !id) return;
      if (!state.htmlMotionJobs || typeof state.htmlMotionJobs !== 'object') {
        state.htmlMotionJobs = {};
      }
      state.htmlMotionJobs[key] = {
        taskId: id,
        pollUrl: String(pollUrl || `/api/user-generated-results/html-motion-tasks/${encodeURIComponent(id)}`),
        rememberedAt: Date.now(),
      };
    }

    function forgetHtmlMotionJob(userGeneratedKey) {
      const key = String(userGeneratedKey || '').trim();
      if (!key || !state.htmlMotionJobs) return;
      delete state.htmlMotionJobs[key];
    }

    function rememberedHtmlMotionJob(userGeneratedKey) {
      const key = String(userGeneratedKey || '').trim();
      const job = state.htmlMotionJobs?.[key];
      if (!job?.taskId) return null;
      return job;
    }

    function currentVideoPreviewUserGeneratedKey() {
      const modal = state.videoPreviewModal || {};
      const playlist = Array.isArray(modal.playlist) ? modal.playlist : [];
      const item = playlist[Number(modal.index || 0)] || playlist[0];
      return String(item?.userGeneratedKey || '').trim();
    }

    function isHtmlMotionTaskTerminal(status) {
      const value = String(status || '').toLowerCase();
      return ['preview_ready', 'preview_failed', 'failed', 'cancelled'].includes(value);
    }

    async function fetchActiveHtmlMotionJob(userGeneratedKey) {
      const key = String(userGeneratedKey || '').trim();
      if (!key) return null;
      const local = rememberedHtmlMotionJob(key);
      try {
        const res = await fetch('/api/user-generated-results/html-motion-active', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data?.active && data?.taskId && !isHtmlMotionTaskTerminal(data.status)) {
          rememberHtmlMotionJob(key, data.taskId, data.pollUrl);
          return {
            taskId: String(data.taskId),
            pollUrl: String(data.pollUrl || ''),
          };
        }
        if (res.ok && data?.active === false && local) {
          // Local memory may be stale after completion; drop it.
          forgetHtmlMotionJob(key);
          return null;
        }
      } catch (_) {
        /* fall through to local */
      }
      if (local?.taskId) return local;
      return null;
    }

    async function resumeHtmlMotionFromVideoPreview(userGeneratedKey, button, confirmButton, video) {
      const key = String(userGeneratedKey || '').trim();
      if (!key) return;
      const job = await fetchActiveHtmlMotionJob(key);
      if (!job?.taskId) {
        await syncHtmlMotionReviewFromVideoPreview(key, confirmButton, video);
        return;
      }
      const requestSeq = Number(state.videoPreviewModal?.htmlMotionRequestSeq || 0);
      if (button) {
        button.disabled = false;
        setVideoPreviewButtonLabel(button, '强行停止');
      }
      if (confirmButton) confirmButton.disabled = true;
      setHtmlMotionPreviewStatus('后台动效仍在生成，已重新接上进度');
      state.videoPreviewModal.htmlMotionTaskId = job.taskId;
      state.videoPreviewModal.htmlMotionSubmitting = false;
      state.videoPreviewModal.htmlMotionCancelRequested = false;
      try {
        await waitForHtmlMotionTask(
          job.taskId,
          job.pollUrl,
          requestSeq,
          button,
          confirmButton,
          key,
        );
      } catch (error) {
        if (!htmlMotionRequestIsCurrent(requestSeq)) return;
        const message = error?.message || '接上 HTML 动效进度失败';
        setHtmlMotionPreviewStatus(`预览失败：${message}`, 'warning');
      } finally {
        if (htmlMotionRequestIsCurrent(requestSeq) && button) {
          setVideoPreviewButtonLabel(button, '重新生成 HTML 动效');
          button.disabled = false;
        }
      }
    }

    async function cancelHtmlMotionFromVideoPreview(button) {
      if (!state.videoPreviewModal) return;
      state.videoPreviewModal.htmlMotionCancelRequested = true;
      const taskId = String(state.videoPreviewModal.htmlMotionTaskId || '').trim();
      if (button) {
        button.disabled = true;
        setVideoPreviewButtonLabel(button, '停止中');
      }
      if (!taskId) {
        state.videoPreviewModal.htmlMotionCancelRequested = false;
        if (button) {
          button.disabled = false;
          setVideoPreviewButtonLabel(button, '重新生成 HTML 动效');
        }
        return;
      }
      try {
        const res = await fetch(`/api/user-generated-results/html-motion-tasks/${encodeURIComponent(taskId)}/cancel`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = await res.json().catch(() => ({}));
        const status = String(data?.status || '').toLowerCase();
        if (!res.ok || data?.ok === false && status !== 'cancelled') {
          throw buildRequestError(data);
        }
        const key = currentVideoPreviewUserGeneratedKey();
        if (key) forgetHtmlMotionJob(key);
        invalidateHtmlMotionPreviewRequest();
        state.videoPreviewModal.htmlMotionSubmitting = false;
        state.videoPreviewModal.htmlMotionCancelRequested = false;
        if (button) {
          button.disabled = false;
          setVideoPreviewButtonLabel(button, '重新生成 HTML 动效');
        }
        setHtmlMotionPreviewStatus('已取消 HTML 动效预览', 'warning');
      } catch (error) {
        state.videoPreviewModal.htmlMotionCancelRequested = false;
        if (button) {
          button.disabled = false;
          setVideoPreviewButtonLabel(button, '强行停止');
        }
        window.alert(error?.message || '强行停止 HTML 动效失败');
      }
    }

    function setHtmlMotionPreviewStatus(message, status = '') {
      if (!state.videoPreviewModal) return;
      const currentTask = state.videoPreviewModal.htmlMotionTaskSnapshot || {};
      state.videoPreviewModal.htmlMotionTaskSnapshot = {
        ...currentTask,
        message: String(message || ''),
        status: String(status || currentTask.status || ''),
      };
      renderHtmlMotionPreviewDrawer();
    }

    function toggleHtmlMotionPreviewDrawer() {
      if (!state.videoPreviewModal) return;
      state.videoPreviewModal.htmlMotionDetailsOpen = !state.videoPreviewModal.htmlMotionDetailsOpen;
      renderHtmlMotionPreviewDrawer();
    }

    function updateHtmlMotionPreviewTaskSnapshot(data, options = {}) {
      if (!state.videoPreviewModal) return;
      const currentTask = state.videoPreviewModal.htmlMotionTaskSnapshot || {};
      // 进度文案由 setHtmlMotionPreviewStatus 维护，避免轮询用接口原文盖掉导致数字闪烁
      state.videoPreviewModal.htmlMotionTaskSnapshot = {
        ...currentTask,
        status: String(data?.taskStatus || data?.status || currentTask.status || ''),
        phase: String(data?.taskPhase || data?.phase || ''),
        elapsedSeconds: Number(data?.elapsedSeconds || 0),
        phaseElapsedSeconds: Number(data?.phaseElapsedSeconds || 0),
        phaseTimings: data?.phaseTimings || {},
        retryCount: Number(data?.retryCount || 0),
        retryLimit: Number(data?.retryLimit || 0),
        auditResult: String(data?.auditResult || ''),
        streamText: String(data?.streamText || ''),
        attemptTraces: Array.isArray(data?.attemptTraces) ? data.attemptTraces : [],
      };
      if (options.render === false) return;
      renderHtmlMotionPreviewDrawer();
    }

    function syncHtmlMotionDrawerWidth() {
      const drawer = els.videoPreviewBody?.querySelector('[data-video-preview-html-motion-drawer]');
      const confirmButton = els.videoPreviewBody?.querySelector('[data-video-preview-action="confirm-html-motion"]');
      if (!drawer || !confirmButton) return;
      const width = Math.round(confirmButton.getBoundingClientRect().right - drawer.getBoundingClientRect().left);
      const next = width > 0 ? `${width}px` : '';
      if (next && drawer.style.width !== next) drawer.style.width = next;
    }

    function renderHtmlMotionPreviewDrawer() {
      const drawer = els.videoPreviewBody?.querySelector('[data-video-preview-html-motion-drawer]');
      const toggles = drawer?.querySelectorAll('[data-video-preview-html-motion-toggle]');
      const summary = drawer?.querySelector('[data-video-preview-html-motion-summary]');
      const detail = drawer?.querySelector('[data-video-preview-html-motion-detail]');
      if (!drawer || !toggles?.length || !summary || !detail) return;
      const task = state.videoPreviewModal?.htmlMotionTaskSnapshot || {};
      const hasTask = Boolean(task.message || task.phase || task.status || task.streamText || task.retryCount || task.attemptTraces?.length);
      const open = !!state.videoPreviewModal?.htmlMotionDetailsOpen;
      const wasExpanded = drawer.classList.contains('has-task') || drawer.classList.contains('is-open');
      const shouldExpand = hasTask || open;
      const nextMessage = task.message || '';
      if (summary.textContent !== nextMessage) summary.textContent = nextMessage;
      summary.hidden = !hasTask;
      toggles.forEach((toggle) => {
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        toggle.setAttribute('aria-label', `${open ? '收起' : '展开'}任务详情`);
      });
      if (shouldExpand && !wasExpanded) {
        drawer.classList.remove('has-task', 'is-open');
        void drawer.offsetHeight;
      }
      drawer.classList.toggle('has-task', hasTask);
      drawer.classList.toggle('is-open', open);
      syncHtmlMotionDrawerWidth();
      if (!open) return;
      const streamText = hasTask
        ? String(task.streamText || '').trim()
        : '任务开始后，这里会显示流式输出。';
      const displayStream = streamText || '等待 AI 返回结果…';
      if (detail.dataset.streamText === displayStream) return;
      detail.dataset.streamText = displayStream;
      const shouldStick = detail.scrollHeight - detail.scrollTop - detail.clientHeight < 16;
      detail.innerHTML = `<pre class="video-preview-html-motion-stream">${escapeHtml(displayStream)}</pre>`;
      if (shouldStick) detail.scrollTop = detail.scrollHeight;
    }

    function formatHtmlMotionElapsed(seconds) {
      const value = Number(seconds);
      if (!Number.isFinite(value) || value < 0) return '0s';
      if (value < 10) return `${value.toFixed(1).replace(/\.0$/, '')}s`;
      return `${Math.round(value)}s`;
    }

    function formatHtmlMotionPhaseSummary(phaseTimings) {
      if (!phaseTimings || typeof phaseTimings !== 'object') return '';
      const labels = {
        queued: '排队',
        preparing: '准备',
        generating: '方案',
        checking: '检查',
        rendering: '渲染',
        compositing: '合成',
        validating: '校验',
      };
      const parts = Object.keys(labels)
        .filter((key) => Number(phaseTimings[key]) > 0)
        .map((key) => `${labels[key]} ${formatHtmlMotionElapsed(phaseTimings[key])}`);
      return parts.join(' / ');
    }

    function resolveHtmlMotionTiming(data, startedAtMs) {
      const elapsedFromApi = Number(data?.elapsedSeconds);
      const phaseFromApi = Number(data?.phaseElapsedSeconds);
      const localElapsed = Math.max(0, (Date.now() - startedAtMs) / 1000);
      const elapsedSeconds = Number.isFinite(elapsedFromApi) ? elapsedFromApi : localElapsed;
      const phaseElapsedSeconds = Number.isFinite(phaseFromApi) ? phaseFromApi : elapsedSeconds;
      const phaseTimings = data?.phaseTimings && typeof data.phaseTimings === 'object'
        ? data.phaseTimings
        : {};
      return { elapsedSeconds, phaseElapsedSeconds, phaseTimings };
    }

    function buildHtmlMotionProgressStatus(phaseLabel, timing) {
      const total = formatHtmlMotionElapsed(timing.elapsedSeconds);
      const phase = formatHtmlMotionElapsed(timing.phaseElapsedSeconds);
      const summary = formatHtmlMotionPhaseSummary(timing.phaseTimings);
      if (summary) {
        return `${phaseLabel} · 已 ${total}（${summary} · 当前 ${phase}）`;
      }
      return `${phaseLabel} · 已 ${total}（本阶段 ${phase}）`;
    }

    function buildHtmlMotionSuccessStatus(timing, overlay = null) {
      const total = formatHtmlMotionElapsed(timing.elapsedSeconds);
      const summary = formatHtmlMotionPhaseSummary(timing.phaseTimings);
      return summary
        ? `预览已生成 · 总耗时 ${total}（${summary}）`
        : `预览已生成 · 总耗时 ${total}，确认后才会替换正式视频`;
    }

    function buildHtmlMotionFailureStatus(message, timing) {
      const total = formatHtmlMotionElapsed(timing.elapsedSeconds);
      return `${message} · 已耗时 ${total}`;
    }

    function waitForHtmlMotionTask(taskId, pollUrl, requestSeq, button, confirmButton, userGeneratedKey = '') {
      const url = pollUrl || `/api/user-generated-results/html-motion-tasks/${encodeURIComponent(taskId)}`;
      const jobKey = String(userGeneratedKey || '').trim();
      const startedAtMs = Date.now();
      let lastTiming = { elapsedSeconds: 0, phaseElapsedSeconds: 0, phaseTimings: {} };
      let lastPhaseLabel = 'HTML 动效处理中';
      let lastStatusTone = '';
      let lastPollAtMs = startedAtMs;

      const stopTick = () => {
        if (state.videoPreviewModal?.htmlMotionTickTimer) {
          clearInterval(state.videoPreviewModal.htmlMotionTickTimer);
          state.videoPreviewModal.htmlMotionTickTimer = null;
        }
      };

      const liveTiming = () => {
        const drift = Math.max(0, (Date.now() - lastPollAtMs) / 1000);
        return {
          elapsedSeconds: Math.max(0, (Date.now() - startedAtMs) / 1000),
          phaseElapsedSeconds: lastTiming.phaseElapsedSeconds + drift,
          phaseTimings: lastTiming.phaseTimings,
        };
      };

      const refreshProgress = () => {
        if (!htmlMotionRequestIsCurrent(requestSeq)) {
          stopTick();
          return;
        }
        setHtmlMotionPreviewStatus(
          buildHtmlMotionProgressStatus(lastPhaseLabel, liveTiming()),
          lastStatusTone,
        );
      };

      const startTick = () => {
        stopTick();
        if (!state.videoPreviewModal) state.videoPreviewModal = {};
        state.videoPreviewModal.htmlMotionTickTimer = setInterval(refreshProgress, 250);
      };

      const poll = async () => {
        if (!htmlMotionRequestIsCurrent(requestSeq)) {
          stopTick();
          return null;
        }
        const res = await fetch(url, { cache: 'no-store' });
        const data = await res.json().catch(() => ({}));
        if (!htmlMotionRequestIsCurrent(requestSeq)) {
          stopTick();
          return null;
        }
        if (res.status === 404) {
          stopTick();
          if (jobKey) forgetHtmlMotionJob(jobKey);
          if (state.videoPreviewModal) {
            state.videoPreviewModal.htmlMotionTaskId = '';
            state.videoPreviewModal.htmlMotionSubmitting = false;
            state.videoPreviewModal.htmlMotionCancelRequested = false;
          }
          if (button) {
            button.disabled = false;
            setVideoPreviewButtonLabel(button, '重新生成 HTML 动效');
          }
          if (confirmButton) confirmButton.disabled = true;
          setHtmlMotionPreviewStatus('任务因服务重启中断，请重新生成', 'warning');
          return { ...data, status: 'interrupted' };
        }
        if (!res.ok || data?.ok === false && data?.status === undefined) {
          stopTick();
          throw buildRequestError(data);
        }
        const status = String(data?.taskStatus || data?.status || '').toLowerCase();
        updateHtmlMotionPreviewTaskSnapshot(data, { render: false });
        const phase = String(data?.taskPhase || data?.phase || status);
        const timing = resolveHtmlMotionTiming(data, startedAtMs);
        const retryCount = Number(data?.retryCount || 0);
        const retryLimit = Number(data?.retryLimit || 0);
        const auditResult = String(data?.auditResult || data?.retryReason || '').trim();
        const retrying = phase === 'generating' && retryCount > 0;
        const retrySummary = summarizeHtmlMotionRetryReason(auditResult);
        const retryLabel = retrying
          ? `审核结果：${retrySummary}・正在第 ${retryCount}/${retryLimit || '?'} 次重试`
          : '';
        const phaseLabel = retryLabel || {
          queued: '排队等待处理',
          preparing: '准备动效素材',
          generating: '正在生成动效方案',
          checking: '正在检查动效布局与时间线',
          rendering: '渲染透明动画',
          compositing: '合成预览画面',
          validating: '检查预览视频',
        }[phase] || data?.message || 'HTML 动效处理中';
        lastTiming = timing;
        lastPhaseLabel = phaseLabel;
        lastStatusTone = retrying ? 'retry' : '';
        lastPollAtMs = Date.now();
        setHtmlMotionPreviewStatus(
          buildHtmlMotionProgressStatus(phaseLabel, timing),
          retrying ? 'retry' : '',
        );
        startTick();
        if (status === 'preview_ready') {
          stopTick();
          if (jobKey) forgetHtmlMotionJob(jobKey);
          const overlay = data?.htmlMotionOverlay || data?.result?.htmlMotionOverlay || {};
          const video = els.videoPreviewBody?.querySelector('video');
          showHtmlMotionPreview(video, overlay.previewUrl || data.previewUrl || data.videoUrl);
          if (confirmButton) confirmButton.disabled = false;
          setHtmlMotionPreviewStatus(buildHtmlMotionSuccessStatus(timing, overlay), 'success');
          return data;
        }
        if (status === 'preview_failed' || status === 'failed' || status === 'cancelled') {
          stopTick();
          if (jobKey) forgetHtmlMotionJob(jobKey);
          const overlay = data?.htmlMotionOverlay || data?.result?.htmlMotionOverlay || {};
          const reason = data?.error || data?.message || overlay.reason || 'HTML 动效预览失败';
          const detail = String(overlay.detail || '').trim();
          const displayReason = detail && !String(reason).includes(detail.slice(0, 24))
            ? `${reason}｜${detail.slice(0, 160)}`
            : reason;
          if (confirmButton) confirmButton.disabled = true;
          const failureMessage = status === 'cancelled' ? '已取消 HTML 动效预览' : `预览失败：${displayReason}`;
          setHtmlMotionPreviewStatus(buildHtmlMotionFailureStatus(failureMessage, timing), 'warning');
          if (status === 'cancelled') return data;
          throw new Error(reason);
        }
        const pollDelay = phase === 'generating' ? 250 : 1000;
        await new Promise((resolve) => {
          state.videoPreviewModal.htmlMotionPollTimer = setTimeout(resolve, pollDelay);
        });
        return poll();
      };
      startTick();
      return poll().catch((error) => {
        stopTick();
        throw error;
      });
    }

    function summarizeHtmlMotionRetryReason(reason) {
      const text = String(reason || '');
      if (text.includes('过长')) return '文案过长';
      if (text.includes('缺少真实痛点') || text.includes('伪问题')) return '问句缺少真实痛点';
      if (text.includes('不是台词连续片段') || text.includes('不是完整意群')) return '与原台词不一致';
      if (text.includes('beats') || text.includes('拍数')) return '拍数不符合设置';
      if (text.includes('重复') || text.includes('互为截断')) return '文案重复';
      if (text.includes('CTA') || text.includes('空泛营销') || text.includes('号召')) return '营销话术不合格';
      if (text.includes('顺序') || text.includes('回跳') || text.includes('交叉')) return '台词顺序不正确';
      return '文案质量未通过';
    }

    async function syncHtmlMotionReviewFromVideoPreview(userGeneratedKey, confirmButton, video) {
      const key = String(userGeneratedKey || '').trim();
      if (!key || !confirmButton) return;
      try {
        const res = await fetch('/api/user-generated-results/html-motion-review', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey: key }),
        });
        const data = await res.json().catch(() => ({}));
        const ready = res.ok && data?.reviewReady === true;
        confirmButton.disabled = !ready;
        if (ready) showHtmlMotionPreview(video, data.previewUrl);
      } catch (_) {
        confirmButton.disabled = true;
      }
    }
