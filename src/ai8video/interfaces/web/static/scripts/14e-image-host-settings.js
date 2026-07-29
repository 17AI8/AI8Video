    function withImageHostSettingsGroup(groups) {
      const output = Array.isArray(groups) ? [...groups] : [];
      if (!output.some((item) => item.label === '图床')) {
        output.push({ label: '图床', fields: [] });
      }
      return output.sort((left, right) => {
        const leftIndex = settingsCategoryOrder.indexOf(left.label);
        const rightIndex = settingsCategoryOrder.indexOf(right.label);
        return (leftIndex < 0 ? 999 : leftIndex) - (rightIndex < 0 ? 999 : rightIndex);
      });
    }

    async function refreshImageHostSettings() {
      const res = await fetch('/api/image-host-settings');
      const data = await res.json().catch(() => ({}));
      state.imageHostSettings = data?.settings || { selectedProviderId: '', providers: [] };
    }

    function buildImageHostSettingsMarkup(labelledBy) {
      const settings = state.imageHostSettings || {};
      const providers = Array.isArray(settings.providers) ? settings.providers : [];
      const selectedId = String(settings.selectedProviderId || '');
      return `
        <section id="settings-category-panel" class="settings-panel image-host-settings-panel" role="tabpanel" aria-labelledby="${labelledBy}">
          <header class="settings-panel-head image-host-settings-head">
            <div>
              <h3 class="settings-section-title">图床降级</h3>
              <p>仅当视频模型拒绝本地 base64 参考图时，才上传到当前图床并自动重试；不会降级为文生视频。</p>
            </div>
            <span class="image-host-settings-status">${escapeHtml(state.settingsModal.imageHostNotice || state.settingsModal.imageHostError || '')}</span>
          </header>
          <div class="image-host-privacy-warning">
            <strong>隐私风险：</strong>MJJ.TODAY 是第三方公共图床。选择后，参考图会离开本机并受对方服务条款、保存策略和可用性影响；敏感、人像或商业保密图片请使用自建图床。
          </div>
          <div class="image-host-provider-list">
            <label class="image-host-provider-option${selectedId ? '' : ' is-selected'}">
              <input type="radio" name="imageHostProvider" value="" ${selectedId ? '' : 'checked'}>
              <span><strong>不使用图床</strong><small>模型不接受 base64 时直接显示错误，不发送图片给第三方。</small></span>
            </label>
            ${providers.map((provider) => buildImageHostProviderMarkup(provider, selectedId)).join('')}
          </div>
          <form id="imageHostCustomForm" class="image-host-custom-form">
            <h4>新增自定义图床</h4>
            <div class="image-host-custom-grid">
              <input name="name" placeholder="名称，例如：公司 OSS" required>
              <input name="uploadUrl" type="url" placeholder="上传接口 https://..." required>
              <input name="fileField" placeholder="文件字段，默认 file" value="file">
              <input name="responseUrlPath" placeholder="返回 URL 路径，默认 data.url" value="data.url">
              <input name="authHeader" placeholder="鉴权 Header，可留空">
              <input name="authToken" type="password" placeholder="鉴权值，可留空">
            </div>
            <button type="submit" class="button-secondary">添加图床</button>
          </form>
        </section>
      `;
    }

    function buildImageHostProviderMarkup(provider, selectedId) {
      const selected = provider.id === selectedId;
      const warning = provider.privacyRisk ? '<em>公共图床 · 存在隐私风险</em>' : '';
      const deleteButton = provider.builtIn
        ? ''
        : `<button type="button" class="image-host-delete" data-delete-image-host="${escapeHtml(provider.id)}">删除</button>`;
      return `
        <label class="image-host-provider-option${selected ? ' is-selected' : ''}">
          <input type="radio" name="imageHostProvider" value="${escapeHtml(provider.id)}" ${selected ? 'checked' : ''}>
          <span>
            <strong>${escapeHtml(provider.name)}</strong>
            <small>${escapeHtml(provider.uploadUrl)}</small>
            ${warning}
          </span>
          ${deleteButton}
        </label>
      `;
    }

    function imageHostSettingsPayload(providers, selectedProviderId) {
      return {
        selectedProviderId,
        providers: providers.filter((item) => !item.builtIn).map((item) => ({
          id: item.id,
          name: item.name,
          uploadUrl: item.uploadUrl,
          fileField: item.fileField,
          responseUrlPath: item.responseUrlPath,
          authHeader: item.authHeader,
          hasAuthToken: item.hasAuthToken,
        })),
      };
    }

    async function saveImageHostSettings(payload, notice = '图床设置已保存') {
      state.settingsModal.savingImageHost = true;
      state.settingsModal.imageHostError = '';
      state.settingsModal.imageHostNotice = '保存中...';
      renderSettingsModal();
      try {
        const res = await fetch('/api/image-host-settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw new Error(data?.error || '图床设置保存失败');
        state.imageHostSettings = data.settings || {};
        state.settingsModal.imageHostNotice = notice;
        showSettingsSavedBadge();
      } catch (error) {
        state.settingsModal.imageHostNotice = '';
        state.settingsModal.imageHostError = error?.message || String(error);
      } finally {
        state.settingsModal.savingImageHost = false;
        renderSettingsModal();
      }
    }

    async function addCustomImageHost(form) {
      const data = new FormData(form);
      const providers = Array.isArray(state.imageHostSettings?.providers)
        ? state.imageHostSettings.providers.filter((item) => !item.builtIn)
        : [];
      const provider = {
        id: `custom-${Date.now()}`,
        name: String(data.get('name') || '').trim(),
        uploadUrl: String(data.get('uploadUrl') || '').trim(),
        fileField: String(data.get('fileField') || 'file').trim(),
        responseUrlPath: String(data.get('responseUrlPath') || 'data.url').trim(),
        authHeader: String(data.get('authHeader') || '').trim(),
        authToken: String(data.get('authToken') || '').trim(),
      };
      if (!provider.name || !provider.uploadUrl) return;
      await saveImageHostSettings({
        selectedProviderId: provider.id,
        providers: [...providers, provider],
      }, '已添加并切换到自定义图床');
    }

    document.addEventListener('change', async (event) => {
      const input = event.target.closest('input[name="imageHostProvider"]');
      if (!input) return;
      const providers = Array.isArray(state.imageHostSettings?.providers) ? state.imageHostSettings.providers : [];
      await saveImageHostSettings(imageHostSettingsPayload(providers, input.value));
    });

    document.addEventListener('submit', async (event) => {
      const form = event.target.closest('#imageHostCustomForm');
      if (!form) return;
      event.preventDefault();
      await addCustomImageHost(form);
    });

    document.addEventListener('click', async (event) => {
      const trigger = event.target.closest('[data-delete-image-host]');
      if (!trigger) return;
      event.preventDefault();
      const providerId = trigger.getAttribute('data-delete-image-host') || '';
      const providers = (state.imageHostSettings?.providers || []).filter(
        (item) => !item.builtIn && item.id !== providerId
      );
      const selected = state.imageHostSettings?.selectedProviderId === providerId
        ? ''
        : state.imageHostSettings?.selectedProviderId || '';
      await saveImageHostSettings({ selectedProviderId: selected, providers }, '自定义图床已删除');
    });
