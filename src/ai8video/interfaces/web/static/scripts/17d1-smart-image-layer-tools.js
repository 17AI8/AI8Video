    function clearSmartImageLayerDropState({ keepDragging = false } = {}) {
      smartImageElements().layerList.querySelectorAll('.smart-image-layer').forEach((layer) => {
        layer.classList.remove('is-drop-before', 'is-drop-after');
        if (!keepDragging) layer.classList.remove('is-dragging');
        delete layer.dataset.dropPosition;
      });
    }

    function beginSmartImageLayerDrag(event) {
      const layer = event.target.closest('[data-smart-image-layer]');
      if (!layer || !event.target.closest('#smartImageLayerList')) return;
      AI8SmartImage.state.draggedLayerId = layer.dataset.smartImageLayer || '';
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', AI8SmartImage.state.draggedLayerId);
      layer.classList.add('is-dragging');
    }

    function moveSmartImageLayerDrag(event) {
      const target = event.target.closest('[data-smart-image-layer]');
      if (!target || !AI8SmartImage.state.draggedLayerId || !event.target.closest('#smartImageLayerList')) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      clearSmartImageLayerDropState({ keepDragging: true });
      const rect = target.getBoundingClientRect();
      const position = event.clientY < rect.top + rect.height / 2 ? 'before' : 'after';
      target.dataset.dropPosition = position;
      target.classList.add(position === 'before' ? 'is-drop-before' : 'is-drop-after');
    }

    function dropSmartImageLayer(event) {
      const target = event.target.closest('[data-smart-image-layer]');
      const sourceId = AI8SmartImage.state.draggedLayerId || event.dataTransfer.getData('text/plain');
      const targetId = target?.dataset.smartImageLayer || '';
      if (!target || !sourceId || !targetId || sourceId === targetId) {
        AI8SmartImage.state.draggedLayerId = '';
        clearSmartImageLayerDropState();
        return;
      }
      event.preventDefault();
      const displayOrder = [...AI8SmartImage.state.nodes].reverse();
      const source = displayOrder.find((node) => node.id === sourceId);
      const withoutSource = displayOrder.filter((node) => node.id !== sourceId);
      const targetIndex = withoutSource.findIndex((node) => node.id === targetId);
      if (!source || targetIndex < 0) {
        AI8SmartImage.state.draggedLayerId = '';
        clearSmartImageLayerDropState();
        return;
      }
      const insertIndex = targetIndex + (target.dataset.dropPosition === 'after' ? 1 : 0);
      AI8SmartImage.pushHistory('调整图层顺序');
      withoutSource.splice(insertIndex, 0, source);
      AI8SmartImage.state.nodes = withoutSource.reverse();
      AI8SmartImage.state.selectedIds = [sourceId];
      AI8SmartImage.state.draggedLayerId = '';
      clearSmartImageLayerDropState();
      AI8SmartImage.commit('图层顺序已调整');
    }

    function toggleSmartImageLayer(id, action) {
      const node = smartImageNodeById(id);
      if (!node) return;
      AI8SmartImage.pushHistory(action === 'visibility' ? '切换图层显示' : '切换图层锁定');
      if (action === 'visibility') node.visible = node.visible === false;
      if (action === 'lock') node.locked = !node.locked;
      AI8SmartImage.commit(action === 'visibility' ? '图层显示状态已更新' : '图层锁定状态已更新');
    }

    function deleteSmartImageLayer(id) {
      const node = smartImageNodeById(id);
      if (!node) return;
      AI8SmartImage.pushHistory('删除图层');
      deleteSmartImageResultFiles(smartImageResultUrls(node));
      AI8SmartImage.state.nodes = AI8SmartImage.state.nodes.filter((item) => item.id !== id);
      AI8SmartImage.state.selectedIds = AI8SmartImage.state.selectedIds.filter((item) => item !== id);
      AI8SmartImage.commit('图层已删除');
    }
