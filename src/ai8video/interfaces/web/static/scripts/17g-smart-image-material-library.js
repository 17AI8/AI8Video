    function smartImageLibraryItems() {
      return Array.isArray(state.userMaterials?.images) ? state.userMaterials.images : [];
    }

    function smartImageLibraryUrl(item) {
      if (item?.url) return String(item.url);
      const relativePath = smartImageLibraryItemPath(item);
      if (!relativePath) return '';
      return `/user-materials/images/${relativePath.split('/').map((part) => encodeURIComponent(part)).join('/')}`;
    }

    function smartImageLibraryItemPath(item) {
      return String(item?.relativePath || item?.name || item || '').trim().replace(/\\/g, '/').replace(/^\/+/, '');
    }

    function smartImageRecentLibraryHistory() {
      return AI8SmartImage.normalizeRecentLibraryHistory?.(AI8SmartImage.state.recentLibraryHistory) || [];
    }

    function smartImageEmptyLibrarySlots(count) {
      return Array.from({ length: Math.max(0, count) }, () => '<span class="smart-image-library-slot" aria-hidden="true"></span>');
    }

    function smartImageLibraryMarkup() {
      const cards = smartImageRecentLibraryHistory().map((entry) => {
        const item = findSmartImageLibraryItem(entry.path);
        if (!item) return '';
        const relativePath = smartImageLibraryItemPath(item);
        const selected = smartImageMaterialSelected(relativePath);
        const sourceKey = `library:${relativePath}`;
        const session = selected
          ? { jobs: AI8SmartImage.state.jobs, results: AI8SmartImage.state.results }
          : (AI8SmartImage.state.sourceSessions?.[sourceKey] || {});
        const jobCount = Array.isArray(session.jobs) ? session.jobs.length : 0;
        const resultCount = Array.isArray(session.results) ? session.results.length : 0;
        const name = String(item.name || relativePath);
        return `<button type="button" class="smart-image-library-card${selected ? ' is-selected' : ''}" data-smart-image-library-item="${escapeHtml(relativePath)}" aria-pressed="${selected}" title="${escapeHtml(name)}"><img src="${escapeHtml(smartImageLibraryUrl(item))}" alt="${escapeHtml(name)}"><span><strong>${escapeHtml(name)}</strong><small>${jobCount} 项任务 · ${resultCount} 张结果</small></span></button>`;
      }).filter(Boolean);
      return [...cards, ...smartImageEmptyLibrarySlots(SMART_IMAGE_RECENT_LIBRARY_LIMIT - cards.length)].join('');
    }

    function renderSmartImageLibrary() {
      const list = document.getElementById('smartImageLibraryList');
      if (list) list.innerHTML = smartImageLibraryMarkup();
    }

    async function refreshSmartImageLibrary() {
      try {
        await refreshUserMaterials();
        const availablePaths = new Set(smartImageLibraryItems().map(smartImageLibraryItemPath).filter(Boolean));
        const currentHistory = smartImageRecentLibraryHistory();
        const availableHistory = currentHistory.filter((entry) => availablePaths.has(entry.path));
        if (availableHistory.length !== currentHistory.length) {
          AI8SmartImage.state.recentLibraryHistory = availableHistory;
          AI8SmartImage.scheduleSave?.();
        }
        renderSmartImageLibrary();
      } catch (error) {
        if (smartImageModalIsOpen()) setSmartImageStatus(error?.message || '图片素材库加载失败', 'error');
      }
    }

    function findSmartImageLibraryItem(relativePath) {
      const targetPath = smartImageLibraryItemPath(relativePath);
      return smartImageLibraryItems().find((item) => smartImageLibraryItemPath(item) === targetPath) || null;
    }

    async function importSmartImageLibraryItem(relativePath, closeLibrary = false) {
      if (AI8SmartImage.state.processing) {
        setSmartImageStatus('当前任务生成中，完成后再替换原图', 'error');
        return;
      }
      try {
        let item = findSmartImageLibraryItem(relativePath);
        if (!item) {
          await refreshSmartImageLibrary();
          item = findSmartImageLibraryItem(relativePath);
        }
        if (!item) throw new Error('图片素材已不存在');
        setSmartImageStatus(`正在导入 ${item.name || '素材图片'}…`);
        const response = await fetch(smartImageLibraryUrl(item));
        if (!response.ok) throw new Error('图片素材读取失败');
        const blob = await response.blob();
        let mime = String(blob.type || '').toLowerCase();
        let fileBlob = blob;
        if (!SMART_IMAGE_ACCEPTED_TYPES.has(mime)) {
          const objectUrl = URL.createObjectURL(blob);
          try {
            const image = await smartImageLoadElement(objectUrl);
            const canvas = document.createElement('canvas');
            const scale = Math.min(1, SMART_IMAGE_MAX_EDGE / Math.max(image.naturalWidth, image.naturalHeight));
            canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
            canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
            canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
            fileBlob = await smartImageCanvasBlob(canvas);
            mime = 'image/png';
          } finally {
            URL.revokeObjectURL(objectUrl);
          }
        }
        const extension = mime === 'image/jpeg' ? '.jpg' : mime === 'image/webp' ? '.webp' : '.png';
        const name = String(item.name || '素材图片').replace(/\.[^.]+$/, '') + extension;
        const imported = await AI8SmartImage.setSourceFile(new File([fileBlob], name, { type: mime }), relativePath);
        if (!imported) return;
        const alreadyRecent = smartImageRecentLibraryHistory().some((entry) => entry.path === smartImageLibraryItemPath(relativePath));
        if (closeLibrary || !alreadyRecent) {
          AI8SmartImage.rememberRecentLibrarySelection?.(relativePath);
          AI8SmartImage.render();
          AI8SmartImage.scheduleSave();
        }
        if (closeLibrary && typeof closeMaterialLibraryModal === 'function') {
          AI8SmartImage.state.managingLibrary = true;
          closeMaterialLibraryModal();
        }
      } catch (error) {
        setSmartImageStatus(error?.message || '素材图片导入失败', 'error');
      }
    }

    function manageSmartImageLibrary() {
      AI8SmartImage.state.managingLibrary = true;
      closeSmartImageEditor();
      openMaterialLibraryModal('image');
    }

    function smartImageMaterialSelected(relativePath) {
      return smartImageLibraryItemPath(AI8SmartImage.state.source?.sourceRelativePath) === smartImageLibraryItemPath(relativePath);
    }

    async function saveSmartImageToLibrary() {
      if (AI8SmartImage.state.processing) return setSmartImageStatus('图片模型生成中，请稍后再保存', 'error');
      try {
        const asset = smartImageActiveAsset();
        if (!asset) throw new Error('请先导入图片');
        setSmartImageStatus('正在保存到图片素材库…');
        const canvas = await renderSmartImageAsset(asset, 'png');
        const blob = await smartImageCanvasBlob(canvas);
        if (!blob) throw new Error('浏览器无法生成素材图片');
        const fileName = `${smartImageSafeName()}-智能修图.png`;
        const form = new FormData();
        form.append('kind', 'image');
        form.append('files', new File([blob], fileName, { type: 'image/png' }), fileName);
        const response = await fetch('/api/upload-user-material', { method: 'POST', body: form });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data?.ok === false || !data?.saved?.length) throw new Error(data?.error || '保存到图片素材库失败');
        await refreshSmartImageLibrary();
        setSmartImageStatus(`已保存到图片素材库：${data.saved[0].name || fileName}`, 'success');
      } catch (error) {
        setSmartImageStatus(error?.message || '保存到图片素材库失败', 'error');
      }
    }

    document.addEventListener('click', (event) => {
      const item = event.target.closest('[data-smart-image-library-item]');
      if (item && smartImageModalIsOpen()) {
        event.preventDefault();
        void importSmartImageLibraryItem(item.dataset.smartImageLibraryItem || '');
        return;
      }
      const editTrigger = event.target.closest('[data-edit-smart-image-material]');
      if (editTrigger) {
        event.preventDefault();
        void importSmartImageLibraryItem(editTrigger.getAttribute('data-edit-smart-image-material') || '', true);
      }
    });

    Object.assign(AI8SmartImage, {
      renderLibrary: renderSmartImageLibrary,
      refreshLibrary: refreshSmartImageLibrary,
      importLibraryItem: importSmartImageLibraryItem,
      manageLibrary: manageSmartImageLibrary,
      isMaterialSelected: smartImageMaterialSelected,
      saveToLibrary: saveSmartImageToLibrary,
    });
