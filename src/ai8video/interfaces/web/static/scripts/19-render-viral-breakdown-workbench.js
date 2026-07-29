    function closeViralBreakdownVideoMenu() {
      const root = document.querySelector('[data-viral-select="video"]');
      const button = document.getElementById('viralBreakdownVideoSelectButton');
      const list = document.getElementById('viralBreakdownVideoSelectList');
      root?.classList.remove('is-open');
      if (button) button.setAttribute('aria-expanded', 'false');
      if (list) list.hidden = true;
    }

    const VIRAL_BREAKDOWN_MAX_FRAME_COUNT = 188;

    function minimumViralBreakdownInterval(item) {
      const duration = Number(item?.media?.durationSeconds || 0);
      if (!Number.isFinite(duration) || duration <= 0) return 0.2;
      return Math.max(0.2, Math.ceil((duration / VIRAL_BREAKDOWN_MAX_FRAME_COUNT) * 10) / 10);
    }

    function clampViralBreakdownInterval(item, intervalSeconds) {
      const minimum = minimumViralBreakdownInterval(item);
      const interval = Number(intervalSeconds);
      return Number.isFinite(interval) && interval > 0 ? Math.max(minimum, interval) : minimum;
    }

    function estimateViralBreakdownFrameCount(item, intervalSeconds) {
      const duration = Number(item?.media?.durationSeconds || 0);
      if (!Number.isFinite(duration) || duration <= 0) return 0;
      return Math.max(1, Math.ceil(duration / clampViralBreakdownInterval(item, intervalSeconds)));
    }

    function syncViralBreakdownFrameEstimate(item, intervalSeconds) {
      const output = document.getElementById('viralBreakdownFrameEstimate');
      if (!output) return;
      const count = estimateViralBreakdownFrameCount(item, intervalSeconds);
      output.textContent = count > 0 ? `预计 ${count} 张` : '预计 — 张';
    }

    function openViralBreakdownGridFrame(event, image, item) {
      const columns = Number(item?.gridColumns || 0);
      const rows = Number(item?.gridRows || 0);
      const frameCount = Number(item?.frameCount || 0);
      if (!columns || !rows || !frameCount || !item?.frameDirKey) return;
      const rect = image.getBoundingClientRect();
      const naturalRatio = image.naturalWidth / Math.max(1, image.naturalHeight);
      const boxRatio = rect.width / Math.max(1, rect.height);
      const width = boxRatio > naturalRatio ? rect.height * naturalRatio : rect.width;
      const height = boxRatio > naturalRatio ? rect.height : rect.width / naturalRatio;
      const left = rect.left + (rect.width - width) / 2;
      const top = rect.top + (rect.height - height) / 2;
      const x = event.clientX - left;
      const y = event.clientY - top;
      if (x < 0 || y < 0 || x >= width || y >= height) return;
      const column = Math.min(columns - 1, Math.floor(x / width * columns));
      const row = Math.min(rows - 1, Math.floor(y / height * rows));
      const frameIndex = row * columns + column + 1;
      if (frameIndex > frameCount) return;
      showViralBreakdownFrameLightbox(item, frameIndex);
    }

    function showViralBreakdownFrameLightbox(item, frameIndex) {
      document.getElementById('viralBreakdownFrameLightbox')?.remove();
      const key = `${String(item.frameDirKey).replace(/\/$/, '')}/frame-${String(frameIndex).padStart(4, '0')}.jpg`;
      const root = document.createElement('div');
      root.id = 'viralBreakdownFrameLightbox';
      root.className = 'viral-breakdown-frame-lightbox';
      root.innerHTML = `<div class="viral-breakdown-frame-lightbox-panel" role="dialog" aria-modal="true" aria-label="截图 ${frameIndex} 大图预览"><button type="button" class="viral-breakdown-frame-lightbox-close" aria-label="关闭">×</button><img src="/api/viral-breakdown/file?key=${encodeURIComponent(key)}&v=${Date.now()}" alt="截图 ${frameIndex}"><strong>截图 ${frameIndex} / ${Number(item.frameCount || 0)}</strong></div>`;
      const close = () => root.remove();
      root.addEventListener('click', (event) => { if (event.target === root) close(); });
      root.querySelector('button')?.addEventListener('click', close);
      document.body.appendChild(root);
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
      button.disabled = !items.length || isViralBreakdownBusy();
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
      if (isViralBreakdownBusy()) return;
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

    function setViralBreakdownContextAction(button, config) {
      const hidden = !!config.hidden;
      button.classList.toggle('hidden', hidden);
      button.setAttribute('aria-hidden', hidden ? 'true' : 'false');
      button.disabled = hidden || !!config.disabled;
      button.textContent = String(config.label || '');
      button.title = String(config.title || '');
    }

    function syncViralBreakdownContextAction() {
      const button = document.getElementById('viralBreakdownContextActionButton');
      if (!button) return;
      const item = getSelectedViralBreakdownItem();
      const tab = getViralBreakdownActiveTab();
      const busy = isViralBreakdownBusy();
      if (tab === 'generated') {
        setViralBreakdownContextAction(button, { hidden: true });
        return;
      }
      if (tab === 'grid') {
        setViralBreakdownContextAction(button, {
          label: state.viralBreakdown.frameProcessing ? '截图中...' : '拆解画面',
          disabled: !item || busy,
          title: item ? '' : '请先选择视频',
        });
        return;
      }
      const framesReady = hasViralBreakdownFrames(item);
      if (tab === 'transcript') {
        setViralBreakdownContextAction(button, {
          label: state.viralBreakdown.transcriptProcessing ? '识别中...' : '分析台词',
          disabled: !framesReady || busy,
          title: framesReady ? '' : '请先完成拆解画面',
        });
        return;
      }
      const transcriptReady = hasViralBreakdownTranscript(item);
      if (tab === 'shot-language') {
        const dirty = hasUnsavedViralBreakdownTranscript(item);
        const hasAnalysis = !!item?.shotLanguageAnalysis?.text;
        setViralBreakdownContextAction(button, {
          label: state.viralBreakdown.shotLanguageProcessing ? '分析中...' : (hasAnalysis ? '重新分析镜头' : '分析镜头'),
          disabled: !transcriptReady || !framesReady || dirty || busy,
          title: dirty ? '请先保存台词修改' : (transcriptReady ? '' : (framesReady ? '请先完成分析台词' : '请先完成拆解画面')),
        });
        return;
      }
      const shotReady = hasCurrentViralBreakdownShotLanguage(item);
      const transcriptDirty = hasUnsavedViralBreakdownTranscript(item);
      setViralBreakdownContextAction(button, {
        label: state.viralBreakdown.scriptGuessProcessing ? '猜测中...' : (state.viralBreakdown.scriptTreeProcessing ? '建树中...' : '猜剧本'),
        disabled: !shotReady || transcriptDirty || busy,
        title: transcriptDirty ? '请先保存修改后的台词并重新分析镜头语言' : (shotReady ? '' : '请先完成有效的镜头语言分析'),
      });
    }

    function renderViralBreakdownWorkbench() {
      const archiveMeta = document.getElementById('viralBreakdownArchiveMeta');
      const statusText = document.getElementById('viralBreakdownStatusText');
      const intervalInput = document.getElementById('viralBreakdownIntervalInput');
      const targetRatioSelect = document.getElementById('viralBreakdownTargetRatio');
      const uploadInput = document.getElementById('viralBreakdownUploadInput');
      const uploadButton = document.getElementById('viralBreakdownUploadButton');
      const saveTranscriptButton = document.getElementById('viralBreakdownSaveTranscriptButton');
      const exportTranscriptMp3Button = document.getElementById('viralBreakdownExportTranscriptMp3Button');
      const originalMeta = document.getElementById('viralBreakdownOriginalMeta');
      const scriptGuessMeta = document.getElementById('viralBreakdownScriptGuessMeta');
      const gridMeta = document.getElementById('viralBreakdownGridMeta');
      const shotLanguageMeta = document.getElementById('viralBreakdownShotLanguageMeta');
      const transcriptMeta = document.getElementById('viralBreakdownTranscriptMeta');
      const transcriptUnsaved = document.getElementById('viralBreakdownTranscriptUnsaved');
      const generatedMeta = document.getElementById('viralBreakdownGeneratedMeta');
      const originalPane = document.getElementById('viralBreakdownOriginalPane');
      const infoPane = document.getElementById('viralBreakdownInfoPane');
      const scriptGuessPane = document.getElementById('viralBreakdownScriptGuessPane');
      const gridPane = document.getElementById('viralBreakdownGridPane');
      const shotLanguagePane = document.getElementById('viralBreakdownShotLanguagePane');
      const transcriptPane = document.getElementById('viralBreakdownTranscriptPane');
      const generatedPane = document.getElementById('viralBreakdownGeneratedPane');
      const currentItem = getSelectedViralBreakdownItem();
      const shotLanguageAnalysis = currentItem?.shotLanguageAnalysis
        && typeof currentItem.shotLanguageAnalysis === 'object'
        ? currentItem.shotLanguageAnalysis
        : null;
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
      const busy = isViralBreakdownBusy();
      const transcriptHasUnsavedChanges = !!currentItem && (
        transcriptDisplayText !== transcriptTextFromItem
        || hasViralBreakdownTranscriptSegmentChanges(currentItem)
      );
      if (archiveMeta) {
        archiveMeta.textContent = state.viralBreakdown.archiveDisplay || '0 个视频 · 0 B';
      }
      syncViralBreakdownVideoSelect(currentItem);
      if (intervalInput) {
        const videoKey = String(currentItem?.videoKey || '');
        const minimumInterval = minimumViralBreakdownInterval(currentItem);
        if (videoKey && state.viralBreakdown.intervalVideoKey !== videoKey) {
          state.viralBreakdown.intervalVideoKey = videoKey;
          state.viralBreakdown.intervalSeconds = clampViralBreakdownInterval(
            currentItem,
            currentItem?.intervalSeconds || minimumInterval,
          );
          state.viralBreakdown.targetRatio = String(currentItem?.targetRatio || '16:9');
        } else {
          state.viralBreakdown.intervalSeconds = clampViralBreakdownInterval(
            currentItem,
            state.viralBreakdown.intervalSeconds,
          );
        }
        intervalInput.min = String(minimumInterval);
        intervalInput.value = String(state.viralBreakdown.intervalSeconds);
        intervalInput.disabled = busy;
        syncViralBreakdownFrameEstimate(currentItem, intervalInput.value);
      }
      if (targetRatioSelect) {
        targetRatioSelect.value = String(state.viralBreakdown.targetRatio || '16:9');
        targetRatioSelect.disabled = busy;
      }
      if (uploadInput) {
        uploadInput.disabled = busy;
      }
      if (uploadButton) {
        uploadButton.classList.toggle('is-disabled', busy);
        uploadButton.setAttribute('aria-disabled', busy ? 'true' : 'false');
        uploadButton.tabIndex = busy ? -1 : 0;
      }
      syncViralBreakdownContextAction();
      syncViralBreakdownSaveScriptTreeButton(scriptTreeDraft);
      if (saveTranscriptButton) {
        saveTranscriptButton.disabled = !currentItem || busy || !transcriptHasUnsavedChanges;
        saveTranscriptButton.textContent = state.viralBreakdown.transcriptSaving
          ? '保存中...'
          : transcriptHasUnsavedChanges
            ? '保存台词'
            : '已保存';
      }
      if (exportTranscriptMp3Button) {
        const hasTranscriptSegments = !!currentItem
          && getViralBreakdownTranscriptSegments(currentItem.videoKey, currentItem.transcriptSegments).length > 0;
        exportTranscriptMp3Button.disabled = !hasTranscriptSegments || busy || !!state.viralBreakdown.transcriptExporting;
        exportTranscriptMp3Button.textContent = state.viralBreakdown.transcriptExporting ? '导出中...' : '导出 MP3';
        exportTranscriptMp3Button.title = hasTranscriptSegments ? '导出当前预览中的完整配音' : '暂无可导出的时间轴台词';
      }
      transcriptUnsaved?.classList.toggle('hidden', !transcriptHasUnsavedChanges);
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
            retryButton.disabled = busy;
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
      if (shotLanguageMeta) {
        const inputFrameCount = Number(shotLanguageAnalysis?.inputFrameCount || shotLanguageAnalysis?.selectedFrames?.length || 0);
        const imageBatchCount = Number(shotLanguageAnalysis?.imageBatchCount || 0);
        const confidence = Number(shotLanguageAnalysis?.confidence);
        if (state.viralBreakdown.shotLanguageProcessing) {
          shotLanguageMeta.textContent = '分析中...';
        } else if (shotLanguageAnalysis?.stale === true) {
          shotLanguageMeta.textContent = '已失效 · 请重新分析';
        } else if (shotLanguageAnalysis) {
          const confidenceText = Number.isFinite(confidence)
            ? ` · 置信度 ${Math.round(confidence * 100)}%`
            : '';
          shotLanguageMeta.textContent = imageBatchCount
            ? `${inputFrameCount} 张全量帧 · ${imageBatchCount} 批${confidenceText}`
            : `${inputFrameCount} 张分析帧${confidenceText}`;
        } else {
          shotLanguageMeta.textContent = '';
        }
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
          ? `<img src="${escapeHtml(String(currentItem.gridImageUrl || ''))}" alt="拼接好的宫格图，可点击任一宫格预览大图" data-viral-grid-preview>`
          : '<div class="viral-breakdown-empty">点击“拆解画面”后，这里会显示按时间顺序拼好的宫格图。</div>';
      }
      if (shotLanguagePane) {
        shotLanguagePane.innerHTML = buildViralBreakdownShotLanguageMarkup(shotLanguageAnalysis);
      }
      if (transcriptPane) {
        renderViralBreakdownTranscriptTree({
          pane: transcriptPane,
          item: currentItem,
          displayText: transcriptDisplayText,
          meta: transcriptMeta,
          saveButton: saveTranscriptButton,
        });
      }
      if (generatedPane) {
        generatedPane.innerHTML = buildViralBreakdownGeneratedPaneMarkup(currentItem);
      }
      syncViralBreakdownPreviewTab();
      syncViralBreakdownActiveTab();
      syncViralBreakdownScriptSubTab();
      if (state.viralBreakdown.libraryVisible) renderViralBreakdownLibraryModal();
    }

    function renderMaterialLibrary(container, kind, items, title, emptyText) {
      if (!container) return;
      const openLabel = kind === 'script' ? '打开知识库' : '打开素材库';
      const meta = items.length ? `${items.length} 个文件` : '暂无文件';
      container.innerHTML = buildSidebarNavItemMarkup({
        icon: kind === 'script' ? 'script' : 'image',
        title,
        meta,
        actionLabel: openLabel,
        attrs: `data-show-user-materials="${escapeHtml(kind)}"`,
      });
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
          : `${items.length} 个图片素材，点击卡片插入对话，也可直接送入智能修图。`,
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
