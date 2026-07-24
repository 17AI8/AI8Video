    function getViralBreakdownScriptTreeDraft(videoKey) {
      const drafts = state.viralBreakdown?.scriptGuessTrees || {};
      return drafts[String(videoKey || '')] || null;
    }

    function setViralBreakdownScriptTreeDraft(videoKey, draft) {
      const key = String(videoKey || '').trim();
      if (!key) return;
      const next = { ...(state.viralBreakdown.scriptGuessTrees || {}) };
      if (draft) next[key] = draft;
      else delete next[key];
      state.viralBreakdown.scriptGuessTrees = next;
    }

    function hydrateViralBreakdownScriptDraftsFromItems(items) {
      const processing = !!state.viralBreakdown.scriptGuessProcessing
        || !!state.viralBreakdown.scriptTreeProcessing;
      const selectedKey = String(state.viralBreakdown.selectedVideoKey || '');
      const nextDrafts = { ...(state.viralBreakdown.scriptGuessDrafts || {}) };
      const nextTrees = { ...(state.viralBreakdown.scriptGuessTrees || {}) };
      for (const item of Array.isArray(items) ? items : []) {
        const key = String(item?.videoKey || '').trim();
        if (!key) continue;
        if (processing && key === selectedKey) continue;
        const draft = item?.scriptDraft;
        if (!draft || typeof draft !== 'object') continue;
        const scriptText = String(draft.scriptText || '');
        if (scriptText) nextDrafts[key] = scriptText;
        const leaves = Array.isArray(draft.leaves) ? draft.leaves : [];
        if (draft.tree && leaves.length) {
          nextTrees[key] = {
            text: String(draft.text || ''),
            scriptText,
            transcriptText: '',
            tree: draft.tree,
            leaves,
            quality: draft.quality || null,
            detail: draft.detail || null,
            saved: !!draft.saved,
            relativePath: String(draft.relativePath || ''),
            documentId: Number(draft.documentId || 0) || 0,
          };
        } else if (Object.prototype.hasOwnProperty.call(nextTrees, key) && !processing) {
          delete nextTrees[key];
        }
      }
      state.viralBreakdown.scriptGuessDrafts = nextDrafts;
      state.viralBreakdown.scriptGuessTrees = nextTrees;
    }

    async function persistViralBreakdownScriptDraft(videoKey, options = {}) {
      const key = String(videoKey || '').trim();
      if (!key) return null;
      const res = await fetch('/api/viral-breakdown/save-script-draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          videoKey: key,
          text: options.scriptText,
          composedText: options.composedText,
          tree: options.tree,
          leaves: options.leaves,
          detail: options.detail,
          quality: options.quality,
          saved: options.saved,
          relativePath: options.relativePath,
          documentId: options.documentId,
          clearTree: !!options.clearTree,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) {
        throw new Error(data?.error || '保存剧本草稿失败');
      }
      return data;
    }

    function buildViralBreakdownScriptTreeDetail(draft) {
      if (!draft?.detail) return null;
      return draft.detail;
    }

    function syncViralBreakdownSaveScriptTreeButton(treeDraft) {
      const saveButton = document.getElementById('viralBreakdownSaveScriptTreeButton');
      if (!saveButton) return;
      const hasTree = !!(treeDraft?.tree && Array.isArray(treeDraft.leaves) && treeDraft.leaves.length);
      const onTreeTab = getViralBreakdownScriptSubTab() === 'tree';
      saveButton.classList.toggle('hidden', !hasTree || !onTreeTab);
      saveButton.disabled = !hasTree || !!treeDraft?.saved || !!state.viralBreakdown.scriptTreeSaving;
      saveButton.textContent = treeDraft?.saved
        ? '已存入知识库'
        : (state.viralBreakdown.scriptTreeSaving ? '存入中...' : '存入知识库');
    }

    function buildViralBreakdownScriptSkeletonPaneMarkup(scriptText) {
      return `
        <textarea
          class="viral-breakdown-text-output viral-breakdown-text-editor viral-breakdown-script-guess-editor"
          spellcheck="false"
          placeholder="点击“猜剧本”后，这里显示多模态生成的剧本骨架（情节逻辑）。"
        >${escapeHtml(scriptText)}</textarea>
      `;
    }

    function buildViralBreakdownScriptTreePaneMarkup(treeDraft) {
      const detail = buildViralBreakdownScriptTreeDetail(treeDraft);
      const treeMarkup = detail ? buildScriptKnowledgeTreeMarkup(detail) : '';
      if (!treeMarkup) {
        return '<div class="viral-breakdown-empty">先生成剧本骨架；知识库 Agent 会结合骨架与台词建临时知识树，未点「存入知识库」前只留在本窗。</div>';
      }
      return `
        <div class="viral-breakdown-script-tree">
          ${treeMarkup}
        </div>
      `;
    }

    function buildViralBreakdownScriptGuessPaneMarkup(scriptText, treeDraft) {
      if (getViralBreakdownScriptSubTab() === 'tree') {
        return buildViralBreakdownScriptTreePaneMarkup(treeDraft);
      }
      return buildViralBreakdownScriptSkeletonPaneMarkup(scriptText);
    }

    async function buildViralBreakdownScriptTreeFromText(videoKey, scriptText, transcriptText) {
      const res = await fetch('/api/viral-breakdown/build-script-tree', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          videoKey,
          text: scriptText,
          transcript: transcriptText,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data?.error || '知识库 Agent 建树失败');
      }
      setViralBreakdownScriptTreeDraft(videoKey, {
        text: String(data?.text || ''),
        scriptText: String(data?.scriptText || scriptText || ''),
        transcriptText: String(data?.transcriptText || transcriptText || ''),
        tree: data?.tree || null,
        leaves: Array.isArray(data?.leaves) ? data.leaves : [],
        quality: data?.quality || null,
        detail: data?.detail || null,
        saved: false,
        relativePath: '',
        documentId: 0,
      });
      return data;
    }

    async function saveViralBreakdownScriptTreeDraft(videoKey) {
      const draft = getViralBreakdownScriptTreeDraft(videoKey);
      if (!draft?.tree || !Array.isArray(draft.leaves) || !draft.leaves.length) {
        throw new Error('没有可存入的临时知识树');
      }
      state.viralBreakdown.scriptTreeSaving = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在把临时知识树存入知识库...';
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/save-script-tree', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            videoKey,
            text: draft.text || getViralBreakdownScriptGuessDraft(videoKey),
            tree: draft.tree,
            leaves: draft.leaves,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '存入知识库失败');
        }
        setViralBreakdownScriptTreeDraft(videoKey, {
          ...draft,
          saved: true,
          relativePath: String(data?.relativePath || ''),
          documentId: Number(data?.documentId || 0) || 0,
          detail: data?.detail || draft.detail,
        });
        state.viralBreakdown.notice = data?.relativePath
          ? `已存入知识库：${data.relativePath}`
          : '已存入知识库';
      } finally {
        state.viralBreakdown.scriptTreeSaving = false;
        renderViralBreakdownWorkbench();
      }
    }

    function toggleViralBreakdownScriptTreeNode(toggleButton) {
      const item = toggleButton.closest('.script-knowledge-tree-branch, .script-knowledge-tree-leaf');
      if (!item || item.classList.contains('is-empty')) return;
      const open = !item.classList.contains('is-open');
      item.classList.toggle('is-open', open);
      item.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
