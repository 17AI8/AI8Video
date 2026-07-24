    function closeViralBreakdownVideoMenu() {
      const root = document.querySelector('[data-viral-select="video"]');
      const button = document.getElementById('viralBreakdownVideoSelectButton');
      const list = document.getElementById('viralBreakdownVideoSelectList');
      root?.classList.remove('is-open');
      if (button) button.setAttribute('aria-expanded', 'false');
      if (list) list.hidden = true;
    }

    function syncViralBreakdownVideoSelect(currentItem) {
      const button = document.getElementById('viralBreakdownVideoSelectButton');
      const label = document.getElementById('viralBreakdownVideoSelectLabel');
      const list = document.getElementById('viralBreakdownVideoSelectList');
      if (!button || !label || !list) return;
      const items = Array.isArray(state.viralBreakdown.items) ? state.viralBreakdown.items : [];
      const selectedKey = currentItem ? String(currentItem.videoKey || '') : '';
      const labelText = currentItem
        ? String(currentItem.name || currentItem.videoKey || '未命名视频')
        : (items.length ? '请选择视频' : '还没有上传视频');
      button.disabled = !items.length || !!state.viralBreakdown.loading;
      label.textContent = labelText;
      button.title = labelText;
      list.innerHTML = items.map((item) => {
        const key = String(item?.videoKey || '');
        const name = String(item?.name || item?.videoKey || '未命名视频');
        const active = key && key === selectedKey ? ' is-active' : '';
        return `<button type="button" class="viral-breakdown-select-option${active}" role="option" aria-selected="${key === selectedKey ? 'true' : 'false'}" data-viral-video-key="${escapeHtml(key)}" title="${escapeHtml(name)}">${escapeHtml(name)}</button>`;
      }).join('');
    }

    function selectViralBreakdownVideo(videoKey) {
      const nextKey = String(videoKey || '');
      if (nextKey && nextKey === String(state.viralBreakdown.selectedVideoKey || '')) {
        closeViralBreakdownVideoMenu();
        return;
      }
      state.viralBreakdown.selectedVideoKey = nextKey;
      state.viralBreakdown.activeTab = 'grid';
      state.viralBreakdown.error = '';
      if (!state.viralBreakdown.loading) {
        state.viralBreakdown.notice = nextKey ? '已切换当前视频。' : '';
      }
      closeViralBreakdownVideoMenu();
      renderViralBreakdownWorkbench();
    }

    function renderViralBreakdownWorkbench() {
      const archiveMeta = document.getElementById('viralBreakdownArchiveMeta');
      const statusText = document.getElementById('viralBreakdownStatusText');
      const intervalInput = document.getElementById('viralBreakdownIntervalInput');
      const targetRatioSelect = document.getElementById('viralBreakdownTargetRatio');
      const processFramesButton = document.getElementById('viralBreakdownProcessFramesButton');
      const transcribeButton = document.getElementById('viralBreakdownTranscribeButton');
      const guessScriptButton = document.getElementById('viralBreakdownGuessScriptButton');
      const saveTranscriptButton = document.getElementById('viralBreakdownSaveTranscriptButton');
      const originalMeta = document.getElementById('viralBreakdownOriginalMeta');
      const infoMeta = document.getElementById('viralBreakdownInfoMeta');
      const scriptGuessMeta = document.getElementById('viralBreakdownScriptGuessMeta');
      const gridMeta = document.getElementById('viralBreakdownGridMeta');
      const transcriptMeta = document.getElementById('viralBreakdownTranscriptMeta');
      const generatedMeta = document.getElementById('viralBreakdownGeneratedMeta');
      const originalPane = document.getElementById('viralBreakdownOriginalPane');
      const infoPane = document.getElementById('viralBreakdownInfoPane');
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
      const scriptTreeDraft = currentItem
        ? getViralBreakdownScriptTreeDraft(currentItem.videoKey)
        : null;
      const scriptSubTab = getViralBreakdownScriptSubTab();
      const transcriptHasUnsavedChanges = !!currentItem && transcriptDisplayText !== transcriptTextFromItem;
      if (archiveMeta) {
        archiveMeta.textContent = state.viralBreakdown.archiveDisplay || '0 个视频 · 0 B';
      }
      syncViralBreakdownVideoSelect(currentItem);
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
        const guessing = !!state.viralBreakdown.scriptGuessProcessing || !!state.viralBreakdown.scriptTreeProcessing;
        guessScriptButton.disabled = !currentItem || guessing || !!state.viralBreakdown.loading;
        guessScriptButton.textContent = state.viralBreakdown.scriptGuessProcessing
          ? '猜测中...'
          : (state.viralBreakdown.scriptTreeProcessing ? '建树中...' : '猜剧本');
      }
      syncViralBreakdownSaveScriptTreeButton(scriptTreeDraft);
      if (saveTranscriptButton) {
        saveTranscriptButton.disabled = !currentItem || !!state.viralBreakdown.transcriptSaving || !transcriptHasUnsavedChanges;
        saveTranscriptButton.textContent = state.viralBreakdown.transcriptSaving
          ? '保存中...'
          : transcriptHasUnsavedChanges
            ? '保存台词'
            : '已保存';
      }
      if (statusText) {
        const error = String(state.viralBreakdown.error || '');
        const notice = String(state.viralBreakdown.notice || '');
        const resumeStage = String(state.viralBreakdown.scriptResumeStage || '');
        const message = error || notice;
        statusText.classList.toggle('is-error', Boolean(error));
        if (!message) {
          statusText.innerHTML = '';
        } else {
          const retryLabel = resumeStage === 'tree'
            ? '从知识库建树重试'
            : (resumeStage === 'skeleton' ? '重新生成骨架' : '');
          statusText.innerHTML = `
            <span class="viral-breakdown-status-message" title="${escapeHtml(message)}">${escapeHtml(message)}</span>
            ${retryLabel ? `<button type="button" id="viralBreakdownRetryButton" class="viral-breakdown-retry-button">${escapeHtml(retryLabel)}</button>` : ''}
          `;
          const retryButton = document.getElementById('viralBreakdownRetryButton');
          if (retryButton) {
            retryButton.disabled = !!state.viralBreakdown.scriptGuessProcessing
              || !!state.viralBreakdown.scriptTreeProcessing
              || !!state.viralBreakdown.loading;
            retryButton.onclick = async () => {
              try {
                await retryViralBreakdownScriptFromBreakpoint();
              } catch (error) {
                console.error(error);
                state.viralBreakdown.error = friendlyViralBreakdownScriptError(
                  error,
                  state.viralBreakdown.scriptResumeStage || 'tree',
                );
                renderViralBreakdownWorkbench();
              }
            };
          }
        }
      }
      if (originalMeta) {
        originalMeta.textContent = currentItem ? `${currentItem.sizeLabel || humanizeByteSize(currentItem.sizeBytes || 0)}` : '';
      }
      if (infoMeta) {
        infoMeta.textContent = currentItem ? viralBreakdownMediaSummary(currentItem) : '';
      }
      if (scriptGuessMeta) {
        if (scriptSubTab === 'tree') {
          if (scriptTreeDraft?.saved) {
            const leafCount = Array.isArray(scriptTreeDraft.leaves) ? scriptTreeDraft.leaves.length : 0;
            scriptGuessMeta.textContent = leafCount ? `${leafCount} 段 · 已存入知识库` : '已存入知识库';
          } else if (scriptTreeDraft?.tree) {
            const leafCount = Array.isArray(scriptTreeDraft.leaves) ? scriptTreeDraft.leaves.length : 0;
            scriptGuessMeta.textContent = `临时树 ${leafCount} 段 · 未存入前只留本窗`;
          } else if (state.viralBreakdown.scriptTreeProcessing) {
            scriptGuessMeta.textContent = '建树中...';
          } else {
            scriptGuessMeta.textContent = '等待建树';
          }
        } else if (state.viralBreakdown.scriptGuessProcessing) {
          scriptGuessMeta.textContent = scriptGuessDisplayText
            ? `${scriptGuessDisplayText.length} 字 · 生成中`
            : '生成中';
        } else {
          scriptGuessMeta.textContent = scriptGuessDisplayText ? `${scriptGuessDisplayText.length} 字 · 可编辑` : '等待猜剧本';
        }
      }
      if (gridMeta) {
        gridMeta.textContent = currentItem?.frameCount ? `${currentItem.frameCount} 张截图` : '';
      }
      if (transcriptMeta) {
        transcriptMeta.textContent = transcriptDisplayText ? `${transcriptDisplayText.length} 字` : '';
      }
      if (generatedMeta) {
        generatedMeta.textContent = currentItem?.generatedVideoUrl
          ? '已有成片'
          : (isViralBreakdownGenerateReady(currentItem) ? '可回填' : '待准备');
      }
      if (originalPane) {
        originalPane.innerHTML = currentItem?.videoUrl
          ? `<video src="${escapeHtml(String(currentItem.videoUrl || ''))}" controls playsinline preload="metadata"></video>`
          : '<div class="viral-breakdown-empty">请先上传一个视频。</div>';
      }
      if (infoPane) {
        infoPane.innerHTML = buildViralBreakdownVideoInfoMarkup(currentItem);
      }
      if (scriptGuessPane) {
        scriptGuessPane.innerHTML = buildViralBreakdownScriptGuessPaneMarkup(
          scriptGuessDisplayText,
          scriptTreeDraft,
        );
        const scriptGuessEditor = scriptGuessPane.querySelector('.viral-breakdown-script-guess-editor');
        if (scriptGuessEditor instanceof HTMLTextAreaElement && currentItem?.videoKey) {
          scriptGuessEditor.oninput = () => {
            const normalizedVideoKey = String(currentItem.videoKey || '').trim();
            const nextScriptGuessText = String(scriptGuessEditor.value || '');
            state.viralBreakdown.scriptGuessDrafts = {
              ...(state.viralBreakdown.scriptGuessDrafts || {}),
              [normalizedVideoKey]: nextScriptGuessText,
            };
            const existingTree = getViralBreakdownScriptTreeDraft(normalizedVideoKey);
            const treeScriptText = String(existingTree?.scriptText || '');
            if (existingTree && treeScriptText && treeScriptText !== nextScriptGuessText) {
              setViralBreakdownScriptTreeDraft(normalizedVideoKey, null);
              syncViralBreakdownSaveScriptTreeButton(null);
            }
            if (scriptGuessMeta && getViralBreakdownScriptSubTab() === 'skeleton') {
              scriptGuessMeta.textContent = nextScriptGuessText ? `${nextScriptGuessText.length} 字 · 可编辑` : '等待猜剧本';
            }
            clearTimeout(state.viralBreakdown.scriptDraftSaveTimer);
            state.viralBreakdown.scriptDraftSaveTimer = setTimeout(() => {
              persistViralBreakdownScriptDraft(normalizedVideoKey, {
                scriptText: nextScriptGuessText,
                clearTree: !!(existingTree && treeScriptText && treeScriptText !== nextScriptGuessText),
              }).catch((error) => console.warn(error));
            }, 800);
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
        generatedPane.innerHTML = buildViralBreakdownGeneratedPaneMarkup(currentItem);
      }
      syncViralBreakdownPreviewTab();
      syncViralBreakdownActiveTab();
      syncViralBreakdownScriptSubTab();
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
          ? '检索并引用本地剧本知识'
          : `${items.length} 个图片素材，点击卡片可插入到当前对话。`,
        emptyText: kind === 'script'
          ? '还没有剧本知识。点右上角添加 TXT、Markdown 或 DOCX。'
          : '还没有图片素材。点右上角打开文件夹，把图片放进去。',
      };
    }

    function renderScriptKnowledgeModal() {
      const knowledge = state.scriptKnowledge;
      const previousStream = els.materialLibraryWall.querySelector('.script-knowledge-ingestion-stream');
      const previousScrollTop = previousStream?.scrollTop || 0;
      const shouldStickToBottom = previousStream
        ? previousStream.scrollHeight - previousStream.scrollTop - previousStream.clientHeight < 24
        : true;
      if (els.scriptKnowledgeSearchInput && document.activeElement !== els.scriptKnowledgeSearchInput) {
        els.scriptKnowledgeSearchInput.value = knowledge.query || '';
      }
      const statusModel = getScriptKnowledgeStatusModel();
      els.scriptKnowledgeStatus.textContent = statusModel.text;
      els.scriptKnowledgeStatus.classList.toggle('is-error', statusModel.error);
      els.materialLibraryWall.innerHTML = buildScriptKnowledgeLayoutMarkup();
      const nextStream = els.materialLibraryWall.querySelector('.script-knowledge-ingestion-stream');
      if (nextStream) {
        nextStream.scrollTop = shouldStickToBottom ? nextStream.scrollHeight : previousScrollTop;
        syncScriptKnowledgeTypewriter(nextStream);
      }
      if (knowledge.resetDetailScroll) {
        const panel = els.materialLibraryWall.querySelector('.script-knowledge-panel.is-active');
        if (panel) panel.scrollTop = 0;
        knowledge.resetDetailScroll = false;
      }
    }

    function syncScriptKnowledgeTypewriter(stream) {
      const documentId = Number(state.scriptKnowledge.selectedId || 0);
      const lines = stream.querySelectorAll('[data-typewriter-text]');
      const activeKeys = new Set();
      lines.forEach((line) => {
        const key = `${documentId}:${line.dataset.stage || ''}`;
        const target = line.dataset.typewriterText || '';
        const current = scriptKnowledgeTypewriterLines.get(key);
        const shown = current && target.startsWith(current.shown) ? current.shown : '';
        scriptKnowledgeTypewriterLines.set(key, { shown, target });
        line.dataset.typewriterKey = key;
        line.textContent = shown;
        activeKeys.add(key);
      });
      for (const key of scriptKnowledgeTypewriterLines.keys()) {
        if (!activeKeys.has(key)) scriptKnowledgeTypewriterLines.delete(key);
      }
      startScriptKnowledgeTypewriter();
    }

    function startScriptKnowledgeTypewriter() {
      if (scriptKnowledgeTypewriterTimer || !scriptKnowledgeTypewriterLines.size) return;
      scriptKnowledgeTypewriterTimer = window.setInterval(tickScriptKnowledgeTypewriter, 32);
    }

    function tickScriptKnowledgeTypewriter() {
      let pending = false;
      scriptKnowledgeTypewriterLines.forEach((lineState, key) => {
        if (lineState.shown.length >= lineState.target.length) return;
        const remaining = lineState.target.length - lineState.shown.length;
        const step = remaining > 120 ? 3 : remaining > 40 ? 2 : 1;
        lineState.shown = lineState.target.slice(0, lineState.shown.length + step);
        const line = els.materialLibraryWall.querySelector(`[data-typewriter-key="${key}"]`);
        if (line) line.textContent = lineState.shown;
        pending = true;
      });
      if (pending) return;
      window.clearInterval(scriptKnowledgeTypewriterTimer);
      scriptKnowledgeTypewriterTimer = null;
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
        return `<div class="script-knowledge-empty">PostgreSQL 当前不可用。<br>${localCount} 份原始剧本仍安全保存在本地文件夹，数据库恢复后可再次执行知识入库。</div>`;
      }
      if (!items.length) {
        const copy = state.scriptKnowledge.query
          ? '没有找到匹配内容，可以换一个关键词。'
          : '知识库还是空的，请添加 TXT、Markdown 或 DOCX。';
        return `<div class="script-knowledge-empty">${escapeHtml(copy)}</div>`;
      }
      return items.map((item) => buildScriptKnowledgeCardMarkup(item)).join('');
    }
