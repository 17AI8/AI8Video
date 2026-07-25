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
        status: '已接入',
        tone: 'live',
        description: '负责智能分集与视频任务规划。',
        responsibilities: [
          '理解全文并判断合理的分集数量与内容边界',
          '为每集生成独立主题、提示词与可追踪规划结果',
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
    ];
    const multiAgentConfigDefinitions = [
      {
        key: 'shared-model',
        label: '共享模型',
      },
    ];

    function renderSettingsModal() {
      if (!els.settingsModal) return;
      const visible = !!state.settingsModal.visible;
      els.settingsModal.classList.toggle('hidden', !visible);
      if (!visible) return;
      const settings = state.authSettings || {};
      const videoSettings = state.videoModelSettings || {};
      const fields = Array.isArray(settings.fields) ? settings.fields : [];
      const groups = groupSettingsFields(fields);
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
      return `
        <div class="settings-tabs" role="tablist" aria-label="设置分类">
          ${groups.map((group, index) => {
            const active = group.label === activeCategory;
            return `
              <button type="button" id="settings-category-tab-${index}" class="settings-tab${active ? ' active' : ''}" data-settings-category="${escapeHtml(group.label)}" role="tab" aria-selected="${active ? 'true' : 'false'}" aria-controls="settings-category-panel" tabindex="${active ? '0' : '-1'}">
                ${escapeHtml(settingsCategoryDisplayLabel(group.label))}
              </button>
            `;
          }).join('')}
        </div>
      `;
    }

    function buildAuthSettingsMarkup(groups, activeCategory) {
      if (!groups.length) {
        return '<div class="empty">当前没有可显示的鉴权信息。</div>';
      }
      const group = groups.find((item) => item.label === activeCategory) || groups[0];
      const activeIndex = Math.max(0, groups.indexOf(group));
      const labelledBy = `settings-category-tab-${activeIndex}`;
      if (group.label === 'AI8video') {
        return buildMultiAgentSettingsMarkup(group, labelledBy);
      }
      const archiveArtifacts = state.archiveArtifacts || state.authSettings?.archiveArtifacts || {};
      const archiveTotal = String(archiveArtifacts.totalDisplay || '0 B');
      const fieldCount = group.fields.length;
      const isArchive = group.label === '归档';
      const density = fieldCount <= 2 ? 'is-compact' : fieldCount <= 5 ? 'is-cozy' : '';
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
          <p class="multi-agent-lede">意图路由、运行态、智能分集与知识入库已闭环；媒体审核仍为影子模式。</p>
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
          <p class="multi-agent-footnote">模型与鉴权在对应设置页配置；此处只看职责与接入状态。</p>
        </div>
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

    const settingsCategoryOrder = ['运行模式', 'TTS', 'AI8video', '文本/视频规划模型', '多模态模型', '图片模型', '视频模型', 'HTML 动效', '归档', '其他'];
    const settingsCategoryAliasMap = {};
