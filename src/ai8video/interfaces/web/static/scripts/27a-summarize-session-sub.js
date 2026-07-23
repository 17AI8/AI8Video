    function summarizeSessionSub(session) {
      const last = session.messages.at(-1);
      if (!last) return '等待输入提示词';
      if (last.role === 'user') return WAITING_REPLY_TITLE;
      if (last.error) return '本轮请求失败';
      if (isPendingPayload(last.payload)) return '后台执行中';
      if (last.payload?.meta?.operation === 'batch_run' && last.payload?.summary) {
        const summary = last.payload.summary;
        const topUpRounds = Number(summary.expansionRoundCount || 0);
        const done = Number(summary.successCount ?? summary.passCount ?? 0);
        const target = Number(summary.targetGenerationCount ?? summary.targetPassCount ?? 0);
        return topUpRounds > 0
          ? `批量 ${done}/${target} · 补量 ${topUpRounds} 轮`
          : `批量 ${done}/${target}`;
      }
      if (last.payload?.summary) return `已生成 ${last.payload.summary.videoCount} 条结果`;
      if (last.payload?.awaiting === 'batch_seed_messages') return '等待补充批量候选';
      if (last.payload?.awaiting === 'video_count') return '等待补充视频数量';
      if (last.payload?.awaiting === 'reference_image') return '等待补充参考图';
      if (last.payload?.awaiting === 'content_completion') return '等待补充台词';
      if (last.payload?.awaiting === 'core_keywords') return '等待确认核心主题';
      if (last.payload?.awaiting === 'concurrent_generation') return '等待选择生成模式';
      return '继续补充需求';
    }
