    // 历史会话只在加载时迁移；运行态和后续保存统一使用 video 字段。
    function migrateLegacyVideoSchema(value) {
      const aliases = {
        episode: 'video', episodes: 'videos', episodeCount: 'videoCount',
        episodeIndex: 'videoIndex', episodeTitle: 'videoTitle', episodePrompt: 'videoPrompt',
        episode_count: 'video_count', episode_index: 'video_index',
        rewrittenEpisodeIndex: 'rewrittenVideoIndex', rewriteEpisodeIndex: 'rewriteVideoIndex',
        rewrite_episode_index: 'rewrite_video_index',
      };
      const modes = { multi_episode_script: 'batch_videos', single_prompt: 'single_video' };
      let changed = false;
      const visit = (item) => {
        if (Array.isArray(item)) return item.map(visit);
        if (!item || typeof item !== 'object') return item;
        const output = {};
        Object.entries(item).forEach(([key, child]) => {
          if (!aliases[key]) output[key] = visit(child);
        });
        Object.entries(item).forEach(([key, child]) => {
          const target = aliases[key];
          if (!target) return;
          changed = true;
          if (!(target in output)) output[target] = visit(child);
        });
        if (typeof output.mode === 'string' && modes[output.mode]) {
          output.mode = modes[output.mode];
          changed = true;
        }
        return output;
      };
      return { value: visit(value), changed };
    }

    function replaceLegacyAssistantSemantics(value) {
      if (typeof value !== 'string') return value;
      return value
        .replace(
          /参考图可以下一句再给。如果暂时不用参考图，直接回复“不用参考图”。/g,
          '参考图标签页未选择时默认不用；需要时可在标签页选择，也可以直接发送图片路径。',
        )
        .replace(
          /把提示词或多集剧本直接发我。多集剧本记得写目标集数；/g,
          '把提示词、脚本素材或批量视频需求直接发我；批量生成请写清目标视频数量。',
        )
        .replace(
          /先把提示词或多集剧本发我。多集剧本最好顺手写清楚要拆成几集。/g,
          '先把提示词、脚本素材或批量视频需求发我。批量生成请写清要生成几条视频。',
        )
        .replace(/多集剧本/g, '批量视频素材')
        .replace(/目标集数/g, '目标视频数量')
        .replace(/拆成几集/g, '生成几条视频')
        .replace(/第几集/g, '第几条视频')
        .replace(/第(\d+)集/g, '第$1条视频')
        .replace(/每集/g, '每条视频')
        .replace(/这一集/g, '这条视频')
        .replace(/上集/g, '上一条视频')
        .replace(/下集/g, '下一条视频')
        .replace(/剧集/g, '批量视频')
        .replace(/集数/g, '视频数量')
        .replace(/拆集/g, '拆分视频任务')
        .replace(/连续剧/g, '批量独立短视频');
    }
