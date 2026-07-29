    function viralBreakdownLibraryBadges(item) {
      const badges = ['原视频'];
      if (Number(item?.frameCount || 0) > 0) badges.push(`${Number(item.frameCount)} 张截图`);
      if (item?.gridImageKey) badges.push('宫格图');
      if (item?.transcriptJsonKey) badges.push('台词');
      if (item?.shotLanguageAnalysisKey) badges.push('镜头语言');
      if (item?.scriptDraft) badges.push('猜剧本');
      if (item?.generateSession) badges.push('生成会话');
      if (item?.generatedVideoKey) badges.push('成片副本');
      return badges;
    }

    function renderViralBreakdownLibraryModal() {
      const modal = document.getElementById('viralBreakdownLibraryModal');
      const wall = document.getElementById('viralBreakdownLibraryWall');
      const meta = document.getElementById('viralBreakdownLibraryMeta');
      const selectAll = document.getElementById('viralBreakdownLibrarySelectAllButton');
      const deleteSelected = document.getElementById('viralBreakdownLibraryDeleteSelectedButton');
      if (!modal || !wall) return;
      modal.classList.toggle('hidden', !state.viralBreakdown.libraryVisible);
      const items = Array.isArray(state.viralBreakdown.items) ? state.viralBreakdown.items : [];
      const selected = new Set(state.viralBreakdown.librarySelectedKeys || []);
      if (meta) meta.textContent = `${items.length} 个视频 · ${state.viralBreakdown.sizeLabel || '0 B'} · 已选 ${selected.size} 个`;
      if (selectAll) selectAll.textContent = items.length && selected.size === items.length ? '取消全选' : '全选';
      if (deleteSelected) deleteSelected.disabled = !selected.size || state.viralBreakdown.libraryDeleting;
      wall.innerHTML = items.length ? items.map((item) => viralBreakdownLibraryItemMarkup(item, selected)).join('')
        : '<div class="viral-breakdown-library-empty">还没有上传视频</div>';
    }

    function viralBreakdownLibraryItemMarkup(item, selected) {
      const key = String(item?.videoKey || '');
      const isSelected = selected.has(key);
      const badges = viralBreakdownLibraryBadges(item)
        .map((label) => `<span class="viral-breakdown-library-badge">${escapeHtml(label)}</span>`)
        .join('');
      return `<div class="viral-breakdown-library-item${isSelected ? ' is-selected' : ''}">
        <input type="checkbox" data-viral-library-select="${escapeHtml(key)}" aria-label="选择 ${escapeHtml(String(item?.name || key))}" ${isSelected ? 'checked' : ''}>
        <div class="viral-breakdown-library-copy">
          <div class="viral-breakdown-library-name" title="${escapeHtml(String(item?.name || key))}">${escapeHtml(String(item?.name || key))}</div>
          <div class="viral-breakdown-library-details"><span>${escapeHtml(String(item?.archiveSizeLabel || item?.sizeLabel || '0 B'))}</span>${badges}</div>
        </div>
        <button type="button" class="viral-breakdown-library-delete" data-delete-viral-library-video="${escapeHtml(key)}">删除</button>
      </div>`;
    }

    function openViralBreakdownLibraryModal() {
      state.viralBreakdown.libraryVisible = true;
      state.viralBreakdown.librarySelectedKeys = [];
      closeViralBreakdownVideoMenu();
      renderViralBreakdownLibraryModal();
    }

    function closeViralBreakdownLibraryModal() {
      state.viralBreakdown.libraryVisible = false;
      state.viralBreakdown.librarySelectedKeys = [];
      renderViralBreakdownLibraryModal();
    }

    function toggleViralBreakdownLibrarySelection(videoKey, checked) {
      const selected = new Set(state.viralBreakdown.librarySelectedKeys || []);
      if (checked) selected.add(String(videoKey || ''));
      else selected.delete(String(videoKey || ''));
      state.viralBreakdown.librarySelectedKeys = Array.from(selected).filter(Boolean);
      renderViralBreakdownLibraryModal();
    }

    function toggleAllViralBreakdownLibraryItems() {
      const items = Array.isArray(state.viralBreakdown.items) ? state.viralBreakdown.items : [];
      const selected = state.viralBreakdown.librarySelectedKeys || [];
      state.viralBreakdown.librarySelectedKeys = selected.length === items.length
        ? []
        : items.map((item) => String(item?.videoKey || '')).filter(Boolean);
      renderViralBreakdownLibraryModal();
    }

    function clearDeletedViralBreakdownDrafts(videoKeys) {
      const stores = ['transcriptDrafts', 'transcriptSegmentDrafts', 'transcriptTtsBusy', 'scriptGuessDrafts', 'scriptGuessTrees'];
      stores.forEach((storeName) => {
        const store = state.viralBreakdown[storeName] || {};
        videoKeys.forEach((key) => { delete store[key]; });
      });
    }

    async function deleteViralBreakdownLibraryItems(videoKeys) {
      const keys = Array.from(new Set((videoKeys || []).map((key) => String(key || '')).filter(Boolean)));
      if (!keys.length || state.viralBreakdown.libraryDeleting) return;
      const message = `确认删除选中的 ${keys.length} 个视频？\n\n原视频、截图、宫格图、台词、镜头语言、猜剧本、生成会话及爆款拆解成片副本都会一并删除，且无法恢复。`;
      if (!window.confirm(message)) return;
      state.viralBreakdown.libraryDeleting = true;
      renderViralBreakdownLibraryModal();
      try {
        const res = await fetch('/api/viral-breakdown/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ videoKeys: keys }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data?.error || '删除爆款拆解素材失败');
        clearDeletedViralBreakdownDrafts(keys);
        state.viralBreakdown.librarySelectedKeys = [];
        await refreshViralBreakdownWorkspace({ keepSelection: true });
        state.viralBreakdown.notice = `已删除 ${Number(data.deletedCount || keys.length)} 个视频及其相关产物。`;
      } finally {
        state.viralBreakdown.libraryDeleting = false;
        renderViralBreakdownWorkbench();
        renderViralBreakdownLibraryModal();
      }
    }

    document.getElementById('viralBreakdownLibraryButton')?.addEventListener('click', openViralBreakdownLibraryModal);
    document.getElementById('viralBreakdownLibraryCloseButton')?.addEventListener('click', closeViralBreakdownLibraryModal);
    document.getElementById('viralBreakdownLibrarySelectAllButton')?.addEventListener('click', toggleAllViralBreakdownLibraryItems);
    document.getElementById('viralBreakdownLibraryDeleteSelectedButton')?.addEventListener('click', () => {
      deleteViralBreakdownLibraryItems(state.viralBreakdown.librarySelectedKeys).catch(handleViralBreakdownLibraryError);
    });

    document.getElementById('viralBreakdownLibraryModal')?.addEventListener('change', (event) => {
      const input = event.target.closest('[data-viral-library-select]');
      if (input) toggleViralBreakdownLibrarySelection(input.getAttribute('data-viral-library-select'), input.checked);
    });

    document.getElementById('viralBreakdownLibraryModal')?.addEventListener('click', (event) => {
      if (event.target.id === 'viralBreakdownLibraryModal') closeViralBreakdownLibraryModal();
      const trigger = event.target.closest('[data-delete-viral-library-video]');
      if (trigger) deleteViralBreakdownLibraryItems([trigger.getAttribute('data-delete-viral-library-video')]).catch(handleViralBreakdownLibraryError);
    });

    function handleViralBreakdownLibraryError(error) {
      console.error(error);
      state.viralBreakdown.error = error?.message || String(error);
      renderViralBreakdownWorkbench();
      renderViralBreakdownLibraryModal();
    }
