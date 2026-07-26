    function cloneSmartImageNode(node) {
      return {
        ...node,
        filter: { ...(node.filter || {}) },
        strokes: (node.strokes || []).map((stroke) => ({ ...stroke, points: (stroke.points || []).map((point) => ({ ...point })) })),
        batchItems: (node.batchItems || []).map((item) => ({ ...item })),
      };
    }

    function captureSmartImageState() {
      return {
        nodes: AI8SmartImage.state.nodes.map(cloneSmartImageNode),
        selectedIds: [...AI8SmartImage.state.selectedIds],
        viewport: { ...AI8SmartImage.state.viewport },
        background: AI8SmartImage.state.background,
        projectName: AI8SmartImage.state.projectName,
        modelPrompt: smartImageElements().prompt.value,
        modelBatchCount: AI8SmartImage.state.modelBatchCount,
      };
    }

    function restoreSmartImageState(snapshot) {
      AI8SmartImage.state.nodes = (snapshot.nodes || []).slice(0, SMART_IMAGE_MAX_LAYERS).map(cloneSmartImageNode);
      const restoredIds = new Set(AI8SmartImage.state.nodes.map((node) => node.id));
      AI8SmartImage.state.selectedIds = [...(snapshot.selectedIds || [])].filter((id) => restoredIds.has(id));
      AI8SmartImage.state.viewport = { ...(snapshot.viewport || { x: 120, y: 90, zoom: 1 }) };
      AI8SmartImage.state.background = snapshot.background || 'grid';
      AI8SmartImage.state.projectName = snapshot.projectName || '未命名画布';
      AI8SmartImage.state.modelBatchCount = Math.min(8, Math.max(1, Number(snapshot.modelBatchCount) || 1));
      smartImageElements().prompt.value = String(snapshot.modelPrompt || smartImageElements().prompt.defaultValue || '');
      const batchCount = document.getElementById('smartImageModelBatchCount');
      if (batchCount) batchCount.value = String(AI8SmartImage.state.modelBatchCount);
      AI8SmartImage.render();
    }

    function pushSmartImageHistory() {
      AI8SmartImage.state.history.push(captureSmartImageState());
      if (AI8SmartImage.state.history.length > 50) AI8SmartImage.state.history.shift();
      AI8SmartImage.state.future = [];
    }

    function undoSmartImage() {
      const previous = AI8SmartImage.state.history.pop();
      if (!previous) return;
      AI8SmartImage.state.future.push(captureSmartImageState());
      restoreSmartImageState(previous);
      scheduleSmartImageSave();
      setSmartImageStatus('已撤销上一步', 'success');
    }

    function redoSmartImage() {
      const next = AI8SmartImage.state.future.pop();
      if (!next) return;
      AI8SmartImage.state.history.push(captureSmartImageState());
      restoreSmartImageState(next);
      scheduleSmartImageSave();
      setSmartImageStatus('已重做', 'success');
    }

    function smartImageProjectPayload() {
      return {
        product: 'AI8video 智能修图',
        version: AI8SmartImage.version,
        savedAt: new Date().toISOString(),
        project: captureSmartImageState(),
      };
    }

    async function saveSmartImageProject() {
      if (AI8SmartImage.state.saveTimer) clearTimeout(AI8SmartImage.state.saveTimer);
      AI8SmartImage.state.saveTimer = null;
      try {
        const response = await fetch('/api/smart-image-editor/project', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(smartImageProjectPayload()),
        });
        if (!response.ok) throw new Error('画布保存失败');
        localStorage.removeItem(SMART_IMAGE_PROJECT_KEY);
        const status = document.getElementById('smartImageStatus');
        if (status?.textContent === '自动保存失败，请稍后重试') status.textContent = '画布已自动保存';
      } catch (error) {
        const status = document.getElementById('smartImageStatus');
        if (status) status.textContent = '自动保存失败，请稍后重试';
      }
    }

    function scheduleSmartImageSave() {
      if (AI8SmartImage.state.saveTimer) clearTimeout(AI8SmartImage.state.saveTimer);
      AI8SmartImage.state.saveTimer = setTimeout(saveSmartImageProject, 500);
    }

    function commitSmartImageChange(label = '画布已更新') {
      AI8SmartImage.render();
      scheduleSmartImageSave();
      if (label) setSmartImageStatus(label, 'success');
    }

    async function restoreSmartImageProject() {
      try {
        const response = await fetch('/api/smart-image-editor/project', { cache: 'no-store' });
        let payload;
        if (response.ok) payload = await response.json();
        else {
          const raw = localStorage.getItem(SMART_IMAGE_PROJECT_KEY);
          if (!raw) return AI8SmartImage.render();
          payload = JSON.parse(raw);
        }
        const truncated = payload?.project?.nodes?.length > SMART_IMAGE_MAX_LAYERS;
        if (truncated) {
          payload.project.nodes = payload.project.nodes.slice(0, SMART_IMAGE_MAX_LAYERS);
          const nodeIds = new Set(payload.project.nodes.map((node) => node.id));
          payload.project.selectedIds = (payload.project.selectedIds || []).filter((id) => nodeIds.has(id));
        }
        validateSmartImageProject(payload);
        restoreSmartImageState(payload.project);
        saveSmartImageProject();
        setSmartImageStatus(truncated ? `已恢复上次画布的前 ${SMART_IMAGE_MAX_LAYERS} 层` : '已恢复上次智能修图画布', 'success');
      } catch {
        setSmartImageStatus('画布数据无法读取，请重新导入图片', 'error');
      }
    }

    function validateSmartImageProject(payload) {
      if (!payload || typeof payload !== 'object' || !payload.project || !Array.isArray(payload.project.nodes)) {
        throw new Error('不是有效的智能修图项目');
      }
      if (payload.project.nodes.length > SMART_IMAGE_MAX_LAYERS) throw new Error(`项目图层不能超过 ${SMART_IMAGE_MAX_LAYERS} 层`);
      payload.project.nodes.forEach((node) => {
        if (!node?.id || !['image', 'text', 'shape'].includes(node.type)) throw new Error('项目包含无效图层');
        if (node.type === 'image' && !String(node.dataUrl || '').startsWith('data:image/')) throw new Error('项目图片数据无效');
      });
    }

    function downloadSmartImageBlob(blob, fileName) {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1200);
    }

    function smartImageSafeName(suffix = '') {
      const raw = String(AI8SmartImage.state.projectName || '智能修图').replace(/[\\/:*?"<>|]+/g, '-').trim().slice(0, 70) || '智能修图';
      return `${raw}${suffix}`;
    }

    function exportSmartImageProject() {
      const blob = new Blob([JSON.stringify(smartImageProjectPayload(), null, 2)], { type: 'application/json' });
      downloadSmartImageBlob(blob, `${smartImageSafeName()}-画布项目.json`);
      setSmartImageStatus('画布项目已保存，可稍后继续编辑', 'success');
    }

    async function importSmartImageProject(file) {
      try {
        const payload = JSON.parse(await file.text());
        validateSmartImageProject(payload);
        pushSmartImageHistory();
        restoreSmartImageState(payload.project);
        scheduleSmartImageSave();
        setSmartImageStatus('画布项目导入完成', 'success');
      } catch (error) {
        setSmartImageStatus(error?.message || '项目导入失败', 'error');
      }
    }

    function smartImageDrawImage(context, image, node) {
      const sourceRatio = image.naturalWidth / image.naturalHeight;
      const targetRatio = node.width / node.height;
      let sx = 0; let sy = 0; let sw = image.naturalWidth; let sh = image.naturalHeight;
      if (node.fit === 'cover') {
        if (sourceRatio > targetRatio) {
          sw = image.naturalHeight * targetRatio;
          sx = (image.naturalWidth - sw) / 2;
        } else {
          sh = image.naturalWidth / targetRatio;
          sy = (image.naturalHeight - sh) / 2;
        }
      }
      context.drawImage(image, sx, sy, sw, sh, -node.width / 2, -node.height / 2, node.width, node.height);
    }

    async function smartImageDrawBatchItems(context, node) {
      for (const item of node.batchItems || []) {
        const image = await AI8SmartImage.loadImage(item.dataUrl);
        context.save();
        context.translate(-node.width / 2 + item.x + item.width / 2, -node.height / 2 + item.y + item.height / 2);
        context.rotate((Number(item.rotation || 0) * Math.PI) / 180);
        context.scale(item.flipX ? -1 : 1, 1);
        smartImageDrawImage(context, image, { ...node, width: item.width, height: item.height, fit: 'cover' });
        context.restore();
      }
    }

    async function drawSmartImageNode(context, node, bounds, scale) {
      if (node.visible === false) return;
      context.save();
      context.scale(scale, scale);
      context.translate(node.x - bounds.left + node.width / 2, node.y - bounds.top + node.height / 2);
      context.rotate((Number(node.rotation || 0) * Math.PI) / 180);
      context.globalAlpha = smartImageClamp(node.opacity, 0, 100) / 100;
      if (node.type === 'image') {
        context.save();
        context.beginPath();
        context.rect(-node.width / 2, -node.height / 2, node.width, node.height);
        context.clip();
        context.scale(node.flipX ? -1 : 1, 1);
        context.filter = smartImageNodeFilter(node);
        if (node.batchItems?.length) await smartImageDrawBatchItems(context, node);
        else smartImageDrawImage(context, await AI8SmartImage.loadImage(node.dataUrl), node);
        context.restore();
      } else if (node.type === 'shape') {
        context.fillStyle = node.fill || '#725cf3';
        if (node.shape === 'ellipse') {
          context.beginPath();
          context.ellipse(0, 0, node.width / 2, node.height / 2, 0, 0, Math.PI * 2);
          context.fill();
        } else context.fillRect(-node.width / 2, -node.height / 2, node.width, node.height);
      } else {
        context.fillStyle = node.color || '#ffffff';
        context.font = `700 ${Number(node.fontSize || 36)}px Inter, system-ui, sans-serif`;
        context.textAlign = node.textAlign || 'center';
        context.textBaseline = 'middle';
        context.fillText(node.text || '文字', 0, 0, node.width - 16);
      }
      context.restore();
    }

    async function renderSmartImageComposite(nodes) {
      const visible = nodes.filter((node) => node.visible !== false);
      const bounds = AI8SmartImage.bounds(visible);
      if (!bounds) throw new Error('画布没有可导出的内容');
      const scale = Math.min(1, SMART_IMAGE_MAX_EDGE / Math.max(bounds.width, bounds.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(bounds.width * scale));
      canvas.height = Math.max(1, Math.round(bounds.height * scale));
      const context = canvas.getContext('2d');
      for (const node of visible) await drawSmartImageNode(context, node, bounds, scale);
      return canvas;
    }

    function smartImageCanvasBlob(canvas) {
      return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
    }

    function smartImageCurrentExportNodes() {
      const node = smartImagePrimaryNode();
      if (!node) return [];
      if (!node.batchItems?.length) return [node];
      const item = node.batchItems.find((candidate) => candidate.id === node.activeBatchItemId) || node.batchItems[0];
      if (!item) return [];
      return [{
        ...node,
        x: 0,
        y: 0,
        width: item.width,
        height: item.height,
        dataUrl: item.dataUrl,
        batchItems: [],
        rotation: item.rotation || 0,
        flipX: !!item.flipX,
        fit: 'cover',
      }];
    }

    async function exportSmartImageCanvas(selectedOnly = false) {
      const nodes = selectedOnly ? smartImageCurrentExportNodes() : AI8SmartImage.state.nodes;
      try {
        if (!nodes.length) throw new Error('请先选择要导出的图片');
        setSmartImageStatus('正在生成 PNG…');
        const blob = await smartImageCanvasBlob(await renderSmartImageComposite(nodes));
        if (!blob) throw new Error('浏览器无法生成 PNG');
        downloadSmartImageBlob(blob, `${smartImageSafeName(selectedOnly ? '-选中' : '')}-智能修图.png`);
        setSmartImageStatus('PNG 已导出，原图片未改动', 'success');
      } catch (error) {
        setSmartImageStatus(error?.message || '导出失败', 'error');
      }
    }

    Object.assign(AI8SmartImage, {
      capture: captureSmartImageState,
      pushHistory: pushSmartImageHistory,
      undo: undoSmartImage,
      redo: redoSmartImage,
      commit: commitSmartImageChange,
      saveProject: saveSmartImageProject,
      restoreProject: restoreSmartImageProject,
      exportProject: exportSmartImageProject,
      importProject: importSmartImageProject,
      composite: renderSmartImageComposite,
      canvasBlob: smartImageCanvasBlob,
      currentExportNodes: smartImageCurrentExportNodes,
      downloadBlob: downloadSmartImageBlob,
      exportCanvas: exportSmartImageCanvas,
    });
