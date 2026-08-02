    function smartImageModelPrompt(job) {
      const prompt = String(job.prompt || SMART_IMAGE_DEFAULT_PROMPT).trim();
      const ratio = String(job.source?.edits?.ratio || 'original');
      if (ratio === 'original') return prompt;
      return `${prompt}\n输出图片必须保持 ${ratio} 画幅，主体完整、构图自然，不要生成拼图或前后对比图。`;
    }

    async function smartImageSourceBlob(source) {
      const response = await fetch(source.dataUrl);
      const blob = await response.blob();
      if (!blob.size) throw new Error('原图无法转换为模型输入');
      return blob;
    }

    async function requestSmartImageResult(job, sourceBlob) {
      const form = new FormData();
      form.append('file', sourceBlob, job.source.sourceName || '图片.png');
      form.append('prompt', smartImageModelPrompt(job));
      form.append('mode', 'edit');
      form.append('maxConcurrency', '1');
      const response = await fetch('/api/smart-image-editor/render', { method: 'POST', body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data?.ok === false) throw new Error(data?.error || '图片模型修图失败');
      return data;
    }

    async function buildSmartImageResult(data, job, index) {
      const resultUrl = String(data.resultUrl || '');
      if (!resultUrl.startsWith('/smart-image-results/') || !smartImageSafeSource(resultUrl)) {
        throw new Error('图片模型返回了无效的结果地址');
      }
      let width = job.source.width;
      let height = job.source.height;
      try {
        const image = await smartImageLoadElement(resultUrl);
        width = image.naturalWidth;
        height = image.naturalHeight;
      } catch {}
      return {
        id: smartImageId('result'),
        jobId: job.id,
        url: resultUrl,
        fileName: String(data.fileName || `${job.source.name}-AI修图-${index + 1}.png`),
        model: String(data.model || AI8SmartImage.state.modelName || ''),
        prompt: job.prompt,
        presetId: job.presetId,
        createdAt: new Date().toISOString(),
        width,
        height,
        edits: smartImageDefaultEdits(),
      };
    }

    function enqueueSmartImageJob() {
      const source = AI8SmartImage.state.source;
      const prompt = String(AI8SmartImage.state.prompt || '').trim();
      if (!source) return setSmartImageStatus('请先导入一张图片', 'error');
      if (!state.health?.hasImageModel) return setSmartImageStatus('请先在设置中配置图片模型', 'error');
      if (!prompt) return setSmartImageStatus('请填写修图要求', 'error');
      const preset = smartImagePresetById(AI8SmartImage.state.selectedPresetId);
      const job = {
        id: smartImageId('job'),
        source: { ...source, edits: smartImageCloneEdits(source.edits) },
        prompt: prompt.slice(0, 2000),
        presetId: preset?.id || 'custom',
        presetLabel: preset?.label || '自定义修图',
        total: smartImageClamp(AI8SmartImage.state.batchCount, 1, 8),
        done: 0,
        successful: 0,
        remaining: smartImageClamp(AI8SmartImage.state.batchCount, 1, 8),
        attemptDone: 0,
        attemptTotal: 0,
        status: 'queued',
        error: '',
        createdAt: new Date().toISOString(),
        resultIds: [],
      };
      AI8SmartImage.state.jobs.push(job);
      AI8SmartImage.state.selectedJobId = job.id;
      AI8SmartImage.state.selectedResultId = '';
      AI8SmartImage.state.viewMode = 'source';
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
      setSmartImageStatus(AI8SmartImage.state.processing ? '任务已加入队列' : '任务已提交，准备调用图片模型', 'success');
      void runNextSmartImageJob();
    }

    async function runNextSmartImageJob() {
      if (AI8SmartImage.state.processing) return;
      const job = AI8SmartImage.state.jobs.find((item) => item.status === 'queued');
      if (!job) return;
      AI8SmartImage.state.processing = true;
      job.status = 'running';
      job.attemptTotal = smartImageClamp(job.remaining || job.total, 1, job.total);
      job.attemptDone = 0;
      job.error = '';
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
      setSmartImageStatus(`正在生成 ${job.attemptTotal} 张“${job.presetLabel}”结果…`);
      try {
        const sourceBlob = await smartImageSourceBlob(job.source);
        const tasks = Array.from({ length: job.attemptTotal }, (_, index) => requestSmartImageResult(job, sourceBlob)
          .then((data) => buildSmartImageResult(data, job, index))
          .finally(() => {
            job.attemptDone += 1;
            AI8SmartImage.render();
          }));
        const settled = await Promise.allSettled(tasks);
        const results = settled.filter((item) => item.status === 'fulfilled').map((item) => item.value);
        const failures = settled.filter((item) => item.status === 'rejected');
        job.successful = smartImageClamp((job.successful || 0) + results.length, 0, job.total);
        job.done = job.successful;
        job.remaining = failures.length;
        if (!results.length) throw failures[0]?.reason || new Error('图片模型没有返回可用结果');
        AI8SmartImage.state.results.push(...results);
        job.resultIds = smartImageSerializableStringList([...(job.resultIds || []), ...results.map((result) => result.id)], 64);
        AI8SmartImage.state.selectedJobId = job.id;
        AI8SmartImage.state.selectedResultId = results[0].id;
        AI8SmartImage.state.viewMode = 'result';
        job.status = job.remaining ? 'partial' : 'done';
        job.error = failures.length ? `${failures.length} 张生成失败` : '';
        setSmartImageStatus(failures.length ? `已生成 ${results.length} 张，${failures.length} 张失败` : `已生成 ${results.length} 张结果，可进行前后对比`, failures.length ? 'error' : 'success');
      } catch (error) {
        job.status = job.successful ? 'partial' : 'error';
        if (!job.remaining) job.remaining = Math.max(1, job.total - (job.successful || 0));
        job.error = error?.message || '图片模型修图失败';
        setSmartImageStatus(job.error, 'error');
      } finally {
        AI8SmartImage.state.processing = false;
        AI8SmartImage.render();
        AI8SmartImage.scheduleSave();
        window.setTimeout(() => void runNextSmartImageJob(), 0);
      }
    }

    function retrySmartImageJob(id) {
      const job = AI8SmartImage.state.jobs.find((item) => item.id === id);
      if (!job || !['error', 'partial'].includes(job.status)) return;
      job.status = 'queued';
      job.attemptDone = 0;
      job.attemptTotal = job.remaining || job.total;
      job.error = '';
      AI8SmartImage.state.selectedJobId = job.id;
      const existingResult = smartImageResultsForJob(job)[0];
      AI8SmartImage.state.selectedResultId = existingResult?.id || '';
      AI8SmartImage.state.viewMode = existingResult ? 'result' : 'source';
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
      setSmartImageStatus('失败任务已重新加入队列', 'success');
      void runNextSmartImageJob();
    }

    async function optimizeSmartImagePrompt() {
      if (AI8SmartImage.state.promptOptimizing) return;
      const prompt = String(AI8SmartImage.state.prompt || '').trim();
      if (!state.health?.hasLLM) return setSmartImageStatus('请先在设置中配置文本模型', 'error');
      if (!prompt) return setSmartImageStatus('请先写一句修图要求', 'error');
      AI8SmartImage.state.promptOptimizing = true;
      AI8SmartImage.render();
      setSmartImageStatus('正在调用文本模型优化修图描述…');
      try {
        const response = await fetch('/api/smart-image-editor/optimize-prompt', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data?.ok === false) throw new Error(data?.error || '提示词优化失败');
        AI8SmartImage.state.prompt = String(data.prompt || prompt).slice(0, 2000);
        AI8SmartImage.state.selectedPresetId = 'custom';
        AI8SmartImage.scheduleSave();
        setSmartImageStatus('修图描述已优化，可继续修改后提交', 'success');
      } catch (error) {
        setSmartImageStatus(error?.message || '提示词优化失败', 'error');
      } finally {
        AI8SmartImage.state.promptOptimizing = false;
        AI8SmartImage.render();
      }
    }

    Object.assign(AI8SmartImage, {
      enqueue: enqueueSmartImageJob,
      runNext: runNextSmartImageJob,
      retryJob: retrySmartImageJob,
      optimizePrompt: optimizeSmartImagePrompt,
    });
