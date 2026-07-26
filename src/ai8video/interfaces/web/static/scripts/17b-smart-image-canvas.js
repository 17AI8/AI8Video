    function smartImageId(prefix = 'node') {
      return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    }

    function smartImageClamp(value, min, max) {
      return Math.min(max, Math.max(min, Number(value) || 0));
    }

    function smartImageRemainingLayers() {
      return Math.max(0, SMART_IMAGE_MAX_LAYERS - AI8SmartImage.state.nodes.length);
    }

    function smartImageCanAddLayers(count = 1, notify = true) {
      const allowed = smartImageRemainingLayers() >= Math.max(1, Number(count) || 1);
      if (!allowed && notify) setSmartImageStatus(`图层最多为 ${SMART_IMAGE_MAX_LAYERS} 层，请先删除现有图层`, 'error');
      return allowed;
    }

    function smartImageNodeById(id) {
      return AI8SmartImage.state.nodes.find((node) => node.id === id) || null;
    }

    function smartImageSelectedNodes() {
      const ids = new Set(AI8SmartImage.state.selectedIds);
      return AI8SmartImage.state.nodes.filter((node) => ids.has(node.id));
    }

    function smartImagePrimaryNode() {
      return smartImageNodeById(AI8SmartImage.state.selectedIds[0]);
    }

    function smartImageDefaultNode(type, overrides = {}) {
      const base = {
        id: smartImageId(type), type, name: type === 'image' ? '图片' : type === 'text' ? '文字' : '形状',
        x: 120, y: 100, width: 320, height: 240, rotation: 0, opacity: 100,
        visible: true, locked: false, groupId: '', flipX: false, fit: 'cover', cropRatio: 'original',
        filter: { brightness: 100, contrast: 100, saturation: 100, blur: 0 },
        strokes: [],
      };
      return { ...base, ...overrides, filter: { ...base.filter, ...(overrides.filter || {}) } };
    }

    function smartImageWorldCenter(width = 320, height = 240) {
      const { viewport } = smartImageElements();
      const stateViewport = AI8SmartImage.state.viewport;
      const x = (viewport.clientWidth / 2 - stateViewport.x) / stateViewport.zoom - width / 2;
      const y = (viewport.clientHeight / 2 - stateViewport.y) / stateViewport.zoom - height / 2;
      return { x: Math.round(x), y: Math.round(y) };
    }

    function smartImageCreateNode(type, overrides = {}, historyLabel = '创建图层') {
      if (!smartImageCanAddLayers()) return null;
      AI8SmartImage.pushHistory?.(historyLabel);
      const node = smartImageDefaultNode(type, overrides);
      AI8SmartImage.state.nodes.push(node);
      AI8SmartImage.state.selectedIds = [node.id];
      AI8SmartImage.commit?.(historyLabel);
      return node;
    }

    function smartImageLoadElement(source) {
      if (AI8SmartImage.imageCache.has(source)) return AI8SmartImage.imageCache.get(source);
      const promise = new Promise((resolve, reject) => {
        const image = new Image();
        image.decoding = 'async';
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error('图片读取失败'));
        image.src = source;
      });
      AI8SmartImage.imageCache.set(source, promise);
      return promise;
    }

    function smartImageReadFile(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error(`无法读取 ${file.name || '图片'}`));
        reader.readAsDataURL(file);
      });
    }

    function smartImageValidateFile(file) {
      if (!SMART_IMAGE_ACCEPTED_TYPES.has(String(file?.type || '').toLowerCase())) {
        throw new Error(`${file?.name || '文件'} 不是支持的图片格式`);
      }
      if (Number(file.size || 0) > SMART_IMAGE_MAX_BYTES) {
        throw new Error(`${file.name || '图片'} 超过 30 MB`);
      }
    }

    async function smartImageFileNode(file, index) {
      smartImageValidateFile(file);
      const dataUrl = await smartImageReadFile(file);
      const image = await smartImageLoadElement(dataUrl);
      const scale = Math.min(1, 440 / Math.max(image.naturalWidth, image.naturalHeight));
      const width = Math.max(80, Math.round(image.naturalWidth * scale));
      const height = Math.max(80, Math.round(image.naturalHeight * scale));
      const center = smartImageWorldCenter(width, height);
      return smartImageDefaultNode('image', {
        name: String(file.name || `图片 ${index + 1}`).replace(/\.[^.]+$/, '').slice(0, 60),
        x: center.x + index * 34, y: center.y + index * 34, width, height,
        dataUrl, originalDataUrl: dataUrl, originalRatio: width / Math.max(1, height), sourceName: file.name || '图片', fit: 'contain',
      });
    }

    async function addSmartImageFiles(files) {
      const requested = Array.from(files || []).slice(0, 20);
      if (!requested.length) return;
      const remaining = smartImageRemainingLayers();
      if (!remaining) {
        smartImageCanAddLayers();
        return;
      }
      const list = requested.slice(0, remaining);
      setSmartImageStatus(`正在导入 ${list.length} 张图片…`);
      try {
        const nodes = await Promise.all(list.map(smartImageFileNode));
        const acceptedNodes = nodes.slice(0, smartImageRemainingLayers());
        if (!acceptedNodes.length) {
          smartImageCanAddLayers();
          return;
        }
        const truncated = acceptedNodes.length < requested.length;
        AI8SmartImage.pushHistory?.('导入图片');
        AI8SmartImage.state.nodes.push(...acceptedNodes);
        AI8SmartImage.state.selectedIds = acceptedNodes.map((node) => node.id);
        AI8SmartImage.commit?.('导入图片');
        AI8SmartImage.fitAll?.();
        setSmartImageStatus(truncated
          ? `已导入 ${acceptedNodes.length} 张图片，画布最多保留 ${SMART_IMAGE_MAX_LAYERS} 层`
          : `已导入 ${acceptedNodes.length} 张图片`, 'success');
      } catch (error) {
        setSmartImageStatus(error?.message || '图片导入失败', 'error');
      }
    }

    function smartImageNodeFilter(node) {
      const filter = node.filter || {};
      return `brightness(${filter.brightness || 100}%) contrast(${filter.contrast || 100}%) saturate(${filter.saturation ?? 100}%) blur(${filter.blur || 0}px)`;
    }

    function smartImageNodeContent(node) {
      if (node.type === 'image') {
        if (node.batchItems?.length) {
          const items = node.batchItems.map((item) => `<img class="${node.activeBatchItemId === item.id ? 'is-active' : ''}" data-smart-image-batch-item="${item.id}" src="${escapeHtml(item.dataUrl || '')}" alt="" draggable="false" style="left:${item.x}px;top:${item.y}px;width:${item.width}px;height:${item.height}px;object-fit:cover;transform:rotate(${item.rotation || 0}deg) scaleX(${item.flipX ? -1 : 1})">`).join('');
          return `<div class="smart-image-batch-images" style="filter:${smartImageNodeFilter(node)};transform:scaleX(${node.flipX ? -1 : 1})">${items}</div>`;
        }
        return `<img src="${escapeHtml(node.dataUrl || '')}" alt="" draggable="false" style="object-fit:${node.fit || 'cover'};filter:${smartImageNodeFilter(node)};transform:scaleX(${node.flipX ? -1 : 1})">`;
      }
      if (node.type === 'text') {
        const style = `color:${node.color || '#ffffff'};font-size:${Number(node.fontSize || 36)}px;text-align:${node.textAlign || 'center'}`;
        return `<div class="smart-image-text-node" style="${style}">${escapeHtml(node.text || '双击编辑文字')}</div>`;
      }
      const radius = node.shape === 'ellipse' ? '50%' : `${Number(node.radius || 18)}px`;
      return `<div class="smart-image-shape-node" style="background:${node.fill || '#725cf3'};border-radius:${radius}"></div>`;
    }

    function smartImageBatchItemTools(item) {
      const left = item.x + item.width / 2;
      const ratioTop = item.y + item.height + 8;
      const transformTop = item.y - 8;
      const ratio = item.width / Math.max(1, item.height);
      const active = (value) => Math.abs(ratio - value) < .01;
      return `<div class="smart-image-batch-item-tools"><div class="smart-image-node-ratio-toolbar smart-image-node-floating-toolbar" style="left:${left}px;top:${ratioTop}px" role="group" aria-label="裁切比例"><button data-smart-image-batch-ratio="1:1" data-item-id="${item.id}" aria-pressed="${item.cropRatio === '1:1' && active(1)}">1:1</button><button data-smart-image-batch-ratio="16:9" data-item-id="${item.id}" aria-pressed="${item.cropRatio === '16:9' && active(16 / 9)}">16:9</button><button data-smart-image-batch-ratio="9:16" data-item-id="${item.id}" aria-pressed="${item.cropRatio === '9:16' && active(9 / 16)}">9:16</button><button data-smart-image-batch-ratio="original" data-item-id="${item.id}" aria-pressed="${item.cropRatio === 'original'}">原比例</button></div><div class="smart-image-node-transform-toolbar smart-image-node-floating-toolbar" style="left:${left}px;top:${transformTop}px" role="group" aria-label="图片调整"><button data-smart-image-library-save="current">保存到素材库</button><button data-smart-image-action="export-current">导出</button><button class="danger" data-smart-image-batch-action="delete-item" data-item-id="${item.id}">删除</button></div></div>`;
    }

    function smartImageNodeMarkup(node) {
      const selected = AI8SmartImage.state.selectedIds.includes(node.id);
      const showRatioToolbar = selected && AI8SmartImage.state.selectedIds.length === 1 && node.type === 'image' && !node.batchItems?.length;
      const hidden = node.visible === false ? ' is-hidden' : '';
      const locked = node.locked ? ' is-locked' : '';
      const style = `left:${node.x}px;top:${node.y}px;width:${node.width}px;height:${node.height}px;opacity:${node.opacity / 100};--node-rotation:${node.rotation || 0}deg;--node-counter-rotation:${-(node.rotation || 0)}deg;transform:rotate(var(--node-rotation))`;
      const currentRatio = node.width / Math.max(1, node.height);
      const ratioActive = (ratio) => Math.abs(currentRatio - ratio) < .01;
      const floatingTools = showRatioToolbar ? `<div class="smart-image-node-ratio-toolbar smart-image-node-floating-toolbar" role="group" aria-label="裁切比例"><button data-smart-image-ratio="1:1" aria-pressed="${node.cropRatio === '1:1' && ratioActive(1)}">1:1</button><button data-smart-image-ratio="16:9" aria-pressed="${node.cropRatio === '16:9' && ratioActive(16 / 9)}">16:9</button><button data-smart-image-ratio="9:16" aria-pressed="${node.cropRatio === '9:16' && ratioActive(9 / 16)}">9:16</button><button data-smart-image-ratio="original" aria-pressed="${node.cropRatio === 'original' && !!node.originalRatio && ratioActive(node.originalRatio)}">原比例</button></div><div class="smart-image-node-transform-toolbar smart-image-node-floating-toolbar" role="group" aria-label="图片调整"><button data-smart-image-library-save="current">保存到素材库</button><button data-smart-image-action="export-current">导出</button><button class="danger" data-smart-image-action="delete">删除</button></div>` : '';
      const batchTools = selected && node.batchItems?.length ? smartImageBatchItemTools(node.batchItems.find((item) => item.id === node.activeBatchItemId) || node.batchItems[0]) : '';
      return `<div class="smart-image-node${selected ? ' is-selected' : ''}${hidden}${locked}" data-smart-image-node="${node.id}" data-node-type="${node.type}" style="${style}">${smartImageNodeContent(node)}${floatingTools}${batchTools}${selected ? '<span class="smart-image-resize-handle" data-smart-image-resize></span>' : ''}</div>`;
    }

    function smartImageLayerIcon(node) {
      if (node.type === 'image') return `<img src="${escapeHtml(node.dataUrl || '')}" alt="">`;
      return smartImageIcon(node.type === 'text' ? 'text' : node.shape === 'ellipse' ? 'ellipse' : 'rect');
    }

    function smartImageLayerListMarkup() {
      if (!AI8SmartImage.state.nodes.length) return '<div class="smart-image-empty-list">暂无图层</div>';
      return [...AI8SmartImage.state.nodes].reverse().map((node) => {
        const selected = AI8SmartImage.state.selectedIds.includes(node.id);
        return `<div class="smart-image-layer${selected ? ' is-selected' : ''}" data-smart-image-layer="${node.id}" draggable="true" title="拖拽调整图层顺序"><span class="smart-image-layer-thumb">${smartImageLayerIcon(node)}</span><button class="smart-image-layer-name" data-smart-image-select-layer="${node.id}">${escapeHtml(node.name)}</button><button data-smart-image-layer-action="visibility" data-node-id="${node.id}" aria-label="${node.visible === false ? '显示' : '隐藏'}图层">${node.visible === false ? '○' : '●'}</button><button class="smart-image-layer-delete" data-smart-image-layer-action="delete" data-node-id="${node.id}" aria-label="删除图层">${smartImageIcon('trash')}</button></div>`;
      }).join('');
    }

    function smartImageAssetListMarkup() {
      const images = AI8SmartImage.state.nodes.filter((node) => node.type === 'image');
      if (!images.length) return '<div class="smart-image-empty-list">导入后的图片会显示在这里</div>';
      return images.map((node) => `<button type="button" data-smart-image-select-layer="${node.id}" class="smart-image-asset-card"><img src="${escapeHtml(node.dataUrl)}" alt=""><span>${escapeHtml(node.name)}</span></button>`).join('');
    }

    function renderSmartImageMask(node) {
      if (node.type !== 'image' || !node.strokes?.length || node.visible === false) return;
      const canvas = smartImageElements().scene.querySelector(`[data-smart-image-mask="${node.id}"]`);
      if (!canvas) return;
      canvas.width = Math.max(1, Math.round(node.width));
      canvas.height = Math.max(1, Math.round(node.height));
      const context = canvas.getContext('2d');
      context.strokeStyle = '#ffffff';
      context.lineCap = 'round';
      context.lineJoin = 'round';
      node.strokes.forEach((stroke) => {
        const points = stroke.points || [];
        if (!points.length) return;
        context.lineWidth = Math.max(4, (stroke.size || 36) * node.width / 400);
        context.beginPath();
        points.forEach((point, index) => {
          const x = point.x * node.width;
          const y = point.y * node.height;
          if (!index) context.moveTo(x, y); else context.lineTo(x, y);
        });
        context.stroke();
      });
    }

    function renderSmartImageNodes() {
      const { scene } = smartImageElements();
      scene.innerHTML = AI8SmartImage.state.nodes.map((node) => {
        const mask = node.type === 'image' ? `<canvas class="smart-image-mask-overlay" data-smart-image-mask="${node.id}"></canvas>` : '';
        return smartImageNodeMarkup(node).replace(/<\/div>$/, `${mask}</div>`);
      }).join('');
      AI8SmartImage.state.nodes.forEach(renderSmartImageMask);
    }

    function renderSmartImageInspector() {
      const elements = smartImageElements();
      const selected = smartImageSelectedNodes();
      const node = selected.length === 1 ? selected[0] : null;
      const layerUsage = `${AI8SmartImage.state.nodes.length}/${SMART_IMAGE_MAX_LAYERS} 层`;
      elements.selectionMeta.textContent = selected.length ? `已选 ${selected.length} 个 · ${layerUsage}` : layerUsage;
      elements.layerList.innerHTML = smartImageLayerListMarkup();
      elements.assetList.innerHTML = smartImageAssetListMarkup();
      elements.assetCount.textContent = String(AI8SmartImage.state.nodes.filter((item) => item.type === 'image').length);
      const contentSection = elements.modal.querySelector('#smartImageContentSection');
      const contentMeta = elements.modal.querySelector('#smartImageContentMeta');
      contentSection?.classList.toggle('is-disabled', !node || node.type === 'image');
      if (contentMeta) contentMeta.textContent = node?.type === 'text' ? '文字图层' : node?.type === 'shape' ? '形状图层' : '选择文字或形状';
      elements.modal.querySelectorAll('[data-smart-image-content]').forEach((input) => {
        const key = input.dataset.smartImageContent;
        const enabled = node?.type === 'text' ? ['text', 'color', 'fontSize'].includes(key) : node?.type === 'shape' && key === 'fill';
        input.disabled = !enabled;
        if (node && enabled) input.value = String(node[key] ?? input.value);
        else if (key === 'text') input.value = '';
      });
      const imageNode = node?.type === 'image' ? node : null;
      elements.modal.querySelector('#smartImageAppearanceSection')?.classList.toggle('is-disabled', !node);
      elements.modal.querySelectorAll('[data-smart-image-adjustment]').forEach((input) => {
        const key = input.dataset.smartImageAdjustment;
        const isOpacity = key === 'opacity';
        input.disabled = isOpacity ? !node : !imageNode;
        input.value = String(isOpacity ? (node?.opacity ?? 100) : (imageNode?.filter?.[key] ?? (key === 'blur' ? 0 : 100)));
        const output = elements.modal.querySelector(`[data-smart-image-value="${key}"]`);
        if (output) output.textContent = input.value;
      });
    }

    function smartImageBounds(nodes = AI8SmartImage.state.nodes) {
      const visible = nodes.filter((node) => node.visible !== false);
      if (!visible.length) return null;
      const left = Math.min(...visible.map((node) => node.x));
      const top = Math.min(...visible.map((node) => node.y));
      const right = Math.max(...visible.map((node) => node.x + node.width));
      const bottom = Math.max(...visible.map((node) => node.y + node.height));
      return { left, top, right, bottom, width: right - left, height: bottom - top };
    }

    function renderSmartImageMinimap() {
      const { minimap } = smartImageElements();
      const bounds = smartImageBounds();
      if (!bounds || AI8SmartImage.state.nodes.length < 2) {
        minimap.innerHTML = '';
        minimap.classList.add('hidden');
        return;
      }
      minimap.classList.remove('hidden');
      minimap.innerHTML = AI8SmartImage.state.nodes.filter((node) => node.visible !== false).map((node) => {
        const left = ((node.x - bounds.left) / Math.max(1, bounds.width)) * 100;
        const top = ((node.y - bounds.top) / Math.max(1, bounds.height)) * 100;
        const width = Math.max(4, node.width / Math.max(1, bounds.width) * 100);
        const height = Math.max(4, node.height / Math.max(1, bounds.height) * 100);
        return `<span class="${AI8SmartImage.state.selectedIds.includes(node.id) ? 'is-selected' : ''}" style="left:${left}%;top:${top}%;width:${width}%;height:${height}%"></span>`;
      }).join('');
    }

    function renderSmartImageCanvas() {
      AI8SmartImage.state.renderQueued = false;
      const elements = smartImageElements();
      const viewport = AI8SmartImage.state.viewport;
      elements.scene.style.transform = `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`;
      elements.scene.style.setProperty('--viewport-inverse-zoom', String(1 / Math.max(.1, viewport.zoom)));
      elements.zoomValue.textContent = `${Math.round(viewport.zoom * 100)}%`;
      elements.empty.classList.toggle('hidden', AI8SmartImage.state.nodes.length > 0);
      elements.viewport.classList.toggle('is-hand', AI8SmartImage.state.tool === 'hand' || AI8SmartImage.state.spacePanning);
      elements.viewport.classList.toggle('is-mask', AI8SmartImage.state.tool === 'mask');
      elements.viewport.classList.remove('is-grid', 'is-dots', 'is-blank');
      elements.viewport.classList.add(`is-${AI8SmartImage.state.background}`);
      elements.modal.querySelectorAll('[data-smart-image-tool]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.smartImageTool === AI8SmartImage.state.tool)));
      renderSmartImageNodes();
      renderSmartImageInspector();
      renderSmartImageMinimap();
    }

    function scheduleSmartImageCanvasRender() {
      if (AI8SmartImage.state.renderQueued) return;
      AI8SmartImage.state.renderQueued = true;
      requestAnimationFrame(renderSmartImageCanvas);
    }

    function fitSmartImageCanvas(nodes = AI8SmartImage.state.nodes) {
      const bounds = smartImageBounds(nodes);
      const { viewport } = smartImageElements();
      if (!bounds) {
        AI8SmartImage.state.viewport = { x: 120, y: 90, zoom: 1 };
      } else {
        const padding = 96;
        const zoom = smartImageClamp(Math.min((viewport.clientWidth - padding) / Math.max(1, bounds.width), (viewport.clientHeight - padding) / Math.max(1, bounds.height)), 0.08, 2.5);
        AI8SmartImage.state.viewport = {
          zoom,
          x: (viewport.clientWidth - bounds.width * zoom) / 2 - bounds.left * zoom,
          y: (viewport.clientHeight - bounds.height * zoom) / 2 - bounds.top * zoom,
        };
      }
      scheduleSmartImageCanvasRender();
    }

    function setSmartImageZoom(nextZoom, anchorX, anchorY) {
      const { viewport } = smartImageElements();
      const current = AI8SmartImage.state.viewport;
      const zoom = smartImageClamp(nextZoom, 0.08, 4);
      const x = Number.isFinite(anchorX) ? anchorX : viewport.clientWidth / 2;
      const y = Number.isFinite(anchorY) ? anchorY : viewport.clientHeight / 2;
      const worldX = (x - current.x) / current.zoom;
      const worldY = (y - current.y) / current.zoom;
      AI8SmartImage.state.viewport = { zoom, x: x - worldX * zoom, y: y - worldY * zoom };
      scheduleSmartImageCanvasRender();
    }

    Object.assign(AI8SmartImage, {
      id: smartImageId,
      clamp: smartImageClamp,
      remainingLayers: smartImageRemainingLayers,
      canAddLayers: smartImageCanAddLayers,
      nodeById: smartImageNodeById,
      selectedNodes: smartImageSelectedNodes,
      primaryNode: smartImagePrimaryNode,
      createNode: smartImageCreateNode,
      addFiles: addSmartImageFiles,
      loadImage: smartImageLoadElement,
      bounds: smartImageBounds,
      render: scheduleSmartImageCanvasRender,
      renderNow: renderSmartImageCanvas,
      fitAll: fitSmartImageCanvas,
      setZoom: setSmartImageZoom,
    });
