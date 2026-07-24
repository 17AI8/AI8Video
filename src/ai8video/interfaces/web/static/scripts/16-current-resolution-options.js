    function currentResolutionOptions(settings, mode = currentResolutionMode(settings)) {
      if (mode === 'size') {
        return currentSizeOptions(settings?.ratio);
      }
      const template = String(settings?.template || 'doubao-seedance');
      const model = String(settings?.model || '');
      if (Array.isArray(settings?.resolutionOptions) && settings.resolutionOptions.length) {
        return settings.resolutionOptions.map((item) => String(item));
      }
      if (template === 'bailian-wan') {
        if (['wan2.6-i2v', 'wan2.6-i2v-flash'].includes(model)) return ['720P', '1080P'];
        if (model === 'wan2.5-i2v-preview') return ['480P', '720P', '1080P'];
        if (model === 'wan2.2-i2v-plus') return ['480P', '1080P'];
        if (['wan2.2-i2v-flash', 'wanx2.1-i2v-turbo'].includes(model)) return ['480P', '720P'];
        if (model === 'wanx2.1-i2v-plus') return ['720P'];
        return ['480P', '720P', '1080P'];
      }
      if (template === 'doubao-seedance' && model.includes('doubao-seedance-2-0-fast-260128')) {
        return ['480p', '720p'];
      }
      return ['480p', '720p', '1080p'];
    }

    function currentSizeOptions(ratio) {
      const preferred = String(ratio || '9:16');
      if (preferred === '16:9') return ['1280x720', '1792x1024', '720x480', '1080x720', '480x720', '720x720', '720x1080', '1080x1080'];
      if (preferred === '1:1') return ['720x720', '1080x1080', '720x1280', '1280x720', '1024x1792', '1792x1024', '480x720', '720x480', '720x1080', '1080x720'];
      return ['720x1280', '1024x1792', '480x720', '720x1080', '720x720', '720x480', '1080x720', '1080x1080'];
    }

    function normalizeResolutionForMode(value, template, options, mode, ratio) {
      if (mode === 'size') {
        const raw = String(value || '').trim().toLowerCase();
        if (options.includes(raw)) return raw;
        return currentSizeOptions(ratio)[0] || '480x720';
      }
      const fallback = String(template || '') === 'bailian-wan' ? '480P' : '480p';
      const raw = String(value || fallback).trim();
      const normalized = String(template || '') === 'bailian-wan' ? raw.toUpperCase() : raw.toLowerCase();
      if (options.includes(normalized)) return normalized;
      if (options.includes(fallback)) return fallback;
      return options[0] || fallback;
    }

    function videoResolutionStatusText(settings) {
      const safeSettings = settings || {};
      const mode = currentResolutionMode(safeSettings);
      const template = String(safeSettings.template || 'doubao-seedance');
      const options = currentResolutionOptions(safeSettings, mode);
      const resolution = normalizeResolutionForMode(
        safeSettings.resolution,
        template,
        options,
        mode,
        safeSettings.ratio,
      );
      return mode === 'size' ? `当前尺寸：${resolution}` : `当前清晰度：${resolution}`;
    }

    function currentVideoTemplateStatusText(settings) {
      const template = String(settings?.template || '').trim();
      if (!template) return '当前模板：未配置';
      return `当前模板：${videoTemplateLabel(template)}`;
    }

    function checkboxMarkup(name, label, checked) {
      return `
        <label class="config-check">
          <input type="checkbox" name="${escapeHtml(name)}" ${checked ? 'checked' : ''} />
          <span>${escapeHtml(label)}</span>
        </label>
      `;
    }

    function videoTemplateLabel(template) {
      const labels = {
        'doubao-seedance': '豆包 Seedance',
        'yunwu-grok': '云雾 Grok',
        'yunwu-omni': '云雾 Omni',
        'yunwu-veo': '云雾 Veo',
        'bailian-wan': '百炼 Wan',
        'openai-compatible': '通用 OpenAI 兼容',
      };
      return labels[template] || template;
    }

    function isSensitiveSettingsField(field) {
      return !!field?.sensitive;
    }

    function isSettingsSecretVisible(envName) {
      return !!state.settingsModal.revealedSecrets?.[String(envName || '')];
    }

    function settingsSecretIconMarkup(visible) {
      if (visible) {
        return `
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6Z"></path>
            <circle cx="12" cy="12" r="3"></circle>
          </svg>
        `;
      }
      return `
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 3l18 18"></path>
          <path d="M10.6 5.2A11.1 11.1 0 0 1 12 5c6.4 0 10 7 10 7a17.2 17.2 0 0 1-3.1 3.9"></path>
          <path d="M6.7 6.7C4.1 8.5 2 12 2 12s3.6 6 10 6a10.5 10.5 0 0 0 5.3-1.4"></path>
          <path d="M9.9 9.9A3 3 0 0 0 14.1 14.1"></path>
        </svg>
      `;
    }

    async function openSettingsModal() {
      await refreshAuthSettings();
      await refreshVideoMergeMode();
      await refreshVideoModelSettings();
      closeSystemPromptModal();
      closeBackgroundMusicDrawer();
      closeDefaultReferenceDrawer();
      closeScriptReferenceDrawer();
      closeFlowerTextDrawer();
      closeGenerationModeDrawer();
      closeSmartSplitDrawer();
      closeHtmlMotionOverlayDrawer();
      state.settingsModal.visible = true;
      state.settingsModal.revealedSecrets = {};
      state.settingsModal.activeCategory = state.settingsModal.activeCategory || 'AI8video';
      renderSettingsModal();
    }

    async function refreshArchiveSettings() {
      state.settingsModal.refreshingArchive = true;
      state.settingsModal.activeCategory = '归档';
      renderSettingsModal();
      try {
        await refreshAuthSettings();
      } catch (error) {
        console.error(error);
      } finally {
        state.settingsModal.refreshingArchive = false;
        renderSettingsModal();
      }
    }

    function closeSettingsModal() {
      const previewAudio = state.settingsModal.localTtsPreviewAudio;
      if (previewAudio) {
        previewAudio.pause();
        previewAudio.currentTime = 0;
      }
      state.settingsModal.visible = false;
      state.settingsModal.revealedSecrets = {};
      state.videoParamsModal.visible = false;
      state.settingsModal.videoModelError = '';
      state.settingsModal.videoModelNotice = '';
      state.settingsModal.refreshingArchive = false;
      state.settingsModal.regeneratingPreviews = false;
      state.settingsModal.cleaningArchiveAll = false;
      renderSettingsModal();
      renderVideoParamsModal();
    }

    function openVideoParamsModal() {
      state.videoParamsModal.visible = true;
      state.settingsModal.videoModelError = '';
      state.settingsModal.videoModelNotice = '';
      renderVideoParamsModal();
    }

    function closeVideoParamsModal() {
      clearVideoParamsAutoSaveTimer();
      clearVideoParamsSaveStatusTimer();
      state.videoParamsModal.visible = false;
      renderVideoParamsModal();
    }

    function closeComposerToolDrawers() {
      if (state.systemPromptModal.visible) closeSystemPromptModal();
      if (state.backgroundMusicDrawer.visible) closeBackgroundMusicDrawer();
      if (state.defaultReferenceDrawer.visible) closeDefaultReferenceDrawer();
      if (state.scriptReferenceDrawer.visible) closeScriptReferenceDrawer();
      if (state.flowerTextDrawer.visible) closeFlowerTextDrawer();
      if (state.generationModeDrawer.visible) closeGenerationModeDrawer();
      if (state.smartSplitDrawer.visible) closeSmartSplitDrawer();
      if (state.htmlMotionOverlayDrawer.visible) closeHtmlMotionOverlayDrawer();
    }

    function renderVideoParamsModal() {
      if (!els.videoParamsModal) return;
      const visible = !!state.videoParamsModal.visible;
      els.videoParamsModal.classList.toggle('hidden', !visible);
      if (!visible) return;
      els.videoParamsModalBody.innerHTML = buildVideoParamsFormMarkup();
    }

    async function openSystemPromptModal() {
      if (state.systemPromptModal.visible) {
        closeSystemPromptModal();
        return;
      }
      closeBackgroundMusicDrawer();
      closeDefaultReferenceDrawer();
      closeScriptReferenceDrawer();
      closeFlowerTextDrawer();
      closeGenerationModeDrawer();
      closeSmartSplitDrawer();
      closeHtmlMotionOverlayDrawer();
      closeSmartSplitDrawer();
      state.systemPromptModal.visible = true;
      state.systemPromptModal.loading = true;
      state.systemPromptModal.error = '';
      state.systemPromptModal.notice = '';
      renderSystemPromptModal();
      try {
        const res = await fetch('/api/system-prompt');
        const payload = await res.json().catch(() => ({}));
        if (!res.ok || payload.error) {
          throw new Error(payload.error || '系统提示词读取失败');
        }
        state.systemPromptModal.payload = payload;
        state.systemPromptModal.draft = String(payload.content || '');
      } catch (error) {
        state.systemPromptModal.error = error?.message || String(error);
      } finally {
        state.systemPromptModal.loading = false;
        renderSystemPromptModal();
      }
    }

    function closeSystemPromptModal() {
      state.systemPromptModal.visible = false;
      state.systemPromptModal.error = '';
      state.systemPromptModal.notice = '';
      clearSystemPromptAutoSaveTimer();
      renderSystemPromptModal();
    }

    async function openBackgroundMusicDrawer() {
      if (state.backgroundMusicDrawer.visible) {
        closeBackgroundMusicDrawer();
        return;
      }
      closeSystemPromptModal();
      closeDefaultReferenceDrawer();
      closeScriptReferenceDrawer();
      closeFlowerTextDrawer();
      closeGenerationModeDrawer();
      closeHtmlMotionOverlayDrawer();
      state.backgroundMusicDrawer.visible = true;
      state.backgroundMusicDrawer.loading = true;
      state.backgroundMusic = {
        ...(state.backgroundMusic || {}),
        error: '',
      };
      renderBackgroundMusicButton();
      renderBackgroundMusicDrawer();
      try {
        await refreshBackgroundMusic();
      } catch (error) {
        state.backgroundMusic = {
          ...(state.backgroundMusic || {}),
          error: error?.message || String(error),
        };
      } finally {
        state.backgroundMusicDrawer.loading = false;
        renderBackgroundMusicButton();
        renderBackgroundMusicDrawer();
      }
    }

    function closeBackgroundMusicDrawer() {
      if (!state.backgroundMusicDrawer.visible) {
        renderBackgroundMusicDrawer();
        return;
      }
      state.backgroundMusicDrawer.visible = false;
      state.backgroundMusicDrawer.loading = false;
      renderBackgroundMusicButton();
      renderBackgroundMusicDrawer();
    }

    async function openDefaultReferenceDrawer() {
      if (state.defaultReferenceDrawer.visible) {
        closeDefaultReferenceDrawer();
        return;
      }
      closeSystemPromptModal();
      closeBackgroundMusicDrawer();
      closeScriptReferenceDrawer();
      closeFlowerTextDrawer();
      closeGenerationModeDrawer();
      closeSmartSplitDrawer();
      closeHtmlMotionOverlayDrawer();
      state.defaultReferenceDrawer.visible = true;
      state.defaultReferenceDrawer.loading = true;
      state.defaultReferenceImage = {
        ...(state.defaultReferenceImage || {}),
        error: '',
      };
      renderDefaultReferenceButton();
      renderDefaultReferenceDrawer();
      try {
        await refreshUserMaterials();
        await refreshDefaultReferenceImage();
      } catch (error) {
        state.defaultReferenceImage = {
          ...(state.defaultReferenceImage || {}),
          error: error?.message || String(error),
        };
      } finally {
        state.defaultReferenceDrawer.loading = false;
        renderDefaultReferenceButton();
        renderDefaultReferenceDrawer();
      }
    }

    function closeDefaultReferenceDrawer() {
      if (!state.defaultReferenceDrawer.visible) {
        renderDefaultReferenceDrawer();
        return;
      }
      state.defaultReferenceDrawer.visible = false;
      state.defaultReferenceDrawer.loading = false;
      renderDefaultReferenceButton();
      renderDefaultReferenceDrawer();
    }

    async function openScriptReferenceDrawer() {
      if (state.scriptReferenceDrawer.visible) {
        closeScriptReferenceDrawer();
        return;
      }
      closeSystemPromptModal();
      closeBackgroundMusicDrawer();
      closeDefaultReferenceDrawer();
      closeFlowerTextDrawer();
      closeGenerationModeDrawer();
      closeSmartSplitDrawer();
      closeHtmlMotionOverlayDrawer();
      state.scriptReferenceDrawer.visible = true;
      state.scriptReferenceDrawer.loading = true;
      state.scriptReference = {
        ...(state.scriptReference || {}),
        error: '',
      };
      renderScriptReferenceButton();
      renderScriptReferenceDrawer();
      try {
        await Promise.all([
          refreshUserMaterials(),
          refreshScriptReference(),
          refreshScriptReferenceKnowledgeItems(),
        ]);
      } catch (error) {
        state.scriptReference = {
          ...(state.scriptReference || {}),
          error: error?.message || String(error),
        };
      } finally {
        state.scriptReferenceDrawer.loading = false;
        renderScriptReferenceButton();
        renderScriptReferenceDrawer();
      }
    }

    function closeScriptReferenceDrawer() {
      if (!state.scriptReferenceDrawer.visible) {
        renderScriptReferenceDrawer();
        return;
      }
      state.scriptReferenceDrawer.visible = false;
      state.scriptReferenceDrawer.loading = false;
      renderScriptReferenceButton();
      renderScriptReferenceDrawer();
    }

    async function openFlowerTextDrawer() {
      if (state.flowerTextDrawer.visible) {
        closeFlowerTextDrawer();
        return;
      }
      closeSystemPromptModal();
      closeBackgroundMusicDrawer();
      closeDefaultReferenceDrawer();
      closeScriptReferenceDrawer();
      closeGenerationModeDrawer();
      closeSmartSplitDrawer();
      closeHtmlMotionOverlayDrawer();
      state.flowerTextDrawer.visible = true;
      state.flowerTextDrawer.loading = true;
      state.flowerText = {
        ...(state.flowerText || {}),
        error: '',
        notice: '',
      };
      renderFlowerTextButton();
      renderFlowerTextDrawer();
      try {
        await refreshUserMaterials();
        await refreshFlowerText();
      } catch (error) {
        state.flowerText = {
          ...(state.flowerText || {}),
          error: error?.message || String(error),
        };
      } finally {
        state.flowerTextDrawer.loading = false;
        renderFlowerTextButton();
        renderFlowerTextDrawer();
        scheduleFlowerTextPreviewRefresh(0);
      }
    }

    function closeFlowerTextDrawer() {
      if (!state.flowerTextDrawer.visible) {
        renderFlowerTextDrawer();
        return;
      }
      const activeColorPicker = state.flowerText?.activeColorPicker;
      if (activeColorPicker) {
        const patch = flowerTextStylePatch(activeColorPicker, state.flowerText?.[activeColorPicker]);
        state.flowerText.activeColorPicker = '';
        saveFlowerText(patch || {}, { rerender: false });
      } else {
        flushFlowerTextEditor({ rerender: false });
      }
      clearFlowerTextAutoSaveTimer();
      clearFlowerTextPreviewTimer();
      state.flowerTextDrawer.visible = false;
      state.flowerTextDrawer.loading = false;
      renderFlowerTextButton();
      renderFlowerTextDrawer();
    }

    async function openGenerationModeDrawer() {
      if (state.generationModeDrawer.visible) {
        closeGenerationModeDrawer();
        return;
      }
      closeSystemPromptModal();
      closeBackgroundMusicDrawer();
      closeDefaultReferenceDrawer();
      closeScriptReferenceDrawer();
      closeFlowerTextDrawer();
      closeHtmlMotionOverlayDrawer();
      state.generationModeDrawer.visible = true;
      state.generationModeDrawer.loading = true;
      state.generationMode = {
        ...(state.generationMode || {}),
        error: '',
      };
      renderGenerationModeButton();
      renderGenerationModeDrawer();
      try {
        await refreshGenerationMode();
      } catch (error) {
        state.generationMode = {
          ...(state.generationMode || {}),
          error: error?.message || String(error),
        };
      } finally {
        state.generationModeDrawer.loading = false;
        renderGenerationModeButton();
        renderGenerationModeDrawer();
      }
    }

    function closeGenerationModeDrawer() {
      if (!state.generationModeDrawer.visible) {
        renderGenerationModeDrawer();
        return;
      }
      state.generationModeDrawer.visible = false;
      state.generationModeDrawer.loading = false;
      renderGenerationModeButton();
      renderGenerationModeDrawer();
    }

    async function openHtmlMotionOverlayDrawer() {
      if (state.htmlMotionOverlayDrawer.visible) {
        closeHtmlMotionOverlayDrawer();
        return;
      }
      closeSystemPromptModal();
      closeBackgroundMusicDrawer();
      closeDefaultReferenceDrawer();
      closeScriptReferenceDrawer();
      closeFlowerTextDrawer();
      closeGenerationModeDrawer();
      closeSmartSplitDrawer();
      state.htmlMotionOverlayDrawer.visible = true;
      state.htmlMotionOverlayDrawer.loading = true;
      state.htmlMotionOverlay = { ...(state.htmlMotionOverlay || {}), error: '' };
      renderHtmlMotionOverlayButton();
      renderHtmlMotionOverlayDrawer();
      try {
        await refreshHtmlMotionOverlay();
      } catch (error) {
        state.htmlMotionOverlay = {
          ...(state.htmlMotionOverlay || {}),
          error: error?.message || String(error),
        };
      } finally {
        state.htmlMotionOverlayDrawer.loading = false;
        renderHtmlMotionOverlayButton();
        renderHtmlMotionOverlayDrawer();
      }
    }
