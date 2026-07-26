    document.addEventListener('click', (event) => {
      const entry = event.target.closest('[data-open-smart-image-editor-entry]');
      if (entry) {
        event.preventDefault();
        openSmartImageEditor();
        return;
      }
      if (!smartImageModalIsOpen()) return;
      if (AI8SmartImage.state.processing) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const batchAction = event.target.closest('[data-smart-image-batch-action]');
      if (batchAction) {
        event.preventDefault();
        transformSmartImageBatchItem(batchAction.dataset.smartImageBatchAction, batchAction.dataset.itemId);
        return;
      }
      const batchRatio = event.target.closest('[data-smart-image-batch-ratio]');
      if (batchRatio) {
        event.preventDefault();
        void applySmartImageBatchItemRatio(batchRatio.dataset.smartImageBatchRatio, batchRatio.dataset.itemId);
        return;
      }
      const action = event.target.closest('[data-smart-image-action]');
      if (action) {
        event.preventDefault();
        handleSmartImageAction(action.dataset.smartImageAction || '', action);
        return;
      }
      const tool = event.target.closest('[data-smart-image-tool]');
      if (tool) {
        event.preventDefault();
        setSmartImageTool(tool.dataset.smartImageTool || 'select');
        return;
      }
      const layer = event.target.closest('[data-smart-image-select-layer]');
      if (layer) {
        selectSmartImageNode(layer.dataset.smartImageSelectLayer, event.shiftKey);
        return;
      }
      const layerAction = event.target.closest('[data-smart-image-layer-action]');
      if (layerAction) {
        if (layerAction.dataset.smartImageLayerAction === 'delete') deleteSmartImageLayer(layerAction.dataset.nodeId);
        else toggleSmartImageLayer(layerAction.dataset.nodeId, layerAction.dataset.smartImageLayerAction);
        return;
      }
      const ratio = event.target.closest('[data-smart-image-ratio]');
      if (ratio) {
        void applySmartImageRatio(ratio.dataset.smartImageRatio || 'original');
        return;
      }
      if (event.target === smartImageElements().modal) closeSmartImageEditor();
    });

    document.addEventListener('pointerdown', (event) => {
      if (!smartImageModalIsOpen()) return;
      if (AI8SmartImage.state.processing) return event.preventDefault();
      const adjustment = event.target.closest('[data-smart-image-adjustment]');
      if (adjustment) AI8SmartImage.pushHistory('调整图层');
      handleSmartImagePointerDown(event);
    });

    document.addEventListener('pointermove', (event) => {
      if (smartImageModalIsOpen() && !AI8SmartImage.state.processing) moveSmartImageInteraction(event);
    });

    document.addEventListener('pointerup', () => {
      if (smartImageModalIsOpen()) finishSmartImageInteraction();
    });

    document.addEventListener('pointercancel', () => {
      if (smartImageModalIsOpen()) finishSmartImageInteraction();
    });

    document.addEventListener('dblclick', (event) => {
      if (smartImageModalIsOpen() && !AI8SmartImage.state.processing) handleSmartImageDoubleClick(event);
    });

    document.addEventListener('wheel', (event) => {
      if (smartImageModalIsOpen() && AI8SmartImage.state.processing) return event.preventDefault();
      if (smartImageModalIsOpen()) handleSmartImageWheel(event);
    }, { passive: false });

    document.addEventListener('change', (event) => {
      if (!smartImageModalIsOpen()) return;
      if (event.target?.id === 'smartImageUploadInput') {
        void AI8SmartImage.addFiles(event.target.files);
        event.target.value = '';
      } else if (event.target?.id === 'smartImageModelBatchCount') {
        AI8SmartImage.state.modelBatchCount = Math.min(8, Math.max(1, Number(event.target.value) || 1));
        scheduleSmartImageSave();
      } else if (event.target.matches?.('[data-smart-image-adjustment]')) {
        AI8SmartImage.commit('图片调整已更新');
      } else if (event.target.matches?.('[data-smart-image-content]')) {
        AI8SmartImage.commit('图层内容已更新');
      }
    });

    document.addEventListener('input', (event) => {
      if (!smartImageModalIsOpen()) return;
      if (event.target.matches?.('[data-smart-image-adjustment]')) {
        applySmartImageAdjustment(event.target);
      } else if (event.target.matches?.('[data-smart-image-content]')) {
        applySmartImageContent(event.target);
      } else if (event.target?.id === 'smartImagePrompt') {
        scheduleSmartImageSave();
      }
    });

    document.addEventListener('focusin', (event) => {
      if (smartImageModalIsOpen() && event.target.matches?.('[data-smart-image-content]') && !AI8SmartImage.state.contentEditing) {
        AI8SmartImage.state.contentEditing = true;
        AI8SmartImage.pushHistory('编辑图层内容');
      }
    });

    document.addEventListener('focusout', (event) => {
      if (!event.target.matches?.('[data-smart-image-content]')) return;
      if (!event.relatedTarget?.matches?.('[data-smart-image-content]')) AI8SmartImage.state.contentEditing = false;
    });

    document.addEventListener('dragstart', (event) => {
      if (smartImageModalIsOpen() && !AI8SmartImage.state.processing) beginSmartImageLayerDrag(event);
    });

    document.addEventListener('dragover', (event) => {
      if (smartImageModalIsOpen() && AI8SmartImage.state.processing) return event.preventDefault();
      if (smartImageModalIsOpen() && event.target.closest('#smartImageLayerList')) {
        moveSmartImageLayerDrag(event);
        return;
      }
      if (!smartImageModalIsOpen() || !event.target.closest('#smartImageCanvasViewport')) return;
      event.preventDefault();
      smartImageElements().viewport.classList.add('is-dragging');
    });

    document.addEventListener('dragleave', (event) => {
      if (!smartImageModalIsOpen() || !event.target.closest('#smartImageCanvasViewport')) return;
      smartImageElements().viewport.classList.remove('is-dragging');
    });

    document.addEventListener('drop', (event) => {
      if (smartImageModalIsOpen() && AI8SmartImage.state.processing) return event.preventDefault();
      if (smartImageModalIsOpen() && event.target.closest('#smartImageLayerList')) {
        dropSmartImageLayer(event);
        return;
      }
      if (!smartImageModalIsOpen() || !event.target.closest('#smartImageCanvasViewport')) return;
      event.preventDefault();
      smartImageElements().viewport.classList.remove('is-dragging');
      void AI8SmartImage.addFiles(event.dataTransfer?.files);
    });

    document.addEventListener('dragend', () => {
      if (!smartImageModalIsOpen()) return;
      AI8SmartImage.state.draggedLayerId = '';
      clearSmartImageLayerDropState();
    });

    document.addEventListener('keydown', (event) => {
      if (!smartImageModalIsOpen()) return;
      if (AI8SmartImage.state.processing) return event.preventDefault();
      const editing = event.target.matches?.('input, textarea, select, [contenteditable="true"]');
      if (event.key === 'Escape') {
        if (AI8SmartImage.state.tool !== 'select') setSmartImageTool('select');
        else closeSmartImageEditor();
        return;
      }
      if (editing) return;
      const command = event.ctrlKey || event.metaKey;
      if (event.key === ' ') {
        AI8SmartImage.state.spacePanning = true;
        AI8SmartImage.render();
        event.preventDefault();
      } else if (command && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        if (event.shiftKey) AI8SmartImage.redo(); else AI8SmartImage.undo();
      } else if (command && event.key.toLowerCase() === 'y') {
        event.preventDefault(); AI8SmartImage.redo();
      } else if (command && event.key.toLowerCase() === 'a') {
        event.preventDefault(); AI8SmartImage.state.selectedIds = AI8SmartImage.state.nodes.filter((node) => node.visible !== false).map((node) => node.id); AI8SmartImage.render();
      } else if (command && event.key.toLowerCase() === 'c') {
        event.preventDefault(); copySmartImageSelection();
      } else if (command && event.key.toLowerCase() === 'v') {
        event.preventDefault(); pasteSmartImageSelection();
      } else if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault(); deleteSmartImageSelection();
      } else if (event.key.toLowerCase() === 'v') setSmartImageTool('select');
      else if (event.key.toLowerCase() === 'h') setSmartImageTool('hand');
      else if (event.key.toLowerCase() === 'b') setSmartImageTool('mask');
      else if (event.key === '0') AI8SmartImage.fitAll();
      else if (event.key === '+' || event.key === '=') AI8SmartImage.setZoom(AI8SmartImage.state.viewport.zoom * 1.2);
      else if (event.key === '-') AI8SmartImage.setZoom(AI8SmartImage.state.viewport.zoom / 1.2);
    });

    document.addEventListener('keyup', (event) => {
      if (event.key === ' ' && AI8SmartImage.state.spacePanning) {
        AI8SmartImage.state.spacePanning = false;
        AI8SmartImage.render();
      }
    });

    window.addEventListener('resize', () => {
      if (smartImageModalIsOpen()) AI8SmartImage.render();
    });

    window.addEventListener('beforeunload', () => {
      if (AI8SmartImage.state.hasOpened) AI8SmartImage.saveProject();
    });
