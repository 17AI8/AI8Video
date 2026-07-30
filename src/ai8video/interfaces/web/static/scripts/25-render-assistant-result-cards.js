    function renderAssistantResultCards(session, payload, resultGroups, summary) {
      const gallery = buildResultGalleryFromPayload(payload);
      const expectedCount = gallery.expectedCount || summary.videoCount || resultGroups.length || 0;
      const returnedCount = getPlayableResultItems(gallery).length;
      const failedCount = getFailedResultItems(gallery).length;
      const hasTerminalResult = !!(payload?.result && payload.meta?.operation !== 'pending');
      const hasUnboundTerminalResult = hasTerminalResult && returnedCount === 0 && failedCount === 0;
      const headline = payload.meta?.operation === 'rewrite' && payload.meta?.rewrittenVideoIndex
        ? `第 ${payload.meta.rewrittenVideoIndex} 条视频已返回`
        : hasUnboundTerminalResult
          ? '本轮结果已回填，未绑定预览'
        : failedCount && returnedCount === 0
          ? `本轮失败 ${failedCount}/${expectedCount} 条`
          : failedCount
            ? `本轮已返回 ${returnedCount}/${expectedCount} 条，失败 ${failedCount} 条`
            : `本轮已返回 ${returnedCount}/${expectedCount} 条`;
      const description = hasUnboundTerminalResult
        ? '当前消息没有绑定可播放文件；已停止等待，请从左侧“查看结果”查看已落盘视频。'
        : failedCount && returnedCount === 0
          ? '本轮没有可播放视频；下面只展示失败条目和失败原因。'
        : failedCount
          ? '已生成视频可播放；失败条目只展示失败原因。'
        : '尺寸固定展示，点击中间播放按钮即可弹窗预览。';
      return `
        <div class="mini-card">
          <strong>${escapeHtml(headline)}</strong>
          <div>${escapeHtml(description)}</div>
          ${renderResultMediaWall(gallery, { wall: false })}
          ${renderResultReviewSuggestions(payload?.result)}
        </div>
      `;
    }

    function renderResultReviewSuggestions(result) {
      const advisoryGroups = new Map();
      const suggestions = [];
      (result?.videos || []).forEach((video) => {
        const review = video?.keyword_guidance?.post_review || {};
        const advisories = Array.isArray(review.userAdvisories) ? review.userAdvisories.filter(Boolean) : [];
        const violations = Array.isArray(review.violations) ? review.violations.filter(Boolean) : [];
        advisories.forEach((text) => {
          const indexes = advisoryGroups.get(text) || [];
          indexes.push(video.index || '-');
          advisoryGroups.set(text, indexes);
        });
        violations.forEach((text) => suggestions.push(`第 ${video.index || '-'} 条｜已修正：${text}`));
        if (review.passes === false && !advisories.length && !violations.length) {
          suggestions.push(`第 ${video.index || '-'} 条｜已修正：后审核已按系统提示词修正最终输出。`);
        }
      });
      advisoryGroups.forEach((indexes, text) => {
        const scope = indexes.length === 1 ? `第 ${indexes[0]} 条` : `第 ${indexes.join('、')} 条`;
        suggestions.unshift(`${scope}｜请注意：${text}`);
      });
      if (!suggestions.length) return '';
      return `
        <details class="result-review-details">
          <summary>审核建议（${suggestions.length}）</summary>
          <ul class="result-review-list">
            ${suggestions.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}
          </ul>
        </details>
      `;
    }

    function renderResultNotifyStrip(items, options = {}) {
      if (!items?.length) return '';
      const context = buildResultNotifyContext(items);
      return `
        <div class="result-notify-strip${options.wall ? ' wall' : ''}${options.compact ? ' compact' : ''}">
          ${items.map((item, index) => buildResultNotifyCardMarkup(item, index, context)).join('')}
        </div>
      `;
    }

    function renderProgressResultStrip(items, pendingCount = 0) {
      const realItems = Array.isArray(items) ? items : [];
      const pendingTotal = Math.max(0, Number(pendingCount || 0) || 0);
      if (!realItems.length && !pendingTotal) return '';
      const context = buildResultNotifyContext(realItems);
      const cards = [
        ...realItems.map((item, index) => buildResultNotifyCardMarkup(item, index, context)),
        ...Array.from({ length: pendingTotal }, (_, index) => buildPendingResultCardMarkup(realItems.length + index + 1)),
      ].join('');
      return `<div class="result-notify-strip wall">${cards}</div>`;
    }

    function renderResultMediaWall(gallery, options = {}) {
      const items = getPlayableResultItems(gallery);
      const failedItems = getFailedResultItems(gallery);
      const expectedCount = gallery.expectedCount || getActiveExpectedResultCount();
      const terminalCount = items.length + failedItems.length;
      const isTerminalResult = !!(gallery.payload?.result && gallery.payload?.meta?.operation !== 'pending');
      const pendingCount = gallery.source === 'folder' || isTerminalResult ? 0 : Math.max(0, expectedCount - terminalCount);
      if (!items.length && !failedItems.length && !pendingCount) return '<div class="empty">当前没有可预览的视频或图片。</div>';
      const context = buildResultNotifyContext([...items, ...failedItems]);
      const cards = [
        ...items.map((item, index) => buildResultNotifyCardMarkup(item, index, context, options)),
        ...failedItems.map((item, index) => buildFailedResultCardMarkup(item, items.length + index + 1)),
        ...Array.from({ length: pendingCount }, (_, index) => buildPendingResultCardMarkup(terminalCount + index + 1)),
      ].join('');
      return `<div class="result-notify-strip${options.wall ? ' wall' : ''}">${cards}</div>`;
    }

    function getPlayableResultItems(gallery) {
      return (gallery.items || []).filter((item) => resolvePlayableVideoSrc(item) || resolvePlayablePreviewSrc(item) || resolvePlayableCoverSrc(item));
    }

    function getFailedResultItems(gallery) {
      if (gallery?.source === 'folder') return [];
      const playableKeys = new Set(
        getPlayableResultItems(gallery).map((item) => String(item.jobId || item.videoIndex || '')).filter(Boolean)
      );
      return (gallery.groups || [])
        .filter((item) => resolveBatchStageLabel(item) === '生成失败')
        .filter((item) => {
          const key = String(item.jobId || item.index || '').trim();
          return !key || !playableKeys.has(key);
        });
    }

    function getActiveExpectedResultCount() {
      if (state.generationProgress?.count) {
        return Number(state.generationProgress.count) || 0;
      }
      const session = getActiveSession();
      const last = session?.messages?.at?.(-1);
      const count = Number(last?.payload?.pendingStatus?.videoCount || 0);
      if (Number.isFinite(count) && count > 0) return count;
      return 0;
    }

    function buildResultNotifyContext(items) {
      const sourceItems = Array.isArray(items) ? items : [];
      const sharedFailureReason = sourceItems
        .map((item) => getGenerationFailureRawReason(item))
        .find((reason) => reason && !isGenericGenerationFailureText(reason) && !isNoUpstreamFailureReason(reason)) || '';
      return { sharedFailureReason };
    }

    function isGenericGenerationFailureText(value) {
      const text = String(value || '').trim();
      const lowered = text.toLowerCase();
      return !text || [
        '生成失败',
        '失败',
        'failed',
        'error',
        'skipped',
        '已跳过',
        '未提交',
        '待提交',
        '准备提交',
      ].includes(lowered);
    }

    function getGenerationFailureRawReason(item, fallback = '') {
      const candidates = [
        item?.error,
        item?.generationReasons,
        item?.archiveError,
        item?.statusLabel,
        item?.stage,
        item?.jobStatus,
        fallback,
      ];
      const normalized = candidates
        .map((value) => String(value || '').trim())
        .filter(Boolean);
      return normalized.find((value) => !isGenericGenerationFailureText(value)) || normalized[0] || '';
    }

    function isNoUpstreamFailureReason(value) {
      const text = String(value || '').trim();
      if (!text) return true;
      const reason = humanizeGenerationFailureReason(text);
      return reason.includes('没有上游返回') || reason.includes('未提交给生成服务');
    }

    function buildResultNotifyCardMarkup(item, index, context = {}, options = {}) {
      if (item?.__progressStatus) {
        return buildProgressStatusResultCardMarkup(item, index, context);
      }
      const videoSrc = resolvePlayableVideoSrc(item);
      const previewSrc = resolvePlayablePreviewSrc(item);
      const coverSrc = previewSrc || resolvePlayableCoverSrc(item);
      const userGeneratedKey = item.userGeneratedKey || deriveUserGeneratedKeyFromMediaUrl(videoSrc);
      const userGeneratedPreviewKey = item.userGeneratedPreviewKey || deriveLocalPreviewKey(userGeneratedKey);
      const userGeneratedCoverKey = item.userGeneratedCoverKey || deriveLocalCoverKey(userGeneratedKey);
      const badgeText = item.videoIndex ? `第 ${item.videoIndex} 条` : `视频 ${index + 1}`;
      const title = cleanDisplayText(item.videoTitle || item.title, badgeText);
      const ratioLabel = buildResultRatioLabel(item);
      const motionOverlay = htmlMotionOverlayDisplay(item);
      const subtitle = isFailedResult(item)
        ? '生成失败'
        : (motionOverlay.label ? `已生成 · ${motionOverlay.label}` : (isGeneratedResult(item) ? '已生成 · 点击播放' : '点击播放'));
      const mergeKey = userGeneratedKey || '';
      const selectionOrder = Array.isArray(options.selectedKeys) ? options.selectedKeys.indexOf(mergeKey) + 1 : 0;
      const batchSelection = options.batchMerge && mergeKey ? `
        <button type="button" class="result-batch-merge-select${selectionOrder ? ' is-selected' : ''}"
          data-result-batch-merge-select="${escapeHtml(mergeKey)}" aria-pressed="${selectionOrder ? 'true' : 'false'}"
          aria-label="${selectionOrder ? `取消第 ${selectionOrder} 个合并视频` : '选择这个视频进行合并'}">
          <span class="result-batch-merge-checkbox" aria-hidden="true">${selectionOrder ? '✓' : ''}</span>
          ${selectionOrder ? `<span class="result-batch-merge-order">${selectionOrder}</span>` : ''}
        </button>
      ` : '';
      return `
        <div class="result-notify-card ${resultNotifyRatioClass(item)}${selectionOrder ? ' is-batch-selected' : ''}">
          <div class="result-notify-preview">
            ${batchSelection}
            ${renderResultVideoPromptButton(item, index)}
            ${coverSrc
              ? `<img alt="${escapeHtml(title)}" src="${escapeHtml(coverSrc)}">`
              : (videoSrc ? `<video muted playsinline preload="none" src="${escapeHtml(videoSrc)}"></video>` : '')}
            ${videoSrc ? `
              <button
                type="button"
                class="result-notify-play"
                data-fullscreen-video="${escapeHtml(videoSrc)}"
                data-video-title="${escapeHtml(title)}"
                data-video-cover="${escapeHtml(coverSrc || '')}"
                data-video-user-generated-key="${escapeHtml(userGeneratedKey)}"
                data-video-user-generated-preview-key="${escapeHtml(userGeneratedPreviewKey)}"
                data-video-user-generated-cover-key="${escapeHtml(userGeneratedCoverKey)}"
                aria-label="播放 ${escapeHtml(title)}"
              ><span aria-hidden="true"></span></button>
            ` : ''}
          </div>
          <div class="result-notify-meta">
            <div class="result-notify-title" title="${escapeHtml(title)}">${renderHoverScrollText(title)}</div>
            <div class="result-notify-sub" title="${escapeHtml(subtitle)}">${renderHoverScrollText(subtitle)}</div>
          </div>
        </div>
      `;
    }

    function renderHoverScrollText(value, threshold = 10) {
      const text = String(value || '');
      const className = Array.from(text).length > threshold ? ' class="hover-scroll-track"' : '';
      return `<span${className}>${escapeHtml(text)}</span>`;
    }

    function resultNotifyRatioClass(item = {}) {
      const [width, height] = buildResultRatioLabel(item).split(':').map(Number);
      return width >= height ? 'ratio-landscape' : 'ratio-portrait';
    }

    function buildProgressStatusResultItem(item, index, progressItems = []) {
      const rawStatus = String(item?.status || '').trim();
      const postProcessing = isPostProcessingProgressItem(item);
      const status = postProcessing ? 'archiving' : rawStatus;
      const historicalSnapshot = Boolean(item?.historicalSnapshot);
      const videoIndex = Number(item?.videoIndex || 0) || index + 1;
      const title = cleanDisplayText(item?.title, `视频 ${videoIndex}`);
      const waiting = !historicalSnapshot && isTailFrameWaitingProgressItem(progressItems, index);
      const stage = waiting ? '等待中' : formatGenerationProgressStatus(item);
      return {
        __progressStatus: true,
        videoIndex,
        title,
        videoPrompt: resultVideoPrompt(item, index),
        ratio: buildResultRatioLabel(item),
        stage,
        status,
        error: item?.error || '',
        generationReasons: item?.generationReasons || '',
        statusLabel: item?.statusLabel || '',
        jobStatus: item?.jobStatus || '',
        providerStatus: item?.providerStatus || '',
        generationBatchId: item?.generationBatchId || '',
        tailFramePreviewUrl: item?.tailFramePreviewUrl || '',
        segmentStatus: Array.isArray(item?.segmentStatus) ? item.segmentStatus : [],
        percent: generationProgressPercent(item),
        pending: !historicalSnapshot && (postProcessing || !isTerminalProgressStatus(status)),
        waiting,
        historicalSnapshot,
        hasLocalAsset: item?.hasLocalAsset !== false,
      };
    }

    function isTailFrameWaitingProgressItem(items, index) {
      if (!state.generationMode?.tailFrameChaining || !Array.isArray(items)) return false;
      const activeIndex = items.findIndex((item) => !isTerminalProgressStatus(item?.status));
      return activeIndex >= 0 && index > activeIndex && !isTerminalProgressStatus(items[index]?.status);
    }

    function buildProgressSegmentSummary(segments) {
      if (!Array.isArray(segments) || !segments.length) return '';
      return segments
        .map((segment) => {
          const label = String(segment?.segmentLabel || (segment?.segmentIndex ? `片段 ${segment.segmentIndex}` : '')).trim();
          if (!label) return '';
          const status = String(segment?.status || '').trim();
          const providerStatus = String(segment?.providerStatus || '').trim();
          const raw = String(segment?.statusLabel || '').trim();
          let text = raw.replace(new RegExp(`^${label}[：:\\s]*`), '').trim();
          if (!text) {
            if (status === 'succeeded') text = '已完成';
            else if (status === 'failed') text = '失败';
            else if (providerStatus) text = providerStatus;
            else if (status) text = status;
            else text = '处理中';
          }
          return `${label.replace(/\s+/g, '')} ${text}`;
        })
        .filter(Boolean)
        .slice(0, 4)
        .join(' · ');
    }

    function buildProgressStatusResultCardMarkup(item, index, context = {}) {
      const status = String(item?.status || '').trim();
      const title = cleanDisplayText(item?.title, `视频 ${index + 1}`);
      const stage = item?.stage || '生成中';
      const segmentSummary = buildProgressSegmentSummary(item?.segmentStatus);
      const percent = Math.max(0, Math.min(100, Number(item?.percent) || 0));
      const isSkipped = status === 'skipped';
      const isFailed = status === 'failed' || stage === '生成失败';
      const isDeletedOrMissing = status === 'deleted' || (status === 'succeeded' && !item?.hasLocalAsset);
      const ratioLabel = buildResultRatioLabel(item);
      if (status === 'preparing_tail_frame') {
        return `
          <div class="result-notify-card ${resultNotifyRatioClass(item)}">
            <div class="result-notify-preview">
              ${renderResultVideoPromptButton(item, index)}
              <div class="result-notify-play waiting-placeholder" aria-hidden="true"><span></span></div>
              <div class="result-notify-progress"><span style="--progress-width: 0%"></span></div>
            </div>
            <div class="result-notify-meta">
              <div class="result-notify-title">${renderHoverScrollText(title)}</div>
              <div class="result-notify-sub">正在准备尾帧，尚未提交生成</div>
            </div>
          </div>
        `;
      }
      if (status === 'awaiting_tail_frame_continue') {
        const previewUrl = String(item?.tailFramePreviewUrl || '').trim();
        const generationBatchId = String(item?.generationBatchId || '').trim();
        return `
          <div class="result-notify-card manual-tail-frame ${resultNotifyRatioClass(item)}">
            <div class="result-notify-preview">
              ${renderResultVideoPromptButton(item, index)}
              ${previewUrl ? `<img alt="${escapeHtml(title)}的尾帧参考图" src="${escapeHtml(previewUrl)}">` : ''}
              <div class="manual-tail-frame-actions">
                <button type="button" data-tail-frame-continue="${item.videoIndex}" data-generation-batch-id="${escapeHtml(generationBatchId)}">继续</button>
                <button type="button" class="secondary" data-tail-frame-refresh="${item.videoIndex}" data-generation-batch-id="${escapeHtml(generationBatchId)}">刷新尾帧</button>
              </div>
              <div class="result-notify-progress"><span style="--progress-width: 0%"></span></div>
            </div>
            <div class="result-notify-meta">
              <div class="result-notify-title">${renderHoverScrollText(title)}</div>
              <div class="result-notify-sub">尾帧已传入，等待继续</div>
            </div>
          </div>
        `;
      }
      if (isDeletedOrMissing) {
        return `
          <div class="result-notify-card deleted ${resultNotifyRatioClass(item)}" title="已生成，文件已删除">
            <div class="result-notify-preview">
              ${renderResultVideoPromptButton(item, index)}
              <div class="result-notify-deleted-mark" aria-hidden="true">已删除</div>
            </div>
            <div class="result-notify-meta">
              <div class="result-notify-title">${renderHoverScrollText(title)}</div>
              <div class="result-notify-sub">已生成，文件已删除</div>
            </div>
          </div>
        `;
      }
      if (isSkipped) {
        const rawLabel = String(item?.statusLabel || stage || '').trim();
        const cancelled = rawLabel.includes('取消') || rawLabel.includes('终止');
        const primary = cancelled ? rawLabel : '生成失败';
        const rawReason = getGenerationFailureRawReason(item);
        const inheritedReason = !cancelled && isNoUpstreamFailureReason(rawReason)
          ? String(context?.sharedFailureReason || '').trim()
          : '';
        const effectiveReason = inheritedReason || rawReason;
        const friendlyReason = effectiveReason ? humanizeGenerationFailureReason(effectiveReason) : '';
        const fallbackReason = cancelled ? primary : '这条未提交给生成服务；没有上游返回。';
        const tooltipReason = friendlyReason || fallbackReason || primary;
        const badgeReason = cancelled ? primary : summarizeGenerationFailureReason(tooltipReason);
        return `
          <div class="result-notify-card failed ${resultNotifyRatioClass(item)}">
            <div class="result-notify-preview" title="${escapeHtml(tooltipReason)}">
              ${renderResultVideoPromptButton(item, index)}
              <div class="result-notify-failed-mark reason">${escapeHtml(badgeReason)}</div>
              <div class="result-notify-progress"><span style="--progress-width: 100%"></span></div>
            </div>
            <div class="result-notify-meta">
              <div class="result-notify-title">${renderHoverScrollText(title)}</div>
              <div class="result-notify-sub">${escapeHtml(primary)}</div>
            </div>
          </div>
        `;
      }
      if (isFailed) {
        const rawReason = getGenerationFailureRawReason(item);
        const friendlyReason = rawReason ? humanizeGenerationFailureReason(rawReason) : '';
        const tooltipReason = friendlyReason || '生成失败';
        const badgeReason = summarizeGenerationFailureReason(tooltipReason);
        const failureStageLabel = getGenerationFailureStageLabel(item);
        return `
          <div class="result-notify-card failed ${resultNotifyRatioClass(item)}">
            <div class="result-notify-preview" title="${escapeHtml(tooltipReason)}">
              ${renderResultVideoPromptButton(item, index)}
              ${renderGenerationRetryButton(item)}
              <div class="result-notify-failed-mark reason">${escapeHtml(badgeReason)}</div>
              <div class="result-notify-progress"><span style="--progress-width: 100%"></span></div>
            </div>
            <div class="result-notify-meta">
              <div class="result-notify-title">${renderHoverScrollText(title)}</div>
              <div class="result-notify-sub">${escapeHtml(failureStageLabel)}</div>
            </div>
          </div>
        `;
      }
      const isTerminal = isTerminalProgressStatus(status) || Boolean(item?.historicalSnapshot);
      const historicalClass = item?.historicalSnapshot ? ' historical-placeholder' : '';
      const processingClass = isPostProcessingProgressStatus(status) ? ' processing-placeholder' : '';
      const waitingClass = item?.waiting ? ' waiting-placeholder' : '';
      return `
          <div class="result-notify-card ${resultNotifyRatioClass(item)}">
            <div class="result-notify-preview">
              ${renderResultVideoPromptButton(item, index)}
            <div class="result-notify-play${isTerminal ? ` terminal-placeholder${historicalClass}` : `${processingClass}${waitingClass}`}" aria-hidden="true"><span></span></div>
            <div class="result-notify-progress"><span${isTerminal || item?.waiting ? '' : ' class="pending"'} style="--progress-width: ${isTerminal ? 100 : (item?.waiting ? 0 : (percent || 46))}%"></span></div>
          </div>
          <div class="result-notify-meta">
            <div class="result-notify-title">${renderHoverScrollText(title)}</div>
            <div class="result-notify-sub">${renderHoverScrollText(segmentSummary ? `${stage} · ${segmentSummary}` : stage)}</div>
          </div>
        </div>
      `;
    }

    function buildPendingResultCardMarkup(index) {
      return `
        <div class="result-notify-card ${resultNotifyRatioClass()}">
          <div class="result-notify-preview">
            ${renderResultVideoPromptButton({}, index - 1)}
            <div class="result-notify-play" aria-hidden="true"><span></span></div>
            <div class="result-notify-progress"><span class="pending" style="--progress-width: 46%"></span></div>
          </div>
          <div class="result-notify-meta">
            <div class="result-notify-title">视频 ${escapeHtml(String(index))}</div>
            <div class="result-notify-sub">等待生成服务返回</div>
          </div>
        </div>
      `;
    }

    function renderGenerationRetryButton(item) {
      const videoIndex = Number(item?.videoIndex || 0);
      const generationBatchId = String(item?.generationBatchId || '').trim();
      if (videoIndex < 1) return '';
      return `<button type="button" class="result-notify-retry-button" data-retry-generation-video="${videoIndex}" data-generation-batch-id="${escapeHtml(generationBatchId)}" title="复用现有方案，必要时重新生成首帧">重试</button>`;
    }

    async function handleManualTailFrameAction(button, action) {
      const videoIndex = Number(button?.getAttribute(`data-tail-frame-${action}`) || 0);
      const generationBatchId = String(button?.getAttribute('data-generation-batch-id') || '').trim();
      const sessionId = String(state.activeId || '').trim();
      if (!sessionId || !generationBatchId || videoIndex < 1 || button.disabled) return;
      button.disabled = true;
      const originalText = button.textContent;
      button.textContent = action === 'continue' ? '启动中' : '刷新中';
      try {
        const res = await fetch(`/api/tail-frame-chain/${action}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionId, generationBatchId, videoIndex }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw new Error(data?.error || '尾帧操作失败');
        if (action === 'continue' && data?.generationBatchId) {
          persistGenerationRetryPendingState(sessionId, videoIndex, data.generationBatchId);
        } else {
          const preview = button.closest('.result-notify-preview')?.querySelector('img');
          if (preview && data?.tailFramePreviewUrl) preview.src = data.tailFramePreviewUrl;
          button.disabled = false;
          button.textContent = originalText;
          schedulePendingPoll(sessionId, 100);
        }
      } catch (error) {
        button.disabled = false;
        button.textContent = originalText;
        window.alert(error?.message || String(error));
      }
    }

    function resultVideoPrompt(item = {}, fallbackIndex = 0) {
      const direct = String(item.videoPrompt || item.prompt || '').trim();
      if (direct) return direct;
      const records = item?.generationMeta?.segmentRecords || item?.generationMeta?.segments || [];
      const segmentPrompt = (Array.isArray(records) ? records : [])
        .map((record) => String(record?.segmentPrompt || '').trim())
        .filter(Boolean)
        .join('\n\n');
      return segmentPrompt;
    }

    function renderResultVideoPromptButton(item, index) {
      const prompt = resultVideoPrompt(item, index);
      const videoIndex = Number(item?.videoIndex || 0) || index + 1;
      const title = cleanDisplayText(item?.videoTitle || item?.title, `视频 ${videoIndex}`);
      return `<button type="button" class="result-video-prompt-button"
        data-result-video-prompt="${escapeHtml(encodeURIComponent(prompt))}"
        data-result-video-index="${videoIndex}"
        data-result-video-prompt-title="${escapeHtml(title)}"
        aria-label="查看${escapeHtml(title)}的视频提示词" title="查看视频提示词">•••</button>`;
    }

    async function openResultVideoPromptModal(button) {
      const modal = document.getElementById('resultVideoPromptModal');
      const body = document.getElementById('resultVideoPromptBody');
      const title = document.getElementById('resultVideoPromptTitle');
      const copyButton = document.getElementById('resultVideoPromptCopyButton');
      if (!modal || !body || !title || !copyButton) return;
      const encoded = String(button?.getAttribute('data-result-video-prompt') || '');
      let prompt = '';
      try { prompt = decodeURIComponent(encoded); } catch (_error) { prompt = encoded; }
      title.textContent = button?.getAttribute('data-result-video-prompt-title') || '视频提示词';
      body.textContent = prompt || '正在读取实际提交给视频模型的最终提示词…';
      body.classList.toggle('is-empty', !prompt);
      copyButton.disabled = !prompt;
      copyButton.dataset.copyText = prompt;
      modal.classList.remove('hidden');
      if (prompt) return;
      const sessionId = String(state.activeId || '').trim();
      const videoIndex = Number(button?.getAttribute('data-result-video-index') || 0);
      try {
        const res = await fetch(`/api/generation/video-prompt?sessionId=${encodeURIComponent(sessionId)}&videoIndex=${videoIndex}`);
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.videoPrompt) throw new Error(data.error || '未找到最终提示词');
        prompt = String(data.videoPrompt).trim();
        body.textContent = prompt;
        body.classList.remove('is-empty');
        copyButton.disabled = false;
        copyButton.dataset.copyText = prompt;
        button.setAttribute('data-result-video-prompt', encodeURIComponent(prompt));
      } catch (error) {
        body.textContent = error?.message || '未找到该视频实际提交的最终提示词';
      }
    }

    function closeResultVideoPromptModal() {
      document.getElementById('resultVideoPromptModal')?.classList.add('hidden');
    }

    async function copyResultVideoPrompt() {
      const button = document.getElementById('resultVideoPromptCopyButton');
      const text = String(button?.dataset?.copyText || '').trim();
      if (!button || !text) return;
      await navigator.clipboard.writeText(text);
      const previous = button.textContent;
      button.textContent = '已复制';
      window.setTimeout(() => { button.textContent = previous; }, 1200);
    }

    function showGenerationRetryPendingCard(button) {
      const card = button?.closest?.('.result-notify-card');
      const preview = card?.querySelector?.('.result-notify-preview');
      if (!card || !preview) return () => {};
      const previousClassName = card.className;
      const previousMarkup = preview.innerHTML;
      const promptButtonMarkup = preview.querySelector('.result-video-prompt-button')?.outerHTML || '';
      card.classList.remove('failed');
      card.classList.add('is-retrying');
      preview.title = '正在重新生成';
      preview.innerHTML = `
        ${promptButtonMarkup}
        <div class="result-notify-play" aria-hidden="true"><span></span></div>
        <div class="result-notify-progress"><span class="pending" style="--progress-width: 46%"></span></div>
      `;
      return () => {
        card.className = previousClassName;
        preview.innerHTML = previousMarkup;
      };
    }

    function persistGenerationRetryPendingState(sessionId, videoIndex, generationBatchId) {
      const session = state.sessions.find((item) => item.id === sessionId);
      const last = session?.messages?.at?.(-1);
      if (!last?.payload || last.role !== 'assistant') return;
      const pending = last.payload.pendingStatus || {};
      const progress = pending.generationProgress || {};
      const items = Array.isArray(progress.items) ? progress.items : [];
      last.payload.meta = { ...(last.payload.meta || {}), operation: 'pending', continuationClosed: false };
      last.payload.generationBatchId = generationBatchId;
      last.payload.pendingStatus = normalizePendingStatusProgress({
        ...pending,
        status: 'pending',
        phase: 'generating',
        statusLabel: '视频生成中',
        generationBatchId,
        generationProgress: {
          ...progress,
          status: 'active',
          generationBatchId,
          items: items.map((item) => Number(item?.videoIndex || 0) === videoIndex
            ? { ...item, status: 'pending_submission', statusLabel: '正在重新生成', error: '', generationBatchId }
            : item),
        },
      });
      persistSessions();
      render();
      schedulePendingPoll(sessionId, 200);
    }

    async function retryFailedGenerationVideo(button) {
      const videoIndex = Number(button?.getAttribute('data-retry-generation-video') || 0);
      const generationBatchId = String(button?.getAttribute('data-generation-batch-id') || '').trim();
      const sessionId = String(state.activeId || '').trim();
      if (!sessionId || videoIndex < 1 || button.disabled) return;
      const restoreFailedCard = showGenerationRetryPendingCard(button);
      try {
        const res = await fetch('/api/generation/retry', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sessionId,
            generationBatchId,
            videoIndex,
            tailFrameChaining: !!state.generationMode?.tailFrameChaining,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        persistGenerationRetryPendingState(
          sessionId,
          videoIndex,
          String(data?.generationBatchId || generationBatchId).trim(),
        );
      } catch (error) {
        restoreFailedCard();
        window.alert(error?.message || String(error));
      }
    }

    function buildFailedResultCardMarkup(item, index) {
      const title = `视频 ${escapeHtml(String(item.index || index))}`;
      const rawReason = getGenerationFailureRawReason(item);
      const reason = humanizeGenerationFailureReason(rawReason || '生成失败');
      const badgeReason = summarizeGenerationFailureReason(reason);
      const failureStageLabel = getGenerationFailureStageLabel(item);
      const ratioLabel = buildResultRatioLabel(item);
      return `
        <div class="result-notify-card failed ${resultNotifyRatioClass(item)}">
          <div class="result-notify-preview" title="${escapeHtml(reason)}">
            ${renderResultVideoPromptButton(item, index - 1)}
            <div class="result-notify-failed-mark reason">${escapeHtml(badgeReason)}</div>
            <div class="result-notify-progress"><span style="--progress-width: 100%"></span></div>
          </div>
          <div class="result-notify-meta">
            <div class="result-notify-title">${title}</div>
            <div class="result-notify-sub">${escapeHtml(reason ? `${failureStageLabel} · ${reason}` : failureStageLabel)}</div>
          </div>
        </div>
      `;
    }

    function buildResultRatioLabel(item = {}) {
      const rawRatio = String(
        item?.request?.ratio
        || item?.generationMeta?.request?.ratio
        || item?.generationMeta?.ratio
        || item?.ratio
        || state.videoModelSettings?.ratio
        || '9:16'
      ).trim();
      const match = rawRatio.match(/^(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)$/);
      if (!match) return '9:16';
      const width = Number(match[1]);
      const height = Number(match[2]);
      if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
        return '9:16';
      }
      return `${stripTrailingZero(width)}:${stripTrailingZero(height)}`;
    }

    function stripTrailingZero(value) {
      return String(value).replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
    }

    function buildResultAspectStyle(item) {
      const ratio = String(item?.request?.ratio || item?.ratio || '').trim();
      const match = ratio.match(/^(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)$/);
      if (!match) return '--result-aspect-ratio: 9 / 16;';
      const width = Number(match[1]);
      const height = Number(match[2]);
      if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
        return '--result-aspect-ratio: 9 / 16;';
      }
      return `--result-aspect-ratio: ${width} / ${height};`;
    }

    function renderExpandedResultWall() {
      const gallery = buildAssetGalleryModel(state.assets);
      const groups = groupAssetsByDay(gallery.items);
      if (!groups.length) return `<div class="progress-results-note">${escapeHtml(gallery.emptyText)}</div>`;
      return `
        <div class="progress-results-note">${escapeHtml(gallery.summaryText)}</div>
        <div class="progress-results">
          ${groups.map((group) => `
            <div class="progress-result-group">
              <div class="progress-result-date">${escapeHtml(group.date)}</div>
              <div class="progress-result-wall">
                ${group.items.map((item) => `<div class="asset-card">${buildAssetCardMarkup(item)}</div>`).join('')}
              </div>
            </div>
          `).join('')}
        </div>
      `;
    }
