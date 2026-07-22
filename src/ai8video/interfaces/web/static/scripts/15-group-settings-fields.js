    function groupSettingsFields(fields) {
      const buckets = new Map(settingsCategoryOrder.map((label) => [label, []]));
      fields.forEach((field) => {
        const category = String(field.category || '其他');
        if (!buckets.has(category)) {
          buckets.set(category, []);
        }
        buckets.get(category).push(field);
        const aliasCategories = Array.isArray(settingsCategoryAliasMap[category])
          ? settingsCategoryAliasMap[category]
          : [];
        aliasCategories.forEach((aliasCategory) => {
          if (!buckets.has(aliasCategory)) {
            buckets.set(aliasCategory, []);
          }
          buckets.get(aliasCategory).push(field);
        });
      });
      return Array.from(buckets.entries())
        .filter(([, items]) => items.length)
        .map(([label, items]) => ({ label, fields: items }));
    }

    function resolveActiveSettingsCategory(groups) {
      const labels = groups.map((group) => group.label);
      if (labels.includes(state.settingsModal.activeCategory)) {
        return state.settingsModal.activeCategory;
      }
      if (labels.includes('AI8video')) {
        state.settingsModal.activeCategory = 'AI8video';
      } else {
        state.settingsModal.activeCategory = labels[0] || 'AI8video';
      }
      return state.settingsModal.activeCategory;
    }

    function buildSettingsRowMarkup(field) {
      const envName = String(field.envName || '');
      const value = String(field.value || '');
      const configured = !!field.configured;
      const displayValue = configured ? value : '';
      const rowValue = buildSettingsRowValueMarkup(field, displayValue, configured);
      const rowActions = buildSettingsRowActionsMarkup(field);
      const inlinePanel = buildSettingsInlinePanelMarkup(field);
      return `
        <div class="settings-row">
          <div class="settings-row-main">
            <span class="settings-row-title">${escapeHtml(field.label || envName)}</span>
            <span class="settings-row-env">${escapeHtml(envName)} · ${escapeHtml(field.source || 'missing')}</span>
          </div>
          ${rowValue}
          ${rowActions}
        </div>
        ${inlinePanel}
      `;
    }

    function buildSettingsRowValueMarkup(field, displayValue, configured) {
      const envName = String(field.envName || '');
      const value = String(field.value || '');
      if (envName === 'AI8VIDEO_VIDEO_MERGE') {
        const currentMode = normalizeVideoMergeMode(state.settingsModal.videoMergeMode);
        return `
          <div class="settings-segmented merge-mode" role="group" aria-label="视频合并">
            <button type="button" class="settings-segmented-button${currentMode === 'none' ? ' active' : ''}" data-video-merge-mode="none" aria-pressed="${currentMode === 'none' ? 'true' : 'false'}">不合并</button>
            <button type="button" class="settings-segmented-button${currentMode === 'merge2' ? ' active' : ''}" data-video-merge-mode="merge2" aria-pressed="${currentMode === 'merge2' ? 'true' : 'false'}">合并 2 个</button>
            <button type="button" class="settings-segmented-button${currentMode === 'merge4' ? ' active' : ''}" data-video-merge-mode="merge4" aria-pressed="${currentMode === 'merge4' ? 'true' : 'false'}">合并 4 个</button>
          </div>
        `;
      }
      if (envName === 'HTML_MOTION_QUALITY_RETRY_COUNT') {
        const count = normalizeHtmlMotionQualityRetryCount(state.htmlMotionOverlay?.qualityRetryCount ?? value);
        return `<input class="settings-value settings-row-select" type="number" min="0" max="10" step="1" value="${count}" data-html-motion-quality-retry aria-label="HTML 动效不合格重试次数" />`;
      }
      if (envName === 'NARRATION_REVIEW_COUNT') {
        const count = normalizeNarrationReviewCount(state.narrationReview?.reviewCount ?? value);
        return `<input class="settings-value settings-row-select" type="number" min="0" max="10" step="1" value="${count}" data-narration-review-count aria-label="台词审核次数" />`;
      }
      if (envName === 'HTML_MOTION_BEAT_INTERVAL_SECONDS') {
        const seconds = normalizeHtmlMotionBeatIntervalSeconds(state.htmlMotionOverlay?.beatIntervalSeconds ?? value);
        const smart = !!state.htmlMotionOverlay?.smartBeatInterval;
        return `
          <div class="html-motion-beat-control" role="group" aria-label="HTML 动效每拍间隔模式">
            ${smart
              ? '<div class="settings-value html-motion-smart-beat-status">已切换为智能模式</div>'
              : `<input class="settings-value settings-row-select" type="number" min="1" max="30" step="0.1" value="${seconds}" data-html-motion-beat-interval aria-label="HTML 动效每拍间隔秒数" />`}
            <button type="button" class="settings-segmented-button html-motion-smart-beat-button${smart ? ' active' : ''}" data-html-motion-smart-beat aria-pressed="${smart ? 'true' : 'false'}">✓ 智能模式</button>
          </div>
        `;
      }
      if (envName === 'AI8VIDEO_LOCAL_TTS_API_BASE_URL') {
        const tts = state.localTts || {};
        return `<input class="settings-value settings-row-select" name="localTtsApiBaseUrl" value="${escapeHtml(tts.apiBaseUrl || 'https://api.xiaomimimo.com/v1')}" placeholder="https://api.xiaomimimo.com/v1" spellcheck="false" />`;
      }
      if (envName === 'AI8VIDEO_LOCAL_TTS_API_KEY') {
        const tts = state.localTts || {};
        const visible = isSettingsSecretVisible(envName);
        const currentValue = String(tts.apiKey || '');
        return `
          <div class="settings-secret-field">
            <input
              class="settings-value settings-row-select settings-secret-input"
              name="localTtsApiKey"
              type="${visible ? 'text' : 'password'}"
              value="${escapeHtml(currentValue)}"
              placeholder="填 MiMo API Key"
              spellcheck="false"
              autocomplete="off"
              title="${escapeHtml(visible ? currentValue : '已隐藏')}"
            />
            <button
              type="button"
              class="settings-action-button settings-secret-toggle"
              data-toggle-setting-secret="${escapeHtml(envName)}"
              aria-pressed="${visible ? 'true' : 'false'}"
              aria-label="${escapeHtml(visible ? '隐藏密钥' : '显示密钥')}"
              title="${escapeHtml(visible ? '隐藏密钥' : '显示密钥')}"
            >
              ${settingsSecretIconMarkup(visible)}
            </button>
          </div>
        `;
      }
      if (envName === 'AI8VIDEO_LOCAL_TTS_MODEL') {
        const tts = state.localTts || {};
        return `<input class="settings-value settings-row-select" name="localTtsModel" value="${escapeHtml(tts.model || 'mimo-v2.5-tts')}" placeholder="mimo-v2.5-tts" spellcheck="false" />`;
      }
      if (envName === 'AI8VIDEO_LOCAL_TTS_CLONE_MODEL') {
        const tts = state.localTts || {};
        return `<input class="settings-value settings-row-select" name="localTtsCloneModel" value="${escapeHtml(tts.cloneModel || 'mimo-v2.5-tts-voiceclone')}" placeholder="mimo-v2.5-tts-voiceclone" spellcheck="false" />`;
      }
      if (envName === 'AI8VIDEO_LOCAL_TTS_VOICE') {
        const tts = state.localTts || {};
        const defaultVoice = '冰糖';
        const currentVoice = String(tts.voice || defaultVoice);
        const currentVoiceLabel = String(tts.voiceLabel || currentVoice);
        const voiceOptions = Array.isArray(tts.voiceOptions) ? tts.voiceOptions : [];
        const previewing = !!state.settingsModal.localTtsPreviewLoading;
        const uploading = !!tts.cloneUploading;
        const previewButton = `<button type="button" class="settings-action-button local-tts-preview-button" data-local-tts-preview title="今天天气真好，你下载AI8video 了吗" ${previewing ? 'disabled' : ''}>${previewing ? '试听中' : '试听'}</button>`;
        if (voiceOptions.length) {
          const hasCurrentVoice = voiceOptions.some((option) => String(option?.value || '') === currentVoice);
          const optionsToRender = hasCurrentVoice
            ? voiceOptions
            : [{ value: currentVoice, label: `当前自定义：${currentVoiceLabel}` }, ...voiceOptions];
          const optionsMarkup = optionsToRender.map((option) => {
            const optionValue = String(option?.value || '');
            const optionLabel = String(option?.label || optionValue);
            return `<option value="${escapeHtml(optionValue)}" ${optionValue === currentVoice ? 'selected' : ''}>${escapeHtml(optionLabel)}</option>`;
          }).join('');
          return `<div class="local-tts-voice-control"><select class="settings-value settings-row-select local-tts-voice-select" name="localTtsVoice" title="${escapeHtml(currentVoiceLabel)}">${optionsMarkup}</select>${previewButton}<button type="button" class="settings-action-button" data-add-local-tts-voice-clone ${uploading ? 'disabled' : ''}>${uploading ? '上传中' : '上传'}</button><button type="button" class="settings-action-button" data-open-local-tts-voice-clone-folder>打开文件夹</button></div>`;
        }
        const placeholder = '默认冰糖，也可填写官方 voice 名';
        return `<div class="local-tts-voice-control"><input class="settings-value settings-row-select local-tts-voice-select" name="localTtsVoice" value="${escapeHtml(currentVoice)}" placeholder="${escapeHtml(placeholder)}" />${previewButton}<button type="button" class="settings-action-button" data-add-local-tts-voice-clone ${uploading ? 'disabled' : ''}>${uploading ? '上传中' : '上传'}</button><button type="button" class="settings-action-button" data-open-local-tts-voice-clone-folder>打开文件夹</button></div>`;
      }
      if (envName === 'AI8VIDEO_LOCAL_TTS_VOLUME') {
        const tts = state.localTts || {};
        const volumePercent = normalizeLocalTtsVolumePercent(Math.round(Number(tts.volume ?? 1) * 100));
        return `
          <div class="settings-value settings-row-select settings-range-field" data-local-tts-volume-control>
            <span class="settings-range-label" data-local-tts-volume-label>音量 ${volumePercent}%</span>
            <input name="localTtsVolume" type="range" min="0" max="400" step="5" value="${volumePercent}" data-local-tts-volume />
          </div>
        `;
      }
      if (envName === 'AI8VIDEO_VIDEO_MODEL' && Array.isArray(state.settingsModal.videoModelCatalog) && state.settingsModal.videoModelCatalog.length) {
        const currentModel = String(state.videoModelSettings?.model || value || '');
        const rowSaving = state.settingsModal.savingVideoModelField === 'model';
        const rowError = String(state.settingsModal.videoModelRowError || '');
        const rowNotice = rowSaving ? '保存中...' : String(state.settingsModal.videoModelRowNotice || '');
        const rowStatus = rowError || rowNotice;
        const rowTone = rowError ? 'error' : rowSaving ? 'info' : rowNotice ? 'ok' : 'info';
        return `
          <div class="settings-row-value-stack">
            <select class="settings-value settings-row-select" name="catalogModel" title="${escapeHtml(currentModel)}" ${rowSaving ? 'disabled' : ''}>
              <option value="">从模型列表选择</option>
              ${state.settingsModal.videoModelCatalog.map((item) => {
                const modelId = String(item.modelId || item.model || item.name || '');
                if (!modelId) return '';
                const label = item.name || modelId;
                return `<option value="${escapeHtml(modelId)}" ${modelId === currentModel ? 'selected' : ''}>${escapeHtml(label)}</option>`;
              }).join('')}
	            </select>
	            <input class="settings-value settings-row-select" name="manualVideoModel" value="${escapeHtml(currentModel)}" placeholder="手动输入模型名" spellcheck="false" ${rowSaving ? 'disabled' : ''} />
	            <span id="videoModelRowStatus" class="settings-row-save-status" data-tone="${escapeHtml(rowTone)}" aria-live="polite" ${rowStatus ? '' : 'hidden'}>${escapeHtml(rowStatus)}</span>
	          </div>
	        `;
      }
      if (isAuthModelField(envName) && Array.isArray(state.settingsModal.authModelCatalogs?.[envName]) && state.settingsModal.authModelCatalogs[envName].length) {
        const currentModel = String(value || '');
        return `
          <select class="settings-value settings-row-select" name="authCatalogModel" data-auth-model-env="${escapeHtml(envName)}" title="${escapeHtml(currentModel)}">
            <option value="">从模型列表选择</option>
            ${state.settingsModal.authModelCatalogs[envName].map((item) => {
              const modelId = String(item.modelId || item.model || item.name || '');
              if (!modelId) return '';
              const label = item.name || modelId;
              return `<option value="${escapeHtml(modelId)}" ${modelId === currentModel ? 'selected' : ''}>${escapeHtml(label)}</option>`;
            }).join('')}
          </select>
        `;
      }
      if (envName === 'AI8VIDEO_VIDEO_TEMPLATE') {
        const currentTemplate = String(state.videoModelSettings?.template || value || 'doubao-seedance');
        return `
          <select class="settings-value settings-row-select" name="template" title="${escapeHtml(currentTemplate)}">
            ${videoTemplateOptions().map((item) => {
              const optionValue = String(item.value || '');
              return `<option value="${escapeHtml(optionValue)}" ${optionValue === currentTemplate ? 'selected' : ''}>${escapeHtml(item.label || optionValue)}</option>`;
            }).join('')}
          </select>
        `;
      }
      if (configured && isSensitiveSettingsField(field)) {
        const visible = isSettingsSecretVisible(envName);
        return `
          <div class="settings-secret-field">
            <input
              class="settings-value settings-row-select settings-secret-input"
              type="${visible ? 'text' : 'password'}"
              value="${escapeHtml(displayValue)}"
              readonly
              spellcheck="false"
              title="${escapeHtml(visible ? value : '已隐藏')}"
              aria-label="${escapeHtml(`${field.label || envName}${visible ? '已显示' : '已隐藏'}`)}"
            />
            <button
              type="button"
              class="settings-action-button settings-secret-toggle"
              data-toggle-setting-secret="${escapeHtml(envName)}"
              aria-pressed="${visible ? 'true' : 'false'}"
              aria-label="${escapeHtml(visible ? '隐藏密钥' : '显示密钥')}"
              title="${escapeHtml(visible ? '隐藏密钥' : '显示密钥')}"
            >
              ${settingsSecretIconMarkup(visible)}
            </button>
          </div>
        `;
      }
      return `<div class="settings-value${configured ? '' : ' empty'}" title="${escapeHtml(value)}">${escapeHtml(displayValue)}</div>`;
    }

    function buildSettingsRowActionsMarkup(field) {
      const envName = String(field.envName || '');
      const actions = [];
      const archiveArtifactKind = archiveArtifactKindForEnv(envName);
      if (envName === 'AI8VIDEO_VIDEO_MODEL') {
        const pulling = !!state.settingsModal.pullingVideoModels;
        actions.push(`<button type="button" class="settings-action-button" data-pull-video-models="1" ${pulling ? 'disabled' : ''}>${pulling ? '拉取中' : '拉取模型'}</button>`);
      } else if (isAuthModelField(envName)) {
        const pulling = state.settingsModal.pullingAuthModelEnvName === envName;
        actions.push(`<button type="button" class="settings-action-button" data-pull-auth-models="${escapeHtml(envName)}" ${pulling ? 'disabled' : ''}>${pulling ? '拉取中' : '拉取模型'}</button>`);
      }
      if (envName === 'AI8VIDEO_VIDEO_TEMPLATE') {
        actions.push('<button type="button" class="settings-action-button" data-open-video-params="1">参数设置</button>');
      }
      if (archiveArtifactKind && !['covers', 'previews'].includes(archiveArtifactKind)) {
        actions.push(`<button type="button" class="settings-action-button" data-open-archive-artifact="${escapeHtml(archiveArtifactKind)}">打开文件夹</button>`);
      }
      if (envName === 'AI8VIDEO_ARCHIVE_COVER_DIR') {
        const cleaning = state.settingsModal.cleaningArchiveArtifactKind === 'covers';
        actions.push(`<button type="button" class="settings-action-button" data-cleanup-archive-artifact="covers" ${cleaning ? 'disabled' : ''}>${cleaning ? '清理中' : '清理孤儿封面'}</button>`);
        actions.push('<button type="button" class="settings-action-button" data-open-user-generated-cover-folder>打开文件夹</button>');
      }
      if (envName === 'AI8VIDEO_ARCHIVE_PREVIEW_DIR') {
        const regenerating = !!state.settingsModal.regeneratingPreviews;
        actions.push(`<button type="button" class="settings-action-button" data-regenerate-user-generated-previews ${regenerating ? 'disabled' : ''}>${regenerating ? '生成中' : '重新生成预览图'}</button>`);
        actions.push('<button type="button" class="settings-action-button" data-open-user-generated-preview-folder>打开文件夹</button>');
      }
      const cleanupLabel = archiveArtifactCleanupLabel(envName);
      if (cleanupLabel && envName !== 'AI8VIDEO_ARCHIVE_COVER_DIR') {
        const cleaning = state.settingsModal.cleaningArchiveArtifactKind === archiveArtifactKind;
        actions.push(`<button type="button" class="settings-action-button" data-cleanup-archive-artifact="${escapeHtml(archiveArtifactKind)}" ${cleaning ? 'disabled' : ''}>${cleaning ? '清理中' : escapeHtml(cleanupLabel)}</button>`);
      }
      if (envName === 'AI8VIDEO_LOCAL_TTS_OUTPUT_DIR') {
        actions.push('<button type="button" class="settings-action-button" data-open-local-tts-folder>打开文件夹</button>');
      }
      return `<div class="settings-row-actions">${actions.join('')}</div>`;
    }

    function archiveArtifactKindForEnv(envName) {
      return {
        AI8VIDEO_ARCHIVE_RESULT_VIDEO_DIR: 'result-videos',
        AI8VIDEO_ARCHIVE_COVER_DIR: 'covers',
        AI8VIDEO_ARCHIVE_PREVIEW_DIR: 'previews',
        AI8VIDEO_ARCHIVE_TTS_OUTPUT_DIR: 'tts-output',
        AI8VIDEO_ARCHIVE_MERGE_TEMP_DIR: 'merge-temp',
        AI8VIDEO_ARCHIVE_REFERENCE_TEMP_DIR: 'reference-temp',
        AI8VIDEO_ARCHIVE_MANIFEST_DIR: 'manifests',
        AI8VIDEO_ARCHIVE_ASSET_INDEX: 'asset-index',
        AI8VIDEO_ARCHIVE_RECYCLE_BIN_DIR: 'recycle-bin',
      }[String(envName || '')] || '';
    }

    function archiveArtifactCleanupLabel(envName) {
      return {
        AI8VIDEO_ARCHIVE_TTS_OUTPUT_DIR: '清理配音输出',
        AI8VIDEO_ARCHIVE_MERGE_TEMP_DIR: '清理临时媒体',
        AI8VIDEO_ARCHIVE_REFERENCE_TEMP_DIR: '清理临时图片',
        AI8VIDEO_ARCHIVE_MANIFEST_DIR: '清理孤儿元数据',
        AI8VIDEO_ARCHIVE_ASSET_INDEX: '压缩孤儿记录',
        AI8VIDEO_ARCHIVE_RECYCLE_BIN_DIR: '清空回收站',
      }[String(envName || '')] || '';
    }

    function isAuthModelField(envName) {
      return ['mykey.py model', 'AI8VIDEO_LLM_MODEL', 'AI8VIDEO_MULTIMODAL_MODEL', 'AI8VIDEO_IMAGE_MODEL'].includes(String(envName || ''));
    }

    function buildSettingsInlinePanelMarkup(field) {
      void field;
      return '';
    }

    function videoTemplateOptions() {
      const settings = state.videoModelSettings || {};
      return Array.isArray(settings.templateOptions) && settings.templateOptions.length
        ? settings.templateOptions
        : [
            { value: 'doubao-seedance', label: '豆包 Seedance' },
            { value: 'yunwu-grok', label: '云雾 Grok' },
            { value: 'yunwu-omni', label: '云雾 Omni' },
            { value: 'yunwu-veo', label: '云雾 Veo' },
            { value: 'bailian-wan', label: '百炼 Wan' },
            { value: 'openai-compatible', label: 'OpenAI 兼容' },
          ];
    }

    function buildVideoParamsFormMarkup() {
      const settings = state.videoModelSettings || {};
      const saving = !!state.settingsModal.savingVideoModel;
      const notice = state.settingsModal.videoModelNotice || '';
      const error = state.settingsModal.videoModelError || '';
      const template = String(settings.template || 'doubao-seedance');
      const resolutionMode = currentResolutionMode(settings);
      const resolutionOptions = currentResolutionOptions(settings, resolutionMode);
      const resolution = normalizeResolutionForMode(settings.resolution, template, resolutionOptions, resolutionMode, settings.ratio);
      const templateLabel = videoTemplateLabel(template);
      const resolutionModeControl = `
        <label class="config-field compact">
          <span class="config-label">清晰度模式</span>
          <span class="resolution-inline-control">
            <span class="settings-segmented" role="radiogroup" aria-label="清晰度模式">
              ${[
                ['ratio', '比例模式'],
                ['size', '尺寸模式'],
              ].map(([value, label]) => `
                <label class="settings-segmented-button${resolutionMode === value ? ' active' : ''}">
                  <input type="radio" name="resolutionMode" value="${value}" ${resolutionMode === value ? 'checked' : ''} style="position:absolute;opacity:0;pointer-events:none;" />
                  ${label}
                </label>
              `).join('')}
            </span>
            <select class="config-input compact" name="resolution" aria-label="清晰度">
              ${resolutionOptions.map((item) => `<option value="${escapeHtml(item)}" ${String(resolution) === String(item) ? 'selected' : ''}>${escapeHtml(item)}</option>`).join('')}
            </select>
          </span>
        </label>
      `;
      const seedInput = `
        <label class="config-field">
          <span class="config-label">随机种子</span>
          <input class="config-input" name="seed" type="number" min="0" max="4294967295" placeholder="留空随机" value="${settings.seed === null || settings.seed === undefined ? '' : escapeHtml(settings.seed)}" />
        </label>
      `;
      const presetField = `
        <label class="config-field">
          <span class="config-label">生成模式</span>
          <select class="config-input" name="preset">
            ${[
              ['custom', '标准'],
              ['fast', '快速'],
              ['quality', '质量优先'],
            ].map(([value, label]) => `<option value="${value}" ${String(settings.preset || 'custom') === value ? 'selected' : ''}>${label}</option>`).join('')}
          </select>
        </label>
      `;
      const currentTemplateParams = buildCurrentTemplateParamsMarkup(template, settings, seedInput, presetField);
      const ratioField = resolutionMode === 'ratio' ? `
            <label class="config-field">
              <span class="config-label">默认比例</span>
              <select class="config-input" name="ratio">
                ${['9:16', '16:9', '1:1'].map((item) => `<option value="${item}" ${String(settings.ratio || '9:16') === item ? 'selected' : ''}>${item}</option>`).join('')}
              </select>
            </label>
      ` : '';
      return `
        <form id="videoModelParamsForm" class="settings-form">
          <div class="settings-section-title">基础参数 · ${escapeHtml(templateLabel)}</div>
          <div class="modal-grid">
            <label class="config-field">
              <span class="config-label">单条秒数</span>
              <input class="config-input" name="seconds" type="number" min="1" max="60" value="${escapeHtml(settings.seconds || 10)}" />
            </label>
            <label class="config-field">
              <span class="config-label">默认条数</span>
              <input class="config-input" name="videoCount" type="number" min="1" max="20" value="${escapeHtml(settings.video_count || 1)}" />
            </label>
            ${ratioField}
            ${resolutionModeControl}
          </div>

          ${currentTemplateParams}
          <div class="modal-note">
            <div class="modal-note-line">比例：480p/720p/1080p；尺寸：480x720 等 size。</div>
            ${notice ? `<div class="modal-note-line">${escapeHtml(notice)}</div>` : ''}
            ${error ? `<div class="modal-error">${escapeHtml(error)}</div>` : ''}
          </div>
          <div class="modal-actions">
            <button type="button" class="secondary-button" data-close-video-params="1">关闭</button>
          </div>
        </form>
      `;
    }

    function buildCurrentTemplateParamsMarkup(template, settings, seedInput, presetField) {
      if (template === 'doubao-seedance') {
        return `
          <div class="settings-section-title">豆包 Seedance 参数</div>
          <div class="modal-grid">
            <label class="config-field">
              <span class="config-label">服务档位</span>
              <select class="config-input" name="serviceTier">
                ${[
                  ['default', '默认'],
                  ['flex', '弹性'],
                ].map(([value, label]) => `<option value="${value}" ${String(settings.service_tier || 'default') === value ? 'selected' : ''}>${label}</option>`).join('')}
              </select>
            </label>
            <label class="config-field">
              <span class="config-label">任务过期秒数</span>
              <input class="config-input" name="executionExpiresAfter" type="number" min="3600" max="259200" step="60" value="${escapeHtml(settings.execution_expires_after || 172800)}" />
            </label>
            ${seedInput}
          </div>
          <div class="settings-check-grid">
            ${checkboxMarkup('generateAudio', '生成音频', settings.generate_audio)}
            ${checkboxMarkup('returnLastFrame', '返回尾帧', settings.return_last_frame !== false)}
            ${checkboxMarkup('draft', '草稿模式', settings.draft)}
            ${checkboxMarkup('cameraFixed', '镜头固定', settings.camera_fixed)}
          </div>
        `;
      }
      if (template === 'bailian-wan') {
        return `
          <div class="settings-section-title">百炼 Wan 参数</div>
          <div class="modal-grid">
            <label class="config-field">
              <span class="config-label">镜头类型</span>
              <select class="config-input" name="shotType">
                ${[
                  ['multi', '多镜头'],
                  ['single', '单镜头'],
                ].map(([value, label]) => `<option value="${value}" ${String(settings.shot_type || 'multi') === value ? 'selected' : ''}>${label}</option>`).join('')}
              </select>
            </label>
            ${seedInput}
            <label class="config-field full">
              <span class="config-label">音频地址</span>
              <input class="config-input" name="audioUrl" type="text" placeholder="可留空" value="${escapeHtml(settings.audio_url || '')}" />
            </label>
          </div>
          <div class="settings-check-grid">
            ${checkboxMarkup('promptExtend', '提示词扩展', settings.prompt_extend !== false)}
            ${checkboxMarkup('audio', '模型音频', settings.audio)}
          </div>
        `;
      }
      if (template === 'openai-compatible') {
        return `
          <div class="settings-section-title">OpenAI 兼容参数</div>
          <div class="modal-grid">
            ${presetField}
            ${seedInput}
          </div>
          <div class="settings-check-grid">
            ${checkboxMarkup('enhancePrompt', '提示词增强', settings.enhance_prompt !== false)}
            ${checkboxMarkup('returnLastFrame', '返回尾帧', settings.return_last_frame !== false)}
          </div>
        `;
      }
      return `
        <div class="settings-section-title">${escapeHtml(videoTemplateLabel(template))} 参数</div>
        <div class="modal-grid">
          ${seedInput}
        </div>
        <div class="settings-check-grid">
          ${checkboxMarkup('enhancePrompt', '提示词增强', settings.enhance_prompt !== false)}
        </div>
      `;
    }

    function currentResolutionMode(settings) {
      const raw = String(settings?.resolution_mode || settings?.resolutionMode || '').trim();
      if (raw === 'size' || raw === 'ratio') return raw;
      return /^\d{3,4}x\d{3,4}$/i.test(String(settings?.resolution || '')) ? 'size' : 'ratio';
    }

