    const multiAgentRoleDefinitions = [
      {
        key: 'overview',
        label: '总览',
      },
      {
        key: 'supervisor',
        label: 'Supervisor',
        mark: 'S',
        status: '已接入',
        tone: 'live',
        description: '统一接收根任务并维护真实运行态。',
        responsibilities: [
          '执行意图判断 Agent 的结构化路由决策',
          '维护根任务、子任务、租约、恢复与终态保护',
          '让取消请求、后台状态与界面进度保持一致',
        ],
        boundary: '不自行猜测用户意图，不重写用户要求的风格、数量、时长。',
      },
      {
        key: 'intent-agent',
        label: '意图判断',
        mark: 'I',
        status: '已接入',
        tone: 'live',
        description: '负责识别新任务、会话跟进、重新分集与改写意图。',
        responsibilities: [
          '输出新任务、继续会话、确认卡跟进、重新分集或改写等结构化路由',
          '结合当前 awaiting 与完成状态判断是否需要重置会话',
        ],
        boundary: '只判断意图和路由；不修改会话、不决定分集数量、不规划或生成内容。',
      },
      {
        key: 'planner',
        label: 'Planner',
        mark: 'P',
        status: 'Capability 已接入',
        tone: 'live',
        description: '负责智能分集与视频任务规划。',
        responsibilities: [
          '理解全文并判断合理的分集数量与内容边界',
          '为每集生成独立主题、提示词与可追踪规划结果',
          '通过类型化 Capability 执行并记录 start、end、error 事件',
        ],
        boundary: '只负责内容规划与智能分集，不提交视频模型、不审核成片、不归档结果。',
      },
      {
        key: 'reviewer',
        label: 'Reviewer',
        mark: 'R',
        status: '知识库已接入',
        tone: 'live',
        shadow: '媒体影子',
        description: '知识入库语义审核已接入；媒体审核仍为影子模式。',
        responsibilities: [
          '审核知识叶子的原子性、覆盖度、层级与检索价值',
          '返回 accept、revise 或 reject，并提供可验证的返工证据',
        ],
        boundary: '最多要求一次知识建树返工；暂不审看 MP4，不直接写库或自行重跑生成。',
      },
      {
        key: 'knowledge-base',
        label: '知识库 Agent',
        mark: 'K',
        status: '已接入',
        tone: 'live',
        description: '负责单份文档的知识树规划与原文单元归属。',
        responsibilities: [
          '把原始文档规划为最多三层的可检索知识树',
          '只选择原文单元编号，正文由程序确定性提取',
        ],
        boundary: '不审核自己的结果、不生成知识正文；只读单份文档，不改业务提示词、生成参数或媒体结果。',
      },
      {
        key: 'viral-shot-language',
        label: '镜头语言 Agent',
        mark: 'V',
        status: '已接入',
        tone: 'live',
        description: '基于代表帧与识别台词提取可复用的镜头语言证据。',
        responsibilities: [
          '分析代表帧中的构图、主体动作、镜头节奏与视觉钩子',
          '把观察结果组织为猜剧本可直接复用的结构化证据',
        ],
        boundary: '只分析已有代表帧与台词证据；不重复识别台词、不猜剧本、不触发视频生成。',
      },
      {
        key: 'viral-script-reconstruction',
        label: '猜剧本 Agent',
        mark: 'G',
        status: '已接入',
        tone: 'live',
        description: '使用镜头语言与识别台词重建爆款视频的剧本框架。',
        responsibilities: [
          '融合镜头语言证据与识别台词，恢复叙事结构和内容节奏',
          '输出可复用的剧本框架，供后续生成准备使用',
        ],
        boundary: '只消费已有分析结果；不再调用旧画面分析流程、不改原视频或素材。',
      },
    ];
    const multiAgentConfigDefinitions = [
      {
        key: 'shared-model',
        label: '共享模型',
      },
    ];
    const modelSettingsCategories = ['文本/视频规划模型', '多模态模型', '图片模型', '视频模型'];
    const modelProfileCategoryKeys = {
      '文本/视频规划模型': 'llm',
      '多模态模型': 'multimodal',
      '图片模型': 'image',
      '视频模型': 'video',
    };

    function renderSettingsModal() {
      if (!els.settingsModal) return;
      const visible = !!state.settingsModal.visible;
      els.settingsModal.classList.toggle('hidden', !visible);
      if (!visible) return;
      const settings = state.authSettings || {};
      const videoSettings = state.videoModelSettings || {};
      const fields = Array.isArray(settings.fields) ? settings.fields : [];
      const groups = withImageHostSettingsGroup(groupSettingsFields(fields));
      const activeCategory = resolveActiveSettingsCategory(groups);
      const templateText = currentVideoTemplateStatusText(videoSettings);
      const videoMergeText = `视频合并：${videoMergeModeLabel(state.settingsModal.videoMergeMode)}`;
      const videoResolutionText = videoResolutionStatusText(videoSettings);
      els.settingsModalSub.innerHTML = `
        <div class="settings-status">
          ${pill(templateText, 'info')}
          ${pill(videoMergeText, 'info')}
          ${pill(videoResolutionText, 'info')}
          ${pill(`单个${Number(videoSettings.seconds || 10) || 10}秒`, 'info')}
        </div>
      `;
      els.settingsModalBody.innerHTML = `
        ${buildSettingsTabsMarkup(groups, activeCategory)}
        ${buildAuthSettingsMarkup(groups, activeCategory)}
      `;
    }

    function buildSettingsTabsMarkup(groups, activeCategory) {
      if (!groups.length) return '';
      const tabs = buildSettingsPrimaryTabs(groups);
      return `
        <div class="settings-tabs" role="tablist" aria-label="设置分类">
          ${tabs.map((tab, index) => {
            const active = tab.modelGroup
              ? modelSettingsCategories.includes(activeCategory)
              : tab.category === activeCategory;
            return `
              <button type="button" id="settings-category-tab-${index}" class="settings-tab${active ? ' active' : ''}" data-settings-category="${escapeHtml(tab.category)}" role="tab" aria-selected="${active ? 'true' : 'false'}" aria-controls="settings-category-panel" tabindex="${active ? '0' : '-1'}">
                ${escapeHtml(tab.label)}
              </button>
            `;
          }).join('')}
        </div>
      `;
    }

    function buildSettingsPrimaryTabs(groups) {
      const tabs = [];
      let modelTabAdded = false;
      groups.forEach((group) => {
        if (modelSettingsCategories.includes(group.label)) {
          if (modelTabAdded) return;
          modelTabAdded = true;
          tabs.push({ label: '模型设置', category: '模型设置', modelGroup: true });
          return;
        }
        tabs.push({
          label: settingsCategoryDisplayLabel(group.label),
          category: group.label,
          modelGroup: false,
        });
      });
      return tabs;
    }

    function buildModelSettingsNavMarkup(groups, activeCategory) {
      const categories = modelSettingsCategories.filter((category) => groups.some((group) => group.label === category));
      if (!categories.length) return '';
      return `
        <nav class="multi-agent-nav model-settings-nav" aria-label="模型设置分类">
          <div class="multi-agent-nav-group" role="tablist" aria-label="模型类型">
          ${categories.map((category) => {
            const active = category === activeCategory;
            return `<button type="button" class="multi-agent-nav-item model-settings-nav-item${active ? ' active' : ''}" data-model-settings-category="${escapeHtml(category)}" role="tab" aria-selected="${active ? 'true' : 'false'}" aria-controls="model-settings-panel" tabindex="${active ? '0' : '-1'}"><span>${escapeHtml(category)}</span></button>`;
          }).join('')}
          </div>
        </nav>
      `;
    }

    function buildModelSettingsPanelMarkup(groups, group, labelledBy, density) {
      const categoryKey = modelProfileCategoryKeys[group.label];
      const bucket = state.authSettings?.modelProfiles?.[categoryKey] || { activeId: '', profiles: [] };
      const profiles = Array.isArray(bucket.profiles) ? bucket.profiles : [];
      const expandedProfiles = state.settingsModal.expandedModelProfiles || {};
      const expandedId = Object.prototype.hasOwnProperty.call(expandedProfiles, categoryKey)
        ? expandedProfiles[categoryKey]
        : bucket.activeId || profiles[0]?.id || '';
      const supplementalFields = group.fields.filter((field) => !['接口地址', 'API Key', '模型名', '模板'].includes(String(field.label || '')));
      return `
        <section id="settings-category-panel" class="multi-agent-settings model-settings-layout${density ? ` ${density}` : ''}" role="tabpanel" aria-labelledby="${labelledBy}">
          ${buildModelSettingsNavMarkup(groups, group.label)}
          <div id="model-settings-panel" class="model-settings-panel" role="tabpanel">
            <div class="model-profile-toolbar">
              <div><strong>${escapeHtml(group.label)}</strong><span>${profiles.length} 套配置</span></div>
              <button type="button" class="settings-action-button" data-create-model-profile="${escapeHtml(categoryKey)}">＋ 新建备选模型</button>
            </div>
            <div class="model-profile-list">
              ${profiles.map((profile, index) => buildModelProfileCardMarkup(categoryKey, profile, bucket.activeId, expandedId, index)).join('')}
            </div>
            ${supplementalFields.length ? `<div class="settings-row-list model-profile-supplemental">${supplementalFields.map((field) => buildSettingsRowMarkup(field)).join('')}</div>` : ''}
          </div>
        </section>
      `;
    }

    function buildModelProfileCardMarkup(categoryKey, profile, activeId, expandedId, index) {
      const active = profile.id === activeId;
      const expanded = profile.id === expandedId;
      const saving = state.settingsModal.savingModelProfileId === profile.id;
      const templateOptions = categoryKey === 'video'
        ? videoTemplateOptions().map((item) => `<option value="${escapeHtml(item.value)}" ${profile.template === item.value ? 'selected' : ''}>${escapeHtml(item.label)}</option>`).join('')
        : '';
      return `
        <article class="model-profile-card${active ? ' is-active' : ''}${expanded ? ' is-expanded' : ''}">
          <div class="model-profile-summary" data-toggle-model-profile="${escapeHtml(categoryKey)}:${escapeHtml(profile.id)}" role="button" tabindex="0" aria-expanded="${expanded ? 'true' : 'false'}">
            <span class="model-profile-chevron">›</span>
            <span class="model-profile-index">${index + 1}</span>
            <span class="model-profile-summary-copy">
              <span class="model-profile-title-row">
                <strong>${escapeHtml(profile.name || `配置 ${index + 1}`)}</strong>
                <button type="button" class="model-profile-switch${active ? ' is-active' : ''}" data-switch-model-profile="${escapeHtml(categoryKey)}:${escapeHtml(profile.id)}" role="switch" aria-checked="${active ? 'true' : 'false'}" aria-label="${active ? '当前启用' : '设为当前配置'}" title="${active ? '当前启用' : '切换为当前配置'}"><span></span></button>
              </span>
              <small>${escapeHtml(profile.model || '尚未填写模型')}</small>
            </span>
            <button type="button" class="model-profile-copy-button" data-duplicate-model-profile="${escapeHtml(categoryKey)}:${escapeHtml(profile.id)}" aria-label="复制${escapeHtml(profile.name || `配置 ${index + 1}`)}">复制</button>
          </div>
          <div class="model-profile-collapse" aria-hidden="${expanded ? 'false' : 'true'}">
            <div class="model-profile-collapse-inner">
            <form class="model-profile-form" data-model-profile-form data-category="${escapeHtml(categoryKey)}" data-profile-id="${escapeHtml(profile.id)}" ${expanded ? '' : 'inert'}>
              <label><span>配置名称</span><input name="name" value="${escapeHtml(profile.name || '')}" maxlength="60" /></label>
              <label><span>接口地址</span><input name="baseUrl" value="${escapeHtml(profile.baseUrl || '')}" placeholder="https://api.example.com" spellcheck="false" /></label>
              <label><span>API Key</span><input name="apiKey" type="password" value="" placeholder="${profile.hasApiKey ? '已保存，留空保持不变' : '填写 API Key'}" autocomplete="off" spellcheck="false" /></label>
              <label><span>模型名</span><input name="model" value="${escapeHtml(profile.model || '')}" spellcheck="false" /></label>
              ${categoryKey === 'video' ? `<label><span>模板</span><select name="template">${templateOptions}</select></label>` : ''}
              <div class="model-profile-actions">
                ${categoryKey === 'video' ? '<button type="button" class="settings-action-button" data-open-video-params="1">参数设置</button>' : ''}
                ${active ? '' : `<button type="button" class="settings-action-button danger" data-delete-model-profile="${escapeHtml(categoryKey)}:${escapeHtml(profile.id)}">删除</button>`}
                <button type="submit" class="primary-button" ${saving ? 'disabled' : ''}>${saving ? '保存中' : '保存配置'}</button>
              </div>
            </form>
            </div>
          </div>
        </article>
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
      state.authSettings = {
        ...(state.authSettings || {}),
        modelProfiles: data.modelProfiles || {},
      };
      return data.modelProfiles || {};
    }

    async function createModelProfileFromSettings(category) {
      const profiles = await mutateModelProfile('create', category, '', { name: '备选配置' });
      const items = profiles?.[category]?.profiles || [];
      const created = items[items.length - 1];
      if (created) {
        state.settingsModal.expandedModelProfiles = {
          ...(state.settingsModal.expandedModelProfiles || {}),
          [category]: created.id,
        };
      }
      renderSettingsModal();
    }

    async function duplicateModelProfileFromSettings(category, profileId) {
      const profiles = await mutateModelProfile('duplicate', category, profileId);
      const items = profiles?.[category]?.profiles || [];
      const duplicate = items[items.length - 1];
      if (duplicate) {
        state.settingsModal.expandedModelProfiles = {
          ...(state.settingsModal.expandedModelProfiles || {}),
          [category]: duplicate.id,
        };
      }
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
      if (!window.confirm('确定删除这套备选模型配置吗？')) return;
      await mutateModelProfile('delete', category, profileId);
      state.settingsModal.expandedModelProfiles = {
        ...(state.settingsModal.expandedModelProfiles || {}),
        [category]: '',
      };
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

    function buildAuthSettingsMarkup(groups, activeCategory) {
      if (!groups.length) {
        return '<div class="empty">当前没有可显示的鉴权信息。</div>';
      }
      const group = groups.find((item) => item.label === activeCategory) || groups[0];
      const primaryTabs = buildSettingsPrimaryTabs(groups);
      const activeIndex = Math.max(0, primaryTabs.findIndex((tab) => (
        tab.modelGroup ? modelSettingsCategories.includes(group.label) : tab.category === group.label
      )));
      const labelledBy = `settings-category-tab-${activeIndex}`;
      if (group.label === 'AI8video') {
        return buildMultiAgentSettingsMarkup(group, labelledBy);
      }
      if (group.label === '图床') {
        return buildImageHostSettingsMarkup(labelledBy);
      }
      const archiveArtifacts = state.archiveArtifacts || state.authSettings?.archiveArtifacts || {};
      const archiveTotal = String(archiveArtifacts.totalDisplay || '0 B');
      const fieldCount = group.fields.length;
      const isArchive = group.label === '归档';
      const density = fieldCount <= 2 ? 'is-compact' : fieldCount <= 5 ? 'is-cozy' : '';
      if (modelSettingsCategories.includes(group.label)) {
        return buildModelSettingsPanelMarkup(groups, group, labelledBy, density);
      }
      return `
        <div id="settings-category-panel" class="settings-panel${density ? ` ${density}` : ''}" role="tabpanel" aria-labelledby="${labelledBy}">
          ${isArchive ? `
            <header class="settings-panel-head">
              <h3 class="settings-section-title">${escapeHtml(settingsCategoryDisplayLabel(group.label))}</h3>
              <div class="settings-section-actions">
                <button type="button" class="settings-section-refresh" data-refresh-archive-settings ${state.settingsModal.refreshingArchive || state.settingsModal.cleaningArchiveAll ? 'disabled' : ''}>${state.settingsModal.refreshingArchive ? '刷新中' : '刷新'}</button>
                <span class="settings-archive-total">总占用 ${escapeHtml(archiveTotal)}</span>
                <button type="button" class="settings-section-cleanup" data-cleanup-archive-all ${state.settingsModal.cleaningArchiveAll ? 'disabled' : ''}>${state.settingsModal.cleaningArchiveAll ? '清理中' : '一键清理'}</button>
              </div>
            </header>
          ` : ''}
          <div class="settings-row-list">
            ${group.fields.map((field) => buildSettingsRowMarkup(field)).join('')}
          </div>
        </div>
      `;
    }

    function buildMultiAgentSettingsMarkup(group, labelledBy) {
      const activeRole = resolveActiveMultiAgentRole();
      const sharedModelReady = isMultiAgentSharedModelConfigured(group.fields);
      return `
        <section id="settings-category-panel" class="multi-agent-settings" role="tabpanel" aria-labelledby="${labelledBy}">
          <nav class="multi-agent-nav" aria-label="Multi-Agent 角色">
            <div class="multi-agent-nav-group" role="tablist" aria-label="角色与总览">
              ${multiAgentRoleDefinitions.map((role) => buildMultiAgentNavItemMarkup(role, activeRole)).join('')}
            </div>
            <div class="multi-agent-nav-group is-config" role="tablist" aria-label="基础配置">
              ${multiAgentConfigDefinitions.map((item) => buildMultiAgentNavItemMarkup(item, activeRole, true)).join('')}
            </div>
          </nav>
          <div id="multi-agent-role-panel" class="multi-agent-panel" role="tabpanel" aria-labelledby="multi-agent-role-tab-${escapeHtml(activeRole)}" tabindex="0">
            ${buildMultiAgentRolePanel(activeRole, group, sharedModelReady)}
          </div>
        </section>
      `;
    }

    function buildMultiAgentNavItemMarkup(item, activeRole, isConfig = false) {
      const active = item.key === activeRole;
      const className = `multi-agent-nav-item${isConfig ? ' is-config' : ''}${active ? ' active' : ''}`;
      const status = item.status
        ? `<span class="multi-agent-nav-status is-${escapeHtml(item.tone || 'live')}${item.shadow ? ' has-shadow' : ''}" aria-hidden="true"></span>`
        : '';
      return `<button type="button" id="multi-agent-role-tab-${escapeHtml(item.key)}" class="${className}" data-agent-settings-role="${escapeHtml(item.key)}" role="tab" aria-selected="${active ? 'true' : 'false'}" aria-controls="multi-agent-role-panel" tabindex="${active ? '0' : '-1'}"><span>${escapeHtml(item.label)}</span>${status}</button>`;
    }

    function buildMultiAgentRolePanel(activeRole, group, sharedModelReady) {
      if (activeRole === 'shared-model') {
        return buildMultiAgentSharedModelPanel(group, sharedModelReady);
      }
      if (activeRole === 'overview') {
        return buildMultiAgentOverviewPanel();
      }
      const role = multiAgentRoleDefinitions.find((item) => item.key === activeRole);
      return role ? buildMultiAgentDetailPanel(role) : buildMultiAgentOverviewPanel();
    }

    function buildMultiAgentRoleStatusMarkup(role) {
      const main = `<span class="multi-agent-role-status is-${escapeHtml(role.tone || 'live')}">${escapeHtml(role.status)}</span>`;
      if (!role.shadow) return main;
      return `${main}<span class="multi-agent-role-status is-shadow">${escapeHtml(role.shadow)}</span>`;
    }

    function buildMultiAgentOverviewPanel() {
      const flowKeys = ['intent-agent', 'supervisor', 'planner', 'knowledge-base', 'reviewer'];
      const roles = flowKeys
        .map((key) => multiAgentRoleDefinitions.find((role) => role.key === key))
        .filter(Boolean);
      return `
        <div class="multi-agent-overview">
          <p class="multi-agent-lede">Planner 已按 Skill 策略层 + Capability 执行层运行；Python 会话和任务账本继续保持唯一真值，媒体审核仍为影子模式。</p>
          <ol class="multi-agent-flow">
            ${roles.map((role, index) => `
              <li>
                <button type="button" class="multi-agent-flow-item" data-agent-settings-role="${escapeHtml(role.key)}">
                  <span class="multi-agent-flow-index" aria-hidden="true">${index + 1}</span>
                  <span class="multi-agent-flow-copy">
                    <strong>${escapeHtml(role.label)}</strong>
                    <span>${escapeHtml(role.description)}</span>
                  </span>
                  <span class="multi-agent-role-flags">${buildMultiAgentRoleStatusMarkup(role)}</span>
                </button>
              </li>
            `).join('')}
          </ol>
          <p class="multi-agent-footnote">模型与鉴权在对应设置页配置；已接入 Skill 显示在所属 Agent 详情中。</p>
        </div>
      `;
    }

    function enabledAgentSkills(agentId) {
      const agents = state.authSettings?.agentSkills?.agents;
      if (!Array.isArray(agents)) return [];
      const agent = agents.find((item) => item?.agentId === agentId);
      if (!Array.isArray(agent?.skills)) return [];
      return agent.skills.filter((skill) => !!skill?.enabled);
    }

    function buildAgentSkillSectionMarkup(role) {
      const skills = enabledAgentSkills(role.key);
      if (!skills.length) return '';
      return `
        <section class="multi-agent-detail-block multi-agent-role-skills" aria-label="已接入 Skills">
          <h4>Skills</h4>
          <ul class="multi-agent-role-skill-list">
            ${skills.map((skill) => `
              <li class="multi-agent-role-skill-item">
                <div>
                  <code>${escapeHtml(skill.name || '')}</code>
                  <p>${escapeHtml(skill.description || '暂无说明')}</p>
                  <small>${escapeHtml(skill.runtimeActive ? '执行能力已绑定' : '仅策略指令')}${skill.version ? ` · v${escapeHtml(skill.version)}` : ''}</small>
                </div>
                ${skill.builtIn ? '<span title="内置 Skill 不可删除">内置</span>' : ''}
              </li>
            `).join('')}
          </ul>
        </section>
      `;
    }

    function buildMultiAgentDetailPanel(role) {
      return `
        <div class="multi-agent-detail">
          <header class="multi-agent-detail-head">
            <div class="multi-agent-detail-copy">
              <div class="multi-agent-role-head">
                <h3>${escapeHtml(role.label)}</h3>
                <span class="multi-agent-role-flags">${buildMultiAgentRoleStatusMarkup(role)}</span>
              </div>
              <p>${escapeHtml(role.description)}</p>
            </div>
          </header>
          <div class="multi-agent-detail-grid">
            <section class="multi-agent-detail-block">
              <h4>当前职责</h4>
              <ul>${role.responsibilities.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
            </section>
            <section class="multi-agent-detail-block">
              <h4>行为边界</h4>
              <p>${escapeHtml(role.boundary)}</p>
            </section>
          </div>
          ${buildAgentSkillSectionMarkup(role)}
        </div>
      `;
    }

    function buildMultiAgentSharedModelPanel(group, sharedModelReady) {
      return `
        <div class="multi-agent-shared-note">
          <div>
            <h3>共享核心模型</h3>
            <p>意图、Planner、知识库与 Reviewer 复用 AI8video 核心模型；Supervisor 不调用模型。</p>
          </div>
          <span class="multi-agent-role-status is-${sharedModelReady ? 'live' : 'shadow'}">${sharedModelReady ? '配置完整' : '等待补齐'}</span>
        </div>
        <div class="settings-panel is-embedded">
          <header class="settings-panel-head">
            <h3 class="settings-section-title">连接与模型</h3>
          </header>
          <div class="settings-row-list">
            ${group.fields.map((field) => buildSettingsRowMarkup(field)).join('')}
          </div>
        </div>
      `;
    }

    function resolveActiveMultiAgentRole() {
      const validRoles = [...multiAgentRoleDefinitions, ...multiAgentConfigDefinitions].map((item) => item.key);
      const activeRole = String(state.settingsModal.activeAgentRole || 'overview');
      if (validRoles.includes(activeRole)) return activeRole;
      state.settingsModal.activeAgentRole = 'overview';
      return 'overview';
    }

    function isMultiAgentSharedModelConfigured(fields) {
      const requiredFields = new Set(['mykey.py apibase', 'mykey.py apikey', 'mykey.py model']);
      const configuredFields = new Set(
        fields.filter((field) => field.configured && requiredFields.has(String(field.envName || '')))
          .map((field) => String(field.envName || '')),
      );
      return Array.from(requiredFields).every((envName) => configuredFields.has(envName));
    }

    function settingsCategoryDisplayLabel(label) {
      return label === 'AI8video' ? 'Multi-Agent' : label;
    }

    function selectMultiAgentSettingsRole(roleKey, focusTab = false) {
      const validItems = [...multiAgentRoleDefinitions, ...multiAgentConfigDefinitions];
      if (!validItems.some((item) => item.key === roleKey)) return;
      state.settingsModal.activeAgentRole = roleKey;
      renderSettingsModal();
      if (!focusTab) return;
      requestAnimationFrame(() => document.getElementById(`multi-agent-role-tab-${roleKey}`)?.focus());
    }

    document.addEventListener('click', (event) => {
      const trigger = event.target.closest('[data-agent-settings-role]');
      if (!trigger) return;
      event.preventDefault();
      selectMultiAgentSettingsRole(trigger.getAttribute('data-agent-settings-role') || 'overview');
    });

    document.addEventListener('keydown', (event) => {
      const trigger = event.target.closest('.multi-agent-nav-item[role="tab"]');
      if (!trigger) return;
      const keys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'];
      if (!keys.includes(event.key)) return;
      const root = trigger.closest('.multi-agent-nav');
      const tabs = Array.from(root?.querySelectorAll('.multi-agent-nav-item[role="tab"]') || []);
      if (!tabs.length) return;
      event.preventDefault();
      const currentIndex = Math.max(0, tabs.indexOf(trigger));
      const delta = (event.key === 'ArrowDown' || event.key === 'ArrowRight') ? 1 : -1;
      const nextIndex = event.key === 'Home' ? 0
        : event.key === 'End' ? tabs.length - 1
          : (currentIndex + delta + tabs.length) % tabs.length;
      selectMultiAgentSettingsRole(tabs[nextIndex].getAttribute('data-agent-settings-role') || 'overview', true);
    });

    const settingsCategoryOrder = ['运行模式', 'TTS', 'AI8video', '文本/视频规划模型', '多模态模型', '图片模型', '视频模型', '图床', 'HTML 动效', '归档', '其他'];
    const settingsCategoryAliasMap = {};
