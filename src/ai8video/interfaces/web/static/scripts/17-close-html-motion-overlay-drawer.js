    function closeHtmlMotionOverlayDrawer() {
      if (!state.htmlMotionOverlayDrawer.visible) {
        renderHtmlMotionOverlayDrawer();
        return;
      }
      state.htmlMotionOverlayDrawer.visible = false;
      state.htmlMotionOverlayDrawer.loading = false;
      renderHtmlMotionOverlayButton();
      renderHtmlMotionOverlayDrawer();
    }

    function clearSystemPromptAutoSaveTimer() {
      if (state.systemPromptModal.autoSaveTimer) {
        clearTimeout(state.systemPromptModal.autoSaveTimer);
        state.systemPromptModal.autoSaveTimer = null;
      }
    }

    function scheduleSystemPromptAutoSave(value) {
      state.systemPromptModal.draft = String(value ?? '').trim();
      state.systemPromptModal.error = '';
      state.systemPromptModal.notice = '输入后自动保存';
      setSystemPromptSaveStatus('输入后自动保存');
      clearSystemPromptAutoSaveTimer();
      state.systemPromptModal.autoSaveTimer = setTimeout(() => {
        state.systemPromptModal.autoSaveTimer = null;
        saveSystemPromptContent(state.systemPromptModal.draft);
      }, 800);
    }

    function setSystemPromptSaveStatus(text) {
      const status = document.getElementById('systemPromptSaveStatus');
      if (status) {
        status.textContent = text || '';
      }
    }

    async function saveSystemPromptContent(value) {
      if (value == null) {
        setSystemPromptSaveStatus('未找到编辑内容，已取消保存');
        return;
      }
      const content = String(value).trim();
      const savedContent = String(state.systemPromptModal.payload?.content || '').trim();
      state.systemPromptModal.draft = content;
      if (content === savedContent && !state.systemPromptModal.error) {
        state.systemPromptModal.notice = content ? '已自动保存' : '';
        setSystemPromptSaveStatus(state.systemPromptModal.notice);
        return;
      }
      const saveSeq = state.systemPromptModal.autoSaveSeq + 1;
      state.systemPromptModal.autoSaveSeq = saveSeq;
      state.systemPromptModal.saving = true;
      state.systemPromptModal.error = '';
      state.systemPromptModal.notice = '保存中...';
      setSystemPromptSaveStatus('保存中...');
      try {
        const res = await fetch('/api/system-prompt', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok || payload.error) {
          throw new Error(payload.error || '系统提示词保存失败');
        }
        if (saveSeq !== state.systemPromptModal.autoSaveSeq) return;
        state.systemPromptModal.payload = payload;
        state.systemPromptModal.draft = String(payload.content || '');
        state.systemPromptModal.notice = content ? '已自动保存' : '已清空';
        setSystemPromptSaveStatus(state.systemPromptModal.notice);
      } catch (error) {
        if (saveSeq !== state.systemPromptModal.autoSaveSeq) return;
        state.systemPromptModal.error = error?.message || String(error);
        setSystemPromptSaveStatus(`保存失败：${state.systemPromptModal.error}`);
      } finally {
        if (saveSeq === state.systemPromptModal.autoSaveSeq) {
          state.systemPromptModal.saving = false;
        }
      }
    }

    function renderSystemPromptModal() {
      if (!els.systemPromptDrawer) return;
      const visible = !!state.systemPromptModal.visible;
      els.systemPromptModal?.classList.add('hidden');
      els.systemPromptDrawer.classList.toggle('open', visible);
      els.systemPromptDrawer.setAttribute('aria-hidden', visible ? 'false' : 'true');
      els.systemPromptButton?.classList.toggle('is-open', visible);
      els.systemPromptButton?.setAttribute('aria-expanded', visible ? 'true' : 'false');
      if (!visible) return;
      const payload = state.systemPromptModal.payload || {};
      if (state.systemPromptModal.loading) {
        els.systemPromptDrawerBody.innerHTML = '<div class="empty">正在读取系统提示词...</div>';
        return;
      }
      if (state.systemPromptModal.error) {
        els.systemPromptDrawerBody.innerHTML = `<div class="empty">提示：${escapeHtml(state.systemPromptModal.error)}</div>`;
        if (!payload.content) return;
      }
      const content = state.systemPromptModal.draft != null
        ? String(state.systemPromptModal.draft || '')
        : String(payload.content || '');
      const saving = !!state.systemPromptModal.saving;
      const notice = state.systemPromptModal.notice ? escapeHtml(state.systemPromptModal.notice) : '';
      const error = state.systemPromptModal.error ? escapeHtml(state.systemPromptModal.error) : '';
      els.systemPromptDrawerBody.innerHTML = `
        <textarea id="systemPromptEditor" class="system-prompt-editor" spellcheck="false" placeholder="在这里输入要附带给剧本规划和视频模型的提示词。留空则不附带额外提示词。">${escapeHtml(content)}</textarea>
        <div class="system-prompt-drawer-actions">
          <div id="systemPromptSaveStatus" class="system-prompt-save-status">${notice || error || '输入后自动保存'}</div>
        </div>
      `;
    }

    function findSettingField(envName) {
      const fields = Array.isArray(state.authSettings?.fields) ? state.authSettings.fields : [];
      return fields.find((field) => String(field.envName || '') === envName) || null;
    }

    function updateVideoModelRowStatus(text, tone = 'info') {
      const status = document.getElementById('videoModelRowStatus');
      if (!status) return;
      const message = String(text || '');
      status.textContent = message;
      status.dataset.tone = tone;
      status.hidden = !message;
    }

    function clearVideoModelRowNoticeTimer() {
      if (state.settingsModal.videoModelRowNoticeTimer) {
        clearTimeout(state.settingsModal.videoModelRowNoticeTimer);
        state.settingsModal.videoModelRowNoticeTimer = null;
      }
    }

    function scheduleVideoModelRowNoticeClear() {
      clearVideoModelRowNoticeTimer();
      state.settingsModal.videoModelRowNoticeTimer = setTimeout(() => {
        state.settingsModal.videoModelRowNoticeTimer = null;
        if (state.settingsModal.videoModelRowError) return;
        state.settingsModal.videoModelRowNotice = '';
        renderSettingsModal();
      }, 2200);
    }

    async function saveVideoModelSelection(value, noticeText = '模型已保存') {
      const model = String(value || '').trim();
      if (!model) return;
      state.videoModelSettings = {
        ...(state.videoModelSettings || {}),
        model,
      };
      state.settingsModal.savingVideoModelField = 'model';
      state.settingsModal.videoModelRowError = '';
      state.settingsModal.videoModelRowNotice = '保存中...';
      state.settingsModal.videoModelError = '';
      renderSettingsModal();
      try {
        await saveVideoModelPayload(currentVideoModelPayload({ model }), {
          renderAfter: false,
          throwOnError: true,
          noticeText,
        });
        state.settingsModal.videoModelRowError = '';
        state.settingsModal.videoModelRowNotice = noticeText;
        state.settingsModal.videoModelError = '';
        renderSettingsModal();
        scheduleVideoModelRowNoticeClear();
      } catch (error) {
        state.settingsModal.videoModelRowNotice = '';
        state.settingsModal.videoModelRowError = error?.message || String(error);
        renderSettingsModal();
      } finally {
        state.settingsModal.savingVideoModelField = '';
        renderSettingsModal();
      }
    }

    async function pullVideoModelCatalog() {
      state.settingsModal.pullingVideoModels = true;
      state.settingsModal.videoModelError = '';
      state.settingsModal.videoModelNotice = '正在拉取模型列表...';
      renderSettingsModal();
      try {
        const res = await fetch('/api/video-model-settings/models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
          throw new Error(data.error || '模型列表拉取失败');
        }
        state.settingsModal.videoModelCatalog = Array.isArray(data.models) ? data.models : [];
        state.settingsModal.videoModelAttempts = Array.isArray(data.attempts) ? data.attempts : [];
        state.settingsModal.videoModelNotice = state.settingsModal.videoModelCatalog.length
          ? `已拉取 ${state.settingsModal.videoModelCatalog.length} 个模型`
          : '没有拉到可用模型，可以手动输入模型名';
      } catch (error) {
        state.settingsModal.videoModelAttempts = [];
        state.settingsModal.videoModelError = error?.message || String(error);
        state.settingsModal.videoModelNotice = '';
      } finally {
        state.settingsModal.pullingVideoModels = false;
        renderSettingsModal();
      }
    }

    async function pullAuthModelCatalog(envName) {
      const key = String(envName || '').trim();
      if (!key) return;
      state.settingsModal.pullingAuthModelEnvName = key;
      state.settingsModal.videoModelError = '';
      state.settingsModal.videoModelNotice = '正在拉取模型列表...';
      renderSettingsModal();
      try {
        const res = await fetch('/api/auth-settings/models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ envName: key }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
          throw new Error(data.error || '模型列表拉取失败');
        }
        state.settingsModal.authModelCatalogs = {
          ...(state.settingsModal.authModelCatalogs || {}),
          [key]: Array.isArray(data.models) ? data.models : [],
        };
        state.settingsModal.videoModelNotice = state.settingsModal.authModelCatalogs[key].length
          ? `已拉取 ${state.settingsModal.authModelCatalogs[key].length} 个模型`
          : '没有拉到可用模型';
      } catch (error) {
        state.settingsModal.videoModelError = error?.message || String(error);
        state.settingsModal.videoModelNotice = '';
      } finally {
        state.settingsModal.pullingAuthModelEnvName = '';
        renderSettingsModal();
      }
    }

    async function saveAuthModelSelection(envName, model) {
      state.settingsModal.savingVideoModel = true;
      state.settingsModal.videoModelError = '';
      state.settingsModal.videoModelNotice = '';
      renderSettingsModal();
      renderVideoParamsModal();
      try {
        const res = await fetch('/api/auth-settings/model-selection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ envName, model }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
          throw new Error(data.error || '保存失败');
        }
        state.settingsModal.videoModelNotice = '模型已保存';
        await refreshAuthSettings();
        await refreshHealth();
        renderSettingsModal();
      } catch (error) {
        state.settingsModal.videoModelError = error?.message || String(error);
      } finally {
        state.settingsModal.savingVideoModel = false;
        render();
        renderVideoParamsModal();
      }
    }

    function currentVideoModelPayload(overrides = {}) {
      const settings = state.videoModelSettings || {};
      return {
        model: String(settings.model || 'doubao-seedance-1-5-pro-251215').trim(),
        template: String(settings.template || 'doubao-seedance').trim(),
        seconds: Number(settings.seconds || 10),
        resolution: String(settings.resolution || '480p'),
        resolutionMode: currentResolutionMode(settings),
        ratio: String(settings.ratio || '9:16'),
        preset: String(settings.preset || 'custom'),
        enhancePrompt: settings.enhance_prompt !== false,
        returnLastFrame: settings.return_last_frame !== false,
        watermark: !!settings.watermark,
        videoCount: Number(settings.video_count || 1),
        generateAudio: !!settings.generate_audio,
        serviceTier: String(settings.service_tier || 'default'),
        executionExpiresAfter: Number(settings.execution_expires_after || 172800),
        draft: !!settings.draft,
        cameraFixed: !!settings.camera_fixed,
        seed: settings.seed === null || settings.seed === undefined ? '' : settings.seed,
        promptExtend: settings.prompt_extend !== false,
        shotType: String(settings.shot_type || 'multi'),
        audio: !!settings.audio,
        audioUrl: String(settings.audio_url || ''),
        ...overrides,
      };
    }

    function isVideoParamsControl(target) {
      return target instanceof HTMLElement
        && !!target.closest('#videoModelParamsForm')
        && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName);
    }

    function clearVideoParamsAutoSaveTimer() {
      if (state.videoParamsModal.autoSaveTimer) {
        clearTimeout(state.videoParamsModal.autoSaveTimer);
        state.videoParamsModal.autoSaveTimer = null;
      }
    }

    function clearVideoParamsSaveStatusTimer() {
      if (state.videoParamsModal.saveStatusTimer) {
        clearTimeout(state.videoParamsModal.saveStatusTimer);
        state.videoParamsModal.saveStatusTimer = null;
      }
    }

    function setVideoParamsSaveStatus(text, tone = 'info') {
      const status = document.getElementById('videoParamsSaveStatus');
      if (!status) return;
      clearVideoParamsSaveStatusTimer();
      status.textContent = text;
      status.dataset.tone = tone;
      status.classList.remove('fading');
      status.classList.toggle('show', !!text);
      if (tone !== 'ok') return;
      state.videoParamsModal.saveStatusTimer = setTimeout(() => {
        status.classList.add('fading');
        state.videoParamsModal.saveStatusTimer = setTimeout(() => {
          status.classList.remove('show', 'fading');
          status.textContent = '';
          state.videoParamsModal.saveStatusTimer = null;
        }, 950);
      }, 1800);
    }

    function scheduleVideoParamsAutoSave() {
      clearVideoParamsAutoSaveTimer();
      setVideoParamsSaveStatus('正在输入，停笔后自动保存。');
      state.videoParamsModal.autoSaveTimer = setTimeout(() => {
        state.videoParamsModal.autoSaveTimer = null;
        autoSaveVideoParamsFromCurrentForm().catch((error) => {
          setVideoParamsSaveStatus(error?.message || String(error), 'error');
        });
      }, 650);
    }

    async function autoSaveVideoParamsFromCurrentForm() {
      const form = document.getElementById('videoModelParamsForm');
      if (!form) return;
      const seq = ++state.videoParamsModal.autoSaveSeq;
      setVideoParamsSaveStatus('保存中...');
      await saveVideoModelParams(form, { renderAfter: false });
      if (seq === state.videoParamsModal.autoSaveSeq) {
        setVideoParamsSaveStatus('已自动保存。', 'ok');
      }
    }

    async function saveVideoModelParams(form, options = {}) {
      const formData = new FormData(form);
      const hasField = (name) => !!form.elements.namedItem(name);
      const patch = {
        seconds: Number(formData.get('seconds') || 10),
        videoCount: Number(formData.get('videoCount') || 1),
        resolution: String(formData.get('resolution') || state.videoModelSettings?.resolution || '480p'),
        resolutionMode: String(formData.get('resolutionMode') || currentResolutionMode(state.videoModelSettings)),
        ratio: String(formData.get('ratio') || state.videoModelSettings?.ratio || '9:16'),
        watermark: false,
      };
      if (hasField('preset')) patch.preset = String(formData.get('preset') || 'custom');
      if (hasField('seed')) patch.seed = String(formData.get('seed') || '').trim();
      if (hasField('enhancePrompt')) patch.enhancePrompt = formData.has('enhancePrompt');
      if (hasField('returnLastFrame')) patch.returnLastFrame = formData.has('returnLastFrame');
      if (hasField('generateAudio')) patch.generateAudio = formData.has('generateAudio');
      if (hasField('serviceTier')) patch.serviceTier = String(formData.get('serviceTier') || 'default');
      if (hasField('executionExpiresAfter')) patch.executionExpiresAfter = Number(formData.get('executionExpiresAfter') || 172800);
      if (hasField('draft')) patch.draft = formData.has('draft');
      if (hasField('cameraFixed')) patch.cameraFixed = formData.has('cameraFixed');
      if (hasField('promptExtend')) patch.promptExtend = formData.has('promptExtend');
      if (hasField('shotType')) patch.shotType = String(formData.get('shotType') || 'multi');
      if (hasField('audio')) patch.audio = formData.has('audio');
      if (hasField('audioUrl')) patch.audioUrl = String(formData.get('audioUrl') || '').trim();
      const payload = currentVideoModelPayload(patch);
      await saveVideoModelPayload(payload, options);
    }

    async function saveVideoModelPayload(payload, options = {}) {
      const renderAfter = options.renderAfter !== false;
      state.settingsModal.savingVideoModel = true;
      state.settingsModal.videoModelError = '';
      state.settingsModal.videoModelNotice = '';
      if (renderAfter) {
        renderSettingsModal();
      }
      try {
        const res = await fetch('/api/video-model-settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data?.error || '保存失败');
        }
        state.videoModelSettings = data?.settings || {};
        if (Array.isArray(data?.modelCatalog)) {
          state.settingsModal.videoModelCatalog = data.modelCatalog;
        }
        await refreshAuthSettings();
        state.settingsModal.videoModelNotice = options.noticeText || '已保存';
        await refreshHealth();
        return data;
      } catch (error) {
        state.settingsModal.videoModelError = error.message || String(error);
        if (!renderAfter) {
          setVideoParamsSaveStatus(state.settingsModal.videoModelError, 'error');
        }
        if (options.throwOnError) {
          throw error;
        }
        return null;
      } finally {
        state.settingsModal.savingVideoModel = false;
        if (renderAfter) {
          render();
        }
      }
    }

    async function handleGuideAction(kind, value) {
      if (state.busy || isRealGenerationUnavailable()) return;
      const actionKind = String(kind || '').trim();
      const text = String(value || '').trim();
      if (!text) return;
      if (actionKind === 'send') {
        setComposerDraft(text, { submit: true });
        return;
      }
      setComposerDraft(text, { submit: false });
    }

    function renderUserMaterials() {
      const materials = state.userMaterials || {};
      renderMaterialLibrary(
        els.imageMaterialList,
        'image',
        materials.images || [],
        '图片素材库',
        '把参考图、角色图、老板照片放到这里，上方参考图按钮可选择。'
      );
      renderMaterialLibrary(
        els.scriptMaterialList,
        'script',
        materials.scripts || [],
        '剧本知识库',
        'PostgreSQL 管理剧本、知识段、标签和全文检索。'
      );
    }

    function renderRecycleBin() {
      if (!els.recycleBinList) return;
      const bin = state.recycleBin || {};
      const count = Number(bin.count || 0) || 0;
      els.recycleBinList.innerHTML = `
        <div class="material-card">
          <div class="material-heading">
            <div class="material-title">回收站</div>
            <div class="material-meta">${escapeHtml(`${count} 个失败任务`)}</div>
          </div>
          <div class="material-actions">
            <button type="button" class="material-library-button" data-show-recycle-bin>查看回收站</button>
          </div>
        </div>
      `;
    }

    function renderAssistantToolsPanel() {
      if (!els.assistantToolsList) return;
      els.assistantToolsList.innerHTML = `
        <div class="material-card">
          <div class="material-heading stacked">
            <div class="material-title">热点雷达</div>
            <div class="material-meta">聚合公开热点数据并生成选题摘要</div>
          </div>
          <div class="material-actions">
            <button type="button" class="material-library-button" data-open-hot-radar-entry>查看热点</button>
          </div>
        </div>
        <div class="material-card">
          <div class="material-heading stacked">
            <div class="material-title">爆款拆解</div>
            <div class="material-meta">一键预填拆解提示词，直接进入对话分析</div>
          </div>
          <div class="material-actions">
            <button type="button" class="material-library-button" data-open-viral-breakdown-entry>开始拆解</button>
          </div>
        </div>
      `;
    }
