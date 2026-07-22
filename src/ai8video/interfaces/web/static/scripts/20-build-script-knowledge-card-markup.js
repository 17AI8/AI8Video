    function buildScriptKnowledgeCardMarkup(item) {
      const active = Number(item?.id || 0) === Number(state.scriptKnowledge.selectedId || 0);
      const title = item?.title || item?.stem || item?.name || '未命名剧本';
      const preview = item?.matchedExcerpt || item?.summary || item?.preview || '暂无摘要';
      const tags = buildScriptKnowledgeTagsMarkup(item?.tags || []);
      const score = Number(item?.score || 0);
      const scoreCopy = score > 0 ? ` · 匹配 ${score.toFixed(2)}` : '';
      const matchedHeading = String(item?.matchedHeading || '').trim();
      const matchMarkup = matchedHeading
        ? `<span class="script-knowledge-meta">命中知识段：${escapeHtml(matchedHeading)}</span>`
        : '';
      return `
        <button type="button" class="script-knowledge-list-card${active ? ' is-active' : ''}"
          data-script-knowledge-document="${Number(item?.id || 0)}">
          <span class="script-knowledge-list-title">${escapeHtml(title)}</span>
          ${matchMarkup}
          <span class="script-knowledge-list-preview">${escapeHtml(normalizeMaterialPreview(preview))}</span>
          ${tags}
          <span class="script-knowledge-meta">${Number(item?.sectionCount || 0)} 个知识段 · ${escapeHtml(formatFileSize(item?.sizeBytes || 0) || '0 B')}${scoreCopy}</span>
        </button>
      `;
    }

    function buildScriptKnowledgeTagsMarkup(tags) {
      const values = Array.isArray(tags) ? tags.filter(Boolean).slice(0, 6) : [];
      if (!values.length) return '<span class="script-knowledge-meta">未设置标签</span>';
      return `<span class="script-knowledge-tags">${values.map((tag) => `<span class="script-knowledge-tag">${escapeHtml(tag)}</span>`).join('')}</span>`;
    }

    function buildScriptKnowledgeDetailMarkup(detail) {
      if (!detail) {
        return '<div class="script-knowledge-empty">选择左侧剧本，查看原文、知识段和元数据。</div>';
      }
      const isSelected = String(state.scriptReference?.item?.relativePath || '') === String(detail.relativePath || '');
      const sections = Array.isArray(detail.sections) ? detail.sections : [];
      return `
        <div class="script-knowledge-detail-inner">
          ${buildScriptKnowledgeDetailHeadMarkup(detail, isSelected)}
          ${buildScriptKnowledgeFormMarkup(detail)}
          <div><strong>知识段</strong><div class="script-knowledge-section-list">${sections.map(buildScriptKnowledgeSectionMarkup).join('')}</div></div>
          <div><strong>原始正文</strong><pre class="script-knowledge-source">${escapeHtml(String(detail.content || '').slice(0, 30000))}</pre></div>
        </div>
      `;
    }

    function buildScriptKnowledgeDetailHeadMarkup(detail, isSelected) {
      return `
        <div class="script-knowledge-detail-head">
          <div>
            <div class="script-knowledge-detail-title">${escapeHtml(detail.title || detail.name || '未命名剧本')}</div>
            <div class="script-knowledge-meta">${escapeHtml(detail.relativePath || '')} · ${Number(detail.sectionCount || 0)} 个知识段</div>
          </div>
          <div class="script-knowledge-detail-actions">
            <button type="button" class="button-secondary" data-script-knowledge-reference="${escapeHtml(detail.relativePath || '')}">${isSelected ? '已设为剧本参考' : '设为剧本参考'}</button>
            <button type="button" class="material-wall-delete-button" data-delete-user-material-kind="script" data-delete-user-material-path="${escapeHtml(detail.relativePath || '')}" data-delete-user-material-name="${escapeHtml(detail.name || '')}">删除</button>
          </div>
        </div>
      `;
    }

    function buildScriptKnowledgeFormMarkup(detail) {
      const tags = Array.isArray(detail.tags) ? detail.tags.join('，') : '';
      return `
        <div class="script-knowledge-form">
          <label class="script-knowledge-field">知识标题<input id="scriptKnowledgeTitleInput" value="${escapeHtml(detail.title || detail.stem || '')}" maxlength="200"></label>
          <label class="script-knowledge-field">标签（用逗号分隔）<input id="scriptKnowledgeTagsInput" value="${escapeHtml(tags)}" maxlength="500" placeholder="行业，人物，场景，钩子"></label>
          <label class="script-knowledge-field">摘要<textarea id="scriptKnowledgeSummaryInput" maxlength="2000" placeholder="说明这份剧本适合什么场景">${escapeHtml(detail.summary || '')}</textarea></label>
          <div class="script-knowledge-detail-actions"><button type="button" class="button-primary" data-script-knowledge-save>${state.scriptKnowledge.saving ? '保存中' : '保存元数据'}</button></div>
        </div>
      `;
    }

    function buildScriptKnowledgeSectionMarkup(section, index) {
      const heading = section?.heading || `知识段 ${index + 1}`;
      const preview = normalizeMaterialPreview(section?.content || '').slice(0, 220);
      return `<div class="script-knowledge-section"><strong>${escapeHtml(heading)}</strong><span>${escapeHtml(preview)}</span></div>`;
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
        title: item?.episodeTitle || '',
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
      const title = item?.episodeTitle || `视频 ${item?.episodeIndex || ''}`.trim();
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

    function humanizeGenerationFailureReason(value) {
      const text = String(value || '').trim();
      const lowered = text.toLowerCase();
      const imageStage = lowered.includes('/v1/images/generations') || text.includes('首帧') || text.includes('图生图');
      if (!text) return '视频生成失败，请重新生成这一条。';
      if (text.includes('未配置图片模型') || text.includes('请设置图片模型')) {
        return '请设置图片模型。';
      }
      if (
        text.includes('前序任务已结束')
        || text.includes('前序失败未提交')
        || text.includes('未提交上游生成')
        || text.includes('前面的视频已经失败')
      ) {
        return '这条未提交给生成服务；没有上游返回。';
      }
      if (
        text.includes('视频未提交')
        || text.includes('没有成功提交')
        || text.includes('没有拿到可轮询')
        || text.includes('没有留下可轮询')
      ) {
        return '后台中断了，这条视频未提交给生成服务。请重新生成。';
      }
      if (
        lowered.includes("didn't pass content review")
        || lowered.includes('content review')
        || text.includes('内容审核')
        || text.includes('敏感信息')
        || lowered.includes('protected ip')
      ) {
        return '内容审核未通过，请换图或改成非真人风格后重试。';
      }
      if (
        lowered.includes('httpsconnectionpool')
        || lowered.includes('max retries exceeded')
        || lowered.includes('sslerror')
        || lowered.includes('ssleoferror')
        || lowered.includes('eof occurred in violation of protocol')
      ) {
        return imageStage ? '首帧图上游连接中断，请稍后重试。' : '上游生成服务连接中断，请稍后重试。';
      }
      if (
        lowered.includes('cannot connect to proxy')
        || lowered.includes('proxyerror')
        || lowered.includes('remote end closed connection')
        || lowered.includes('connection refused')
        || lowered.includes('connection aborted')
        || lowered.includes('connection reset')
      ) {
        return imageStage ? '首帧图上游连接中断，请稍后重试。' : '上游生成服务连接中断，请稍后重试。';
      }
      if (
        text.includes('本地任务超时')
        || text.includes('没有提交给上游生成服务')
        || text.includes('未提交给生成服务')
      ) {
        return '本地任务超时，视频没有提交给上游生成服务。请重新发送或缩短输入后再试。';
      }
      if (lowered.includes('read timed out') || lowered.includes('timed out') || text.includes('超时')) {
        return imageStage ? '首帧图生成超时，请稍后重试。' : '生成服务超时，请稍后重试。';
      }
      if (
        lowered.includes('invalid_seconds')
        || lowered.includes('seconds is invalid')
        || lowered.includes('must be 4, 8, or 12')
      ) {
        return '当前时长不支持，请切换到支持的秒数后重试。';
      }
      if (
        lowered.includes('only [4, 6, 8] seconds')
        || lowered.includes('only [4,6,8] seconds')
        || (text.includes('4, 6, 8') && lowered.includes('seconds') && lowered.includes('supported'))
      ) {
        return '当前模型只支持 4、6 或 8 秒，请把视频时长改成支持的秒数后重试。';
      }
      if (lowered.includes('duration must be 5 or 10 seconds') || text.includes('5 or 10 seconds')) {
        return '视频时长不支持，请切到 5 秒或 10 秒。';
      }
      if (lowered.includes('size must be') || lowered.includes('supported resolution')) {
        return '清晰度不支持，请切换清晰度后重试。';
      }
      if (
        lowered.includes('invalid media')
        || lowered.includes('media url')
        || lowered.includes('media type')
      ) {
        return imageStage ? '首帧图不符合生成要求，请换图后重试。' : '素材不符合生成要求，请更换后重试。';
      }
      if (
        lowered.includes('insufficient')
        || lowered.includes('quota')
        || text.includes('额度不足')
        || text.includes('余额不足')
      ) {
        return '当前账号额度不足，请更换账号或稍后重试。';
      }
      if ((text.includes('上游') && text.includes('失败')) || text.includes('生成未成功') || text.includes('生成状态')) {
        return '生成服务没有成功，请重新生成这一条。';
      }
      if (looksTechnicalError(text)) {
        return imageStage ? '首帧图处理失败，请稍后重试。' : '视频处理失败，请稍后重试。';
      }
      return text;
    }

    function summarizeGenerationFailureReason(value) {
      const reason = humanizeGenerationFailureReason(value);
      if (reason.includes('请设置图片模型')) return '请设置图片模型';
      if (reason.includes('内容审核未通过')) return '内容审核未通过';
      if (reason.includes('首帧图上游连接中断') || reason.includes('首帧图连接生成服务失败')) return '首帧图上游断连';
      if (reason.includes('上游生成服务连接中断') || reason.includes('生成服务连接失败')) return '上游连接中断';
      if (reason.includes('没有提交给上游生成服务')) return '本地超时未提交上游';
      if (reason.includes('首帧图生成超时')) return '首帧图超时';
      if (reason.includes('生成服务超时')) return '生成超时';
      if (reason.includes('当前模型只支持 4、6 或 8 秒')) return '时长仅支持4/6/8秒';
      if (reason.includes('当前时长不支持')) return '当前时长不支持';
      if (reason.includes('视频时长不支持')) return '视频时长不支持';
      if (reason.includes('清晰度不支持')) return '清晰度不支持';
      if (reason.includes('首帧图不符合生成要求')) return '首帧图不符合要求';
      if (reason.includes('素材不符合生成要求')) return '素材不符合要求';
      if (reason.includes('当前账号额度不足')) return '账号额度不足';
      if (reason.includes('首帧图处理失败')) return '首帧图处理失败';
      if (reason.includes('视频处理失败')) return '视频处理失败';
      if (reason.includes('生成服务没有成功')) return '生成服务失败';
      if (reason.includes('没有上游返回')) return '未提交，无上游返回';
      if (reason.includes('未提交给生成服务')) return '未提交，无上游返回';
      if (reason.includes('后台中断了')) return '后台中断，未提交';
      return reason.length > 14 ? `${reason.slice(0, 14)}…` : reason;
    }

