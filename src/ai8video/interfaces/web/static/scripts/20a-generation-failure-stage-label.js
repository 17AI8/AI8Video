    function isLocalVideoPostprocessFailure(value) {
      const text = String(value || '').trim();
      const lowered = text.toLowerCase();
      return [
        '视频开头裁剪失败',
        '归档或后处理失败',
        '视频后处理失败',
        '花字烧录失败',
        'HTML 动效',
        '提取尾帧失败',
        '保存延长截帧失败',
        '截取视频失败',
        '合并视频失败',
        '重新混入背景音乐失败',
      ].some((marker) => text.includes(marker)) || [
        'ffmpeg',
        '_mix_video',
        'text overlay',
      ].some((marker) => lowered.includes(marker));
    }

    function getGenerationFailureStageLabel(itemOrReason = {}) {
      if (typeof itemOrReason === 'string') {
        return isLocalVideoPostprocessFailure(itemOrReason) ? '本地后处理失败' : '生成失败';
      }
      const statusLabel = String(itemOrReason?.statusLabel || '').trim();
      if (statusLabel.includes('本地后处理')) return '本地后处理失败';
      const reason = [
        itemOrReason?.error,
        itemOrReason?.rawError,
        itemOrReason?.generationReasons,
        itemOrReason?.archiveError,
        itemOrReason?.reason,
      ].filter(Boolean).join('；');
      return isLocalVideoPostprocessFailure(reason) ? '本地后处理失败' : '生成失败';
    }
