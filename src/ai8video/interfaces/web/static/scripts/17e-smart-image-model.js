    function smartImageSelectedImage() {
      const selected = smartImageSelectedNodes().filter((node) => node.type === 'image' && node.visible !== false);
      if (selected.length !== 1) throw new Error('请只选择一个图片图层后再调用图片模型');
      return selected[0];
    }

    function updateSmartImageProgress(done, total) {
      AI8SmartImage.state.processingDone = Math.max(0, Number(done) || 0);
      AI8SmartImage.state.processingTotal = Math.max(0, Number(total) || 0);
      const percent = total ? Math.min(100, Math.round(done / total * 100)) : 0;
      const modal = smartImageElements().modal;
      const percentElement = modal.querySelector('#smartImageProcessingPercentValue');
      const countElement = modal.querySelector('#smartImageProcessingCount');
      const bar = modal.querySelector('#smartImageProcessingBar');
      if (percentElement) percentElement.textContent = `${percent}%`;
      if (countElement) countElement.textContent = `${done}/${total}`;
      if (bar) bar.style.width = `${percent}%`;
    }

    function setSmartImageProcessing(processing, total = 0) {
      AI8SmartImage.state.processing = !!processing;
      updateSmartImageProgress(0, processing ? total : 0);
      const modal = smartImageElements().modal;
      modal.querySelectorAll('button, input, select, textarea').forEach((control) => {
        if (processing) {
          control.dataset.smartImageProcessingDisabled = control.disabled ? 'true' : 'false';
          control.disabled = true;
        } else {
          control.disabled = control.dataset.smartImageProcessingDisabled === 'true';
          delete control.dataset.smartImageProcessingDisabled;
        }
      });
      modal.classList.toggle('is-processing', !!processing);
      modal.querySelector('.smart-image-processing-overlay')?.setAttribute('aria-hidden', processing ? 'false' : 'true');
    }

    async function smartImageNodeBlob(node) {
      const canvas = await AI8SmartImage.composite([node]);
      const blob = await AI8SmartImage.canvasBlob(canvas);
      if (!blob) throw new Error('当前图片无法转换为模型输入');
      return blob;
    }

    function smartImageMaskCanvas(node) {
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(node.width));
      canvas.height = Math.max(1, Math.round(node.height));
      const context = canvas.getContext('2d');
      context.fillStyle = '#000000';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.strokeStyle = '#ffffff';
      context.lineCap = 'round';
      context.lineJoin = 'round';
      (node.strokes || []).forEach((stroke) => {
        if (!stroke.points?.length) return;
        context.lineWidth = Math.max(4, (stroke.size || 36) * node.width / 400);
        context.beginPath();
        stroke.points.forEach((point, index) => {
          const x = point.x * canvas.width;
          const y = point.y * canvas.height;
          if (!index) context.moveTo(x, y); else context.lineTo(x, y);
        });
        context.stroke();
      });
      return canvas;
    }

    async function smartImageMaskBlob(node) {
      const blob = await AI8SmartImage.canvasBlob(smartImageMaskCanvas(node));
      if (!blob) throw new Error('局部蒙版生成失败');
      return blob;
    }

    function smartImageMaskRegions(node) {
      return (node.strokes || []).flatMap((stroke) => {
        const points = stroke.points || [];
        if (!points.length) return [];
        const padX = (stroke.size || 36) / Math.max(1, node.width) / 2;
        const padY = (stroke.size || 36) / Math.max(1, node.height) / 2;
        const left = Math.max(0, Math.min(...points.map((point) => point.x)) - padX);
        const top = Math.max(0, Math.min(...points.map((point) => point.y)) - padY);
        const right = Math.min(1, Math.max(...points.map((point) => point.x)) + padX);
        const bottom = Math.min(1, Math.max(...points.map((point) => point.y)) + padY);
        return [{ x: Math.round(left * 1000), y: Math.round(top * 1000), width: Math.round((right - left) * 1000), height: Math.round((bottom - top) * 1000) }];
      });
    }

    function smartImageModelPrompt(node) {
      const prompt = smartImageElements().prompt.value.trim() || SMART_IMAGE_DEFAULT_PROMPT;
      const previewRatio = smartImagePreviewRatio(node);
      const ratioPrompt = `${prompt}\n输出图片必须严格保持当前画布预览比例 ${previewRatio.label}（宽高比 ${previewRatio.value.toFixed(6)}），不得恢复原始图片比例，也不得自行改变画幅。`;
      const regions = smartImageMaskRegions(node);
      if (!regions.length) return ratioPrompt;
      const coordinates = regions.map((region, index) => `区域${index + 1}(x=${region.x}, y=${region.y}, width=${region.width}, height=${region.height})`).join('；');
      return `${ratioPrompt}\n涂抹区域坐标以原图左上角为原点，横纵坐标范围均为 0-1000：${coordinates}。坐标用于辅助定位，精确修改边界以黑白蒙版的白色区域为准。`;
    }

    function smartImagePreviewRatio(node) {
      const width = Math.max(1, Math.round(node.width));
      const height = Math.max(1, Math.round(node.height));
      return {
        value: width / height,
        label: node.cropRatio && node.cropRatio !== 'original' ? node.cropRatio : '原比例',
      };
    }

    async function smartImageBatchItems(sourceNode, resultUrls) {
      const dataUrls = [sourceNode.originalDataUrl || sourceNode.dataUrl, ...resultUrls];
      const columns = dataUrls.length <= 2 ? dataUrls.length : dataUrls.length <= 6 ? 2 : 3;
      const rows = Math.ceil(dataUrls.length / columns);
      const tileWidth = Math.max(1, Math.round(sourceNode.batchTileWidth || sourceNode.width));
      const tileHeight = Math.max(1, Math.round(sourceNode.batchTileHeight || sourceNode.height));
      const gap = 12;
      const items = dataUrls.map((dataUrl, index) => ({
        id: smartImageId('batch-image'), dataUrl,
        originalDataUrl: dataUrl, cropRatio: sourceNode.cropRatio || 'original',
        flipX: false, rotation: 0,
        x: (index % columns) * (tileWidth + gap),
        y: Math.floor(index / columns) * (tileHeight + gap),
        width: tileWidth, height: tileHeight,
      }));
      return {
        items, tileWidth, tileHeight,
        width: tileWidth * columns + gap * (columns - 1),
        height: tileHeight * rows + gap * (rows - 1),
      };
    }

    async function applySmartImageBatchResults(sourceNode, results) {
      const batch = await smartImageBatchItems(sourceNode, results.map((item) => item.resultUrl));
      AI8SmartImage.pushHistory('批量 AI 修图');
      sourceNode.batchItems = batch.items;
      sourceNode.activeBatchItemId = batch.items[0]?.id || '';
      sourceNode.batchTileWidth = batch.tileWidth;
      sourceNode.batchTileHeight = batch.tileHeight;
      sourceNode.width = batch.width;
      sourceNode.height = batch.height;
      sourceNode.fit = 'contain';
      sourceNode.cropRatio = 'original';
      sourceNode.originalRatio = batch.width / Math.max(1, batch.height);
      sourceNode.modelName = results[0]?.model || '';
      sourceNode.strokes = [];
      AI8SmartImage.commit(`已在当前图层生成 ${results.length} 张图片`);
      AI8SmartImage.fitAll([sourceNode]);
    }

    async function requestSmartImageResult(node, sourceBlob, maskBlob, batchCount) {
      const form = new FormData();
      form.append('file', sourceBlob, `${node.name || '图片'}.png`);
      form.append('prompt', smartImageModelPrompt(node));
      form.append('regions', JSON.stringify(smartImageMaskRegions(node)));
      form.append('mode', 'edit');
      form.append('maxConcurrency', String(batchCount));
      if (maskBlob) form.append('mask', maskBlob, 'mask.png');
      const response = await fetch('/api/smart-image-editor/render', { method: 'POST', body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data?.ok === false) throw new Error(data?.error || '图片模型修图失败');
      return data;
    }

    async function runSmartImageModel(action) {
      if (AI8SmartImage.state.processing) return;
      try {
        if (!state.health?.hasImageModel) throw new Error('请先在设置中配置图片模型');
        const node = smartImageSelectedImage();
        const batchCount = Math.min(8, Math.max(1, Number(AI8SmartImage.state.modelBatchCount) || 1));
        setSmartImageProcessing(true, batchCount);
        const hasMask = !!node.strokes?.length;
        setSmartImageStatus(`正在批量生成 ${batchCount} 张图片…`);
        const sourceBlob = await smartImageNodeBlob(node);
        const maskBlob = hasMask ? await smartImageMaskBlob(node) : null;
        let completed = 0;
        const tasks = Array.from({ length: batchCount }, () => requestSmartImageResult(node, sourceBlob, maskBlob, batchCount)
          .finally(() => updateSmartImageProgress(++completed, batchCount)));
        const settled = await Promise.allSettled(tasks);
        const results = settled.filter((item) => item.status === 'fulfilled').map((item) => item.value);
        if (!results.length) throw settled.find((item) => item.status === 'rejected')?.reason || new Error('图片模型修图失败');
        await applySmartImageBatchResults(node, results);
        const failed = batchCount - results.length;
        if (failed) setSmartImageStatus(`已生成 ${results.length} 张，${failed} 张失败`, 'error');
      } catch (error) {
        setSmartImageStatus(error?.message || '图片模型修图失败', 'error');
      } finally {
        setSmartImageProcessing(false);
      }
    }

    AI8SmartImage.runModel = runSmartImageModel;
    const SMART_IMAGE_DEFAULT_PROMPT = '自然提升曝光、白平衡、色彩层次、清晰度和画面质感，保持主体身份、构图与原图含义不变，避免过度锐化或失真。';
