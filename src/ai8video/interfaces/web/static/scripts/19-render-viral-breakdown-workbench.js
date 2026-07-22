    function renderViralBreakdownWorkbench() {
      const archiveMeta = document.getElementById('viralBreakdownArchiveMeta');
      const statusText = document.getElementById('viralBreakdownStatusText');
      const videoSelect = document.getElementById('viralBreakdownVideoSelect');
      const intervalInput = document.getElementById('viralBreakdownIntervalInput');
      const targetRatioSelect = document.getElementById('viralBreakdownTargetRatio');
      const processFramesButton = document.getElementById('viralBreakdownProcessFramesButton');
      const transcribeButton = document.getElementById('viralBreakdownTranscribeButton');
      const guessScriptButton = document.getElementById('viralBreakdownGuessScriptButton');
      const saveTranscriptButton = document.getElementById('viralBreakdownSaveTranscriptButton');
      const originalMeta = document.getElementById('viralBreakdownOriginalMeta');
      const scriptGuessMeta = document.getElementById('viralBreakdownScriptGuessMeta');
      const gridMeta = document.getElementById('viralBreakdownGridMeta');
      const transcriptMeta = document.getElementById('viralBreakdownTranscriptMeta');
      const generatedMeta = document.getElementById('viralBreakdownGeneratedMeta');
      const originalPane = document.getElementById('viralBreakdownOriginalPane');
      const scriptGuessPane = document.getElementById('viralBreakdownScriptGuessPane');
      const gridPane = document.getElementById('viralBreakdownGridPane');
      const transcriptPane = document.getElementById('viralBreakdownTranscriptPane');
      const generatedPane = document.getElementById('viralBreakdownGeneratedPane');
      const currentItem = getSelectedViralBreakdownItem();
      const transcriptTextFromItem = String(currentItem?.transcriptText || '');
      const transcriptDisplayText = currentItem
        ? getViralBreakdownTranscriptDraft(currentItem.videoKey, transcriptTextFromItem)
        : '';
      const scriptGuessDisplayText = currentItem
        ? getViralBreakdownScriptGuessDraft(currentItem.videoKey)
        : '';
      const transcriptHasUnsavedChanges = !!currentItem && transcriptDisplayText !== transcriptTextFromItem;
      if (archiveMeta) {
        archiveMeta.textContent = state.viralBreakdown.archiveDisplay || '0 个视频 · 0 B';
      }
      if (videoSelect) {
        const items = Array.isArray(state.viralBreakdown.items) ? state.viralBreakdown.items : [];
        videoSelect.innerHTML = items.length
          ? items.map((item) => `<option value="${escapeHtml(String(item?.videoKey || ''))}">${escapeHtml(String(item?.name || item?.videoKey || '未命名视频'))}</option>`).join('')
          : '<option value="">还没有上传视频</option>';
        videoSelect.value = currentItem ? String(currentItem.videoKey || '') : '';
        videoSelect.disabled = !items.length || !!state.viralBreakdown.loading;
      }
      if (intervalInput) {
        intervalInput.value = String(state.viralBreakdown.intervalSeconds || 1);
      }
      if (targetRatioSelect) {
        targetRatioSelect.value = String(state.viralBreakdown.targetRatio || '16:9');
      }
      if (processFramesButton) {
        processFramesButton.disabled = !currentItem || !!state.viralBreakdown.frameProcessing || !!state.viralBreakdown.loading;
        processFramesButton.textContent = state.viralBreakdown.frameProcessing ? '截图中...' : '拆解画面';
      }
      if (transcribeButton) {
        transcribeButton.disabled = !currentItem || !!state.viralBreakdown.transcriptProcessing || !!state.viralBreakdown.loading;
        transcribeButton.textContent = state.viralBreakdown.transcriptProcessing ? '识别中...' : '分析台词';
      }
      if (guessScriptButton) {
        guessScriptButton.disabled = !currentItem || !!state.viralBreakdown.scriptGuessProcessing || !!state.viralBreakdown.loading;
        guessScriptButton.textContent = state.viralBreakdown.scriptGuessProcessing ? '猜测中...' : '猜剧本';
      }
      if (saveTranscriptButton) {
        saveTranscriptButton.disabled = !currentItem || !!state.viralBreakdown.transcriptSaving || !transcriptHasUnsavedChanges;
        saveTranscriptButton.textContent = state.viralBreakdown.transcriptSaving
          ? '保存中...'
          : transcriptHasUnsavedChanges
            ? '保存台词'
            : '已保存';
      }
      if (statusText) {
        statusText.textContent = state.viralBreakdown.error || state.viralBreakdown.notice || '';
      }
      if (originalMeta) {
        originalMeta.textContent = currentItem ? `${currentItem.sizeLabel || humanizeByteSize(currentItem.sizeBytes || 0)}` : '';
      }
      if (scriptGuessMeta) {
        scriptGuessMeta.textContent = scriptGuessDisplayText ? `${scriptGuessDisplayText.length} 字 · 可编辑` : '等待猜剧本';
      }
      if (gridMeta) {
        gridMeta.textContent = currentItem?.frameCount ? `${currentItem.frameCount} 张截图` : '';
      }
      if (transcriptMeta) {
        transcriptMeta.textContent = transcriptDisplayText ? `${transcriptDisplayText.length} 字` : '';
      }
      if (generatedMeta) {
        generatedMeta.textContent = currentItem?.generatedVideoUrl ? '已存在' : '暂无';
      }
      if (originalPane) {
        originalPane.innerHTML = currentItem?.videoUrl
          ? `<video src="${escapeHtml(String(currentItem.videoUrl || ''))}" controls playsinline preload="metadata"></video>`
          : '<div class="viral-breakdown-empty">请先上传一个视频。</div>';
      }
      if (scriptGuessPane) {
        scriptGuessPane.innerHTML = `<textarea class="viral-breakdown-text-output viral-breakdown-text-editor viral-breakdown-script-guess-editor" spellcheck="false" placeholder="点击“猜剧本”后，这里会显示多模态模型反推的剧本；也可以先手动输入或粘贴剧本。">${escapeHtml(scriptGuessDisplayText)}</textarea>`;
        const scriptGuessEditor = scriptGuessPane.querySelector('.viral-breakdown-script-guess-editor');
        if (scriptGuessEditor instanceof HTMLTextAreaElement && currentItem?.videoKey) {
          scriptGuessEditor.oninput = () => {
            const normalizedVideoKey = String(currentItem.videoKey || '').trim();
            const nextScriptGuessText = String(scriptGuessEditor.value || '');
            state.viralBreakdown.scriptGuessDrafts = {
              ...(state.viralBreakdown.scriptGuessDrafts || {}),
              [normalizedVideoKey]: nextScriptGuessText,
            };
            if (scriptGuessMeta) {
              scriptGuessMeta.textContent = nextScriptGuessText ? `${nextScriptGuessText.length} 字 · 可编辑` : '等待猜剧本';
            }
          };
        }
      }
      if (gridPane) {
        gridPane.innerHTML = currentItem?.gridImageUrl
          ? `<img src="${escapeHtml(String(currentItem.gridImageUrl || ''))}" alt="拼接好的宫格图">`
          : '<div class="viral-breakdown-empty">点击“拆解画面”后，这里会显示按时间顺序拼好的宫格图。</div>';
      }
      if (transcriptPane) {
        transcriptPane.innerHTML = transcriptDisplayText
          ? `<textarea class="viral-breakdown-text-output viral-breakdown-text-editor" spellcheck="false">${escapeHtml(transcriptDisplayText)}</textarea>`
          : '<div class="viral-breakdown-empty">点击“分析台词”后，这里会显示 Whisper 识别到的文本。</div>';
        const transcriptEditor = transcriptPane.querySelector('.viral-breakdown-text-editor');
        if (transcriptEditor instanceof HTMLTextAreaElement && currentItem?.videoKey) {
          transcriptEditor.oninput = () => {
            const normalizedVideoKey = String(currentItem.videoKey || '').trim();
            const nextTranscriptText = String(transcriptEditor.value || '');
            state.viralBreakdown.transcriptDrafts = {
              ...(state.viralBreakdown.transcriptDrafts || {}),
              [normalizedVideoKey]: nextTranscriptText,
            };
            if (transcriptMeta) {
              transcriptMeta.textContent = nextTranscriptText ? `${nextTranscriptText.length} 字` : '';
            }
            if (saveTranscriptButton) {
              saveTranscriptButton.disabled = false;
              saveTranscriptButton.textContent = '保存台词';
            }
          };
        }
      }
      if (generatedPane) {
        generatedPane.innerHTML = currentItem?.generatedVideoUrl
          ? `<video src="${escapeHtml(String(currentItem.generatedVideoUrl || ''))}" controls playsinline preload="metadata"></video>`
          : '<div class="viral-breakdown-empty">这里预留给后续基于拆解结果生成的用户视频，当前版本先显示空态。</div>';
      }
    }

    function beginViralBreakdownUpload() {
      const input = document.getElementById('viralBreakdownUploadInput');
      if (!input) return;
      input.click();
    }

    async function uploadViralBreakdownVideos(files) {
      const formData = new FormData();
      Array.from(files || []).forEach((file) => formData.append('files', file, file.name));
      state.viralBreakdown.uploading = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在上传视频...';
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/upload', {
          method: 'POST',
          body: formData,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '上传爆款拆解视频失败');
        }
        state.viralBreakdown.notice = Array.isArray(data?.saved) && data.saved.length
          ? `已上传 ${data.saved.length} 个视频`
          : '没有新增视频';
        await refreshViralBreakdownWorkspace({ keepSelection: false });
      } finally {
        state.viralBreakdown.uploading = false;
        renderViralBreakdownWorkbench();
      }
    }

    async function openViralBreakdownFolder(trigger) {
      const previous = trigger?.textContent || '打开文件夹';
      if (trigger) trigger.textContent = '打开中...';
      const res = await fetch('/api/open-viral-breakdown-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        if (trigger) {
          trigger.textContent = '打开失败';
          setTimeout(() => { trigger.textContent = previous; }, 1600);
        }
        throw new Error('open viral breakdown folder failed');
      }
      if (trigger) {
        trigger.textContent = '已打开';
        setTimeout(() => { trigger.textContent = previous; }, 1200);
      }
    }

    async function processSelectedViralBreakdownFrames() {
      const currentItem = getSelectedViralBreakdownItem();
      if (!currentItem?.videoKey) return;
      state.viralBreakdown.frameProcessing = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在按设定间隔截图并拼接宫格图...';
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/process-frames', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            videoKey: currentItem.videoKey,
            intervalSeconds: Number(state.viralBreakdown.intervalSeconds || 1),
            targetRatio: String(state.viralBreakdown.targetRatio || '16:9'),
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '拆解画面失败');
        }
        state.viralBreakdown.notice = `已完成 ${Number(data?.frameCount || 0) || 0} 张截图，并拼成 ${String(data?.targetRatio || state.viralBreakdown.targetRatio)}`;
        await refreshViralBreakdownWorkspace({ keepSelection: true });
      } finally {
        state.viralBreakdown.frameProcessing = false;
        renderViralBreakdownWorkbench();
      }
    }

    async function transcribeSelectedViralBreakdownVideo() {
      const currentItem = getSelectedViralBreakdownItem();
      if (!currentItem?.videoKey) return;
      state.viralBreakdown.transcriptProcessing = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在调用 Whisper 识别台词...';
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/transcribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            videoKey: currentItem.videoKey,
            model: 'base',
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '分析台词失败');
        }
        state.viralBreakdown.transcriptDrafts = {
          ...(state.viralBreakdown.transcriptDrafts || {}),
          [String(currentItem.videoKey || '')]: String(data?.text || ''),
        };
        state.viralBreakdown.notice = data?.text ? '台词识别完成' : '没有识别到可用台词';
        await refreshViralBreakdownWorkspace({ keepSelection: true });
      } finally {
        state.viralBreakdown.transcriptProcessing = false;
        renderViralBreakdownWorkbench();
      }
    }

    async function guessSelectedViralBreakdownScript() {
      const currentItem = getSelectedViralBreakdownItem();
      if (!currentItem?.videoKey) return;
      const transcriptTextFromItem = String(currentItem?.transcriptText || '');
      const transcriptText = getViralBreakdownTranscriptDraft(currentItem.videoKey, transcriptTextFromItem);
      const normalizedVideoKey = String(currentItem.videoKey || '');
      state.viralBreakdown.scriptGuessProcessing = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在把宫格图和台词发给多模态模型猜剧本...';
      state.viralBreakdown.scriptGuessDrafts = {
        ...(state.viralBreakdown.scriptGuessDrafts || {}),
        [normalizedVideoKey]: '',
      };
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/guess-script?stream=1', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            videoKey: currentItem.videoKey,
            text: transcriptText,
          }),
        });
        if (!res.ok) {
          const errorText = await res.text().catch(() => '');
          throw new Error(errorText || '猜剧本失败');
        }
        if (!res.body) {
          throw new Error('当前浏览器不支持流式读取');
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let streamedScriptText = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          streamedScriptText += decoder.decode(value, { stream: true });
          state.viralBreakdown.scriptGuessDrafts = {
            ...(state.viralBreakdown.scriptGuessDrafts || {}),
            [normalizedVideoKey]: streamedScriptText,
          };
          const scriptGuessEditor = document.querySelector('#viralBreakdownScriptGuessPane .viral-breakdown-script-guess-editor');
          const scriptGuessMeta = document.getElementById('viralBreakdownScriptGuessMeta');
          if (scriptGuessEditor instanceof HTMLTextAreaElement) {
            scriptGuessEditor.value = streamedScriptText;
            scriptGuessEditor.scrollTop = scriptGuessEditor.scrollHeight;
          }
          if (scriptGuessMeta) {
            scriptGuessMeta.textContent = streamedScriptText ? `${streamedScriptText.length} 字 · 生成中` : '生成中';
          }
        }
        const trailingText = decoder.decode();
        if (trailingText) {
          streamedScriptText += trailingText;
        }
        state.viralBreakdown.scriptGuessDrafts = {
          ...(state.viralBreakdown.scriptGuessDrafts || {}),
          [normalizedVideoKey]: streamedScriptText,
        };
        if (!streamedScriptText) {
          state.viralBreakdown.error = '多模态模型请求结束，但没有返回任何剧本文本；请检查当前多模态模型是否支持图片输入和 Chat Completions 文本返回。';
        }
        state.viralBreakdown.notice = streamedScriptText ? '剧本已生成，可在左下方编辑' : '多模态模型没有返回可用剧本';
      } finally {
        state.viralBreakdown.scriptGuessProcessing = false;
        renderViralBreakdownWorkbench();
      }
    }

    async function saveSelectedViralBreakdownTranscript() {
      const currentItem = getSelectedViralBreakdownItem();
      if (!currentItem?.videoKey) return;
      const normalizedVideoKey = String(currentItem.videoKey || '');
      const transcriptText = getViralBreakdownTranscriptDraft(normalizedVideoKey, currentItem.transcriptText || '');
      state.viralBreakdown.transcriptSaving = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在保存台词...';
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/save-transcript', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            videoKey: normalizedVideoKey,
            text: transcriptText,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '保存台词失败');
        }
        currentItem.transcriptText = String(data?.text || transcriptText);
        currentItem.transcriptJsonKey = String(data?.transcriptJsonKey || currentItem.transcriptJsonKey || '');
        currentItem.transcriptTextKey = String(data?.transcriptTextKey || currentItem.transcriptTextKey || '');
        state.viralBreakdown.transcriptDrafts = {
          ...(state.viralBreakdown.transcriptDrafts || {}),
          [normalizedVideoKey]: currentItem.transcriptText,
        };
        state.viralBreakdown.notice = '台词已保存';
      } finally {
        state.viralBreakdown.transcriptSaving = false;
        renderViralBreakdownWorkbench();
      }
    }

    function renderMaterialLibrary(container, kind, items, title, emptyText) {
      if (!container) return;
      const openLabel = kind === 'script' ? '打开知识库' : '打开素材库';
      container.innerHTML = `
        <div class="material-card">
          <div class="material-heading">
            <div class="material-title">${escapeHtml(title)}</div>
            <div class="material-meta">${escapeHtml(items.length ? `${items.length} 个文件` : emptyText)}</div>
          </div>
          <div class="material-actions">
            <button type="button" class="material-library-button" data-show-user-materials="${escapeHtml(kind)}">${openLabel}</button>
            <button type="button" class="material-add-button" data-add-user-material="${escapeHtml(kind)}">添加素材</button>
          </div>
        </div>
      `;
    }

    function renderMaterialLibraryModal() {
      const visible = !!state.materialModal.visible;
      if (!els.materialLibraryModal) return;
      const isScriptKnowledge = state.materialModal.kind === 'script';
      els.materialLibraryModal.classList.toggle('hidden', !visible);
      els.materialLibraryModal.classList.toggle('script-knowledge-mode', isScriptKnowledge);
      els.scriptKnowledgeToolbar?.classList.toggle('hidden', !isScriptKnowledge);
      els.materialLibraryWall.classList.toggle('material-wall', !isScriptKnowledge);
      els.materialLibraryAddButton.textContent = isScriptKnowledge ? '导入剧本' : '添加素材';
      els.materialLibraryAddButton.dataset.addUserMaterial = isScriptKnowledge ? 'script' : 'image';
      els.materialLibraryOpenFolderButton.textContent = isScriptKnowledge ? '打开原稿文件夹' : '打开文件夹';
      const model = getMaterialLibraryModalModel();
      els.materialLibraryTitle.textContent = model.title;
      els.materialLibrarySub.textContent = model.sub;
      if (!visible) return;
      if (isScriptKnowledge) {
        renderScriptKnowledgeModal();
        return;
      }
      if (!model.items.length) {
        els.materialLibraryWall.innerHTML = `<div class="empty">${escapeHtml(model.emptyText)}</div>`;
        return;
      }
      els.materialLibraryWall.innerHTML = model.items.map((item) => buildMaterialWallCardMarkup(item)).join('');
    }

    function getMaterialLibraryModalModel() {
      const materials = state.userMaterials || {};
      const kind = state.materialModal.kind === 'script' ? 'script' : 'image';
      const items = kind === 'script' ? (materials.scripts || []) : (materials.images || []);
      return {
        kind,
        items,
        title: kind === 'script' ? '剧本知识库' : '图片素材库',
        sub: kind === 'script'
          ? 'PostgreSQL 词法检索 · pg_trgm + tsvector · 无 Embedding'
          : `${items.length} 个图片素材，点击卡片可插入到当前对话。`,
        emptyText: kind === 'script'
          ? '还没有剧本知识。点右上角添加 TXT、Markdown 或 DOCX。'
          : '还没有图片素材。点右上角打开文件夹，把图片放进去。',
      };
    }

    function renderScriptKnowledgeModal() {
      const knowledge = state.scriptKnowledge;
      if (els.scriptKnowledgeSearchInput && document.activeElement !== els.scriptKnowledgeSearchInput) {
        els.scriptKnowledgeSearchInput.value = knowledge.query || '';
      }
      const statusModel = getScriptKnowledgeStatusModel();
      els.scriptKnowledgeStatus.textContent = statusModel.text;
      els.scriptKnowledgeStatus.classList.toggle('is-error', statusModel.error);
      els.scriptKnowledgeSyncButton.disabled = knowledge.syncing || knowledge.loading;
      els.scriptKnowledgeSyncButton.textContent = knowledge.syncing ? '同步中' : '同步索引';
      els.materialLibraryWall.innerHTML = buildScriptKnowledgeLayoutMarkup();
      if (knowledge.resetDetailScroll) {
        const detailPanel = els.materialLibraryWall.querySelector('.script-knowledge-detail');
        if (detailPanel) detailPanel.scrollTop = 0;
        knowledge.resetDetailScroll = false;
      }
    }

    function getScriptKnowledgeStatusModel() {
      const knowledge = state.scriptKnowledge;
      const status = knowledge.status || {};
      if (knowledge.syncing) return { text: '正在同步索引', error: false };
      if (knowledge.loading) return { text: '正在检索', error: false };
      if (!status.available) {
        const databaseMissing = String(status.error || '').toLowerCase().includes('database "ai8video" does not exist');
        return { text: databaseMissing ? '数据库待初始化' : 'PostgreSQL 未连接', error: true };
      }
      const ready = Number(status.readyCount || 0);
      const total = Number(status.documentCount || 0);
      return { text: `${ready}/${total} 已索引 · 无向量模型`, error: false };
    }

    function buildScriptKnowledgeLayoutMarkup() {
      const knowledge = state.scriptKnowledge;
      const errorMarkup = knowledge.error
        ? `<div class="modal-error">${escapeHtml(knowledge.error)}</div>`
        : '';
      const listMarkup = buildScriptKnowledgeListMarkup(knowledge.items || []);
      const detailMarkup = buildScriptKnowledgeDetailMarkup(knowledge.detail);
      return `
        <div class="script-knowledge-layout">
          <section class="script-knowledge-list">${errorMarkup}${listMarkup}</section>
          <section class="script-knowledge-detail">${detailMarkup}</section>
        </div>
      `;
    }

    function buildScriptKnowledgeListMarkup(items) {
      if (state.scriptKnowledge.loading && !items.length) {
        return '<div class="script-knowledge-empty">正在读取剧本知识库…</div>';
      }
      if (!state.scriptKnowledge.status?.available) {
        const localCount = Number(state.userMaterials?.scriptCount || 0);
        return `<div class="script-knowledge-empty">PostgreSQL 当前不可用。<br>${localCount} 份原始剧本仍安全保存在本地文件夹，数据库恢复后点“同步索引”即可重建。</div>`;
      }
      if (!items.length) {
        const copy = state.scriptKnowledge.query
          ? '没有找到匹配内容，可以换一个关键词。'
          : '知识库还是空的，请添加 TXT、Markdown 或 DOCX。';
        return `<div class="script-knowledge-empty">${escapeHtml(copy)}</div>`;
      }
      return items.map((item) => buildScriptKnowledgeCardMarkup(item)).join('');
    }
