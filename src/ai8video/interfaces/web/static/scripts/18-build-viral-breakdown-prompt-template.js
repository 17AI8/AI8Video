
    function buildViralBreakdownPromptTemplate() {
      const values = arguments[0] || {};
      const platformLabelMap = {
        douyin: '抖音',
        kuaishou: '快手',
        wechat_channels: '视频号',
        xiaohongshu: '小红书',
      };
      const videoLink = String(values.videoLink || '').trim();
      const track = String(values.track || '').trim();
      const platform = platformLabelMap[String(values.platform || '').trim()] || '抖音';
      const reuseDirection = String(values.reuseDirection || '').trim();
      const notes = String(values.notes || '').trim();
      const promptLines = [
        '请帮我做一次爆款拆解，输出要直接可执行。',
        '',
        '分析对象：',
        `1. 视频链接 / 文件：${videoLink}`,
        `2. 赛道：${track}`,
        `3. 目标平台：${platform}`,
        `4. 我想复用的方向：${reuseDirection}`,
      ];
      if (notes) {
        promptLines.push('', '补充说明：', notes);
      }
      promptLines.push(
        '',
        '请按下面结构输出：',
        '1. 一句话总结这个内容为什么容易爆',
        '2. 目标人群与核心痛点',
        '3. 开头钩子拆解',
        '4. 内容结构拆解（分段说明）',
        '5. 情绪与节奏设计',
        '6. 可复用的话术公式',
        '7. 适合我继续仿写的 3 个选题',
        '8. 按AI8video 可直接生成的方式，给我一版可拍短视频脚本'
      );
      return promptLines.join('\n');
    }

    function getViralBreakdownModalValues() {
      return {
        videoLink: document.getElementById('viralBreakdownVideoLink')?.value || '',
        track: document.getElementById('viralBreakdownTrack')?.value || '',
        platform: document.getElementById('viralBreakdownPlatform')?.value || 'douyin',
        reuseDirection: document.getElementById('viralBreakdownReuseDirection')?.value || '',
        notes: document.getElementById('viralBreakdownNotes')?.value || '',
      };
    }

    function updateViralBreakdownModalPreview() {
      const values = getViralBreakdownModalValues();
      const platformLabelMap = {
        douyin: '抖音',
        kuaishou: '快手',
        wechat_channels: '视频号',
        xiaohongshu: '小红书',
      };
      const stageTitle = document.getElementById('viralBreakdownStageTitle');
      const stageMeta = document.getElementById('viralBreakdownStageMeta');
      const preview = document.getElementById('viralBreakdownPromptPreview');
      const platformPill = document.getElementById('viralBreakdownPlatformPill');
      const focusPill = document.getElementById('viralBreakdownFocusPill');
      const platformLabel = platformLabelMap[String(values.platform || '').trim()] || '抖音';
      const trackLabel = String(values.track || '').trim() || '待定赛道';
      const reuseDirectionLabel = String(values.reuseDirection || '').trim() || '待补充复用方向';
      if (stageTitle) {
        stageTitle.textContent = `${trackLabel} 爆款拆解工作台`;
      }
      if (stageMeta) {
        const videoLinkLabel = String(values.videoLink || '').trim() || '待补充视频链接 / 文件';
        stageMeta.textContent = `${platformLabel} · ${reuseDirectionLabel} · ${videoLinkLabel}`;
      }
      if (platformPill) {
        platformPill.textContent = platformLabel;
      }
      if (focusPill) {
        focusPill.textContent = reuseDirectionLabel;
      }
      if (preview) {
        preview.textContent = buildViralBreakdownPromptTemplate(values);
      }
    }

    function resetHotRadarGeneratedContent() {
      state.hotRadar.summaryText = '';
      state.hotRadar.promptText = '';
    }

    function buildHotRadarTopicCardMarkup(item, hotRadar) {
      const id = String(item?.id || '');
      const selected = id === String(hotRadar.selectedTopicId || '');
      const expanded = id === String(hotRadar.expandedTopicId || '');
      const rank = Number(item?.rank || 0) || '-';
      const title = escapeHtml(String(item?.title || '未命名热点'));
      const sourceName = escapeHtml(String(item?.sourceName || '未知来源'));
      const heat = escapeHtml(String(item?.heat || '-'));
      const description = escapeHtml(String(item?.description || '暂无摘要'));
      const unavailableReason = String(hotRadar.unavailableReason || '').trim();
      const summaryText = expanded
        ? escapeHtml(String(
          hotRadar.summaryText || hotRadar.promptText || unavailableReason
          || '展开后可生成摘要、拍摄角度和 AI8video 业务切入点。'
        ))
        : '';
      const summarizing = expanded && !!hotRadar.summarizing;
      const promptBuilding = expanded && !!hotRadar.promptBuilding;
      const canFill = expanded && !!(hotRadar.promptText || hotRadar.summaryText);
      return `<div class="hot-radar-topic-card${selected ? ' active' : ''}${expanded ? ' is-expanded' : ''}" role="button" tabindex="0" data-hot-radar-topic-id="${escapeHtml(id)}" aria-expanded="${expanded ? 'true' : 'false'}"><div class="hot-radar-topic-title">#${rank} ${title}</div><div class="hot-radar-topic-details"><div class="hot-radar-topic-details-inner"><div class="hot-radar-topic-preview"><div class="hot-radar-topic-meta"><span class="hot-radar-topic-meta-item">${sourceName}</span><span class="hot-radar-topic-meta-item">热度 ${heat}</span></div><div class="hot-radar-topic-desc">${description}</div><div class="hot-radar-summary-output" data-hot-radar-summary-output>${summaryText}</div></div><div class="hot-radar-topic-actions"><button type="button" class="hot-radar-primary-button" data-hot-radar-action="summary"${summarizing ? ' disabled' : ''}>${summarizing ? '摘要中...' : 'AI 摘要'}</button><button type="button" class="hot-radar-ghost-button" data-hot-radar-action="prompt"${promptBuilding ? ' disabled' : ''}>${promptBuilding ? '生成中...' : '转成拆解提示词'}</button><button type="button" class="hot-radar-ghost-button" data-hot-radar-action="fill"${canFill ? '' : ' disabled'}>填入对话框</button></div></div></div></div>`;
    }

    function buildHotRadarTopicListMarkup(items, hotRadar, twoColumns) {
      if (!items.length) {
        return `<div class="viral-breakdown-empty">${hotRadar.loading ? '正在拉取热榜...' : '暂无热点，尝试刷新热榜。'}</div>`;
      }
      // 单双列都包进 column：卡片作 grid 直子项时 overflow:hidden 会把标题行压成 0 高。
      if (!twoColumns) {
        const cards = items.map((item) => buildHotRadarTopicCardMarkup(item, hotRadar)).join('');
        return `<div class="hot-radar-topic-column" data-hot-radar-column="0">${cards}</div>`;
      }
      const left = items.filter((_, index) => index % 2 === 0)
        .map((item) => buildHotRadarTopicCardMarkup(item, hotRadar)).join('');
      const right = items.filter((_, index) => index % 2 === 1)
        .map((item) => buildHotRadarTopicCardMarkup(item, hotRadar)).join('');
      return `<div class="hot-radar-topic-column" data-hot-radar-column="0">${left}</div><div class="hot-radar-topic-column" data-hot-radar-column="1">${right}</div>`;
    }

    function syncHotRadarTopicCardStates(topicList, hotRadar) {
      if (!topicList) return;
      topicList.querySelectorAll('.hot-radar-topic-card[data-hot-radar-topic-id]').forEach((card) => {
        const id = String(card.getAttribute('data-hot-radar-topic-id') || '');
        const selected = id === String(hotRadar.selectedTopicId || '');
        const expanded = id === String(hotRadar.expandedTopicId || '');
        card.classList.toggle('active', selected);
        card.classList.toggle('is-expanded', expanded);
        card.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      });
    }

    function renderHotRadarDetailPane(hotRadar = state.hotRadar || {}) {
      const topicList = document.getElementById('hotRadarTopicList');
      if (!topicList) return;
      const expandedId = String(hotRadar.expandedTopicId || '');
      const selectedTopic = getSelectedHotRadarTopic();
      const unavailableReason = String(hotRadar.unavailableReason || '').trim();
      const summaryText = String(
        hotRadar.summaryText || hotRadar.promptText || unavailableReason
        || '展开后可生成摘要、拍摄角度和 AI8video 业务切入点。'
      );
      topicList.querySelectorAll('.hot-radar-topic-card[data-hot-radar-topic-id]').forEach((card) => {
        const id = String(card.getAttribute('data-hot-radar-topic-id') || '');
        const expanded = id === expandedId;
        const output = card.querySelector('[data-hot-radar-summary-output]');
        const summaryButton = card.querySelector('[data-hot-radar-action="summary"]');
        const promptButton = card.querySelector('[data-hot-radar-action="prompt"]');
        const fillButton = card.querySelector('[data-hot-radar-action="fill"]');
        if (output) output.textContent = expanded ? summaryText : '';
        if (summaryButton) {
          summaryButton.disabled = !expanded || !selectedTopic || !!hotRadar.summarizing;
          summaryButton.textContent = hotRadar.summarizing && expanded ? '摘要中...' : 'AI 摘要';
        }
        if (promptButton) {
          promptButton.disabled = !expanded || !selectedTopic || !!hotRadar.promptBuilding;
          promptButton.textContent = hotRadar.promptBuilding && expanded ? '生成中...' : '转成拆解提示词';
        }
        if (fillButton) {
          fillButton.disabled = !expanded || !(hotRadar.promptText || hotRadar.summaryText);
        }
      });
    }

    function selectHotRadarTopicCard(topicCard) {
      const hotRadar = state.hotRadar;
      const topicId = String(topicCard?.getAttribute?.('data-hot-radar-topic-id') || '');
      if (!topicId) return;
      const collapsing = String(hotRadar.expandedTopicId || '') === topicId
        && topicCard.classList.contains('is-expanded');
      hotRadar.selectedTopicId = topicId;
      hotRadar.expandedTopicId = collapsing ? '' : topicId;
      if (!collapsing) resetHotRadarGeneratedContent();
      persistHotRadarViewState(hotRadar);
      const topicList = document.getElementById('hotRadarTopicList');
      if (topicList?.contains?.(topicCard)) {
        syncHotRadarTopicCardStates(topicList, hotRadar);
        renderHotRadarDetailPane(hotRadar);
        return;
      }
      renderHotRadarWorkbench();
    }

    function getSelectedHotRadarTopic() {
      const items = Array.isArray(state.hotRadar?.items) ? state.hotRadar.items : [];
      const selectedTopicId = String(state.hotRadar?.selectedTopicId || '');
      return items.find((item) => String(item?.id || '') === selectedTopicId) || items[0] || null;
    }

    function formatHotRadarUpdatedAt(value) {
      const timestamp = Date.parse(String(value || ''));
      if (!Number.isFinite(timestamp)) return '尚未更新';
      const formatted = new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date(timestamp)).replaceAll('/', '-');
      return `最后更新：${formatted}`;
    }

    function openHotRadarSourceManager() {
      state.hotRadar.sourceDrafts = (state.hotRadar.sources || [])
        .filter((item) => String(item.type || '') === 'custom')
        .map((item) => ({ id: item.id, name: item.name, url: item.url, category: item.category || '自定义', parser: item.parser || 'xml' }));
      renderHotRadarSourceManager();
      document.getElementById('hotRadarSourceManagerModal')?.classList.remove('hidden');
    }

    function closeHotRadarSourceManager() {
      document.getElementById('hotRadarSourceManagerModal')?.classList.add('hidden');
    }

    function renderHotRadarSourceManager() {
      const list = document.getElementById('hotRadarSourceManagerList');
      if (!list) return;
      const customRows = state.hotRadar.sourceDrafts.map((item) => `<div class="hot-radar-source-manager-item"><strong>${escapeHtml(String(item.name || item.id))}</strong><small title="${escapeHtml(String(item.url || ''))}">${escapeHtml(String(item.url || ''))}</small><button type="button" class="button-danger" data-remove-hot-radar-source="${escapeHtml(String(item.id || ''))}">删除</button></div>`).join('');
      list.innerHTML = customRows || '<div class="modal-sub">尚未添加自定义数据源。</div>';
    }

    function addHotRadarCustomSourceDraft() {
      const nameInput = document.getElementById('hotRadarCustomSourceName');
      const idInput = document.getElementById('hotRadarCustomSourceId');
      const urlInput = document.getElementById('hotRadarCustomSourceUrl');
      const errorBox = document.getElementById('hotRadarSourceManagerError');
      const name = String(nameInput?.value || '').trim();
      const id = String(idInput?.value || '').trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
      const url = String(urlInput?.value || '').trim();
      if (!name || !id || !/^https?:\/\//i.test(url)) {
        if (errorBox) errorBox.textContent = '请填写名称、英文标识和有效的 HTTP(S) 订阅地址。';
        return;
      }
      if ((state.hotRadar.sources || []).some((item) => String(item.id) === id) || state.hotRadar.sourceDrafts.some((item) => String(item.id) === id)) {
        if (errorBox) errorBox.textContent = `数据源标识已存在：${id}`;
        return;
      }
      state.hotRadar.sourceDrafts.push({ id, name, url, category: '自定义', parser: 'xml' });
      if (nameInput) nameInput.value = '';
      if (idInput) idInput.value = '';
      if (urlInput) urlInput.value = '';
      if (errorBox) errorBox.textContent = '';
      renderHotRadarSourceManager();
    }

    async function saveHotRadarCustomSources() {
      const saveButton = document.getElementById('hotRadarSourceManagerSaveButton');
      const errorBox = document.getElementById('hotRadarSourceManagerError');
      if (saveButton) saveButton.disabled = true;
      try {
        const res = await fetch('/api/hot-topics/sources', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ feeds: state.hotRadar.sourceDrafts }) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        state.hotRadar.sources = Array.isArray(data.sources) ? data.sources : [];
        state.hotRadar.categories = data.categories && typeof data.categories === 'object' ? data.categories : {};
        closeHotRadarSourceManager();
        await refreshHotRadarTopics({ forceRefresh: true });
      } catch (error) {
        if (errorBox) errorBox.textContent = error?.message || String(error);
      } finally {
        if (saveButton) saveButton.disabled = false;
      }
    }

    function renderHotRadarSourceSelect(hotRadar, sources, sourceSelect) {
      if (sourceSelect) {
        sourceSelect.innerHTML = '<option value="">全部来源</option>' + sources.map((source) => {
          const available = source.available !== false;
          const disabled = available ? '' : ' disabled';
          return `<option value="${escapeHtml(String(source.id || ''))}"${disabled}>${escapeHtml(String(source.name || source.id || '未知源'))}</option>`;
        }).join('') + '<option value="__add_source__">＋ 新增数据源…</option>';
        sourceSelect.value = String(hotRadar.selectedSourceId || '');
        sourceSelect.disabled = !!hotRadar.loading;
      }
    }

    function renderHotRadarWorkbench() {
      const hotRadar = state.hotRadar || {};
      const sourceSelect = document.getElementById('hotRadarSourceSelect');
      const listMeta = document.getElementById('hotRadarListMeta');
      const topicList = document.getElementById('hotRadarTopicList');
      const keywordInput = document.getElementById('hotRadarKeywordInput');
      const columnToggleButton = document.getElementById('hotRadarColumnToggleButton');
      const sources = Array.isArray(hotRadar.sources) ? hotRadar.sources : [];
      const unavailableReason = String(hotRadar.unavailableReason || '').trim();
      if (keywordInput && document.activeElement !== keywordInput) keywordInput.value = String(hotRadar.keyword || '');
      renderHotRadarSourceSelect(hotRadar, sources, sourceSelect);
      if (listMeta) {
        const updatedLabel = formatHotRadarUpdatedAt(hotRadar.updatedAt);
        listMeta.textContent = hotRadar.loading
          ? '正在更新热点…'
          : hotRadar.error || unavailableReason || `${updatedLabel}${hotRadar.stale ? ' · 缓存' : ''}`;
      }
      if (topicList) {
        const items = Array.isArray(hotRadar.items) ? hotRadar.items : [];
        if (
          hotRadar.expandedTopicId
          && !items.some((item) => String(item?.id || '') === String(hotRadar.expandedTopicId || ''))
        ) {
          hotRadar.expandedTopicId = '';
        }
        const preferTwoColumns = Number(hotRadar.columnCount) === 2;
        const twoColumns = preferTwoColumns && window.matchMedia('(min-width: 901px)').matches;
        topicList.classList.toggle('is-two-columns', twoColumns);
        topicList.classList.toggle('is-switching', !!hotRadar.loading);
        topicList.setAttribute('aria-busy', hotRadar.loading ? 'true' : 'false');
        topicList.innerHTML = buildHotRadarTopicListMarkup(items, hotRadar, twoColumns);
      }
      if (columnToggleButton) {
        const twoColumns = Number(hotRadar.columnCount) === 2;
        columnToggleButton.textContent = twoColumns ? '双列' : '单列';
        columnToggleButton.setAttribute('aria-pressed', twoColumns ? 'true' : 'false');
        columnToggleButton.title = twoColumns ? '切换为单列显示' : '切换为双列显示';
      }
      renderHotRadarDetailPane(hotRadar);
    }

    async function refreshHotRadarTopics(options = {}) {
      const hotRadar = state.hotRadar;
      const requestSeq = Number(hotRadar.requestSeq || 0) + 1;
      hotRadar.requestSeq = requestSeq;
      hotRadar.loading = true;
      hotRadar.error = '';
      hotRadar.unavailableReason = '';
      hotRadar.stale = false;
      hotRadar.realDataAvailable = false;
      hotRadar.expandedTopicId = '';
      hotRadar.notice = '正在同步公开热点数据...';
      renderHotRadarWorkbench();
      const params = new URLSearchParams();
      if (hotRadar.selectedSourceId) params.set('sources', hotRadar.selectedSourceId);
      if (hotRadar.keyword) params.set('keyword', hotRadar.keyword);
      if (options.forceRefresh) params.set('refresh', '1');
      try {
        const res = await fetch(`/api/hot-topics?${params.toString()}`);
        const data = await res.json().catch(() => ({}));
        if (requestSeq !== hotRadar.requestSeq) return;
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        hotRadar.sources = Array.isArray(data.sources) ? data.sources : [];
        hotRadar.categories = data.categories && typeof data.categories === 'object' ? data.categories : {};
        hotRadar.items = Array.isArray(data.items) ? data.items : [];
        hotRadar.updatedAt = String(data.updatedAt || '');
        hotRadar.errors = Array.isArray(data.errors) ? data.errors : [];
        hotRadar.fetchRouteLabel = String(data.fetchRouteLabel || '公开数据源');
        hotRadar.stale = !!data.stale;
        hotRadar.realDataAvailable = !!data.realDataAvailable;
        hotRadar.unavailableReason = String(data.unavailableReason || '');
        if (!hotRadar.items.some((item) => String(item?.id || '') === String(hotRadar.selectedTopicId || ''))) {
          hotRadar.selectedTopicId = String(hotRadar.items[0]?.id || '');
        }
        persistHotRadarViewState(hotRadar);
        hotRadar.loading = false;
        hotRadar.notice = hotRadar.unavailableReason
          ? hotRadar.unavailableReason
          : hotRadar.stale
            ? `已回退到最近一次真实缓存，共 ${hotRadar.items.length} 条热点`
            : `已同步 ${hotRadar.items.length} 条热点`;
        persistHotRadarSnapshot(hotRadar);
        renderHotRadarWorkbench();
      } catch (error) {
        if (requestSeq !== hotRadar.requestSeq) return;
        hotRadar.loading = false;
        hotRadar.notice = '';
        throw error;
      }
    }

    async function summarizeSelectedHotRadarTopic() {
      const topic = getSelectedHotRadarTopic();
      if (!topic) return;
      const hotRadar = state.hotRadar;
      hotRadar.summarizing = true;
      hotRadar.error = '';
      hotRadar.summaryText = '正在调用文本模型生成热点摘要...';
      renderHotRadarWorkbench();
      try {
        const res = await fetch('/api/hot-topics/summary', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ topic }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        hotRadar.summaryText = String(data.text || '');
      } finally {
        hotRadar.summarizing = false;
        renderHotRadarWorkbench();
      }
    }

    async function buildSelectedHotRadarPrompt() {
      const topic = getSelectedHotRadarTopic();
      if (!topic) return;
      const hotRadar = state.hotRadar;
      hotRadar.promptBuilding = true;
      hotRadar.error = '';
      renderHotRadarWorkbench();
      try {
        const res = await fetch('/api/hot-topics/to-prompt', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ topic }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        hotRadar.promptText = String(data.prompt || '');
        hotRadar.summaryText = hotRadar.promptText;
      } finally {
        hotRadar.promptBuilding = false;
        renderHotRadarWorkbench();
      }
    }

    function fillHotRadarPromptIntoComposer() {
      const text = String(state.hotRadar?.promptText || state.hotRadar?.summaryText || '').trim();
      if (!text) return;
      const messageEditor = document.getElementById('messageEditor');
      const messageInput = document.getElementById('messageInput');
      if (messageEditor) messageEditor.textContent = text;
      if (messageInput) messageInput.value = text;
      closeHotRadarModal();
      messageEditor?.focus?.();
    }

    function openHotRadarModal() {
      const modal = document.getElementById('hotRadarModal');
      if (!modal) return;
      modal.classList.remove('hidden');
      renderHotRadarWorkbench();
      const needsFilteredRestore = !!state.hotRadar.selectedSourceId || !!state.hotRadar.keyword;
      if (!Array.isArray(state.hotRadar.items) || !state.hotRadar.items.length || needsFilteredRestore) {
        refreshHotRadarTopics().catch((error) => {
          console.error(error);
          state.hotRadar.loading = false;
          state.hotRadar.error = error?.message || String(error);
          renderHotRadarWorkbench();
        });
      }
    }

    function closeHotRadarModal() {
      document.getElementById('hotRadarModal')?.classList.add('hidden');
    }

    function openViralBreakdownModal() {
      const modal = document.getElementById('viralBreakdownModal');
      if (!modal) return;
      modal.classList.remove('hidden');
      state.viralBreakdown.activeTab = 'grid';
      state.viralBreakdown.loading = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在读取爆款拆解归档...';
      renderViralBreakdownWorkbench();
      refreshViralBreakdownWorkspace({ keepSelection: true }).catch((error) => {
        console.error(error);
        state.viralBreakdown.loading = false;
        state.viralBreakdown.error = error?.message || String(error);
        renderViralBreakdownWorkbench();
      });
    }

    function closeViralBreakdownModal() {
      closeViralBreakdownVideoMenu();
      document.getElementById('viralBreakdownModal')?.classList.add('hidden');
    }

    function humanizeByteSize(size) {
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let value = Number(size || 0) || 0;
      for (const unit of units) {
        if (value < 1024 || unit === units[units.length - 1]) {
          return unit === 'B' ? `${Math.round(value)} ${unit}` : `${value.toFixed(1)} ${unit}`;
        }
        value /= 1024;
      }
      return '0 B';
    }

    function getSelectedViralBreakdownItem() {
      const items = Array.isArray(state.viralBreakdown?.items) ? state.viralBreakdown.items : [];
      if (!items.length) return null;
      return items.find((item) => String(item?.videoKey || '') === String(state.viralBreakdown.selectedVideoKey || '')) || items[0] || null;
    }

    function getViralBreakdownTranscriptDraft(videoKey, fallbackText = '') {
      const normalizedVideoKey = String(videoKey || '').trim();
      if (!normalizedVideoKey) return String(fallbackText || '');
      const drafts = state.viralBreakdown?.transcriptDrafts || {};
      if (Object.prototype.hasOwnProperty.call(drafts, normalizedVideoKey)) {
        return String(drafts[normalizedVideoKey] || '');
      }
      return String(fallbackText || '');
    }

    function getViralBreakdownScriptGuessDraft(videoKey) {
      const normalizedVideoKey = String(videoKey || '').trim();
      if (!normalizedVideoKey) return '';
      const drafts = state.viralBreakdown?.scriptGuessDrafts || {};
      if (Object.prototype.hasOwnProperty.call(drafts, normalizedVideoKey)) {
        return String(drafts[normalizedVideoKey] || '');
      }
      return '';
    }

    async function refreshViralBreakdownWorkspace(options = {}) {
      const keepSelection = options.keepSelection !== false;
      const selectedVideoKeyBeforeRefresh = keepSelection ? String(state.viralBreakdown.selectedVideoKey || '') : '';
      const res = await fetch('/api/viral-breakdown?limit=200');
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || '读取爆款拆解归档失败');
      }
      state.viralBreakdown.root = String(data?.root || '');
      state.viralBreakdown.itemCount = Number(data?.itemCount || 0) || 0;
      state.viralBreakdown.sizeBytes = Number(data?.sizeBytes || 0) || 0;
      state.viralBreakdown.sizeLabel = String(data?.sizeLabel || humanizeByteSize(data?.sizeBytes || 0));
      state.viralBreakdown.archiveDisplay = String(data?.archiveDisplay || `${state.viralBreakdown.itemCount} 个视频 · ${state.viralBreakdown.sizeLabel}`);
      state.viralBreakdown.items = Array.isArray(data?.items) ? data.items : [];
      hydrateViralBreakdownScriptDraftsFromItems(state.viralBreakdown.items);
      const hasSelectedVideoAfterRefresh = state.viralBreakdown.items.some((item) => String(item?.videoKey || '') === selectedVideoKeyBeforeRefresh);
      state.viralBreakdown.selectedVideoKey = hasSelectedVideoAfterRefresh
        ? selectedVideoKeyBeforeRefresh
        : String(state.viralBreakdown.items[0]?.videoKey || '');
      state.viralBreakdown.loading = false;
      if (!state.viralBreakdown.error) {
        state.viralBreakdown.notice = '';
      }
      syncViralBreakdownScriptResumeAvailability();
      renderViralBreakdownWorkbench();
    }
