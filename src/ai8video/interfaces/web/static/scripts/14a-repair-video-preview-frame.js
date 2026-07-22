    function frameRepairReferencePaths() {
      return Array.isArray(state.videoPreviewModal?.frameRepairReferencePaths)
        ? state.videoPreviewModal.frameRepairReferencePaths
        : [];
    }

    function renderVideoPreviewFrameRepairActions() {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid.extension-active');
      const actionBar = stageGrid?.querySelector('.video-preview-extension-action-bar');
      if (!actionBar || !stageGrid?.dataset.extensionFrameKey) return;
      actionBar.querySelector('.video-preview-frame-repair-actions')?.remove();
      const selected = frameRepairReferencePaths();
      const customPrompt = String(state.videoPreviewModal?.frameRepairPrompt || '');
      const images = Array.isArray(state.userMaterials?.images) ? state.userMaterials.images : [];
      const items = images.length
        ? images.map((item) => {
          const path = String(item.relativePath || '');
          const active = selected.includes(path);
          return `<button type="button" class="video-preview-frame-repair-item${active ? ' selected' : ''}" data-frame-repair-reference="${escapeHtml(path)}">
            ${item.url ? `<img src="${escapeHtml(item.url)}" alt="">` : ''}<span>${escapeHtml(item.name || path)}</span>
          </button>`;
        }).join('')
        : '<div class="empty">暂无图片素材</div>';
      actionBar.insertAdjacentHTML('afterbegin', `
        <div class="video-preview-frame-repair-actions">
          <div class="video-preview-split-button" role="group" aria-label="截图修图">
            <button type="button" class="video-preview-button" data-frame-repair-prompt>提示词</button>
            <button type="button" class="video-preview-button" data-frame-repair-toggle>参考图${selected.length ? ` · ${selected.length}` : ''}</button>
            <button type="button" class="video-preview-button" data-frame-repair-start ${selected.length ? '' : 'disabled'}>开始修图</button>
          </div>
          <div class="video-preview-frame-repair-prompt-drawer hidden">
            <textarea data-frame-repair-prompt-input placeholder="补充修图要求，例如：将人物服装改为商务正装。">${escapeHtml(customPrompt)}</textarea>
            <button type="button" class="video-preview-button" data-frame-repair-prompt-save>保存</button>
          </div>
          <div class="video-preview-frame-repair-drawer hidden">${items}</div>
        </div>
      `);
    }

    async function repairVideoPreviewFrame(button) {
      const stageGrid = els.videoPreviewBody?.querySelector('.video-preview-stage-grid.extension-active');
      const frameKey = String(stageGrid?.dataset.extensionFrameKey || '').trim();
      const referencePaths = frameRepairReferencePaths();
      const customPrompt = String(state.videoPreviewModal?.frameRepairPrompt || '').trim();
      if (!frameKey || !referencePaths.length || button.disabled) return;
      button.disabled = true;
      button.textContent = '修图中';
      try {
        const res = await fetch('/api/user-generated-results/extension-frame/repair', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ frameKey, referencePaths, customPrompt }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || '修图失败');
        const imageUrl = `${data.frameUrl}?v=${Date.now()}`;
        const stage = stageGrid.querySelector('.video-preview-extension-stage');
        stage?.querySelector('video[data-frame-preview]')?.replaceWith(Object.assign(document.createElement('img'), { src: imageUrl, alt: '修图截图' }));
        stageGrid.dataset.extensionFrameUrl = String(data.frameUrl || '');
        renderVideoPreviewFrameRepairActions();
      } catch (error) {
        button.disabled = false;
        button.textContent = '开始修图';
        window.alert(error?.message || '修图失败');
      }
    }

    els.videoPreviewBody?.addEventListener('click', async (event) => {
      const target = event.target?.closest?.('[data-frame-repair-toggle], [data-frame-repair-reference], [data-frame-repair-start], [data-frame-repair-prompt], [data-frame-repair-prompt-save]');
      if (!target) return;
      if (target.matches('[data-frame-repair-toggle]')) {
        const drawer = target.closest('.video-preview-frame-repair-actions')?.querySelector('.video-preview-frame-repair-drawer');
        const shouldOpen = !!drawer?.classList.contains('hidden');
        if (shouldOpen) {
          await refreshUserMaterials();
          renderVideoPreviewFrameRepairActions();
        }
        const refreshedDrawer = els.videoPreviewBody
          ?.querySelector('.video-preview-frame-repair-actions .video-preview-frame-repair-drawer');
        refreshedDrawer?.classList.toggle('hidden', !shouldOpen);
        return;
      }
      if (target.matches('[data-frame-repair-prompt]')) {
        target.closest('.video-preview-frame-repair-actions')?.querySelector('.video-preview-frame-repair-prompt-drawer')?.classList.toggle('hidden');
        return;
      }
      if (target.matches('[data-frame-repair-prompt-save]')) {
        const actions = target.closest('.video-preview-frame-repair-actions');
        const prompt = String(actions?.querySelector('[data-frame-repair-prompt-input]')?.value || '').trim();
        state.videoPreviewModal = { ...(state.videoPreviewModal || {}), frameRepairPrompt: prompt };
        const key = String(els.videoPreviewBody?.querySelector('.video-preview-stage-grid')?.dataset.leftVideoKey || '').trim();
        persistVideoPreviewExtensionState(key, { ...(loadVideoPreviewExtensionStates()[key] || {}), frameRepairPrompt: prompt });
        actions?.querySelector('.video-preview-frame-repair-prompt-drawer')?.classList.add('hidden');
        return;
      }
      if (target.matches('[data-frame-repair-reference]')) {
        const path = String(target.dataset.frameRepairReference || '');
        const selected = new Set(frameRepairReferencePaths());
        if (selected.has(path)) selected.delete(path); else selected.add(path);
        state.videoPreviewModal = { ...(state.videoPreviewModal || {}), frameRepairReferencePaths: [...selected].slice(0, 4) };
        renderVideoPreviewFrameRepairActions();
        return;
      }
      if (isVideoPreviewExtensionBatchMode()) await repairVideoPreviewFrameBatch(target);
      else await repairVideoPreviewFrame(target);
    });
