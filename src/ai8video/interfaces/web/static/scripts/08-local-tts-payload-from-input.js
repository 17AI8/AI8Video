    document.addEventListener('change', async (event) => {
      const target = event.target;
      if ((target instanceof HTMLInputElement || target instanceof HTMLSelectElement) && target.name && target.name.startsWith('localTts')) {
        await saveLocalTtsSettings(localTtsPayloadFromInput(target));
        return;
      }
      if (target instanceof HTMLInputElement && target.name === 'manualVideoModel') {
        const value = String(target.value || '').trim();
        if (!value) {
          state.settingsModal.videoModelRowError = '';
          state.settingsModal.videoModelRowNotice = '请输入模型名';
          renderSettingsModal();
          scheduleVideoModelRowNoticeClear();
          return;
        }
        await saveVideoModelSelection(value, '模型已保存');
        return;
      }
      if (!(target instanceof HTMLSelectElement)) return;
      if (target.name === 'template') {
        state.videoModelSettings = {
          ...(state.videoModelSettings || {}),
          template: target.value || 'doubao-seedance',
        };
        await saveVideoModelPayload(currentVideoModelPayload());
        state.settingsModal.videoModelNotice = '模板已保存';
        state.settingsModal.videoModelError = '';
        renderSettingsModal();
        return;
      }
      if (target.name === 'catalogModel') {
        const value = String(target.value || '').trim();
        if (!value) {
          state.settingsModal.videoModelRowError = '';
          state.settingsModal.videoModelRowNotice = '请选择一个模型';
          renderSettingsModal();
          scheduleVideoModelRowNoticeClear();
          return;
        }
        await saveVideoModelSelection(value, '模型已保存');
        return;
      }
      if (target.name === 'authCatalogModel') {
        const value = String(target.value || '').trim();
        const envName = String(target.getAttribute('data-auth-model-env') || '').trim();
        if (!value || !envName) return;
        await saveAuthModelSelection(envName, value);
      }
    });

    function localTtsPayloadFromInput(input) {
      const name = String(input?.name || '');
      if (name === 'localTtsApiBaseUrl') return { apiBaseUrl: input.value || 'https://api.xiaomimimo.com/v1' };
      if (name === 'localTtsApiKey') return { apiKey: input.value || '' };
      if (name === 'localTtsModel') return { model: input.value || 'mimo-v2.5-tts' };
      if (name === 'localTtsCloneModel') return { cloneModel: input.value || 'mimo-v2.5-tts-voiceclone' };
      if (name === 'localTtsVoice') return { voice: input.value || '' };
      if (name === 'localTtsVolume') return { volume: normalizeLocalTtsVolumePercent(input.value) / 100 };
      return {};
    }

    async function saveLocalTtsSettings(payload) {
      const res = await fetch('/api/local-tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) {
        state.settingsModal.videoModelError = data?.error || 'TTS设置保存失败';
        renderSettingsModal();
        return;
      }
      state.localTts = data;
      await refreshAuthSettings();
      state.settingsModal.videoModelError = '';
      renderSettingsModal();
    }

    async function previewLocalTtsVoice() {
      if (state.settingsModal.localTtsPreviewLoading) return;
      const voiceInput = els.settingsModalBody?.querySelector('[name="localTtsVoice"]');
      const apiBaseUrlInput = els.settingsModalBody?.querySelector('[name="localTtsApiBaseUrl"]');
      const apiKeyInput = els.settingsModalBody?.querySelector('[name="localTtsApiKey"]');
      const modelInput = els.settingsModalBody?.querySelector('[name="localTtsModel"]');
      const cloneModelInput = els.settingsModalBody?.querySelector('[name="localTtsCloneModel"]');
      const volumeInput = els.settingsModalBody?.querySelector('[name="localTtsVolume"]');
      const defaultVoice = '冰糖';
      const payload = {
        voice: voiceInput?.value || state.localTts?.voice || defaultVoice,
        apiBaseUrl: apiBaseUrlInput?.value || state.localTts?.apiBaseUrl || 'https://api.xiaomimimo.com/v1',
        apiKey: apiKeyInput?.value || state.localTts?.apiKey || '',
        model: modelInput?.value || state.localTts?.model || 'mimo-v2.5-tts',
        cloneModel: cloneModelInput?.value || state.localTts?.cloneModel || 'mimo-v2.5-tts-voiceclone',
        volume: volumeInput ? (normalizeLocalTtsVolumePercent(volumeInput.value) / 100) : (state.localTts?.volume ?? 1),
      };
      const previewSignature = JSON.stringify(payload);
      const cachedAudio = state.settingsModal.localTtsPreviewAudio;
      if (cachedAudio && state.settingsModal.localTtsPreviewSignature === previewSignature) {
        try {
          cachedAudio.pause();
          cachedAudio.currentTime = 0;
          await cachedAudio.play();
          return;
        } catch (error) {
          state.settingsModal.localTtsPreviewAudio = null;
          state.settingsModal.localTtsPreviewUrl = '';
          state.settingsModal.localTtsPreviewSignature = '';
        }
      }
      state.settingsModal.localTtsPreviewLoading = true;
      state.settingsModal.videoModelError = '';
      state.settingsModal.videoModelNotice = '';
      renderSettingsModal();
      try {
        const res = await fetch('/api/local-tts/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false || !data?.audioUrl) {
          throw new Error(data?.error || '试听生成失败');
        }
        const audioUrl = data?.cacheKey
          ? `${data.audioUrl}?v=${encodeURIComponent(data.cacheKey)}`
          : data.audioUrl;
        let audio = state.settingsModal.localTtsPreviewAudio;
        if (!audio || state.settingsModal.localTtsPreviewUrl !== audioUrl) {
          audio = new Audio(audioUrl);
          audio.preload = 'auto';
          state.settingsModal.localTtsPreviewAudio = audio;
          state.settingsModal.localTtsPreviewUrl = audioUrl;
        }
        state.settingsModal.localTtsPreviewSignature = previewSignature;
        audio.pause();
        audio.currentTime = 0;
        await audio.play();
      } catch (error) {
        state.settingsModal.videoModelError = error?.message || String(error);
      } finally {
        state.settingsModal.localTtsPreviewLoading = false;
        renderSettingsModal();
      }
    }

    function normalizeBackgroundAudioMode(value) {
      const mode = String(value || '').trim();
      return ['original', 'muted', 'tts'].includes(mode) ? mode : 'original';
    }

    function currentBackgroundAudioMode() {
      if (state.localTts?.enabled) return 'tts';
      return state.backgroundMusic?.preserveOriginalAudio === false ? 'muted' : 'original';
    }

    function backgroundAudioModeSettings(mode) {
      const normalized = normalizeBackgroundAudioMode(mode);
      return {
        mode: normalized,
        preserveOriginalAudio: normalized === 'original',
        localTtsEnabled: normalized === 'tts',
      };
    }

    async function updateBackgroundAudioMode(mode) {
      const settings = backgroundAudioModeSettings(mode);
      const previousPreserveOriginalAudio = state.backgroundMusic?.preserveOriginalAudio !== false;
      const previousLocalTtsEnabled = !!state.localTts?.enabled;
      state.backgroundMusic = {
        ...(state.backgroundMusic || {}),
        preserveOriginalAudio: settings.preserveOriginalAudio,
        error: '',
      };
      state.localTts = {
        ...(state.localTts || {}),
        enabled: settings.localTtsEnabled,
      };
      renderBackgroundMusicDrawer();
      try {
        const originalAudioRes = await fetch('/api/background-music/original-audio', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preserveOriginalAudio: settings.preserveOriginalAudio }),
        });
        const originalAudioData = await originalAudioRes.json().catch(() => ({}));
        if (!originalAudioRes.ok || !originalAudioData.ok) {
          throw new Error(originalAudioData.error || '视频原声设置保存失败');
        }
        state.backgroundMusic = {
          ...originalAudioData,
          uploading: false,
          selecting: false,
          error: '',
        };
        const ttsRes = await fetch('/api/local-tts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: settings.localTtsEnabled }),
        });
        const ttsData = await ttsRes.json().catch(() => ({}));
        if (!ttsRes.ok || ttsData?.ok === false) {
          throw new Error(ttsData?.error || 'TTS设置保存失败');
        }
        state.localTts = ttsData;
        await refreshAuthSettings();
      } catch (error) {
        state.backgroundMusic = {
          ...(state.backgroundMusic || {}),
          preserveOriginalAudio: previousPreserveOriginalAudio,
          error: error?.message || String(error),
        };
        state.localTts = {
          ...(state.localTts || {}),
          enabled: previousLocalTtsEnabled,
          error: error?.message || String(error),
        };
      } finally {
        renderBackgroundMusicDrawer();
        renderSettingsModal();
      }
    }

    document.addEventListener('focusin', (event) => {
      const target = event.target;
      if (!(target instanceof HTMLSelectElement) || target.name !== 'catalogModel') return;
      updateVideoModelRowStatus('选择后自动保存', 'info');
    });

    document.addEventListener('submit', async (event) => {
      if (event.target?.id === 'videoModelParamsForm') {
        event.preventDefault();
        await saveVideoModelParams(event.target);
        return;
      }
    });

    function render() {
      renderProgress();
      renderUserMaterials();
      renderRecycleBin();
      renderAssistantToolsPanel();
      renderBackgroundMusicButton();
      renderBackgroundMusicDrawer();
      renderDefaultReferenceButton();
      renderDefaultReferenceDrawer();
      renderScriptReferenceButton();
      renderScriptReferenceDrawer();
      renderFlowerTextButton();
      renderFlowerTextDrawer();
      renderGenerationModeButton();
      renderGenerationModeDrawer();
      renderSmartSplitButton();
      renderSmartSplitDrawer();
      renderHtmlMotionOverlayButton();
      renderHtmlMotionOverlayDrawer();
      renderMessages();
      renderStatus();
      renderAssets();
      renderProgressModal();
      renderResultModal();
      renderSettingsModal();
      renderMaterialLibraryModal();
      renderRecycleBinModal();


      renderSupervisorConfigModal();
      ensurePendingPolls();
      ensureCollectingSyncs();
    }

    function renderBackgroundMusicButton() {
      const button = els.backgroundMusicButton;
      if (!button) return;
      const music = state.backgroundMusic || {};
      const enabled = !!music.enabled;
      const uploading = !!music.uploading;
      const selecting = !!music.selecting;
      button.classList.toggle('is-ready', enabled && !uploading);
      button.classList.toggle('is-uploading', uploading);
      button.classList.toggle('is-open', !!state.backgroundMusicDrawer?.visible);
      button.disabled = uploading || selecting;
      button.textContent = uploading ? '上传中' : '背景音乐';
      button.setAttribute('aria-expanded', state.backgroundMusicDrawer?.visible ? 'true' : 'false');
      if (uploading) {
        button.title = '正在上传背景音乐';
      } else if (selecting) {
        button.title = '正在切换背景音乐';
      } else if (enabled) {
        button.title = `当前背景音乐：${music.name || 'current.mp3'}。点击展开列表，可添加或切换。`;
      } else if (music.error) {
        button.title = `背景音乐上传失败：${music.error}`;
      } else {
        button.title = '点击展开背景音乐列表，可上传 MP3 或视频。';
      }
    }

    function renderBackgroundMusicDrawer() {
      if (!els.backgroundMusicDrawer || !els.backgroundMusicDrawerBody) return;
      const visible = !!state.backgroundMusicDrawer?.visible;
      els.backgroundMusicDrawer.classList.toggle('open', visible);
      els.backgroundMusicDrawer.setAttribute('aria-hidden', visible ? 'false' : 'true');
      els.backgroundMusicButton?.classList.toggle('is-open', visible);
      els.backgroundMusicButton?.setAttribute('aria-expanded', visible ? 'true' : 'false');
      if (!visible) return;
      const music = state.backgroundMusic || {};
      const items = Array.isArray(music.items) ? music.items : [];
      const selectedId = String(music.selectedId || music.id || '');
      const loading = !!state.backgroundMusicDrawer.loading;
      const uploading = !!music.uploading;
      const selecting = !!music.selecting;
      const error = String(music.error || '').trim();
      const audioMode = currentBackgroundAudioMode();
      const statusText = uploading ? '正在添加音乐...' : selecting ? '正在切换...' : error ? `提示：${error}` : '支持 MP3 或视频，视频自动提取音频';
      let listMarkup = '';
      if (loading) {
        listMarkup = '<div class="empty">正在读取背景音乐...</div>';
      } else if (!items.length) {
        listMarkup = '<div class="empty">还没有背景音乐。添加 MP3，或选择视频自动提取音乐。</div>';
      } else {
        listMarkup = `
          <div class="background-music-list">
            ${items.map((item) => buildBackgroundMusicItemMarkup(item, selectedId, music)).join('')}
          </div>
        `;
      }
      els.backgroundMusicDrawerBody.innerHTML = `
        <div class="background-music-head">
          <div class="background-music-status">${escapeHtml(statusText)}</div>
          <div class="background-music-actions">
            <div class="background-audio-mode" role="group" aria-label="视频音轨模式">
              <button type="button" class="background-audio-mode-button${audioMode === 'original' ? ' active' : ''}" data-background-audio-mode="original" aria-pressed="${audioMode === 'original' ? 'true' : 'false'}" ${selecting ? 'disabled' : ''}>视频原声</button>
              <button type="button" class="background-audio-mode-button${audioMode === 'muted' ? ' active' : ''}" data-background-audio-mode="muted" aria-pressed="${audioMode === 'muted' ? 'true' : 'false'}" ${selecting ? 'disabled' : ''}>视频无声</button>
              <button type="button" class="background-audio-mode-button${audioMode === 'tts' ? ' active' : ''}" data-background-audio-mode="tts" aria-pressed="${audioMode === 'tts' ? 'true' : 'false'}" ${selecting ? 'disabled' : ''}>TTS 配音</button>
            </div>
            <button type="button" class="background-music-add-button" data-add-background-music ${uploading ? 'disabled' : ''}>添加音乐</button>
            <button type="button" class="background-music-add-button background-music-folder-button" data-open-background-music-folder>打开文件夹</button>
          </div>
        </div>
        ${listMarkup}
      `;
    }

    function buildBackgroundMusicItemMarkup(item, selectedId, music = {}) {
      const id = String(item?.id || '');
      const selected = !!item?.selected || (!!selectedId && id === selectedId);
      const name = String(item?.name || item?.sourceName || '背景音乐');
      const volumePercent = normalizeBackgroundMusicVolumePercent(music.volumePercent ?? ((music.volume ?? 0.28) * 100));
      return `
        <button type="button" class="material-option background-music-option${selected ? ' selected' : ''}" data-select-background-music="${escapeHtml(id)}" data-background-music-selected="${selected ? '1' : '0'}">
          <span class="material-option-thumb" aria-hidden="true">🎵</span>
          <span>
            <span class="material-title-row">
              <span class="material-title">${escapeHtml(name)}</span>
              ${selected ? '<span class="material-selected-badge">已选择</span>' : ''}
              ${selected ? `
                <span class="background-music-volume" data-background-music-volume-control>
                  <span data-background-music-volume-label>音量 ${volumePercent}%</span>
                  <input type="range" min="0" max="100" step="1" value="${volumePercent}" data-background-music-volume>
                </span>
              ` : ''}
            </span>
          </span>
        </button>
      `;
    }

    function normalizeBackgroundMusicVolumePercent(value) {
      const number = Number.parseInt(String(value ?? ''), 10);
      if (!Number.isFinite(number)) return 28;
      return Math.min(100, Math.max(0, number));
    }

    function normalizeLocalTtsVolumePercent(value) {
      const number = Number.parseInt(String(value ?? ''), 10);
      if (!Number.isFinite(number)) return 100;
      return Math.min(400, Math.max(0, number));
    }

    function renderDefaultReferenceButton() {
      const button = els.defaultReferenceButton;
      if (!button) return;
      const ref = state.defaultReferenceImage || {};
      const item = ref.item || {};
      const enabled = !!ref.enabled && !!item;
      const selecting = !!ref.selecting;
      button.classList.toggle('is-ready', enabled);
      button.classList.toggle('is-open', !!state.defaultReferenceDrawer?.visible);
      button.disabled = selecting;
      button.textContent = '参考图';
      button.setAttribute('aria-expanded', state.defaultReferenceDrawer?.visible ? 'true' : 'false');
      if (selecting) {
        button.title = '正在切换默认参考图';
      } else if (enabled) {
        button.title = `默认参考图：${item.name || item.relativePath || '图片素材'}。点击展开列表，可切换或取消。`;
      } else if (ref.error) {
        button.title = `参考图设置失败：${ref.error}`;
      } else {
        button.title = '当前未选择，生成时默认不用参考图。点击可从图片素材库选择。';
      }
    }

    function renderDefaultReferenceDrawer() {
      if (!els.defaultReferenceDrawer || !els.defaultReferenceDrawerBody) return;
      const visible = !!state.defaultReferenceDrawer?.visible;
      els.defaultReferenceDrawer.classList.toggle('open', visible);
      els.defaultReferenceDrawer.setAttribute('aria-hidden', visible ? 'false' : 'true');
      els.defaultReferenceButton?.classList.toggle('is-open', visible);
      els.defaultReferenceButton?.setAttribute('aria-expanded', visible ? 'true' : 'false');
      if (!visible) return;
      if (state.defaultReferenceDrawer.customPromptComposing || isDefaultReferenceCustomPromptFocused()) return;
      const ref = state.defaultReferenceImage || {};
      const images = Array.isArray(state.userMaterials?.images) ? state.userMaterials.images : [];
      const selectedPath = String(ref.item?.relativePath || '');
      const loading = !!state.defaultReferenceDrawer.loading;
      const selecting = !!ref.selecting;
      const error = String(ref.error || '').trim();
      const statusText = selecting ? '正在切换参考图...' : error ? `提示：${error}` : '未选择时默认不用参考图；选择后自动用于生成';
      const effectDefinitions = normalizeDefaultReferenceEffects(ref.effectDefinitions);
      const options = normalizeDefaultReferenceOptions(ref.options, effectDefinitions);
      let listMarkup = '';
      if (loading) {
        listMarkup = '<div class="empty">正在读取图片素材...</div>';
      } else if (!images.length) {
        listMarkup = '<div class="empty">还没有图片素材。先添加图片素材。</div>';
      } else {
        listMarkup = `
          <div class="background-music-list">
            ${images.map((item) => buildDefaultReferenceItemMarkup(item, selectedPath)).join('')}
          </div>
        `;
      }
      els.defaultReferenceDrawerBody.innerHTML = `
        <div class="default-reference-layout">
          <div class="default-reference-panel">
            <div class="background-music-head">
              <div class="background-music-status">${escapeHtml(statusText)}</div>
              <div class="background-music-actions">
                <button type="button" class="background-music-add-button" data-add-default-reference-image>添加图片</button>
              </div>
            </div>
            ${listMarkup}
          </div>
          <div class="default-reference-panel default-reference-settings">
            ${buildDefaultReferenceOptionsMarkup(options, effectDefinitions)}
          </div>
        </div>
      `;
    }

    function buildDefaultReferenceItemMarkup(item, selectedPath) {
      const relativePath = String(item?.relativePath || item?.name || '');
      const selected = !!selectedPath && selectedPath === relativePath;
      const name = String(item?.name || relativePath || '参考图');
      return `
        <button type="button" class="material-option background-music-option${selected ? ' selected' : ''}" data-select-default-reference="${escapeHtml(relativePath)}" data-default-reference-selected="${selected ? '1' : '0'}">
          ${item.url ? `<img class="material-option-thumb" src="${escapeHtml(item.url)}" alt="">` : '<span class="material-option-thumb">图</span>'}
          <span>
            <span class="material-title-row">
              <span class="material-title">${escapeHtml(name)}</span>
              ${selected ? '<span class="material-selected-badge">已选择</span>' : ''}
            </span>
          </span>
        </button>
      `;
    }

    function normalizeDefaultReferenceEffects(effects) {
      return (Array.isArray(effects) ? effects : [])
        .map((item) => ({
          key: String(item?.key || '').trim(),
          label: String(item?.label || item?.key || '').trim(),
        }))
        .filter((item) => item.key && item.label);
    }

    function normalizeDefaultReferenceOptions(options, effects = []) {
      const source = options || {};
      return normalizeDefaultReferenceEffects(effects).reduce((bucket, effect) => {
        bucket[effect.key] = !!source[effect.key];
        return bucket;
      }, {});
    }

    function normalizeDefaultReferenceCustomPromptDraft(value) {
      return String(value || '').replace(/\r\n/g, '\n');
    }

    function normalizeDefaultReferenceCustomPrompt(value) {
      return normalizeDefaultReferenceCustomPromptDraft(value).trim();
    }

    function syncDefaultReferenceCustomPromptDraft(value) {
      const customPrompt = normalizeDefaultReferenceCustomPromptDraft(value);
      state.defaultReferenceImage = {
        ...(state.defaultReferenceImage || {}),
        customPrompt,
        error: '',
      };
      return customPrompt;
    }
