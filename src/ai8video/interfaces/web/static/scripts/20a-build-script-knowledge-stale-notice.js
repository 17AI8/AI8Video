    function buildScriptKnowledgeStaleNotice(detail, sections) {
      const job = state.scriptKnowledge.ingestionJob;
      const sameDocument = Number(job?.documentId || 0) === Number(detail?.id || 0);
      if (!sections.length || !sameDocument || job?.state !== 'failed') return '';
      const reason = formatScriptKnowledgeError(job?.error || 'Reviewer 未通过本次知识入库');
      return `
        <div class="script-knowledge-stale-notice" role="status">
          <strong>本次知识入库未通过审核</strong>
          <span>以下仍展示上一次成功索引，没有写入本次候选结果。</span>
          <small>${escapeHtml(reason)}</small>
        </div>
      `;
    }
