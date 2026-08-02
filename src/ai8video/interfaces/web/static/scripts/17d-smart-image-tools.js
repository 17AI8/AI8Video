    function smartImagePresetById(id) {
      return SMART_IMAGE_PRESETS.find((item) => item.id === id) || null;
    }

    function resetSmartImagePrompt() {
      const preset = smartImagePresetById(AI8SmartImage.state.selectedPresetId) || smartImagePresetById('natural');
      AI8SmartImage.state.selectedPresetId = preset.id;
      AI8SmartImage.state.prompt = preset.prompt;
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
      setSmartImageStatus('已恢复建议描述', 'success');
    }

    function selectSmartImageResult(id) {
      if (id === 'source') {
        AI8SmartImage.state.selectedResultId = '';
        AI8SmartImage.state.viewMode = 'source';
      } else {
        const result = smartImageResultById(id);
        if (!result) return;
        const job = AI8SmartImage.state.jobs.find((item) => item.id === result.jobId || item.resultIds?.includes(result.id));
        if (job) AI8SmartImage.state.selectedJobId = job.id;
        AI8SmartImage.state.selectedResultId = result.id;
        AI8SmartImage.state.viewMode = 'result';
      }
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
    }

    function applySmartImageJobSelection(job) {
      AI8SmartImage.state.selectedJobId = job?.id || '';
      const results = smartImageResultsForJob(job);
      if (!results.some((result) => result.id === AI8SmartImage.state.selectedResultId)) {
        AI8SmartImage.state.selectedResultId = results[0]?.id || '';
      }
      AI8SmartImage.state.viewMode = AI8SmartImage.state.selectedResultId ? 'result' : 'source';
      if (!job) return;
      AI8SmartImage.state.prompt = String(job.prompt || AI8SmartImage.state.prompt || SMART_IMAGE_DEFAULT_PROMPT);
      AI8SmartImage.state.batchCount = smartImageClamp(job.total || 1, 1, 8);
      AI8SmartImage.state.selectedPresetId = SMART_IMAGE_PRESETS.some((preset) => preset.id === job.presetId)
        ? job.presetId
        : 'custom';
    }

    function selectSmartImageJob(id) {
      const job = smartImageJobById(id);
      if (!job) return;
      applySmartImageJobSelection(job);
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
      setSmartImageStatus(`已切换到当前素材的任务，显示 ${smartImageResultsForJob(job).length} 张结果`, 'success');
    }

    function setSmartImageViewMode(mode) {
      if (!['result', 'source', 'compare'].includes(mode)) return;
      if (mode !== 'source' && !smartImageSelectedResult()) {
        setSmartImageStatus('先生成并选择一张 AI 结果', 'error');
        return;
      }
      AI8SmartImage.state.viewMode = mode;
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
    }

    function updateSmartImageAdjustment(input) {
      const asset = smartImageActiveAsset();
      if (!asset) return;
      asset.edits = smartImageCloneEdits(asset.edits);
      const key = input.dataset.smartImageAdjustment;
      asset.edits[key] = Number(input.value);
      const output = smartImageElements().modal.querySelector(`[data-smart-image-adjustment-value="${key}"]`);
      if (output) output.textContent = input.value;
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
    }

    function resetSmartImageAdjustments() {
      const asset = smartImageActiveAsset();
      if (!asset) return;
      asset.edits = smartImageDefaultEdits();
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
      setSmartImageStatus('当前图片的微调已重置', 'success');
    }

    function transformSmartImageAsset(action) {
      const asset = smartImageActiveAsset();
      if (!asset) return;
      asset.edits = smartImageCloneEdits(asset.edits);
      if (action === 'rotate') asset.edits.rotation = (Number(asset.edits.rotation || 0) + 90) % 360;
      if (action === 'flip') asset.edits.flipX = !asset.edits.flipX;
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
      setSmartImageStatus(action === 'rotate' ? '图片已旋转 90°' : '图片已水平翻转', 'success');
    }

    function setSmartImageRatio(ratio) {
      if (!['original', '1:1', '4:5', '9:16', '16:9'].includes(ratio)) return;
      const asset = smartImageActiveAsset();
      if (!asset) return;
      asset.edits = smartImageCloneEdits(asset.edits);
      asset.edits.ratio = ratio;
      AI8SmartImage.render();
      AI8SmartImage.scheduleSave();
      setSmartImageStatus(`导出比例已设为 ${ratio === 'original' ? '原比例' : ratio}`, 'success');
    }

    async function deleteSmartImageResultFile(result) {
      const resultUrl = String(result?.url || '');
      if (!resultUrl.startsWith('/smart-image-results/')) return true;
      const encodedName = resultUrl.split('/').pop()?.split('?')[0] || '';
      if (!encodedName) return false;
      let fileName = encodedName;
      try { fileName = decodeURIComponent(encodedName); } catch {}
      const response = await fetch(`/api/smart-image-editor/results/${encodeURIComponent(fileName)}`, { method: 'DELETE' });
      return response.ok || response.status === 404;
    }

    async function removeSmartImageJob(id) {
      const job = smartImageJobById(id);
      if (!job || job.status === 'running') return;
      const results = smartImageResultsForJob(job);
      if (results.length && !window.confirm(`删除这个任务及其 ${results.length} 张生成结果？\n原素材图片不会被删除。`)) return;
      const snapshot = {
        jobs: AI8SmartImage.state.jobs,
        results: AI8SmartImage.state.results,
        selectedJobId: AI8SmartImage.state.selectedJobId,
        selectedResultId: AI8SmartImage.state.selectedResultId,
        deletedResultKeys: AI8SmartImage.state.deletedResultKeys,
        deletedJobIds: AI8SmartImage.state.deletedJobIds,
        sourceSessions: AI8SmartImage.state.sourceSessions,
        prompt: AI8SmartImage.state.prompt,
        batchCount: AI8SmartImage.state.batchCount,
        selectedPresetId: AI8SmartImage.state.selectedPresetId,
        viewMode: AI8SmartImage.state.viewMode,
      };
      const resultIds = new Set(results.map((result) => result.id));
      AI8SmartImage.state.deletedJobIds = smartImageSerializableStringList([...(AI8SmartImage.state.deletedJobIds || []), job.id], 128);
      AI8SmartImage.state.deletedResultKeys = smartImageSerializableStringList([
        ...(AI8SmartImage.state.deletedResultKeys || []),
        ...results.map(smartImageResultPersistenceKey),
      ], 256);
      AI8SmartImage.state.jobs = AI8SmartImage.state.jobs.filter((item) => item.id !== job.id);
      AI8SmartImage.state.results = AI8SmartImage.state.results.filter((result) => !resultIds.has(result.id));
      const nextJob = [...AI8SmartImage.state.jobs].reverse()[0] || null;
      applySmartImageJobSelection(nextJob);
      AI8SmartImage.render();
      const saved = await AI8SmartImage.saveProject();
      if (!saved) {
        Object.assign(AI8SmartImage.state, snapshot);
        try { localStorage.setItem(SMART_IMAGE_PROJECT_KEY, JSON.stringify(AI8SmartImage.projectPayload())); } catch {}
        AI8SmartImage.render();
        setSmartImageStatus('任务删除未保存，已保留任务和结果，请稍后重试', 'error');
        return;
      }
      const cleanup = await Promise.allSettled(results.map(deleteSmartImageResultFile));
      const failed = cleanup.filter((item) => item.status === 'rejected' || !item.value).length;
      setSmartImageStatus(
        failed ? `任务和 ${results.length} 张结果已移除，${failed} 个结果文件稍后清理` : `已删除任务及其 ${results.length} 张结果`,
        failed ? 'error' : 'success',
      );
    }

    function handleSmartImageAction(action) {
      if (action === 'close') closeSmartImageEditor();
      else if (action === 'prompt-reset') resetSmartImagePrompt();
      else if (action === 'prompt-optimize') void AI8SmartImage.optimizePrompt?.();
      else if (action === 'enqueue') AI8SmartImage.enqueue?.();
      else if (action === 'reset-adjustments') resetSmartImageAdjustments();
      else if (['rotate', 'flip'].includes(action)) transformSmartImageAsset(action);
      else if (action === 'export-current') void AI8SmartImage.exportCurrent();
      else if (action === 'save-library') void AI8SmartImage.saveToLibrary?.();
      else if (action === 'manage-library') AI8SmartImage.manageLibrary?.();
    }

    Object.assign(AI8SmartImage, {
      selectResult: selectSmartImageResult,
      selectJob: selectSmartImageJob,
      setView: setSmartImageViewMode,
      updateAdjustment: updateSmartImageAdjustment,
      setRatio: setSmartImageRatio,
      removeJob: removeSmartImageJob,
    });
