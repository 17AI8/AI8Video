    function renderSmartSplitButton() {
      const button = els.smartSplitButton;
      if (!button) return;
      const mode = state.generationMode || {};
      const enabled = !!mode.smartSplit;
      button.classList.toggle('is-ready', enabled);
      button.classList.toggle('is-open', !!state.smartSplitDrawer?.visible);
      button.disabled = !!mode.saving;
      button.textContent = mode.saving ? '保存中' : '智能分集';
      button.setAttribute('aria-expanded', state.smartSplitDrawer?.visible ? 'true' : 'false');
      button.title = enabled ? 'Planner 会根据全文智能规划分集。' : '点击设置智能分集。';
    }

    function renderSmartSplitDrawer() {
      if (!els.smartSplitDrawer || !els.smartSplitDrawerBody) return;
      const visible = !!state.smartSplitDrawer?.visible;
      els.smartSplitDrawer.classList.toggle('open', visible);
      els.smartSplitDrawer.setAttribute('aria-hidden', visible ? 'false' : 'true');
      if (!visible) return;
      const mode = state.generationMode || {};
      const saving = !!mode.saving;
      els.smartSplitDrawerBody.innerHTML = `
        <div class="generation-mode-panel">
          <label class="generation-mode-toggle">
            <span>智能分集</span>
            <input type="checkbox" data-smart-split-toggle ${mode.smartSplit ? 'checked' : ''} ${saving ? 'disabled' : ''}>
          </label>
          <div class="generation-mode-note">开启后，Planner 根据全文内容规划合理集数和每集主题；已明确数量时仍按指定数量规划。</div>
          <label class="generation-mode-toggle">
            <span>分集后询问</span>
            <input type="checkbox" data-smart-split-confirm-toggle ${mode.confirmSmartSplit ? 'checked' : ''} ${!mode.smartSplit || saving ? 'disabled' : ''}>
          </label>
          <div class="generation-mode-note">开启后先用气泡展示分集方案并等待确认；关闭后自动进入视频生成。</div>
          <label class="generation-mode-toggle">
            <span>传尾帧模式</span>
            <input type="checkbox" data-tail-frame-chaining-toggle ${mode.tailFrameChaining ? 'checked' : ''} ${!mode.smartSplit || saving ? 'disabled' : ''}>
          </label>
          <div class="generation-mode-note">
            关闭时各条视频独立生成，可并发且更快，但画面差异可能较大。开启后改为串联生成：上一条成片尾帧会作为下一条参考图，连续性更强但速度更慢；每条提示词会追加“最后一秒主体必须正对镜头”。
          </div>
        </div>
      `;
    }

    async function openSmartSplitDrawer() {
      if (state.smartSplitDrawer.visible) {
        closeSmartSplitDrawer();
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
