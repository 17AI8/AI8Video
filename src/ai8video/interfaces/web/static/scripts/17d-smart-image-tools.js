    function selectSmartImageNode(id, additive = false) {
      const node = smartImageNodeById(id);
      if (!node) return;
      const groupIds = node.groupId
        ? AI8SmartImage.state.nodes.filter((item) => item.groupId === node.groupId).map((item) => item.id)
        : [id];
      if (!additive) AI8SmartImage.state.selectedIds = groupIds;
      else {
        const selected = new Set(AI8SmartImage.state.selectedIds);
        const shouldRemove = groupIds.every((item) => selected.has(item));
        groupIds.forEach((item) => shouldRemove ? selected.delete(item) : selected.add(item));
        AI8SmartImage.state.selectedIds = [...selected];
      }
      AI8SmartImage.render();
    }

    function createSmartImageTextNode() {
      const size = { width: 360, height: 100 };
      smartImageCreateNode('text', { ...smartImageWorldCenter(size.width, size.height), ...size, name: '文字', text: '双击编辑文字', fontSize: 36, color: '#ffffff' }, '创建文字');
    }

    function smartImageResultUrls(node) {
      const urls = [node?.dataUrl, ...(node?.batchItems || []).flatMap((item) => [item.dataUrl, item.originalDataUrl])];
      return [...new Set(urls.filter((url) => String(url || '').startsWith('/smart-image-results/')))];
    }

    function deleteSmartImageResultFiles(urls) {
      urls.forEach((url) => {
        const filename = String(url).split('/').pop();
        if (!filename) return;
        fetch(`/api/smart-image-editor/results/${encodeURIComponent(filename)}`, { method: 'DELETE' }).catch(() => {});
      });
    }

    function deleteSmartImageSelection() {
      const selected = new Set(AI8SmartImage.state.selectedIds);
      const removable = AI8SmartImage.state.nodes.filter((node) => selected.has(node.id) && !node.locked);
      if (!removable.length) return;
      AI8SmartImage.pushHistory('删除图层');
      deleteSmartImageResultFiles(removable.flatMap(smartImageResultUrls));
      AI8SmartImage.state.nodes = AI8SmartImage.state.nodes.filter((node) => !removable.includes(node));
      AI8SmartImage.state.selectedIds = [];
      AI8SmartImage.commit(`已删除 ${removable.length} 个图层`);
    }

    function duplicateSmartImageSelection() {
      const selected = smartImageSelectedNodes();
      if (!selected.length) return;
      if (!AI8SmartImage.canAddLayers(selected.length)) return;
      AI8SmartImage.pushHistory('复制图层');
      const groupMap = new Map();
      const clones = selected.map((node) => {
        const groupId = node.groupId ? (groupMap.get(node.groupId) || smartImageId('group')) : '';
        if (node.groupId) groupMap.set(node.groupId, groupId);
        return { ...cloneSmartImageNode(node), id: smartImageId(node.type), name: `${node.name} 副本`, x: node.x + 28, y: node.y + 28, groupId };
      });
      AI8SmartImage.state.nodes.push(...clones);
      AI8SmartImage.state.selectedIds = clones.map((node) => node.id);
      AI8SmartImage.commit(`已复制 ${clones.length} 个图层`);
    }

    function smartImageBatchItem(node, itemId) {
      return node?.batchItems?.find((item) => item.id === itemId) || null;
    }

    function updateSmartImageBatchBounds(node) {
      if (!node?.batchItems?.length) return;
      node.width = Math.max(...node.batchItems.map((item) => item.x + item.width));
      node.height = Math.max(...node.batchItems.map((item) => item.y + item.height));
      node.originalRatio = node.width / Math.max(1, node.height);
    }

    function transformSmartImageBatchItem(action, itemId) {
      const node = smartImagePrimaryNode();
      const item = smartImageBatchItem(node, itemId);
      if (!item || node.locked) return;
      AI8SmartImage.pushHistory('调整图层内图片');
      if (action === 'rotate-left') item.rotation = (Number(item.rotation || 0) + 270) % 360;
      if (action === 'rotate-right') item.rotation = (Number(item.rotation || 0) + 90) % 360;
      if (action === 'flip-horizontal') item.flipX = !item.flipX;
      if (action === 'restore-source') { item.dataUrl = item.originalDataUrl; item.flipX = false; item.rotation = 0; }
      if (action === 'delete-item') {
        if (node.batchItems.length <= 1) return setSmartImageStatus('请从右侧图层列表删除整个图层', 'error');
        deleteSmartImageResultFiles(smartImageResultUrls({ batchItems: [item] }));
        node.batchItems = node.batchItems.filter((candidate) => candidate.id !== item.id);
        node.activeBatchItemId = node.batchItems[0]?.id || '';
        updateSmartImageBatchBounds(node);
      }
      AI8SmartImage.commit(action === 'delete-item' ? '图层内图片已删除' : '图片调整已更新');
    }

    async function applySmartImageBatchItemRatio(ratio, itemId) {
      const node = smartImagePrimaryNode();
      const item = smartImageBatchItem(node, itemId);
      if (!item || node.locked) return;
      const ratios = { '1:1': 1, '9:16': 9 / 16, '16:9': 16 / 9 };
      try {
        let targetRatio = ratios[ratio];
        if (ratio === 'original') {
          const image = await AI8SmartImage.loadImage(item.originalDataUrl || item.dataUrl);
          targetRatio = image.naturalWidth / Math.max(1, image.naturalHeight);
        }
        if (!targetRatio) return;
        AI8SmartImage.pushHistory('调整图层内图片比例');
        item.cropRatio = ratio;
        item.height = Math.round(item.width / targetRatio);
        updateSmartImageBatchBounds(node);
        AI8SmartImage.commit(`当前图片比例已设为 ${ratio === 'original' ? '原比例' : ratio}`);
      } catch (error) {
        setSmartImageStatus(error?.message || '图片比例读取失败', 'error');
      }
    }

    function setSmartImageTool(tool) {
      AI8SmartImage.state.tool = ['select', 'hand', 'mask'].includes(tool) ? tool : 'select';
      setSmartImageStatus(tool === 'mask' ? '在选中图片上涂抹紫红色蒙版区域' : tool === 'hand' ? '拖动画布，滚轮缩放' : '选择、移动或缩放图层');
      AI8SmartImage.render();
    }

    function smartImagePointerPosition(event) {
      const rect = smartImageElements().viewport.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    function smartImageMaskPoint(event, nodeElement) {
      const rect = nodeElement.getBoundingClientRect();
      return {
        x: smartImageClamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1),
        y: smartImageClamp((event.clientY - rect.top) / Math.max(1, rect.height), 0, 1),
      };
    }

    function beginSmartImagePan(event) {
      AI8SmartImage.state.interaction = {
        type: 'pan', startX: event.clientX, startY: event.clientY,
        viewport: { ...AI8SmartImage.state.viewport },
      };
    }

    function beginSmartImageDrag(event, node) {
      if (node.locked) return;
      AI8SmartImage.pushHistory('移动图层');
      const positions = Object.fromEntries(smartImageSelectedNodes().filter((item) => !item.locked).map((item) => [item.id, { x: item.x, y: item.y }]));
      AI8SmartImage.state.interaction = { type: 'drag', startX: event.clientX, startY: event.clientY, positions };
    }

    function beginSmartImageBatchItemDrag(event, node, itemId) {
      if (node.locked) return;
      const item = node.batchItems?.find((candidate) => candidate.id === itemId);
      if (!item) return;
      AI8SmartImage.pushHistory('移动图层内图片');
      node.activeBatchItemId = item.id;
      AI8SmartImage.state.interaction = {
        type: 'batch-item-drag', startX: event.clientX, startY: event.clientY,
        nodeId: node.id, itemId: item.id, x: item.x, y: item.y,
      };
      AI8SmartImage.render();
    }

    function beginSmartImageResize(event, node) {
      if (node.locked) return;
      AI8SmartImage.pushHistory('缩放图层');
      AI8SmartImage.state.interaction = {
        type: 'resize', startX: event.clientX, startY: event.clientY,
        id: node.id, width: node.width, height: node.height, ratio: node.width / Math.max(1, node.height),
      };
    }

    function beginSmartImageMask(event, node, nodeElement) {
      if (node.type !== 'image' || node.locked) {
        setSmartImageStatus('局部蒙版只能绘制在未锁定的图片图层上', 'error');
        return;
      }
      AI8SmartImage.pushHistory('绘制局部蒙版');
      const stroke = { size: 36, points: [smartImageMaskPoint(event, nodeElement)] };
      node.strokes = [...(node.strokes || []), stroke];
      AI8SmartImage.state.interaction = { type: 'mask', id: node.id, stroke, nodeElement };
      AI8SmartImage.render();
    }

    function beginSmartImageMarquee(event) {
      const point = smartImagePointerPosition(event);
      AI8SmartImage.state.selectedIds = [];
      AI8SmartImage.state.interaction = { type: 'marquee', start: point, current: point };
      renderSmartImageMarquee();
      AI8SmartImage.render();
    }

    function handleSmartImagePointerDown(event) {
      const viewport = event.target.closest('#smartImageCanvasViewport');
      if (!viewport || event.button > 1) return;
      const nodeElement = event.target.closest('[data-smart-image-node]');
      const node = nodeElement ? smartImageNodeById(nodeElement.dataset.smartImageNode) : null;
      event.preventDefault();
      if (event.target.closest('.smart-image-node-floating-toolbar')) return;
      viewport.setPointerCapture?.(event.pointerId);
      if (event.button === 1 || AI8SmartImage.state.tool === 'hand' || AI8SmartImage.state.spacePanning) return beginSmartImagePan(event);
      if (event.target.closest('[data-smart-image-resize]') && node) return beginSmartImageResize(event, node);
      if (AI8SmartImage.state.tool === 'mask' && node) {
        if (!AI8SmartImage.state.selectedIds.includes(node.id)) selectSmartImageNode(node.id);
        return beginSmartImageMask(event, node, nodeElement);
      }
      if (node) {
        if (!AI8SmartImage.state.selectedIds.includes(node.id) || event.shiftKey) selectSmartImageNode(node.id, event.shiftKey);
        const batchItem = event.target.closest('[data-smart-image-batch-item]');
        if (batchItem) return beginSmartImageBatchItemDrag(event, node, batchItem.dataset.smartImageBatchItem);
        return beginSmartImageDrag(event, node);
      }
      beginSmartImageMarquee(event);
    }

    function moveSmartImageInteraction(event) {
      const interaction = AI8SmartImage.state.interaction;
      if (!interaction) return;
      const zoom = AI8SmartImage.state.viewport.zoom;
      if (interaction.type === 'pan') {
        AI8SmartImage.state.viewport.x = interaction.viewport.x + event.clientX - interaction.startX;
        AI8SmartImage.state.viewport.y = interaction.viewport.y + event.clientY - interaction.startY;
      } else if (interaction.type === 'drag') {
        Object.entries(interaction.positions).forEach(([id, position]) => {
          const node = smartImageNodeById(id);
          if (node) { node.x = Math.round(position.x + (event.clientX - interaction.startX) / zoom); node.y = Math.round(position.y + (event.clientY - interaction.startY) / zoom); }
        });
      } else if (interaction.type === 'batch-item-drag') {
        const node = smartImageNodeById(interaction.nodeId);
        const item = node?.batchItems?.find((candidate) => candidate.id === interaction.itemId);
        if (node && item) {
          item.x = Math.round(smartImageClamp(interaction.x + (event.clientX - interaction.startX) / zoom, 0, node.width - item.width));
          item.y = Math.round(smartImageClamp(interaction.y + (event.clientY - interaction.startY) / zoom, 0, node.height - item.height));
        }
      } else if (interaction.type === 'resize') {
        const node = smartImageNodeById(interaction.id);
        if (node) {
          const width = Math.max(24, interaction.width + (event.clientX - interaction.startX) / zoom);
          node.width = Math.round(width);
          node.height = Math.round(width / interaction.ratio);
        }
      } else if (interaction.type === 'mask') {
        const node = smartImageNodeById(interaction.id);
        if (node) interaction.stroke.points.push(smartImageMaskPoint(event, event.target.closest(`[data-smart-image-node="${node.id}"]`) || smartImageElements().scene.querySelector(`[data-smart-image-node="${node.id}"]`)));
      } else if (interaction.type === 'marquee') {
        interaction.current = smartImagePointerPosition(event);
        renderSmartImageMarquee();
      }
      AI8SmartImage.render();
    }

    function renderSmartImageMarquee() {
      const interaction = AI8SmartImage.state.interaction;
      const marquee = smartImageElements().marquee;
      if (!interaction || interaction.type !== 'marquee') {
        marquee.classList.add('hidden');
        return;
      }
      const left = Math.min(interaction.start.x, interaction.current.x);
      const top = Math.min(interaction.start.y, interaction.current.y);
      marquee.style.cssText = `left:${left}px;top:${top}px;width:${Math.abs(interaction.current.x - interaction.start.x)}px;height:${Math.abs(interaction.current.y - interaction.start.y)}px`;
      marquee.classList.remove('hidden');
    }

    function finishSmartImageMarquee(interaction) {
      const left = Math.min(interaction.start.x, interaction.current.x);
      const top = Math.min(interaction.start.y, interaction.current.y);
      const right = Math.max(interaction.start.x, interaction.current.x);
      const bottom = Math.max(interaction.start.y, interaction.current.y);
      const viewport = AI8SmartImage.state.viewport;
      AI8SmartImage.state.selectedIds = AI8SmartImage.state.nodes.filter((node) => {
        if (node.visible === false) return false;
        const nodeLeft = node.x * viewport.zoom + viewport.x;
        const nodeTop = node.y * viewport.zoom + viewport.y;
        const nodeRight = nodeLeft + node.width * viewport.zoom;
        const nodeBottom = nodeTop + node.height * viewport.zoom;
        return nodeRight >= left && nodeLeft <= right && nodeBottom >= top && nodeTop <= bottom;
      }).map((node) => node.id);
    }

    function finishSmartImageInteraction() {
      const interaction = AI8SmartImage.state.interaction;
      if (!interaction) return;
      if (interaction.type === 'marquee') finishSmartImageMarquee(interaction);
      AI8SmartImage.state.interaction = null;
      renderSmartImageMarquee();
      if (['drag', 'resize', 'mask', 'batch-item-drag'].includes(interaction.type)) AI8SmartImage.commit(interaction.type === 'mask' ? '局部蒙版已更新' : '图层位置已更新');
      else AI8SmartImage.render();
    }

    function applySmartImageAdjustment(input) {
      const node = smartImagePrimaryNode();
      if (!node) return;
      const key = input.dataset.smartImageAdjustment;
      if (key === 'opacity') {
        node.opacity = smartImageClamp(Number(input.value), 0, 100);
        AI8SmartImage.renderNow();
        const output = smartImageElements().modal.querySelector('[data-smart-image-value="opacity"]');
        if (output) output.textContent = String(node.opacity);
        return;
      }
      if (node.type !== 'image') return;
      node.filter[key] = Number(input.value);
      const output = smartImageElements().modal.querySelector(`[data-smart-image-value="${key}"]`);
      if (output) output.textContent = input.value;
      AI8SmartImage.render();
      scheduleSmartImageSave();
    }

    function resetSmartImageAdjustments() {
      const node = smartImagePrimaryNode();
      if (!node) return;
      AI8SmartImage.pushHistory('重置画面调整');
      node.opacity = 100;
      if (node.type === 'image') {
        node.filter = { brightness: 100, contrast: 100, saturation: 100, blur: 0 };
        node.flipX = false; node.rotation = 0; node.strokes = [];
      }
      AI8SmartImage.commit('画面调整已重置');
    }

    function transformSmartImageSelection(action) {
      const selected = smartImageSelectedNodes().filter((node) => !node.locked);
      if (!selected.length) return;
      AI8SmartImage.pushHistory('变换图层');
      selected.forEach((node) => {
        if (action === 'rotate-left') node.rotation = (Number(node.rotation || 0) + 270) % 360;
        if (action === 'rotate-right') node.rotation = (Number(node.rotation || 0) + 90) % 360;
        if (action === 'flip-horizontal' && node.type === 'image') node.flipX = !node.flipX;
        if (action === 'restore-source' && node.type === 'image' && node.originalDataUrl) {
          node.dataUrl = node.originalDataUrl;
          node.filter = { brightness: 100, contrast: 100, saturation: 100, blur: 0 };
          node.flipX = false; node.rotation = 0; node.strokes = [];
        }
      });
      AI8SmartImage.commit(action === 'restore-source' ? '已恢复原始图片' : '图层变换已更新');
    }

    function applySmartImageContent(input) {
      const node = smartImagePrimaryNode();
      if (!node || node.locked) return;
      const key = input.dataset.smartImageContent;
      if (node.type === 'text' && key === 'text') node.text = String(input.value || '').slice(0, 500);
      else if (node.type === 'text' && key === 'color') node.color = input.value;
      else if (node.type === 'text' && key === 'fontSize') node.fontSize = smartImageClamp(input.value, 8, 240);
      else if (node.type === 'shape' && key === 'fill') node.fill = input.value;
      AI8SmartImage.render();
      scheduleSmartImageSave();
    }

    async function applySmartImageRatio(ratio) {
      const node = smartImagePrimaryNode();
      if (!node || node.type !== 'image') return;
      const ratios = { '1:1': 1, '9:16': 9 / 16, '16:9': 16 / 9 };
      try {
        if (ratio === 'original') {
          const image = await AI8SmartImage.loadImage(node.originalDataUrl || node.dataUrl);
          node.originalRatio = image.naturalWidth / Math.max(1, image.naturalHeight);
        }
        const targetRatio = ratio === 'original' ? node.originalRatio : ratios[ratio];
        if (!targetRatio) return;
        AI8SmartImage.pushHistory('调整裁切比例');
        node.cropRatio = ratio;
        node.fit = ratio === 'original' ? 'contain' : 'cover';
        node.height = Math.round(node.width / targetRatio);
        AI8SmartImage.commit(`裁切比例已设为 ${ratio === 'original' ? '原比例' : ratio}`);
      } catch (error) {
        setSmartImageStatus(error?.message || '原图比例读取失败', 'error');
      }
    }

    function cycleSmartImageBackground(button) {
      const modes = ['grid', 'dots', 'blank'];
      const index = modes.indexOf(AI8SmartImage.state.background);
      AI8SmartImage.state.background = modes[(index + 1) % modes.length];
      button.textContent = { grid: '网格背景', dots: '点阵背景', blank: '纯色背景' }[AI8SmartImage.state.background];
      AI8SmartImage.commit('画布背景已切换');
    }

    function handleSmartImageAction(action, button) {
      if (action === 'close') closeSmartImageEditor();
      else if (action === 'upload') smartImageElements().uploadInput.click();
      else if (action === 'add-text') createSmartImageTextNode();
      else if (action === 'undo') AI8SmartImage.undo();
      else if (action === 'redo') AI8SmartImage.redo();
      else if (action === 'delete') deleteSmartImageSelection();
      else if (action === 'duplicate') duplicateSmartImageSelection();
      else if (action === 'reset-adjustments') resetSmartImageAdjustments();
      else if (['rotate-left', 'rotate-right', 'flip-horizontal', 'restore-source'].includes(action)) transformSmartImageSelection(action);
      else if (action === 'zoom-in') AI8SmartImage.setZoom(AI8SmartImage.state.viewport.zoom * 1.2);
      else if (action === 'zoom-out') AI8SmartImage.setZoom(AI8SmartImage.state.viewport.zoom / 1.2);
      else if (action === 'zoom-reset') AI8SmartImage.setZoom(1);
      else if (action === 'fit') AI8SmartImage.fitAll(AI8SmartImage.state.selectedIds.length ? smartImageSelectedNodes() : AI8SmartImage.state.nodes);
      else if (action === 'background') cycleSmartImageBackground(button);
      else if (action === 'export') void AI8SmartImage.exportCanvas(false);
      else if (action === 'export-selected') void AI8SmartImage.exportCanvas(true);
      else if (action === 'export-current') void AI8SmartImage.exportCanvas(true);
      else if (action.startsWith('model-')) void AI8SmartImage.runModel?.(action);
    }

    function handleSmartImageDoubleClick(event) {
      const node = smartImageNodeById(event.target.closest('[data-smart-image-node]')?.dataset.smartImageNode);
      if (!node || node.type !== 'text') return;
      const value = window.prompt('编辑文字', node.text || '');
      if (value == null) return;
      AI8SmartImage.pushHistory('编辑文字');
      node.text = String(value).slice(0, 500);
      AI8SmartImage.commit('文字已更新');
    }

    function handleSmartImageWheel(event) {
      if (!event.target.closest('#smartImageCanvasViewport')) return;
      event.preventDefault();
      const point = smartImagePointerPosition(event);
      AI8SmartImage.setZoom(AI8SmartImage.state.viewport.zoom * Math.exp(-event.deltaY * 0.0015), point.x, point.y);
    }

    function copySmartImageSelection() {
      AI8SmartImage.state.clipboard = smartImageSelectedNodes().map(cloneSmartImageNode);
      setSmartImageStatus(`已复制 ${AI8SmartImage.state.clipboard.length} 个图层`);
    }

    function pasteSmartImageSelection() {
      if (!AI8SmartImage.state.clipboard.length) return;
      if (!AI8SmartImage.canAddLayers(AI8SmartImage.state.clipboard.length)) return;
      AI8SmartImage.pushHistory('粘贴图层');
      const groupMap = new Map();
      const pasted = AI8SmartImage.state.clipboard.map((source) => {
        const groupId = source.groupId ? (groupMap.get(source.groupId) || smartImageId('group')) : '';
        if (source.groupId) groupMap.set(source.groupId, groupId);
        return { ...cloneSmartImageNode(source), id: smartImageId(source.type), x: source.x + 36, y: source.y + 36, groupId };
      });
      AI8SmartImage.state.nodes.push(...pasted);
      AI8SmartImage.state.selectedIds = pasted.map((node) => node.id);
      AI8SmartImage.commit(`已粘贴 ${pasted.length} 个图层`);
    }

    function smartImageModalIsOpen() {
      const modal = document.getElementById('smartImageEditorModal');
      return !!modal && !modal.classList.contains('hidden');
    }

    Object.assign(AI8SmartImage, {
      selectNode: selectSmartImageNode,
      deleteSelection: deleteSmartImageSelection,
      duplicateSelection: duplicateSmartImageSelection,
      setTool: setSmartImageTool,
      applyRatio: applySmartImageRatio,
    });
