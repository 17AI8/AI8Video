    function isViralBreakdownGenerateReady(item) {
      if (!item?.videoKey || !item.gridImageUrl) return false;
      const transcriptText = getViralBreakdownTranscriptDraft(item.videoKey, item.transcriptText || '');
      const scriptText = getViralBreakdownComposerScript(item);
      const treeDraft = getViralBreakdownScriptTreeDraft(item.videoKey);
      return !!(
        String(transcriptText || '').trim()
        && scriptText
        && treeDraft?.tree
        && Array.isArray(treeDraft.leaves)
        && treeDraft.leaves.length
      );
    }

    function getViralBreakdownGenerateMissingLabels(item) {
      const missing = [];
      if (!item?.gridImageUrl) missing.push('拼接宫格');
      const transcriptText = item
        ? getViralBreakdownTranscriptDraft(item.videoKey, item.transcriptText || '')
        : '';
      if (!String(transcriptText || '').trim()) missing.push('识别台词');
      if (!getViralBreakdownComposerScript(item)) missing.push('剧本骨架');
      const treeDraft = item?.videoKey ? getViralBreakdownScriptTreeDraft(item.videoKey) : null;
      if (!(treeDraft?.tree && Array.isArray(treeDraft.leaves) && treeDraft.leaves.length)) {
        missing.push('临时知识库');
      }
      return missing;
    }

    function getViralBreakdownComposerScript(item) {
      if (!item?.videoKey) return '';
      return String(
        getViralBreakdownScriptGuessDraft(item.videoKey)
        || item?.scriptDraft?.scriptText
        || '',
      ).trim();
    }

    function compactViralBreakdownTemporaryLeaves(leaves) {
      const compact = [];
      let remainingChars = 32000;
      for (const leaf of Array.isArray(leaves) ? leaves.slice(0, 80) : []) {
        if (remainingChars <= 0) break;
        const path = Array.isArray(leaf?.path)
          ? leaf.path.map((part) => String(part || '').trim()).filter(Boolean)
          : [];
        const heading = String(leaf?.heading || path.join(' / ') || '未命名叶节点').trim();
        const rawContent = String(leaf?.content || '').trim();
        if (!rawContent) continue;
        const content = rawContent.slice(0, Math.min(2400, remainingChars));
        compact.push({ heading, path, content });
        remainingChars -= content.length;
      }
      return compact;
    }

    function buildViralBreakdownTemporaryKnowledgeReference(item) {
      if (!item?.videoKey) return null;
      const treeDraft = getViralBreakdownScriptTreeDraft(item.videoKey);
      const leaves = compactViralBreakdownTemporaryLeaves(treeDraft?.leaves);
      if (!treeDraft?.tree || !leaves.length) return null;
      const tree = treeDraft.tree || {};
      const sourceName = String(item.name || item.videoKey || '爆款拆解视频').trim();
      const stem = sourceName.replace(/\.[^.]+$/, '');
      const summary = String(
        tree.summary
        || treeDraft?.detail?.summary
        || `基于《${stem}》的宫格、台词和猜剧本骨架生成。`,
      ).trim();
      const tags = Array.isArray(tree.tags) ? tree.tags : [];
      return {
        kind: 'viralBreakdownTemporaryKnowledge',
        videoKey: String(item.videoKey),
        sourceVideoName: sourceName,
        title: `猜剧本临时知识库 · ${stem}`,
        summary,
        tags: ['临时知识库', '猜剧本', ...tags].filter((tag, index, values) => (
          String(tag || '').trim() && values.indexOf(tag) === index
        )),
        leafCount: Array.isArray(treeDraft.leaves) ? treeDraft.leaves.length : leaves.length,
        leaves,
      };
    }

    function buildTemporaryScriptKnowledgeChatPayload() {
      const temporary = state.temporaryScriptKnowledge;
      if (!temporary || !Array.isArray(temporary.leaves) || !temporary.leaves.length) return null;
      return temporary;
    }

    function clearTemporaryScriptKnowledgeReference() {
      if (!state.temporaryScriptKnowledge) return;
      state.temporaryScriptKnowledge = null;
      renderScriptReferenceButton();
      renderScriptReferenceDrawer();
    }

    function buildTemporaryScriptKnowledgeReferenceMarkup(temporary) {
      const leafCount = Number(temporary?.leafCount || temporary?.leaves?.length || 0);
      const preview = normalizeMaterialPreview(temporary?.summary || '本次猜剧本生成的临时知识树');
      return `
        <article class="script-knowledge-list-card script-reference-knowledge-card is-active is-temporary" aria-label="临时知识库，已锁定">
          <span class="script-knowledge-list-title">
            ${escapeHtml(temporary?.title || '猜剧本临时知识库')}
            <span class="material-selected-badge script-reference-temporary-badge">临时知识库 · 已锁定</span>
          </span>
          ${buildScriptKnowledgeTagsMarkup(temporary?.tags || ['猜剧本'])}
          <span class="script-knowledge-list-preview">${escapeHtml(preview)}</span>
          <span class="script-knowledge-list-foot">
            <span>${leafCount} 个叶节点</span>
            <span>发送后自动解绑</span>
          </span>
        </article>
      `;
    }

    function buildViralBreakdownGeneratedPaneMarkup(item) {
      const ready = isViralBreakdownGenerateReady(item);
      if (item?.generatedVideoUrl) {
        return `
          <div class="viral-breakdown-generate-ready is-result">
            <video src="${escapeHtml(String(item.generatedVideoUrl || ''))}" controls playsinline preload="metadata"></video>
            ${ready ? '<button type="button" class="viral-breakdown-ghost-button" data-viral-breakdown-start-generate>准备再次生成</button>' : ''}
            <p>已有成片会继续保留；准备新一轮时只回填主界面，不会在这里自动调用 Agent。</p>
          </div>
        `;
      }
      if (ready) {
        return `
          <div class="viral-breakdown-generate-ready">
            <button type="button" class="viral-breakdown-start-bubble" data-viral-breakdown-start-generate>
              <span>开始生成</span>
            </button>
            <p>点击后把猜出的剧本框架填入主界面，并锁定本次临时知识库。你确认配置后再手动发送，才会走正常生成流程。</p>
          </div>
        `;
      }
      const missing = getViralBreakdownGenerateMissingLabels(item);
      const tip = missing.length
        ? `请先完成：${missing.join('、')}`
        : '请先选择一个视频并完成前面步骤';
      return `<div class="viral-breakdown-empty">${escapeHtml(tip)}</div>`;
    }

    async function startViralBreakdownGeneration() {
      const currentItem = getSelectedViralBreakdownItem();
      if (!isViralBreakdownGenerateReady(currentItem)) {
        const missing = getViralBreakdownGenerateMissingLabels(currentItem);
        throw new Error(missing.length ? `还不能准备生成，请先完成：${missing.join('、')}` : '还不能准备生成');
      }
      const temporary = buildViralBreakdownTemporaryKnowledgeReference(currentItem);
      const scriptText = getViralBreakdownComposerScript(currentItem);
      if (!temporary || !scriptText) throw new Error('临时知识库或剧本骨架不可用，请重新猜剧本');
      state.temporaryScriptKnowledge = temporary;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '已回填主界面；临时知识库将在下一次发送后自动解绑';
      closeViralBreakdownModal();
      setComposerDraft(scriptText, { submit: false });
      renderScriptReferenceButton();
      renderScriptReferenceDrawer();
      window.requestAnimationFrame(() => {
        els.messageEditor?.focus();
        els.messageEditor?.scrollIntoView({ block: 'nearest' });
      });
    }
