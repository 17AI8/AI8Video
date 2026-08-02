    document.addEventListener('click', (event) => {
      const entry = event.target.closest('[data-open-smart-image-editor-entry]');
      if (entry) {
        event.preventDefault();
        openSmartImageEditor();
        return;
      }
      if (!smartImageModalIsOpen()) return;
      const action = event.target.closest('[data-smart-image-action]');
      if (action) {
        event.preventDefault();
        handleSmartImageAction(action.dataset.smartImageAction || '');
        return;
      }
      const result = event.target.closest('[data-smart-image-result]');
      if (result) {
        event.preventDefault();
        AI8SmartImage.selectResult(result.dataset.smartImageResult || 'source');
        return;
      }
      const view = event.target.closest('[data-smart-image-view]');
      if (view) {
        event.preventDefault();
        AI8SmartImage.setView(view.dataset.smartImageView || 'result');
        return;
      }
      const ratio = event.target.closest('[data-smart-image-ratio]');
      if (ratio) {
        event.preventDefault();
        AI8SmartImage.setRatio(ratio.dataset.smartImageRatio || 'original');
        return;
      }
      const removeJob = event.target.closest('[data-smart-image-remove-job]');
      if (removeJob) {
        event.preventDefault();
        void AI8SmartImage.removeJob(removeJob.dataset.smartImageRemoveJob || '');
        return;
      }
      const retryJob = event.target.closest('[data-smart-image-retry-job]');
      if (retryJob) {
        event.preventDefault();
        AI8SmartImage.retryJob(retryJob.dataset.smartImageRetryJob || '');
        return;
      }
      const job = event.target.closest('[data-smart-image-job]');
      if (job) {
        event.preventDefault();
        AI8SmartImage.selectJob(job.dataset.smartImageJob || '');
        return;
      }
      if (event.target === smartImageElements().modal) closeSmartImageEditor();
    });

    document.addEventListener('change', (event) => {
      if (!smartImageModalIsOpen()) return;
      if (event.target?.id === 'smartImageBatchCount') {
        AI8SmartImage.state.batchCount = smartImageClamp(event.target.value, 1, 8);
        AI8SmartImage.render();
        AI8SmartImage.scheduleSave();
      } else if (event.target?.id === 'smartImageExportFormat') {
        AI8SmartImage.state.exportFormat = ['png', 'jpeg', 'webp'].includes(event.target.value) ? event.target.value : 'png';
        AI8SmartImage.render();
        AI8SmartImage.scheduleSave();
      }
    });

    document.addEventListener('input', (event) => {
      if (!smartImageModalIsOpen()) return;
      if (event.target?.id === 'smartImagePrompt') {
        AI8SmartImage.state.prompt = String(event.target.value || '').slice(0, 2000);
        const preset = smartImagePresetById(AI8SmartImage.state.selectedPresetId);
        if (!preset || preset.prompt !== AI8SmartImage.state.prompt) AI8SmartImage.state.selectedPresetId = 'custom';
        AI8SmartImage.scheduleSave();
        const summary = smartImageElements().presetSummary;
        if (summary) summary.textContent = '自定义描述';
      } else if (event.target.matches?.('[data-smart-image-adjustment]')) {
        AI8SmartImage.updateAdjustment(event.target);
      } else if (event.target?.id === 'smartImageCompareRange') {
        AI8SmartImage.state.comparePosition = smartImageClamp(event.target.value, 0, 100);
        const compare = event.target.closest('.smart-image-compare');
        if (compare) compare.style.setProperty('--compare-position', `${AI8SmartImage.state.comparePosition}%`);
        AI8SmartImage.scheduleSave();
      } else if (event.target?.id === 'smartImageExportQuality') {
        AI8SmartImage.state.exportQuality = smartImageClamp(event.target.value, 60, 100);
        const output = smartImageElements().modal.querySelector('#smartImageExportQualityValue');
        if (output) output.textContent = String(AI8SmartImage.state.exportQuality);
        AI8SmartImage.scheduleSave();
      }
    });

    document.addEventListener('keydown', (event) => {
      if (!smartImageModalIsOpen()) return;
      if (event.key === 'Escape') {
        closeSmartImageEditor();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        event.preventDefault();
        AI8SmartImage.enqueue();
      }
    });

    window.addEventListener('beforeunload', () => {
      if (AI8SmartImage.state.hasOpened) AI8SmartImage.saveProject();
    });
