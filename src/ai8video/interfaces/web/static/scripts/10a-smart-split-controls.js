    function renderSmartSplitButton() {
      const mode = state.generationMode || {};
      const smartSelected = mode.splitMode !== 'manual';
      const visible = !!state.smartSplitDrawer?.visible;
      [
        [els.smartSplitButton, smartSelected, '智能分集'],
        [els.manualSplitButton, !smartSelected, '手动批量'],
      ].forEach(([button, selected, label]) => {
        if (!button) return;
        button.classList.toggle('is-ready', selected);
        button.classList.toggle('is-open', visible && selected);
        button.disabled = !!mode.saving;
        button.textContent = mode.saving && selected ? '保存中' : label;
        button.setAttribute('aria-pressed', selected ? 'true' : 'false');
        button.setAttribute('aria-expanded', visible ? 'true' : 'false');
      });
    }

    function renderSmartSplitDrawer() {
      if (!els.smartSplitDrawer || !els.smartSplitDrawerBody) return;
      const visible = !!state.smartSplitDrawer?.visible;
      els.smartSplitDrawer.classList.toggle('open', visible);
      els.smartSplitDrawer.setAttribute('aria-hidden', visible ? 'false' : 'true');
      if (!visible) return;
      const mode = state.generationMode || {};
      const manual = mode.splitMode === 'manual';
      const saving = !!mode.saving;
      els.smartSplitDrawerBody.innerHTML = `
        <div class="generation-mode-panel">
          ${manual ? `
            <label class="generation-mode-count-field">
              <span>视频数量</span>
              <input type="number" min="1" max="12" step="1" data-manual-split-count value="${Number(mode.manualVideoCount || 2)}" ${saving ? 'disabled' : ''}>
            </label>
            <div class="generation-mode-note">手动模式固定按这里的数量规划，范围 1—12 条。</div>
          ` : `
            <div class="generation-mode-note">Planner 根据全文容量决定数量，并在确认方案中展示分集依据；正文中的数字不参与数量决策。</div>
            <label class="generation-mode-toggle">
              <span>分集后询问</span>
              <input type="checkbox" data-smart-split-confirm-toggle ${mode.confirmSmartSplit ? 'checked' : ''} ${saving ? 'disabled' : ''}>
            </label>
            <div class="generation-mode-note">开启后先展示规划方案并等待确认。</div>
            <label class="generation-mode-toggle">
              <span>传尾帧模式</span>
              <input type="checkbox" data-tail-frame-chaining-toggle ${mode.tailFrameChaining ? 'checked' : ''} ${saving ? 'disabled' : ''}>
            </label>
            <div class="generation-mode-note">开启后串联生成，上一条成片尾帧会作为下一条参考图；连续性更强，但速度更慢。</div>
          `}
        </div>
      `;
    }

    async function openSmartSplitDrawer(splitMode) {
      const requestedMode = splitMode === 'manual' ? 'manual' : 'smart';
      const currentMode = state.generationMode?.splitMode === 'manual' ? 'manual' : 'smart';
      if (requestedMode !== currentMode) {
        await saveGenerationMode({ splitMode: requestedMode, smartSplit: requestedMode === 'smart' });
      }
      if (state.smartSplitDrawer.visible) {
        renderSmartSplitButton();
        renderSmartSplitDrawer();
        return;
      }
      closeComposerToolDrawers();
      state.smartSplitDrawer.visible = true;
      state.smartSplitDrawer.loading = true;
      renderSmartSplitButton();
      renderSmartSplitDrawer();
      try {
        await refreshGenerationMode();
      } catch (error) {
        state.generationMode.error = error?.message || String(error);
      } finally {
        state.smartSplitDrawer.loading = false;
        renderSmartSplitButton();
        renderSmartSplitDrawer();
      }
    }

    function closeSmartSplitDrawer() {
      state.smartSplitDrawer.visible = false;
      state.smartSplitDrawer.loading = false;
      renderSmartSplitButton();
      renderSmartSplitDrawer();
    }
