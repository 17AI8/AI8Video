    function smartImageLibraryItems() {
      return Array.isArray(state.userMaterials?.images) ? state.userMaterials.images : [];
    }

    function smartImageLibraryUrl(item) {
      if (item?.url) return String(item.url);
      const relativePath = String(item?.relativePath || item?.name || '').trim().replace(/^\/+/, '');
      if (!relativePath) return '';
      return `/user-materials/images/${relativePath.split('/').map((part) => encodeURIComponent(part)).join('/')}`;
    }

    async function refreshSmartImageLibrary() {
      try {
        await refreshUserMaterials();
      } catch (error) {
        setSmartImageStatus(error?.message || '图片素材库加载失败', 'error');
      }
    }

    function findSmartImageLibraryItem(relativePath) {
      const normalized = String(relativePath || '').trim();
      return smartImageLibraryItems().find((item) => String(item?.relativePath || item?.name || '') === normalized) || null;
    }

    async function smartImageLibraryBlobToFile(blob, item) {
      const sourceName = String(item?.name || item?.relativePath || '素材图片');
      const sourceType = String(blob.type || '').toLowerCase();
      if (SMART_IMAGE_ACCEPTED_TYPES.has(sourceType)) {
        return new File([blob], sourceName, { type: sourceType });
      }
      const objectUrl = URL.createObjectURL(blob);
      try {
        const image = await smartImageLoadElement(objectUrl);
        const scale = Math.min(1, SMART_IMAGE_MAX_EDGE / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
        const pngBlob = await AI8SmartImage.canvasBlob(canvas);
        if (!pngBlob) throw new Error('素材格式转换失败');
        return new File([pngBlob], `${sourceName.replace(/\.[^.]+$/, '')}.png`, { type: 'image/png' });
      } finally {
        AI8SmartImage.imageCache.delete(objectUrl);
        URL.revokeObjectURL(objectUrl);
      }
    }

    async function addSmartImageLibraryItem(relativePath) {
      const item = findSmartImageLibraryItem(relativePath);
      if (!item) {
        await refreshSmartImageLibrary();
      }
      const resolved = findSmartImageLibraryItem(relativePath);
      if (!resolved) {
        setSmartImageStatus('图片素材已不存在，请刷新后重试', 'error');
        return;
      }
      setSmartImageStatus(`正在从素材库导入 ${resolved.name || '图片'}…`);
      try {
        const previousIds = new Set(AI8SmartImage.state.nodes.map((node) => node.id));
        const response = await fetch(smartImageLibraryUrl(resolved));
        if (!response.ok) throw new Error('图片素材读取失败');
        const file = await smartImageLibraryBlobToFile(await response.blob(), resolved);
        await AI8SmartImage.addFiles([file]);
        AI8SmartImage.state.nodes.forEach((node) => {
          if (!previousIds.has(node.id)) node.sourceRelativePath = String(relativePath || '');
        });
        AI8SmartImage.render();
      } catch (error) {
        setSmartImageStatus(error?.message || '图片素材导入失败', 'error');
      }
    }

    function smartImageLibraryOutputName() {
      const project = String(AI8SmartImage.state.projectName || '智能修图').replace(/[\\/:*?"<>|]+/g, '-').trim().slice(0, 60) || '智能修图';
      return `${project}-智能修图.png`;
    }

    async function saveSmartImageCanvasToLibrary(selectedOnly = false) {
      if (AI8SmartImage.state.processing) return;
      AI8SmartImage.state.processing = true;
      smartImageElements().modal.classList.add('is-processing');
      setSmartImageStatus('正在保存到图片素材库…');
      try {
        const nodes = selectedOnly ? AI8SmartImage.currentExportNodes() : AI8SmartImage.state.nodes;
        if (!nodes.length) throw new Error('请先选择要保存的图片');
        const canvas = await AI8SmartImage.composite(nodes);
        const blob = await AI8SmartImage.canvasBlob(canvas);
        if (!blob) throw new Error('浏览器无法生成素材图片');
        const fileName = smartImageLibraryOutputName();
        const form = new FormData();
        form.append('kind', 'image');
        form.append('files', new File([blob], fileName, { type: 'image/png' }), fileName);
        const response = await fetch('/api/upload-user-material', { method: 'POST', body: form });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data?.ok === false || !data?.saved?.length) throw new Error(data?.error || '保存到图片素材库失败');
        await refreshUserMaterials();
        setSmartImageStatus(`已保存到图片素材库：${data.saved[0].name || fileName}`, 'success');
      } catch (error) {
        setSmartImageStatus(error?.message || '保存到图片素材库失败', 'error');
      } finally {
        AI8SmartImage.state.processing = false;
        smartImageElements().modal.classList.remove('is-processing');
      }
    }

    function manageSmartImageLibrary() {
      AI8SmartImage.state.managingLibrary = true;
      closeSmartImageEditor();
      openMaterialLibraryModal('image');
    }

    async function toggleSmartImageLibraryItem(relativePath) {
      const selected = AI8SmartImage.state.nodes.filter((node) => node.sourceRelativePath === relativePath);
      if (selected.length) {
        AI8SmartImage.state.selectedIds = selected.map((node) => node.id);
        deleteSmartImageSelection();
      } else {
        await addSmartImageLibraryItem(relativePath);
      }
      renderMaterialLibraryModal();
    }

    document.addEventListener('click', (event) => {
      const editTrigger = event.target.closest('[data-edit-smart-image-material]');
      if (editTrigger) {
        event.preventDefault();
        const relativePath = editTrigger.getAttribute('data-edit-smart-image-material') || '';
        void toggleSmartImageLibraryItem(relativePath);
        return;
      }
      if (!smartImageModalIsOpen()) return;
      if (event.target.closest('[data-smart-image-library-manage]')) {
        event.preventDefault();
        manageSmartImageLibrary();
      } else if (event.target.closest('[data-smart-image-library-save]')) {
        event.preventDefault();
        const selectedOnly = event.target.closest('[data-smart-image-library-save]')?.dataset.smartImageLibrarySave === 'current';
        void saveSmartImageCanvasToLibrary(selectedOnly);
      }
    });

    Object.assign(AI8SmartImage, {
      addLibraryItem: addSmartImageLibraryItem,
      saveToLibrary: saveSmartImageCanvasToLibrary,
    });
