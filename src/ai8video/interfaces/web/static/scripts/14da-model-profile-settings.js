    const modelSettingsCategories = ['文本/视频规划模型', '多模态模型', '图片模型', '视频模型'];
    const modelProfileCategoryKeys = {
      '文本/视频规划模型': 'llm',
      '多模态模型': 'multimodal',
      '图片模型': 'image',
      '视频模型': 'video',
    };
    const modelSettingsCategoryDetails = {
      llm: { shortLabel: '文本规划', description: '负责对话理解、意图判断与视频内容规划。' },
      multimodal: { shortLabel: '多模态', description: '负责图片、视频帧与文本的联合理解。' },
      image: { shortLabel: '图片', description: '用于首帧生成、参考图处理与智能修图。' },
      video: { shortLabel: '视频', description: '用于视频生成、延长、尾帧续接与任务轮询。' },
    };

    function modelSettingsDetails(categoryKey) {
      return modelSettingsCategoryDetails[categoryKey] || {
        shortLabel: '模型',
        description: '管理当前模型类型的连接与启用配置。',
      };
    }

    function modelProfileReady(profile) {
      return !!(profile && String(profile.model || '').trim() && profile.hasApiKey && String(profile.baseUrl || '').trim());
    }

    function modelProfileEndpointHost(profile) {
      try {
        return profile.baseUrl ? new URL(profile.baseUrl).host : '未填接口';
      } catch (_error) {
        return profile.baseUrl || '未填接口';
      }
    }

    function resolveExpandedModelProfileId(categoryKey, bucket, profiles) {
      const expandedProfiles = state.settingsModal.expandedModelProfiles || {};
      if (Object.prototype.hasOwnProperty.call(expandedProfiles, categoryKey) && expandedProfiles[categoryKey]) {
        const preferred = String(expandedProfiles[categoryKey] || '');
        if (profiles.some((profile) => profile.id === preferred)) return preferred;
      }
      return bucket.activeId || profiles[0]?.id || '';
    }

    function buildModelSettingsNavMarkup(groups, activeCategory) {
      const categories = modelSettingsCategories.filter((category) => groups.some((group) => group.label === category));
      if (!categories.length) return '';
      return `
        <nav class="multi-agent-nav" aria-label="模型类型">
          <div class="multi-agent-nav-group" role="tablist" aria-label="模型类型">
            ${categories.map((category) => {
              const categoryKey = modelProfileCategoryKeys[category];
              const bucket = state.authSettings?.modelProfiles?.[categoryKey] || {};
              const profiles = Array.isArray(bucket.profiles) ? bucket.profiles : [];
              const activeProfile = profiles.find((profile) => profile.id === bucket.activeId);
              const details = modelSettingsDetails(categoryKey);
              const active = category === activeCategory;
              const ready = modelProfileReady(activeProfile);
              return `
                <button type="button" id="model-settings-type-tab-${escapeHtml(categoryKey)}" class="multi-agent-nav-item${active ? ' active' : ''}" data-model-settings-category="${escapeHtml(category)}" role="tab" aria-selected="${active ? 'true' : 'false'}" aria-controls="model-settings-panel" tabindex="${active ? '0' : '-1'}" title="${escapeHtml(`${category}：${details.description}`)}">
                  <span>${escapeHtml(details.shortLabel)}</span>
                  <span class="multi-agent-nav-status is-${ready ? 'live' : 'shadow'}" aria-hidden="true"></span>
                </button>
              `;
            }).join('')}
          </div>
        </nav>
      `;
    }

    function buildModelSettingsPanelMarkup(groups, group, labelledBy) {
      const categoryKey = modelProfileCategoryKeys[group.label];
      const bucket = state.authSettings?.modelProfiles?.[categoryKey] || { activeId: '', profiles: [] };
      const profiles = Array.isArray(bucket.profiles) ? bucket.profiles : [];
      const selectedId = resolveExpandedModelProfileId(categoryKey, bucket, profiles);
      const selectedProfile = profiles.find((profile) => profile.id === selectedId) || null;
      const activeProfile = profiles.find((profile) => profile.id === bucket.activeId) || null;
      const details = modelSettingsDetails(categoryKey);
      const supplementalFields = group.fields.filter((field) => !['接口地址', 'API Key', '模型名', '模板'].includes(String(field.label || '')));
      const ready = modelProfileReady(activeProfile);
      const saving = selectedProfile && state.settingsModal.savingModelProfileId === selectedProfile.id;
      return `
        <section id="settings-category-panel" class="multi-agent-settings" role="tabpanel" aria-labelledby="${labelledBy}">
          ${buildModelSettingsNavMarkup(groups, group.label)}
          <div id="model-settings-panel" class="multi-agent-panel" role="tabpanel" aria-labelledby="model-settings-type-tab-${escapeHtml(categoryKey)}" tabindex="0">
            <div class="multi-agent-detail model-settings-detail">
              <header class="multi-agent-detail-head">
                <div class="multi-agent-detail-copy">
                  <div class="multi-agent-role-head">
                    <h3>${escapeHtml(group.label)}</h3>
                    <span class="multi-agent-role-flags">
                      <span class="multi-agent-role-status is-${ready ? 'live' : 'shadow'}">${ready ? '配置完整' : '等待补齐'}</span>
                      <button type="button" class="settings-action-button" data-create-model-profile="${escapeHtml(categoryKey)}">新建</button>
                    </span>
                  </div>
                  <p>${escapeHtml(details.description)}${activeProfile ? ` 当前使用「${escapeHtml(activeProfile.name || '默认配置')}」· ${escapeHtml(activeProfile.model || '未填模型')}。` : ' 尚未启用配置。'}</p>
                </div>
              </header>

              ${profiles.length
                ? `<ol class="multi-agent-flow model-profile-flow" aria-label="配置列表">${profiles.map((profile, index) => buildModelProfileFlowItemMarkup(categoryKey, profile, bucket.activeId, selectedId, index)).join('')}</ol>`
                : '<div class="model-profile-empty"><strong>还没有模型配置</strong><span>点击“新建”添加第一套连接信息。</span></div>'}

              ${selectedProfile
                ? buildModelProfileFormMarkup(categoryKey, selectedProfile, selectedProfile.id === bucket.activeId, true, saving)
                : ''}

              ${buildModelProfileSupplementalMarkup(supplementalFields, `${details.shortLabel}通用配置`)}
            </div>
          </div>
        </section>
      `;
    }

    function buildModelProfileFlowItemMarkup(categoryKey, profile, activeId, selectedId, index) {
      const active = profile.id === activeId;
      const selected = profile.id === selectedId;
      const host = modelProfileEndpointHost(profile);
      const keyReady = !!profile.hasApiKey;
      return `
        <li>
          <div class="multi-agent-flow-item model-profile-flow-item${selected ? ' active' : ''}${active ? ' is-current' : ''}">
            <button type="button" class="model-profile-flow-select" data-toggle-model-profile="${escapeHtml(categoryKey)}:${escapeHtml(profile.id)}" aria-expanded="${selected ? 'true' : 'false'}">
              <span class="multi-agent-flow-index" aria-hidden="true">${index + 1}</span>
              <span class="multi-agent-flow-copy">
                <strong>${escapeHtml(profile.name || `配置 ${index + 1}`)}</strong>
                <span>${escapeHtml(profile.model || '尚未填写模型')} · ${escapeHtml(host)}</span>
              </span>
            </button>
            <span class="multi-agent-role-flags model-profile-flow-flags">
              ${active
                ? '<span class="multi-agent-role-status model-profile-current-status is-live">使用中</span>'
                : `<button type="button" class="model-profile-activate-button" data-switch-model-profile="${escapeHtml(categoryKey)}:${escapeHtml(profile.id)}" role="switch" aria-checked="false">设为当前</button>`}
              <span class="multi-agent-role-status is-${keyReady ? 'live' : 'shadow'}">${keyReady ? '密钥就绪' : '缺密钥'}</span>
              <button type="button" class="model-profile-copy-button" data-duplicate-model-profile="${escapeHtml(categoryKey)}:${escapeHtml(profile.id)}" aria-label="复制${escapeHtml(profile.name || `配置 ${index + 1}`)}" title="复制配置">复制</button>
            </span>
          </div>
        </li>
      `;
    }

    let modelProfileSelectionTransitionVersion = 0;
    let currentModelProfileSelectionTransition = null;

    function activeModelProfileFlowItem() {
      return els.settingsModalBody?.querySelector('.model-profile-flow-item.active') || null;
    }

    function updateExpandedModelProfileSelection(category, profileId) {
      state.settingsModal.expandedModelProfiles = {
        ...(state.settingsModal.expandedModelProfiles || {}),
        [category]: profileId,
      };
    }

    function modelProfileSelectionBounds(element) {
      if (!element) return null;
      const bounds = element.getBoundingClientRect();
      return { left: bounds.left, top: bounds.top };
    }

    function animateModelProfileSelectionFallback(previousBounds) {
      const selectedItem = activeModelProfileFlowItem();
      if (!previousBounds || !selectedItem || typeof selectedItem.animate !== 'function') return;
      const nextBounds = selectedItem.getBoundingClientRect();
      const horizontalOffset = previousBounds.left - nextBounds.left;
      const verticalOffset = previousBounds.top - nextBounds.top;
      if (Math.abs(horizontalOffset) < 1 && Math.abs(verticalOffset) < 1) return;
      const animation = selectedItem.animate([
        { transform: `translate3d(${horizontalOffset}px, ${verticalOffset}px, 0)` },
        { transform: 'translate3d(0, 0, 0)' },
      ], {
        duration: 280,
        easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
        fill: 'both',
      });
      animation.finished.catch(() => {}).finally(() => animation.cancel());
    }

    function renderModelProfileSelectionFallback(category, profileId, previousBounds) {
      updateExpandedModelProfileSelection(category, profileId);
      renderSettingsModal();
      animateModelProfileSelectionFallback(previousBounds);
    }

    function selectModelProfileFromSettings(category, profileId) {
      const previousSelection = activeModelProfileFlowItem();
      const previousSelectionControl = previousSelection?.querySelector('[data-toggle-model-profile]');
      if (previousSelectionControl?.getAttribute('data-toggle-model-profile') === `${category}:${profileId}`) return;
      const previousBounds = modelProfileSelectionBounds(previousSelection);
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (reduceMotion || typeof document.startViewTransition !== 'function' || !previousSelection) {
        renderModelProfileSelectionFallback(category, profileId, previousBounds);
        return;
      }

      const transitionVersion = ++modelProfileSelectionTransitionVersion;
      currentModelProfileSelectionTransition?.skipTransition();
      document.documentElement.classList.add('is-model-profile-selection-transition');
      previousSelection.style.viewTransitionName = 'model-profile-selection';
      let nextSelection = null;
      try {
        currentModelProfileSelectionTransition = document.startViewTransition(() => {
          updateExpandedModelProfileSelection(category, profileId);
          renderSettingsModal();
          nextSelection = activeModelProfileFlowItem();
          if (nextSelection) nextSelection.style.viewTransitionName = 'model-profile-selection';
        });
      } catch (_error) {
        previousSelection.style.viewTransitionName = '';
        document.documentElement.classList.remove('is-model-profile-selection-transition');
        renderModelProfileSelectionFallback(category, profileId, previousBounds);
        return;
      }
      currentModelProfileSelectionTransition.finished.finally(() => {
        previousSelection.style.viewTransitionName = '';
        if (nextSelection) nextSelection.style.viewTransitionName = '';
        if (transitionVersion !== modelProfileSelectionTransitionVersion) return;
        currentModelProfileSelectionTransition = null;
        document.documentElement.classList.remove('is-model-profile-selection-transition');
      });
    }

    function buildModelProfileSupplementalMarkup(fields, title) {
      if (!fields.length) return '';
      return `
        <div class="settings-panel is-embedded model-profile-supplemental" aria-label="类型参数">
          <header class="settings-panel-head">
            <h3 class="settings-section-title">${escapeHtml(title)}</h3>
            <span class="settings-archive-total">作用于当前模型类型</span>
          </header>
          <div class="settings-row-list">${fields.map((field) => buildSettingsRowMarkup(field)).join('')}</div>
        </div>
      `;
    }

    function buildModelProfileFormMarkup(categoryKey, profile, active, _expanded, saving) {
      const templateOptions = categoryKey === 'video'
        ? videoTemplateOptions().map((item) => `<option value="${escapeHtml(item.value)}" ${profile.template === item.value ? 'selected' : ''}>${escapeHtml(item.label)}</option>`).join('')
        : '';
      return `
        <form class="settings-panel is-embedded model-profile-editor" data-model-profile-form data-category="${escapeHtml(categoryKey)}" data-profile-id="${escapeHtml(profile.id)}">
          <header class="settings-panel-head">
            <h3 class="settings-section-title">连接与模型</h3>
            <div class="settings-row-actions">
              ${categoryKey === 'video' ? '<button type="button" class="settings-action-button" data-open-video-params="1">参数设置</button>' : ''}
              ${active ? '' : `<button type="button" class="settings-action-button danger" data-delete-model-profile="${escapeHtml(categoryKey)}:${escapeHtml(profile.id)}">删除</button>`}
              <button type="submit" class="primary-button" ${saving ? 'disabled' : ''}>${saving ? '保存中' : '保存'}</button>
            </div>
          </header>
          <div class="settings-row-list">
            <label class="settings-row model-profile-settings-row">
              <span class="settings-row-main"><span class="settings-row-title">配置名称</span></span>
              <input class="settings-value" name="name" value="${escapeHtml(profile.name || '')}" maxlength="60" autocomplete="off" />
            </label>
            <label class="settings-row model-profile-settings-row">
              <span class="settings-row-main"><span class="settings-row-title">模型名</span></span>
              <input class="settings-value" name="model" value="${escapeHtml(profile.model || '')}" placeholder="填写模型 ID" autocomplete="off" spellcheck="false" />
            </label>
            <label class="settings-row model-profile-settings-row">
              <span class="settings-row-main"><span class="settings-row-title">接口地址</span></span>
              <input class="settings-value" name="baseUrl" value="${escapeHtml(profile.baseUrl || '')}" placeholder="https://api.example.com" inputmode="url" autocomplete="url" spellcheck="false" />
            </label>
            <label class="settings-row model-profile-settings-row">
              <span class="settings-row-main">
                <span class="settings-row-title">API Key</span>
                <span class="settings-row-meta">${profile.hasApiKey ? '已保存，留空保持不变' : '尚未设置密钥'}</span>
              </span>
              <input class="settings-value settings-secret-input" name="apiKey" type="password" value="" placeholder="${profile.hasApiKey ? '已保存，留空保持不变' : '填写 API Key'}" autocomplete="new-password" spellcheck="false" />
            </label>
            ${categoryKey === 'video' ? `
              <label class="settings-row model-profile-settings-row">
                <span class="settings-row-main"><span class="settings-row-title">视频模板</span></span>
                <select class="settings-value settings-row-select" name="template">${templateOptions}</select>
              </label>
            ` : ''}
          </div>
          <p class="multi-agent-footnote model-profile-footnote">${active ? '此配置正在使用；保存后立即更新当前连接。' : '保存只更新此配置，点击“设为当前”后才会生效。'}</p>
        </form>
      `;
    }

    async function mutateModelProfile(action, category, profileId = '', profile = {}) {
      const res = await fetch('/api/model-profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, category, profileId, profile }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || '模型配置操作失败');
      state.authSettings = { ...(state.authSettings || {}), modelProfiles: data.modelProfiles || {} };
      return data.modelProfiles || {};
    }

    function expandNewestModelProfile(category, profiles) {
      const items = profiles?.[category]?.profiles || [];
      const newest = items[items.length - 1];
      if (!newest) return;
      state.settingsModal.expandedModelProfiles = {
        ...(state.settingsModal.expandedModelProfiles || {}),
        [category]: newest.id,
      };
    }

    async function createModelProfileFromSettings(category) {
      const profiles = await mutateModelProfile('create', category, '', { name: '备选配置' });
      expandNewestModelProfile(category, profiles);
      renderSettingsModal();
    }

    async function duplicateModelProfileFromSettings(category, profileId) {
      const profiles = await mutateModelProfile('duplicate', category, profileId);
      expandNewestModelProfile(category, profiles);
      renderSettingsModal();
    }

    async function activateModelProfileFromSettings(category, profileId) {
      await mutateModelProfile('activate', category, profileId);
      await refreshAuthSettings();
      await refreshVideoModelSettings();
      await refreshHealth();
      renderSettingsModal();
    }

    async function deleteModelProfileFromSettings(category, profileId) {
      if (!window.confirm('确定删除这套模型配置吗？')) return;
      await mutateModelProfile('delete', category, profileId);
      state.settingsModal.expandedModelProfiles = { ...(state.settingsModal.expandedModelProfiles || {}), [category]: '' };
      renderSettingsModal();
    }

    document.addEventListener('submit', async (event) => {
      const form = event.target.closest('[data-model-profile-form]');
      if (!form) return;
      event.preventDefault();
      const category = String(form.dataset.category || '');
      const profileId = String(form.dataset.profileId || '');
      const values = new FormData(form);
      state.settingsModal.savingModelProfileId = profileId;
      renderSettingsModal();
      try {
        await mutateModelProfile('update', category, profileId, {
          name: String(values.get('name') || '').trim(),
          baseUrl: String(values.get('baseUrl') || '').trim(),
          apiKey: String(values.get('apiKey') || '').trim(),
          model: String(values.get('model') || '').trim(),
          template: String(values.get('template') || '').trim(),
        });
        await refreshAuthSettings();
        await refreshVideoModelSettings();
        await refreshHealth();
      } catch (error) {
        window.alert(error?.message || '模型配置保存失败');
      } finally {
        state.settingsModal.savingModelProfileId = '';
        renderSettingsModal();
      }
    });
