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
            <div class="generation-mode-note">同一份完整提示词按这里的数量重复提交，由视频模型自然生成相近但不同的版本；不进入分集流程。范围 1—12 条。</div>
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
            ${mode.tailFrameChaining ? `
              <div class="tail-frame-chaining-mode" role="group" aria-label="传尾帧生成方式">
                <label class="tail-frame-chaining-mode-option">
                  <input type="radio" name="tail-frame-chaining-mode" value="auto" data-tail-frame-chaining-mode ${mode.tailFrameChainingMode !== 'manual' ? 'checked' : ''} ${saving ? 'disabled' : ''}>
                  <span>自动</span>
                </label>
                <label class="tail-frame-chaining-mode-option">
                  <input type="radio" name="tail-frame-chaining-mode" value="manual" data-tail-frame-chaining-mode ${mode.tailFrameChainingMode === 'manual' ? 'checked' : ''} ${saving ? 'disabled' : ''}>
                  <span>手动</span>
                </label>
              </div>
            ` : ''}
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
      if (requestedMode === 'manual') await cancelActiveSmartSplitPlan();
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

    async function cancelActiveSmartSplitPlan() {
      const session = getActiveSession();
      if (!session?.id) return;
      const last = session.messages?.at?.(-1);
      const guide = last?.payload?.meta?.guide;
      const pendingPlan = last?.payload?.awaiting === 'smart_split_confirmation'
        || guide?.kind === 'smart_split_confirmation';
      if (!pendingPlan) return;
      const res = await fetch('/api/chat-plan-cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId: session.id }),
      });
      if (!res.ok) return;
      last.payload.awaiting = null;
      last.payload.stage = 'collecting';
      last.payload.text = '已切换为手动批量。后续将按选定数量提交同一份完整提示词，不再进入分集流程。';
      if (last.payload.meta) delete last.payload.meta.guide;
      persistSessions();
      render();
    }

    function closeSmartSplitDrawer() {
      state.smartSplitDrawer.visible = false;
      state.smartSplitDrawer.loading = false;
      renderSmartSplitButton();
      renderSmartSplitDrawer();
    }
