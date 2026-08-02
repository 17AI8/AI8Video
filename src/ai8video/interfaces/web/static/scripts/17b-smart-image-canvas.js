    function smartImageDefaultEdits() {
      return { brightness: 100, contrast: 100, saturation: 100, rotation: 0, flipX: false, ratio: 'original' };
    }

    function smartImageCloneEdits(edits) {
      return { ...smartImageDefaultEdits(), ...(edits || {}) };
    }

    function smartImageResultById(id) {
      return AI8SmartImage.state.results.find((item) => item.id === id) || null;
    }

    function smartImageJobById(id) {
      return AI8SmartImage.state.jobs.find((item) => item.id === id) || null;
    }

    function smartImageSelectedJob() {
      return smartImageJobById(AI8SmartImage.state.selectedJobId);
    }

    function smartImageResultsForJob(job) {
      if (!job) return [];
      const resultIds = smartImageSerializableStringList(job.resultIds, 64);
      const resultById = new Map(AI8SmartImage.state.results.map((result) => [result.id, result]));
      const ordered = resultIds.map((resultId) => resultById.get(resultId)).filter(Boolean);
      const included = new Set(ordered.map((result) => result.id));
      return ordered.concat(AI8SmartImage.state.results.filter((result) => result.jobId === job.id && !included.has(result.id)));
    }

    function smartImageVisibleResults() {
      return smartImageResultsForJob(smartImageSelectedJob());
    }

    function smartImageSelectedResult() {
      return smartImageVisibleResults().find((item) => item.id === AI8SmartImage.state.selectedResultId) || null;
    }

    function smartImageActiveAsset() {
      return smartImageSelectedResult() || AI8SmartImage.state.source;
    }

    function smartImageAssetSource(asset) {
      return String(asset?.url || asset?.dataUrl || '');
    }

    function smartImageCssFilter(asset) {
      const edits = smartImageCloneEdits(asset?.edits);
      return `brightness(${edits.brightness}%) contrast(${edits.contrast}%) saturate(${edits.saturation}%)`;
    }

    function smartImageRotation(asset) {
      const edits = smartImageCloneEdits(asset?.edits);
      return ((Number(edits.rotation || 0) % 360) + 360) % 360;
    }

    function smartImageRatioValue(ratio, asset) {
      const fixed = { '1:1': 1, '4:5': 4 / 5, '9:16': 9 / 16, '16:9': 16 / 9 };
      if (fixed[ratio]) return fixed[ratio];
      return Math.max(.05, Number(asset?.width || 1) / Math.max(1, Number(asset?.height || 1)));
    }

    function smartImagePreviewRatio(asset) {
      const edits = smartImageCloneEdits(asset?.edits);
      const ratio = smartImageRatioValue(edits.ratio, asset);
      return smartImageRotation(asset) % 180 ? 1 / ratio : ratio;
    }

    function smartImagePreviewStyle(asset, includeFilter = true) {
      const edits = smartImageCloneEdits(asset?.edits);
      const rotation = smartImageRotation(asset);
      const ratio = smartImagePreviewRatio(asset);
      const rotated = rotation % 180 !== 0;
      const width = rotated ? `calc(100% / ${ratio})` : '100%';
      const height = rotated ? `calc(100% * ${ratio})` : '100%';
      const filter = includeFilter ? `filter:${smartImageCssFilter(asset)};` : '';
      return `${filter}position:absolute;left:50%;top:50%;width:${width};height:${height};transform:translate(-50%,-50%) rotate(${rotation}deg) scaleX(${edits.flipX ? -1 : 1})`;
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

    function smartImageStableHash(value) {
      let hash = 2166136261;
      const text = String(value || '');
      for (let index = 0; index < text.length; index += 1) {
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return (hash >>> 0).toString(36);
    }

    function smartImageSourceKey(source) {
      const relativePath = String(source?.sourceRelativePath || '').trim().replace(/\\/g, '/').replace(/^\/+/, '');
      if (relativePath) return `library:${relativePath}`;
      const dataUrl = String(source?.dataUrl || '');
      const dataSample = dataUrl.length > 2048 ? `${dataUrl.slice(0, 1024)}|${dataUrl.slice(-1024)}` : dataUrl;
      const fingerprint = [
        source?.sourceName,
        source?.mime,
        source?.size,
        source?.lastModified,
        source?.width,
        source?.height,
        dataSample,
      ].join('|');
      return `local:${smartImageStableHash(fingerprint)}`;
    }

    function smartImageEnsureSourceKey(source) {
      if (!source) return '';
      const sourceKey = String(source.sourceKey || smartImageSourceKey(source));
      source.sourceKey = sourceKey;
      return sourceKey;
    }

    function smartImageValidateFile(file) {
      if (!SMART_IMAGE_ACCEPTED_TYPES.has(String(file?.type || '').toLowerCase())) {
        throw new Error('仅支持 JPG、PNG 或 WebP 图片');
      }
      if (!file.size) throw new Error('图片文件为空');
      if (Number(file.size) > SMART_IMAGE_MAX_BYTES) throw new Error('图片超过 30 MB，请压缩后再试');
    }

    async function smartImageAssetFromFile(file, sourceRelativePath = '') {
      smartImageValidateFile(file);
      const dataUrl = await smartImageReadFile(file);
      const image = await smartImageLoadElement(dataUrl);
      const source = {
        id: smartImageId('source'),
        name: String(file.name || '待修图片').replace(/\.[^.]+$/, '').slice(0, 80),
        sourceName: String(file.name || '待修图片').slice(0, 120),
        mime: String(file.type || 'image/png'),
        size: Number(file.size || 0),
        lastModified: Number(file.lastModified || 0),
        width: image.naturalWidth,
        height: image.naturalHeight,
        dataUrl,
        sourceRelativePath: String(sourceRelativePath || ''),
        edits: smartImageDefaultEdits(),
      };
      source.sourceKey = smartImageSourceKey(source);
      return source;
    }

    async function setSmartImageSourceFile(file, sourceRelativePath = '') {
      try {
        if (AI8SmartImage.state.processing || AI8SmartImage.state.jobs.some((job) => job.status === 'queued')) {
          throw new Error('任务队列执行中，请完成后再替换原图');
        }
        setSmartImageStatus('正在读取图片…');
        const source = await smartImageAssetFromFile(file, sourceRelativePath);
        smartImageRememberSourceSession();
        const restored = smartImageActivateSourceSession(source);
        AI8SmartImage.render();
        AI8SmartImage.scheduleSave();
        setSmartImageStatus(
          restored
            ? `已切回 ${source.sourceName}，恢复 ${AI8SmartImage.state.results.length} 张结果`
            : `已导入 ${source.sourceName}`,
          'success',
        );
        return true;
      } catch (error) {
        setSmartImageStatus(error?.message || '图片导入失败', 'error');
        return false;
      }
    }

    function smartImageEmptyPreviewMarkup() {
      return `<div class="smart-image-preview-empty"><span>${smartImageIcon('library')}</span><strong>先从素材库选择一张图片</strong><p>选中图片后，它的任务与结果会一起恢复并显示。</p><button type="button" data-smart-image-action="manage-library">打开素材库</button><small>最近选择会固定显示在左侧六宫格</small></div>`;
    }

    function smartImagePreviewImageMarkup(asset, label) {
      const source = smartImageAssetSource(asset);
      const ratio = smartImagePreviewRatio(asset);
      return `<div class="smart-image-preview-frame" style="--preview-ratio:${ratio}"><img src="${escapeHtml(source)}" alt="${escapeHtml(label)}" style="${smartImagePreviewStyle(asset)}"><span class="smart-image-preview-label">${escapeHtml(label)}</span></div>`;
    }

    function smartImageCompareMarkup(result) {
      const source = AI8SmartImage.state.source;
      const position = smartImageClamp(AI8SmartImage.state.comparePosition, 0, 100);
      const ratio = smartImagePreviewRatio(result || source);
      const beforeStyle = smartImagePreviewStyle(result || source, false);
      return `<div class="smart-image-compare" style="--compare-position:${position}%;--preview-ratio:${ratio}"><img class="smart-image-compare-before" src="${escapeHtml(source.dataUrl)}" alt="原图" style="${beforeStyle}"><div class="smart-image-compare-after"><img src="${escapeHtml(result.url)}" alt="AI 修图结果" style="${smartImagePreviewStyle(result)}"></div><span class="smart-image-compare-label before">原图</span><span class="smart-image-compare-label after">AI 结果</span><i class="smart-image-compare-handle"></i><input id="smartImageCompareRange" type="range" min="0" max="100" value="${position}" aria-label="调整原图与结果的对比位置"></div>`;
    }

    function smartImagePreviewMarkup() {
      const source = AI8SmartImage.state.source;
      if (!source) return smartImageEmptyPreviewMarkup();
      const result = smartImageSelectedResult();
      const visibleResults = smartImageVisibleResults();
      if (AI8SmartImage.state.viewMode === 'source' || !result) return smartImagePreviewImageMarkup(source, '原图');
      if (AI8SmartImage.state.viewMode === 'compare') return smartImageCompareMarkup(result);
      return smartImagePreviewImageMarkup(result, `AI 结果 ${visibleResults.indexOf(result) + 1}`);
    }

    function smartImageResultListMarkup() {
      const source = AI8SmartImage.state.source;
      if (!source) return '<div class="smart-image-empty-list">选择图片后，生成结果会出现在这里</div>';
      const selectedJob = smartImageSelectedJob();
      if (!selectedJob) return '<div class="smart-image-empty-list">还没有任务。请在右侧描述效果并开始 AI 修图。</div>';
      const visibleResults = smartImageVisibleResults();
      if (!visibleResults.length) {
        return `<div class="smart-image-empty-list">${['queued', 'running'].includes(selectedJob.status) ? '当前任务正在生成，结果会自动出现在这里。' : '当前任务还没有可用结果。'}</div>`;
      }
      const results = visibleResults.map((item, index) => {
        const selected = item.id === AI8SmartImage.state.selectedResultId;
        return `<button type="button" class="smart-image-result-card${selected ? ' is-selected' : ''}" data-smart-image-result="${item.id}" aria-pressed="${selected}"><span class="smart-image-result-thumb"><img src="${escapeHtml(item.url)}" alt="AI 结果 ${index + 1}"><i>结果 ${index + 1}</i></span><strong>${escapeHtml(item.model || '图片模型')}</strong><small>${new Date(item.createdAt || Date.now()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</small></button>`;
      }).join('');
      return results;
    }

    function smartImageJobListMarkup() {
      const jobs = [...AI8SmartImage.state.jobs].reverse();
      if (!jobs.length) return '<div class="smart-image-empty-list">暂无任务。设置修图要求后点击“开始 AI 修图”。</div>';
      const label = { queued: '等待中', running: '生成中', done: '已完成', partial: '部分完成', error: '失败' };
      return jobs.map((job) => {
        const selected = job.id === AI8SmartImage.state.selectedJobId;
        const attemptTotal = Math.max(1, Number(job.attemptTotal || job.total || 1));
        const attemptDone = Math.min(attemptTotal, Number(job.attemptDone || 0));
        const percent = Math.round(attemptDone / attemptTotal * 100);
        const removable = !['running'].includes(job.status);
        const resultCount = smartImageResultsForJob(job).length;
        const actions = removable ? `<span class="smart-image-job-actions">${['error', 'partial'].includes(job.status) ? `<button type="button" data-smart-image-retry-job="${job.id}">重试</button>` : ''}<button type="button" data-smart-image-remove-job="${job.id}" aria-label="删除任务及其 ${resultCount} 张结果" title="删除任务及其结果">${smartImageIcon('close')}</button></span>` : '';
        return `<article class="smart-image-job${selected ? ' is-selected' : ''}" data-status="${job.status}"><button type="button" class="smart-image-job-select" data-smart-image-job="${job.id}" aria-pressed="${selected}"><span class="smart-image-job-state">${job.status === 'done' ? smartImageIcon('check') : smartImageIcon('sparkle')}</span><span class="smart-image-job-copy"><strong>修图任务 · 目标 ${job.total} 张</strong><span>${escapeHtml(label[job.status] || job.status)} · ${resultCount} 张结果${job.status === 'running' ? ` · 本轮 ${attemptDone}/${attemptTotal}` : ''}</span><small>${escapeHtml(job.error || job.prompt)}</small>${job.status === 'running' ? `<i class="smart-image-job-progress"><b style="width:${percent}%"></b></i>` : ''}</span></button>${actions}</article>`;
      }).join('');
    }

    function renderSmartImageStudio() {
      const elements = smartImageElements();
      const source = AI8SmartImage.state.source;
      const result = smartImageSelectedResult();
      const visibleResults = smartImageVisibleResults();
      const active = smartImageActiveAsset();
      elements.preview.innerHTML = smartImagePreviewMarkup();
      elements.resultList.innerHTML = smartImageResultListMarkup();
      elements.resultCount.textContent = `${visibleResults.length} 张`;
      elements.jobList.innerHTML = smartImageJobListMarkup();
      elements.jobCount.textContent = `${AI8SmartImage.state.jobs.length} 项`;
      if (elements.jobOwner) elements.jobOwner.textContent = source ? `${source.name} · 点击任务切换结果` : '先选择素材图片';
      elements.modal.querySelector('.smart-image-result-section')?.classList.toggle('is-empty', !visibleResults.length);
      elements.previewTitle.textContent = result ? `AI 结果 ${visibleResults.indexOf(result) + 1}` : source ? source.name : '等待导入图片';
      elements.previewMeta.textContent = result ? `${result.model || '图片模型'} · 原图可随时对比` : source ? `${source.width} × ${source.height} · 原图始终保留` : '原图始终保留，不会被覆盖';
      elements.presetSummary.textContent = AI8SmartImage.state.selectedPresetId === 'custom' ? '自定义描述' : '建议描述';
      elements.callHint.textContent = `本任务预计调用图片模型 ${AI8SmartImage.state.batchCount} 次${AI8SmartImage.state.promptOptimizing ? ' · 正在优化描述' : ''}`;
      elements.generateLabel.textContent = AI8SmartImage.state.processing ? '加入任务队列' : '开始 AI 修图';
      if (document.activeElement !== elements.prompt) elements.prompt.value = AI8SmartImage.state.prompt;
      elements.batchCount.value = String(AI8SmartImage.state.batchCount);
      elements.exportFormat.value = AI8SmartImage.state.exportFormat;
      elements.exportQuality.value = String(AI8SmartImage.state.exportQuality);
      const qualityValue = elements.modal.querySelector('#smartImageExportQualityValue');
      if (qualityValue) qualityValue.textContent = String(AI8SmartImage.state.exportQuality);
      elements.modal.querySelector('#smartImageExportQualityRow')?.classList.toggle('is-disabled', AI8SmartImage.state.exportFormat === 'png');
      elements.modal.querySelector('#smartImageAdjustSection')?.classList.toggle('is-disabled', !active);
      elements.modal.querySelector('#smartImageFinishSection')?.classList.toggle('is-disabled', !active);
      const effectiveViewMode = result ? AI8SmartImage.state.viewMode : 'source';
      elements.modal.querySelectorAll('[data-smart-image-view]').forEach((button) => {
        const mode = button.dataset.smartImageView;
        button.setAttribute('aria-pressed', String(mode === effectiveViewMode));
        button.disabled = mode !== 'source' && !result;
      });
      const edits = smartImageCloneEdits(active?.edits);
      elements.modal.querySelectorAll('[data-smart-image-adjustment]').forEach((input) => {
        input.value = String(edits[input.dataset.smartImageAdjustment] ?? 100);
        const output = elements.modal.querySelector(`[data-smart-image-adjustment-value="${input.dataset.smartImageAdjustment}"]`);
        if (output) output.textContent = input.value;
      });
      elements.modal.querySelectorAll('[data-smart-image-ratio]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.smartImageRatio === edits.ratio)));
      const enqueueButton = elements.modal.querySelector('[data-smart-image-action="enqueue"]');
      if (enqueueButton) enqueueButton.disabled = !source || !state.health?.hasImageModel || AI8SmartImage.state.promptOptimizing;
      const optimizeButton = elements.modal.querySelector('[data-smart-image-action="prompt-optimize"]');
      if (optimizeButton) {
        optimizeButton.disabled = AI8SmartImage.state.promptOptimizing || !state.health?.hasLLM;
        optimizeButton.innerHTML = AI8SmartImage.state.promptOptimizing ? `${smartImageIcon('sparkle')}优化中…` : `${smartImageIcon('sparkle')}AI 优化描述`;
        optimizeButton.title = state.health?.hasLLM ? '调用当前文本模型优化描述（1 次文本调用）' : '请先在设置中配置文本模型';
      }
      elements.modal.querySelectorAll('[data-smart-image-action="export-current"], [data-smart-image-action="save-library"]').forEach((button) => { button.disabled = !active; });
      AI8SmartImage.renderLibrary?.();
    }

    Object.assign(AI8SmartImage, {
      id: smartImageId,
      clamp: smartImageClamp,
      loadImage: smartImageLoadElement,
      activeAsset: smartImageActiveAsset,
      selectedJob: smartImageSelectedJob,
      visibleResults: smartImageVisibleResults,
      selectedResult: smartImageSelectedResult,
      setSourceFile: setSmartImageSourceFile,
      render: renderSmartImageStudio,
    });
