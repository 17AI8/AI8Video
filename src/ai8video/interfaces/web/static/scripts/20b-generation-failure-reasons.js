    function humanizeGenerationFailureReason(value) {
      const text = String(value || '').trim();
      const lowered = text.toLowerCase();
      const imageStage = lowered.includes('/v1/images/generations') || text.includes('首帧') || text.includes('图生图');
      if (!text) return '视频生成失败，请重新生成这一条。';
      if (
        text.includes('视频开头裁剪失败')
        && (lowered.includes('libx264') || lowered.includes("unrecognized option 'preset'"))
      ) {
        return '本机视频后处理编码器不兼容，开头裁剪失败。已自动改用可用编码器，请重试这一条。';
      }
      if (text.includes('未配置图片模型') || text.includes('请设置图片模型')) {
        return '请设置图片模型。';
      }
      if (
        text.includes('前序任务已结束')
        || text.includes('前序失败未提交')
        || text.includes('未提交上游生成')
        || text.includes('前面的视频已经失败')
      ) {
        return '这条未提交给生成服务；没有上游返回。';
      }
      if (
        text.includes('视频未提交')
        || text.includes('没有成功提交')
        || text.includes('没有拿到可轮询')
        || text.includes('没有留下可轮询')
      ) {
        return '后台中断了，这条视频未提交给生成服务。请重新生成。';
      }
      if (
        lowered.includes("didn't pass content review")
        || lowered.includes('content review')
        || text.includes('内容审核')
        || text.includes('敏感信息')
        || lowered.includes('protected ip')
      ) {
        return '内容审核未通过，请换图或改成非真人风格后重试。';
      }
      if (
        lowered.includes('httpsconnectionpool')
        || lowered.includes('max retries exceeded')
        || lowered.includes('sslerror')
        || lowered.includes('ssleoferror')
        || lowered.includes('eof occurred in violation of protocol')
      ) {
        return imageStage ? '首帧图上游连接中断，请稍后重试。' : '上游生成服务连接中断，请稍后重试。';
      }
      if (
        lowered.includes('cannot connect to proxy')
        || lowered.includes('proxyerror')
        || lowered.includes('remote end closed connection')
        || lowered.includes('connection refused')
        || lowered.includes('connection aborted')
        || lowered.includes('connection reset')
      ) {
        return imageStage ? '首帧图上游连接中断，请稍后重试。' : '上游生成服务连接中断，请稍后重试。';
      }
      if (
        text.includes('本地任务超时')
        || text.includes('没有提交给上游生成服务')
        || text.includes('未提交给生成服务')
      ) {
        return '本地任务超时，视频没有提交给上游生成服务。请重新发送或缩短输入后再试。';
      }
      if (lowered.includes('read timed out') || lowered.includes('timed out') || text.includes('超时')) {
        return imageStage ? '首帧图生成超时，请稍后重试。' : '生成服务超时，请稍后重试。';
      }
      if (
        lowered.includes('invalid_seconds')
        || lowered.includes('seconds is invalid')
        || lowered.includes('must be 4, 8, or 12')
      ) {
        return '当前时长不支持，请切换到支持的秒数后重试。';
      }
      if (
        lowered.includes('only [4, 6, 8] seconds')
        || lowered.includes('only [4,6,8] seconds')
        || (text.includes('4, 6, 8') && lowered.includes('seconds') && lowered.includes('supported'))
      ) {
        return '当前模型只支持 4、6 或 8 秒，请把视频时长改成支持的秒数后重试。';
      }
      if (lowered.includes('duration must be 5 or 10 seconds') || text.includes('5 or 10 seconds')) {
        return '视频时长不支持，请切到 5 秒或 10 秒。';
      }
      if (lowered.includes('size must be') || lowered.includes('supported resolution')) {
        return '清晰度不支持，请切换清晰度后重试。';
      }
      if (
        lowered.includes('invalid media')
        || lowered.includes('media url')
        || lowered.includes('media type')
      ) {
        return imageStage ? '首帧图不符合生成要求，请换图后重试。' : '素材不符合生成要求，请更换后重试。';
      }
      if (
        lowered.includes('insufficient')
        || lowered.includes('quota')
        || text.includes('额度不足')
        || text.includes('余额不足')
      ) {
        return '当前账号额度不足，请更换账号或稍后重试。';
      }
      if ((text.includes('上游') && text.includes('失败')) || text.includes('生成未成功') || text.includes('生成状态')) {
        return '生成服务没有成功，请重新生成这一条。';
      }
      if (looksTechnicalError(text)) {
        return imageStage ? '首帧图处理失败，请稍后重试。' : '视频处理失败，请稍后重试。';
      }
      return text;
    }
    function summarizeGenerationFailureReason(value) {
      const reason = humanizeGenerationFailureReason(value);
      if (reason.includes('请设置图片模型')) return '请设置图片模型';
      if (reason.includes('内容审核未通过')) return '内容审核未通过';
      if (reason.includes('首帧图上游连接中断') || reason.includes('首帧图连接生成服务失败')) return '首帧图上游断连';
      if (reason.includes('上游生成服务连接中断') || reason.includes('生成服务连接失败')) return '上游连接中断';
      if (reason.includes('没有提交给上游生成服务')) return '本地超时未提交上游';
      if (reason.includes('首帧图生成超时')) return '首帧图超时';
      if (reason.includes('生成服务超时')) return '生成超时';
      if (reason.includes('当前模型只支持 4、6 或 8 秒')) return '时长仅支持4/6/8秒';
      if (reason.includes('当前时长不支持')) return '当前时长不支持';
      if (reason.includes('视频时长不支持')) return '视频时长不支持';
      if (reason.includes('清晰度不支持')) return '清晰度不支持';
      if (reason.includes('首帧图不符合生成要求')) return '首帧图不符合要求';
      if (reason.includes('素材不符合生成要求')) return '素材不符合要求';
      if (reason.includes('当前账号额度不足')) return '账号额度不足';
      if (reason.includes('首帧图处理失败')) return '首帧图处理失败';
      if (reason.includes('视频处理失败')) return '视频处理失败';
      if (reason.includes('生成服务没有成功')) return '生成服务失败';
      if (reason.includes('没有上游返回')) return '未提交，无上游返回';
      if (reason.includes('未提交给生成服务')) return '未提交，无上游返回';
      if (reason.includes('后台中断了')) return '后台中断，未提交';
      return reason.length > 14 ? `${reason.slice(0, 14)}…` : reason;
    }
