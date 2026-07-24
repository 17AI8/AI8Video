    function getScriptKnowledgeActiveTab() {
      const tab = String(state.scriptKnowledge.activeTab || 'tree').trim();
      return ['tree', 'edit', 'source'].includes(tab) ? tab : 'tree';
    }

    function buildScriptKnowledgeCardMarkup(item) {
      const active = Number(item?.id || 0) === Number(state.scriptKnowledge.selectedId || 0);
      const title = item?.title || item?.stem || item?.name || '未命名剧本';
      const preview = item?.matchedExcerpt || item?.summary || item?.preview || '暂无摘要';
      const tags = buildScriptKnowledgeTagsMarkup(item?.tags || []);
      const score = Number(item?.score || 0);
      const scoreCopy = score > 0 ? `<span>匹配 ${score.toFixed(2)}</span>` : '';
      return `
        <button type="button" class="script-knowledge-list-card${active ? ' is-active' : ''}"
          data-script-knowledge-document="${Number(item?.id || 0)}">
          <span class="script-knowledge-list-title">${escapeHtml(title)}</span>
          ${tags}
          <span class="script-knowledge-list-preview">${escapeHtml(normalizeMaterialPreview(preview))}</span>
          <span class="script-knowledge-list-foot">
            <span>${Number(item?.sectionCount || 0)} 个叶节点</span>
            <span>${escapeHtml(formatFileSize(item?.sizeBytes || 0) || '0 B')}</span>
            ${scoreCopy}
          </span>
        </button>
      `;
    }

    function buildScriptKnowledgeTagsMarkup(tags) {
      const values = Array.isArray(tags) ? tags.filter(Boolean).slice(0, 6) : [];
      if (!values.length) return '<span class="script-knowledge-tags is-empty"><span class="script-knowledge-tag is-empty">未设标签</span></span>';
      return `<span class="script-knowledge-tags">${values.map((tag) => `<span class="script-knowledge-tag">${escapeHtml(tag)}</span>`).join('')}</span>`;
    }

    function buildScriptKnowledgeTabButton(tab, label, activeTab) {
      const active = tab === activeTab;
      return `
        <button
          type="button"
          class="script-knowledge-tab${active ? ' is-active' : ''}"
          data-script-knowledge-tab="${tab}"
          aria-selected="${active ? 'true' : 'false'}"
        >${label}</button>
      `;
    }

    function formatScriptKnowledgeLeafContent(value) {
      return normalizeMaterialPreview(value)
        .replace(/\*\*([^*]+)\*\*/g, '$1')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/(^|\s)>\s+/g, '$1')
        .replace(/\s{2,}/g, ' ')
        .trim();
    }

    function buildScriptKnowledgeTreeNodeMarkup(node, sectionByHeading, parentPath = [], depth = 0, isLast = true) {
      const title = String(node?.title || '未命名节点').trim();
      const summary = String(node?.summary || '').trim();
      const children = Array.isArray(node?.children) ? node.children : [];
      const path = [...parentPath, title];
      let inner = '';
      if (!children.length) {
        const unitCount = Array.isArray(node?.sourceUnitIds) ? node.sourceUnitIds.length : 0;
        const section = sectionByHeading.get(path.join(' / '));
        const content = formatScriptKnowledgeLeafContent(section?.content || summary);
        const meta = unitCount
          ? `<span class="script-knowledge-tree-meta">${unitCount} 单元</span>`
          : (content ? '<span class="script-knowledge-tree-meta">详情</span>' : '');
        if (!content) {
          inner = `
            <article class="script-knowledge-tree-leaf is-empty" role="treeitem">
              <span class="script-knowledge-tree-dot" aria-hidden="true"></span>
              <strong>${escapeHtml(title)}</strong>
              ${meta}
            </article>
          `;
        } else {
          inner = `
            <article class="script-knowledge-tree-leaf" role="treeitem" aria-expanded="false">
              <button type="button" class="script-knowledge-tree-summary" data-script-knowledge-tree-toggle>
                <i class="script-knowledge-tree-chevron" aria-hidden="true"></i>
                <strong>${escapeHtml(title)}</strong>
                ${meta}
              </button>
              <div class="script-knowledge-tree-drawer">
                <div class="script-knowledge-tree-drawer-slot">
                  <div class="script-knowledge-tree-leaf-body">${escapeHtml(content)}</div>
                </div>
              </div>
            </article>
          `;
        }
      } else {
        const open = depth === 0;
        inner = `
          <article class="script-knowledge-tree-branch${open ? ' is-open' : ''}" data-depth="${depth}" role="treeitem" aria-expanded="${open ? 'true' : 'false'}">
            <button type="button" class="script-knowledge-tree-summary"${summary ? ` title="${escapeHtml(summary)}"` : ''} data-script-knowledge-tree-toggle>
              <i class="script-knowledge-tree-chevron" aria-hidden="true"></i>
              <strong>${escapeHtml(title)}</strong>
              <span class="script-knowledge-tree-meta">${children.length}</span>
            </button>
            <div class="script-knowledge-tree-drawer">
              <div class="script-knowledge-tree-drawer-slot">
                <div class="script-knowledge-tree-children" role="group">
                  ${children.map((child, index) => buildScriptKnowledgeTreeNodeMarkup(
                    child,
                    sectionByHeading,
                    path,
                    depth + 1,
                    index === children.length - 1
                  )).join('')}
                </div>
              </div>
            </div>
          </article>
        `;
      }
      return `
        <div class="script-knowledge-tree-node" data-depth="${depth}" data-last="${isLast ? 'true' : 'false'}">
          ${inner}
        </div>
      `;
    }

    function buildScriptKnowledgeTreeMarkup(detail) {
      const tree = Array.isArray(detail?.metadata?.knowledgeTree)
        ? detail.metadata.knowledgeTree.filter((node) => node && typeof node === 'object')
        : [];
      if (!tree.length) return buildScriptKnowledgeIngestionMarkup(detail);
      const sections = Array.isArray(detail?.sections) ? detail.sections : [];
      const sectionByHeading = new Map(sections.map((section) => [String(section?.heading || '').trim(), section]));
      const staleNotice = buildScriptKnowledgeStaleNotice(detail, sections);
      return `
        ${staleNotice}
        <div class="script-knowledge-tree-head">
          <strong>${tree.length} 个一级节点</strong>
          <span>点叶节点展开正文</span>
        </div>
        <div class="script-knowledge-tree" role="tree">
          ${tree.map((node, index) => buildScriptKnowledgeTreeNodeMarkup(
            node,
            sectionByHeading,
            [],
            0,
            index === tree.length - 1
          )).join('')}
        </div>
      `;
    }

    function buildScriptKnowledgeDetailMarkup(detail) {
      if (!detail) {
        return '<div class="script-knowledge-empty"><strong>选择左侧剧本</strong><p>可查看知识树、编辑信息，或阅读原文。</p></div>';
      }
      const isSelected = String(state.scriptReference?.item?.relativePath || '') === String(detail.relativePath || '');
      const activeTab = getScriptKnowledgeActiveTab();
      return `
        <div class="script-knowledge-detail-inner">
          ${buildScriptKnowledgeDetailHeadMarkup(detail, isSelected)}
          <div class="script-knowledge-tabs" role="tablist" aria-label="剧本详情分区">
            ${buildScriptKnowledgeTabButton('tree', '知识树', activeTab)}
            ${buildScriptKnowledgeTabButton('edit', '编辑信息', activeTab)}
            ${buildScriptKnowledgeTabButton('source', '原文', activeTab)}
          </div>
          <div class="script-knowledge-panels">
            <div class="script-knowledge-panel${activeTab === 'tree' ? ' is-active' : ''}" data-script-knowledge-panel="tree">
              ${buildScriptKnowledgeTreeMarkup(detail)}
            </div>
            <div class="script-knowledge-panel${activeTab === 'edit' ? ' is-active' : ''}" data-script-knowledge-panel="edit">
              ${buildScriptKnowledgeFormMarkup(detail)}
            </div>
            <div class="script-knowledge-panel${activeTab === 'source' ? ' is-active' : ''}" data-script-knowledge-panel="source">
              <pre class="script-knowledge-source">${escapeHtml(String(detail.content || '').slice(0, 30000))}</pre>
            </div>
          </div>
        </div>
      `;
    }

    function buildScriptKnowledgeDetailHeadMarkup(detail, isSelected) {
      const job = getScriptKnowledgeIngestionJob(detail?.id);
      const ingesting = ['queued', 'running'].includes(job?.state);
      return `
        <div class="script-knowledge-detail-head">
          <div>
            <div class="script-knowledge-detail-title">${escapeHtml(detail.title || detail.name || '未命名剧本')}</div>
            <div class="script-knowledge-meta">${escapeHtml(detail.relativePath || '')} · ${Number(detail.sectionCount || 0)} 个叶节点</div>
          </div>
          <div class="script-knowledge-detail-actions">
            <button type="button" class="button-primary" data-script-knowledge-ingest="${Number(detail.id || 0)}"${ingesting ? ' disabled' : ''}>${ingesting ? '知识入库中' : '知识入库'}</button>
            <button type="button" class="button-secondary" data-script-knowledge-reference="${escapeHtml(detail.relativePath || '')}">${isSelected ? '已设为知识库参考' : '设为知识库参考'}</button>
            <button type="button" class="material-wall-delete-button" data-delete-user-material-kind="script" data-delete-user-material-path="${escapeHtml(detail.relativePath || '')}" data-delete-user-material-name="${escapeHtml(detail.name || '')}">删除</button>
          </div>
        </div>
      `;
    }

    function buildScriptKnowledgeIngestionMarkup(detail) {
      const job = getScriptKnowledgeIngestionJob(detail?.id);
      const events = compactScriptKnowledgeIngestionEvents(
        Array.isArray(job?.events) ? job.events : [],
      );
      const ingesting = ['queued', 'running'].includes(job?.state);
      if (!ingesting && !events.length) {
        return '<div class="script-knowledge-empty is-inline"><strong>尚未知识入库</strong><p>点击上方“知识入库”，后台会建立知识树、叶子内容与检索索引。</p></div>';
      }
      const body = events.map((event) => buildScriptKnowledgeIngestionLineMarkup(event, job)).join('');
      const title = job?.state === 'failed' ? '知识入库失败' : job?.state === 'succeeded' ? '知识入库完成' : '正在知识入库';
      return `<div class="script-knowledge-ingestion"><strong>${title}</strong><div class="script-knowledge-ingestion-stream">${body || '<p>正在准备任务…</p>'}</div></div>`;
    }

    function buildScriptKnowledgeIngestionLineMarkup(event, job) {
      const kind = escapeHtml(event?.kind || 'step');
      const stage = escapeHtml(event?.stage || '');
      const text = formatScriptKnowledgeIngestionEvent(event, job);
      const isLiveProgress = ['knowledge_agent', 'reviewer'].includes(event?.stage)
        && event?.kind === 'progress'
        && job?.state === 'running';
      const typewriter = isLiveProgress ? ` data-typewriter-text="${escapeHtml(text)}"` : '';
      return `<p class="script-knowledge-ingestion-line is-${kind}" data-stage="${stage}"${typewriter}>${escapeHtml(text)}</p>`;
    }

    function formatScriptKnowledgeIngestionEvent(event, job) {
      const text = String(event?.text || '');
      if (event?.stage === 'reviewer' && text === 'Reviewer 正在审核原子性、覆盖度与检索价值') {
        return 'Reviewer 正在进行全树审核：原子性、覆盖度与检索价值';
      }
      const liveStage = ['knowledge_agent', 'reviewer'].includes(event?.stage);
      if (job?.state === 'running' && liveStage && event?.kind === 'step') {
        const elapsedSeconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(event?.at || 0)));
        return `${text}（等待模型返回 ${elapsedSeconds} 秒）`;
      }
      return text;
    }

    function compactScriptKnowledgeIngestionEvents(events) {
      const liveStages = new Set(['knowledge_agent', 'reviewer']);
      const latestIndex = new Map();
      events.forEach((event, index) => {
        if (liveStages.has(event?.stage)) latestIndex.set(event.stage, index);
      });
      return events.filter((event, index) => (
        !liveStages.has(event?.stage) || latestIndex.get(event.stage) === index
      ));
    }

    function buildScriptKnowledgeFormMarkup(detail) {
      const tags = Array.isArray(detail.tags) ? detail.tags.join('，') : '';
      return `
        <div class="script-knowledge-form">
          <label class="script-knowledge-field">知识标题<input id="scriptKnowledgeTitleInput" value="${escapeHtml(detail.title || detail.stem || '')}" maxlength="200"></label>
          <label class="script-knowledge-field">标签（用逗号分隔）<input id="scriptKnowledgeTagsInput" value="${escapeHtml(tags)}" maxlength="500" placeholder="行业，人物，场景，钩子"></label>
          <label class="script-knowledge-field is-wide">摘要<textarea id="scriptKnowledgeSummaryInput" maxlength="2000" placeholder="说明这份剧本适合什么场景">${escapeHtml(detail.summary || '')}</textarea></label>
          <div class="script-knowledge-detail-actions is-wide"><button type="button" class="button-primary" data-script-knowledge-save>${state.scriptKnowledge.saving ? '保存中' : '保存元数据'}</button></div>
        </div>
      `;
    }

    function getMaterialMentionName(item) {
      if (!item) return '素材';
      return item.name || item.relativePath || item.stem || '素材';
    }

    function buildMaterialWallCardMarkup(item) {
      const name = getMaterialMentionName(item);
      const meta = item.relativePath || item.name || '';
      const preview = normalizeMaterialPreview(item.preview || '');
      const relativePath = String(item.relativePath || item.name || name);
      const deleteButton = `
        <div class="material-wall-entry-actions">
          <button
            type="button"
            class="material-wall-delete-button"
            data-delete-user-material-kind="${escapeHtml(item.kind === 'script' ? 'script' : 'image')}"
            data-delete-user-material-path="${escapeHtml(relativePath)}"
            data-delete-user-material-name="${escapeHtml(name)}"
          >删除</button>
        </div>
      `;
      if (item.kind === 'image') {
        return `
          <article class="material-wall-entry">
            <button type="button" class="material-wall-card" data-pick-material="${escapeHtml(name)}">
              ${item.url ? `<img class="material-wall-thumb" src="${escapeHtml(item.url)}" alt="">` : '<span class="material-wall-icon">图</span>'}
              <span class="material-title">@${escapeHtml(name)}</span>
              <span class="material-meta">${escapeHtml(meta)}</span>
            </button>
            ${deleteButton}
          </article>
        `;
      }
      return `
        <article class="material-wall-entry">
          <button type="button" class="material-wall-card" data-select-script-reference-library="${escapeHtml(relativePath)}">
            <span class="material-wall-doc-preview">${escapeHtml(preview || '暂无可预览文本')}</span>
            <span class="material-title">${escapeHtml(name)}</span>
          </button>
          ${deleteButton}
        </article>
      `;
    }

    function openMaterialLibraryModal(kind) {
      state.materialModal.visible = true;
      state.materialModal.kind = kind === 'script' ? 'script' : 'image';
      if (state.materialModal.kind === 'script') state.scriptKnowledge.resetDetailScroll = true;
      renderMaterialLibraryModal();
      if (state.materialModal.kind === 'script') {
        refreshScriptKnowledge({ preserveSelection: true });
      }
    }

    function closeMaterialLibraryModal() {
      state.materialModal.visible = false;
      renderMaterialLibraryModal();
    }

    function openRecycleBinModal() {
      state.recycleBinModal.visible = true;
      renderRecycleBinModal();
    }

    function closeRecycleBinModal() {
      state.recycleBinModal.visible = false;
      state.recycleBinModal.renderedSignature = '';
      state.recycleBinModal.selectedFolders = [];
      renderRecycleBinModal();
    }

    function syncRecycleBinBatchDeleteButton() {
      if (!els.recycleBinBatchDeleteButton || !els.recycleBinSelectAllButton) return;
      const availableFolders = (state.recycleBin?.items || [])
        .map((item) => String(item?.folder || '').trim())
        .filter(Boolean);
      const selectedCount = (state.recycleBinModal.selectedFolders || []).length;
      const deleting = !!state.recycleBinModal.deleting;
      const allSelected = availableFolders.length > 0 && selectedCount === availableFolders.length;
      els.recycleBinSelectAllButton.disabled = deleting || availableFolders.length === 0;
      els.recycleBinSelectAllButton.textContent = allSelected ? '取消全选' : '一键全选';
      els.recycleBinBatchDeleteButton.disabled = deleting || selectedCount === 0;
      els.recycleBinBatchDeleteButton.textContent = deleting
        ? '删除中'
        : selectedCount > 0
          ? `删除选中（${selectedCount}）`
          : '批量删除';
    }

    function toggleAllRecycleBinTasks() {
      const availableFolders = (state.recycleBin?.items || [])
        .map((item) => String(item?.folder || '').trim())
        .filter(Boolean);
      const selectedFolders = new Set(state.recycleBinModal.selectedFolders || []);
      const allSelected = availableFolders.length > 0
        && availableFolders.every((folder) => selectedFolders.has(folder));
      state.recycleBinModal.selectedFolders = allSelected ? [] : availableFolders;
      els.recycleBinWall
        .querySelectorAll('[data-select-recycle-bin-folder]')
        .forEach((checkbox) => {
          checkbox.checked = !allSelected;
        });
      syncRecycleBinBatchDeleteButton();
    }

    function renderRecycleBinModal() {
      const visible = !!state.recycleBinModal.visible;
      if (!els.recycleBinModal) return;
      els.recycleBinModal.classList.toggle('hidden', !visible);
      const bin = state.recycleBin || {};
      const items = Array.isArray(bin.items) ? bin.items : [];
      els.recycleBinTitle.textContent = '回收站';
      els.recycleBinSub.textContent = `${Number(bin.count || items.length || 0)} 个失败任务。失败但已产出视频的任务会放到这里。`;
      syncRecycleBinBatchDeleteButton();
      if (!visible) return;
      if (!items.length) {
        if (state.recycleBinModal.renderedSignature !== 'empty') {
          els.recycleBinWall.innerHTML = '';
          state.recycleBinModal.renderedSignature = 'empty';
        }
        return;
      }
      const signature = JSON.stringify(items.map((item) => ({
        folder: item?.folder || '',
        title: item?.videoTitle || '',
        reason: item?.displayReason || item?.reason || '',
        createdAt: item?.createdAt || '',
        videos: (Array.isArray(item?.videos) ? item.videos : []).map((video) => ({
          url: video?.url || '',
          name: video?.name || '',
        })),
      })));
      if (state.recycleBinModal.renderedSignature === signature) return;
      els.recycleBinWall.innerHTML = items.map((item) => buildRecycleBinCardMarkup(item)).join('');
      state.recycleBinModal.renderedSignature = signature;
    }

    async function deleteSelectedRecycleBinTasks() {
      const folders = [...(state.recycleBinModal.selectedFolders || [])];
      if (!folders.length || state.recycleBinModal.deleting) return;
      if (!window.confirm(`确认永久删除选中的 ${folders.length} 个回收站任务？此操作无法撤销。`)) return;
      state.recycleBinModal.deleting = true;
      syncRecycleBinBatchDeleteButton();
      try {
        const res = await fetch('/api/user-recycle-bin/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ folders }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 404) {
          throw new Error('批量删除接口未加载，请重启AI8video 服务并刷新页面后重试。');
        }
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        state.recycleBinModal.selectedFolders = [];
        state.recycleBinModal.renderedSignature = '';
        await refreshRecycleBin();
        renderRecycleBin();
        renderRecycleBinModal();
      } catch (error) {
        window.alert(error?.message || '批量删除失败');
      } finally {
        state.recycleBinModal.deleting = false;
        syncRecycleBinBatchDeleteButton();
      }
    }

    async function restoreRecycleBinTask(trigger) {
      const folder = String(trigger?.getAttribute('data-restore-recycle-bin-folder') || '').trim();
      if (!folder || state.recycleBinModal.restoringFolder) return;
      const previousLabel = trigger.textContent || '恢复到生成结果';
      state.recycleBinModal.restoringFolder = folder;
      trigger.disabled = true;
      trigger.textContent = '恢复中...';
      try {
        const res = await fetch('/api/user-recycle-bin/restore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ folder }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        state.recycleBinModal.selectedFolders = (state.recycleBinModal.selectedFolders || [])
          .filter((selectedFolder) => selectedFolder !== folder);
        state.recycleBinModal.renderedSignature = '';
        await refreshUserGeneratedResults();
        renderProgress();
        renderResultModal();
        renderRecycleBin();
        renderRecycleBinModal();
      } catch (error) {
        window.alert(error?.message || '恢复视频失败');
      } finally {
        state.recycleBinModal.restoringFolder = '';
        if (trigger.isConnected) {
          trigger.disabled = false;
          trigger.textContent = previousLabel;
        }
      }
    }





































    function buildRecycleBinCardMarkup(item) {
      const videos = Array.isArray(item?.videos) ? item.videos : [];
      const firstVideo = videos[0] || null;
      const folder = String(item?.folder || '').trim();
      const selected = (state.recycleBinModal.selectedFolders || []).includes(folder);
      const restoring = state.recycleBinModal.restoringFolder === folder;
      const title = item?.videoTitle || `视频 ${item?.videoIndex || ''}`.trim();
      const rawReason = String(item?.reason || '').trim();
      const reason = humanizeRecycleBinReason(item?.displayReason || rawReason || '任务失败');
      const meta = [
        item?.createdAt ? formatAssetTime(item.createdAt) : '',
        videos.length ? `${videos.length} 个视频` : '',
      ].filter(Boolean).join(' · ');
      const showTechnicalReason = rawReason && rawReason !== reason && looksTechnicalError(rawReason);
      return `
        <article class="material-wall-card">
          <label class="result-modal-batch-toggle">
            <input type="checkbox" data-select-recycle-bin-folder="${escapeHtml(folder)}" ${selected ? 'checked' : ''}>
            <span>选择此任务</span>
          </label>
          ${firstVideo?.url ? `<video class="recycle-video-preview" controls preload="metadata" src="${escapeHtml(firstVideo.url)}"></video>` : '<span class="material-wall-icon">!</span>'}
          <span class="material-title">${escapeHtml(title)}</span>
          ${meta ? `<span class="material-meta">${escapeHtml(meta)}</span>` : ''}
          <span class="material-wall-doc-preview">${escapeHtml(reason)}</span>
          ${showTechnicalReason ? `<details class="material-meta"><summary>技术详情</summary>${escapeHtml(rawReason)}</details>` : ''}
          ${videos.length > 1 ? `<span class="material-meta">${escapeHtml(videos.map((video) => video.name || '视频').join('；'))}</span>` : ''}
          <div class="recycle-bin-card-actions">
            <button type="button" class="recycle-bin-restore-button" data-restore-recycle-bin-folder="${escapeHtml(folder)}" ${!folder || restoring ? 'disabled' : ''}>${restoring ? '恢复中...' : '恢复到生成结果'}</button>
          </div>
        </article>
      `;
    }
