    function getScriptKnowledgeStatusModel() {
      const knowledge = state.scriptKnowledge;
      const status = knowledge.status || {};
      const job = getScriptKnowledgeIngestionJob();
      if (['queued', 'running'].includes(job?.state)) return { text: '正在知识入库', error: false };
      if (job?.state === 'failed') {
        return { text: '本次入库失败 · 保留旧索引', error: true };
      }
      if (knowledge.loading) return { text: '正在检索', error: false };
      if (!status.available) {
        const databaseMissing = String(status.error || '').toLowerCase().includes('database "ai8video" does not exist');
        return { text: databaseMissing ? '数据库待初始化' : 'PostgreSQL 未连接', error: true };
      }
      const ready = Number(status.readyCount || 0);
      const total = Number(status.documentCount || 0);
      return { text: `${ready}/${total} 已索引 · 无向量模型`, error: false };
    }
