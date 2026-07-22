    async function regenerateUserGeneratedPreviews(trigger) {
      const previous = trigger?.textContent || '重新生成预览图';
      state.settingsModal.regeneratingPreviews = true;
      renderSettingsModal();
      try {
        const res = await fetch('/api/user-generated-previews/regenerate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw buildRequestError(data);
        }
        await refreshUserGeneratedResults();
        await refreshAuthSettings();
        renderProgress();
        renderProgressModal();
        renderResultModal();
        renderStatus();
      } finally {
        state.settingsModal.regeneratingPreviews = false;
        renderSettingsModal();
        if (trigger) trigger.textContent = previous;
      }
    }

    async function refreshResultModalData(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '刷新中...';
      trigger.disabled = true;
      try {
        await refreshHealth();
        await refreshAssets();
        await refreshUserGeneratedResults();
        render();
        trigger.textContent = '已刷新';
        setTimeout(() => {
          trigger.textContent = previous;
          trigger.disabled = false;
        }, 1200);
      } catch (error) {
        trigger.textContent = '刷新失败';
        setTimeout(() => {
          trigger.textContent = previous;
          trigger.disabled = false;
        }, 1600);
        throw error;
      }
    }

    function beginUserMaterialUpload(kind, options = {}) {
      const normalizedKind = kind === 'script' ? 'script' : kind === 'flower-watermark' ? 'flower-watermark' : 'image';
      els.userMaterialUploadInput.dataset.kind = normalizedKind;
      els.userMaterialUploadInput.dataset.purpose = String(options?.purpose || '');
      els.userMaterialUploadInput.accept = normalizedKind === 'script'
        ? '.txt,.md,.docx'
        : '.jpg,.jpeg,.png,.webp,.gif,.bmp,image/*';
      els.userMaterialUploadInput.click();
    }

    async function deleteUserMaterial(kind, relativePath, name, button) {
      const normalizedKind = kind === 'script' ? 'script' : 'image';
      const target = String(relativePath || '').trim();
      const materialName = String(name || target || '素材').trim();
      if (!target) return;
      if (!window.confirm(`确定删除素材“${materialName}”？删除后会立刻从素材库移除。`)) {
        return;
      }
      const previous = button?.textContent || '删除';
      if (button) {
        button.disabled = true;
        button.textContent = '删除中';
      }
      try {
        const res = await fetch('/api/delete-user-material', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind: normalizedKind, relativePath: target }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw buildRequestError(data);
        }
        const refreshes = [
          refreshUserMaterials(),
          refreshDefaultReferenceImage(),
          refreshScriptReference(),
        ];
        if (normalizedKind === 'script') {
          refreshes.push(refreshScriptKnowledge({ preserveSelection: false }));
        }
        await Promise.all(refreshes);
        renderUserMaterials();
        renderDefaultReferenceButton();
        renderDefaultReferenceDrawer();
        renderScriptReferenceButton();
        renderScriptReferenceDrawer();
        renderMaterialLibraryModal();
        renderMaterialMentionPicker();
      } catch (error) {
        window.alert(error?.message || '删除素材失败');
        if (button) {
          button.disabled = false;
          button.textContent = previous;
        }
      }
    }

    function beginBackgroundMusicUpload() {
      if (!els.backgroundMusicUploadInput) return;
      els.backgroundMusicUploadInput.click();
    }

    function beginLocalTtsVoiceCloneUpload() {
      if (!els.localTtsVoiceCloneUploadInput) return;
      els.localTtsVoiceCloneUploadInput.click();
    }

    async function selectBackgroundMusic(id) {
      const itemId = String(id || '').trim();
      if (!itemId) return;
      state.backgroundMusic = {
        ...(state.backgroundMusic || {}),
        selecting: true,
        error: '',
      };
      renderBackgroundMusicButton();
      renderBackgroundMusicDrawer();
      try {
        const res = await fetch('/api/background-music/select', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: itemId }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '背景音乐切换失败');
        }
        state.backgroundMusic = {
          ...data,
          uploading: false,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.backgroundMusic = {
          ...(state.backgroundMusic || {}),
          selecting: false,
          error: error?.message || String(error),
        };
      } finally {
        renderBackgroundMusicButton();
        renderBackgroundMusicDrawer();
      }
    }

    async function clearBackgroundMusicSelection() {
      state.backgroundMusic = {
        ...(state.backgroundMusic || {}),
        selecting: true,
        error: '',
      };
      renderBackgroundMusicButton();
      renderBackgroundMusicDrawer();
      try {
        const res = await fetch('/api/background-music/clear', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '背景音乐取消失败');
        }
        state.backgroundMusic = {
          ...data,
          uploading: false,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.backgroundMusic = {
          ...(state.backgroundMusic || {}),
          selecting: false,
          error: error?.message || String(error),
        };
      } finally {
        renderBackgroundMusicButton();
        renderBackgroundMusicDrawer();
      }
    }

    async function updateBackgroundMusicVolume(value) {
      const percent = normalizeBackgroundMusicVolumePercent(value);
      state.backgroundMusic = {
        ...(state.backgroundMusic || {}),
        volumePercent: percent,
        volume: percent / 100,
        error: '',
      };
      renderBackgroundMusicButton();
      renderBackgroundMusicDrawer();
      try {
        const res = await fetch('/api/background-music/volume', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ volume: percent }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '背景音乐音量保存失败');
        }
        state.backgroundMusic = {
          ...data,
          uploading: false,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.backgroundMusic = {
          ...(state.backgroundMusic || {}),
          error: error?.message || String(error),
        };
      } finally {
        renderBackgroundMusicButton();
        renderBackgroundMusicDrawer();
      }
    }

    async function updateBackgroundMusicOriginalAudio(checked) {
      const preserveOriginalAudio = checked !== false;
      state.backgroundMusic = {
        ...(state.backgroundMusic || {}),
        preserveOriginalAudio,
        error: '',
      };
      renderBackgroundMusicButton();
      renderBackgroundMusicDrawer();
      try {
        const res = await fetch('/api/background-music/original-audio', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preserveOriginalAudio }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '视频原声设置保存失败');
        }
        state.backgroundMusic = {
          ...data,
          uploading: false,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.backgroundMusic = {
          ...(state.backgroundMusic || {}),
          preserveOriginalAudio: !preserveOriginalAudio,
          error: error?.message || String(error),
        };
      } finally {
        renderBackgroundMusicButton();
        renderBackgroundMusicDrawer();
      }
    }

    async function selectDefaultReferenceImage(relativePath) {
      const target = String(relativePath || '').trim();
      if (!target) return;
      state.defaultReferenceImage = {
        ...(state.defaultReferenceImage || {}),
        selecting: true,
        error: '',
      };
      renderDefaultReferenceButton();
      renderDefaultReferenceDrawer();
      try {
        const res = await fetch('/api/default-reference-image', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ relativePath: target }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '参考图切换失败');
        }
        state.defaultReferenceImage = {
          ...data,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.defaultReferenceImage = {
          ...(state.defaultReferenceImage || {}),
          selecting: false,
          error: error?.message || String(error),
        };
      } finally {
        renderDefaultReferenceButton();
        renderDefaultReferenceDrawer();
      }
    }

    async function clearDefaultReferenceImage() {
      state.defaultReferenceImage = {
        ...(state.defaultReferenceImage || {}),
        selecting: true,
        error: '',
      };
      renderDefaultReferenceButton();
      renderDefaultReferenceDrawer();
      try {
        const res = await fetch('/api/default-reference-image/clear', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '参考图取消失败');
        }
        state.defaultReferenceImage = {
          ...data,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.defaultReferenceImage = {
          ...(state.defaultReferenceImage || {}),
          selecting: false,
          error: error?.message || String(error),
        };
      } finally {
        renderDefaultReferenceButton();
        renderDefaultReferenceDrawer();
      }
    }

    async function updateDefaultReferenceOptions(patch) {
      const effectDefinitions = normalizeDefaultReferenceEffects(state.defaultReferenceImage?.effectDefinitions);
      const currentOptions = normalizeDefaultReferenceOptions(state.defaultReferenceImage?.options, effectDefinitions);
      const nextOptions = normalizeDefaultReferenceOptions({ ...currentOptions, ...(patch || {}) }, effectDefinitions);
      const customPrompt = normalizeDefaultReferenceCustomPrompt(state.defaultReferenceImage?.customPrompt);
      state.defaultReferenceImage = {
        ...(state.defaultReferenceImage || {}),
        options: nextOptions,
        error: '',
      };
      renderDefaultReferenceDrawer();
      try {
        const res = await fetch('/api/default-reference-image/options', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ options: nextOptions, customPrompt }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '参考图设定保存失败');
        }
        state.defaultReferenceImage = {
          ...data,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.defaultReferenceImage = {
          ...(state.defaultReferenceImage || {}),
          options: currentOptions,
          selecting: false,
          error: error?.message || String(error),
        };
      } finally {
        renderDefaultReferenceButton();
        renderDefaultReferenceDrawer();
      }
    }

    function clearDefaultReferenceCustomPromptSaveTimer() {
      if (state.defaultReferenceDrawer.customPromptSaveTimer) {
        clearTimeout(state.defaultReferenceDrawer.customPromptSaveTimer);
        state.defaultReferenceDrawer.customPromptSaveTimer = null;
      }
    }

    function scheduleDefaultReferenceCustomPromptSave(value, { immediate = false } = {}) {
      syncDefaultReferenceCustomPromptDraft(value);
      clearDefaultReferenceCustomPromptSaveTimer();
      if (state.defaultReferenceDrawer.customPromptComposing) return;
      if (immediate) {
        void saveDefaultReferenceCustomPrompt();
        return;
      }
      state.defaultReferenceDrawer.customPromptSaveTimer = setTimeout(() => {
        state.defaultReferenceDrawer.customPromptSaveTimer = null;
        saveDefaultReferenceCustomPrompt();
      }, 700);
    }

    async function saveDefaultReferenceCustomPrompt() {
      const effectDefinitions = normalizeDefaultReferenceEffects(state.defaultReferenceImage?.effectDefinitions);
      const currentOptions = normalizeDefaultReferenceOptions(state.defaultReferenceImage?.options, effectDefinitions);
      const customPrompt = normalizeDefaultReferenceCustomPrompt(state.defaultReferenceImage?.customPrompt);
      clearDefaultReferenceCustomPromptSaveTimer();
      try {
        const res = await fetch('/api/default-reference-image/options', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ options: currentOptions, customPrompt }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '自定义提示词保存失败');
        }
        state.defaultReferenceImage = {
          ...data,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.defaultReferenceImage = {
          ...(state.defaultReferenceImage || {}),
          error: error?.message || String(error),
        };
      } finally {
        renderDefaultReferenceButton();
        renderDefaultReferenceDrawer();
      }
    }

    async function selectScriptReference(relativePath) {
      const target = String(relativePath || '').trim();
      if (!target) return;
      state.scriptReference = {
        ...(state.scriptReference || {}),
        selecting: true,
        error: '',
      };
      renderScriptReferenceButton();
      renderScriptReferenceDrawer();
      try {
        const res = await fetch('/api/default-script-reference', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ relativePath: target }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '剧本参考切换失败');
        }
        state.scriptReference = {
          ...data,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.scriptReference = {
          ...(state.scriptReference || {}),
          selecting: false,
          error: error?.message || String(error),
        };
      } finally {
        renderScriptReferenceButton();
        renderScriptReferenceDrawer();
      }
    }

    async function clearScriptReference() {
      state.scriptReference = {
        ...(state.scriptReference || {}),
        selecting: true,
        error: '',
      };
      renderScriptReferenceButton();
      renderScriptReferenceDrawer();
      try {
        const res = await fetch('/api/default-script-reference/clear', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '剧本参考取消失败');
        }
        state.scriptReference = {
          ...data,
          selecting: false,
          error: '',
        };
      } catch (error) {
        state.scriptReference = {
          ...(state.scriptReference || {}),
          selecting: false,
          error: error?.message || String(error),
        };
      } finally {
        renderScriptReferenceButton();
        renderScriptReferenceDrawer();
      }
    }

    function clearFlowerTextAutoSaveTimer() {
      if (state.flowerText?.autoSaveTimer) {
        clearTimeout(state.flowerText.autoSaveTimer);
        state.flowerText.autoSaveTimer = null;
      }
    }

