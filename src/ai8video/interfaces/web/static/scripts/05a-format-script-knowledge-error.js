    function formatScriptKnowledgeError(value) {
      const detail = String(value || '').trim();
      const normalized = detail.toLowerCase();
      if (normalized.includes('database "ai8video" does not exist')) {
        return '剧本知识库尚未初始化：本机 PostgreSQL 服务可用，但 AI8video 数据库还未创建。初始化后点击“同步索引”即可重建知识库。';
      }
      if (normalized.includes('connection to server') || normalized.includes('connection refused')) {
        return '暂时无法连接 PostgreSQL。请确认本机数据库服务已启动；原始剧本仍安全保存在本地文件夹。';
      }
      if (normalized.includes('password authentication failed')) {
        return 'PostgreSQL 账号验证失败。请检查剧本知识库的数据库配置。';
      }
      return detail ? `剧本知识库暂时不可用：${detail}` : '剧本知识库暂时不可用。';
    }
