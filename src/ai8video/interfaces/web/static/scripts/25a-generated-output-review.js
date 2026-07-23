    function collectGeneratedOutputReviewSuggestions(result) {
      const suggestions = [];
      (result?.videos || []).forEach((video) => {
        const index = video?.index || '-';
        const guidance = video?.keyword_guidance || {};
        const review = guidance.generated_output_review || {};
        const iteration = guidance.iteration || {};
        (review.issues || []).forEach((text) => suggestions.push(`第 ${index} 条｜成片问题：${text}`));
        (review.improvements || []).forEach((text) => suggestions.push(`第 ${index} 条｜优化空间：${text}`));
        if (review.status === 'unavailable') {
          suggestions.push(`第 ${index} 条｜成片审查暂不可用；系统不会在缺少反馈时继续盲目生成。`);
        }
        if (iteration.applied && iteration.sourceVideoIndex) {
          suggestions.push(`第 ${index} 条｜已吸收第 ${iteration.sourceVideoIndex} 条成片反馈后再生成。`);
        }
      });
      return suggestions;
    }
