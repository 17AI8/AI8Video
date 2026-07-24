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
          '维护根任务、子任务、租约、恢复与终态保护',
          '让取消请求、后台状态与界面进度保持一致',
        ],
        boundary: '不推断或重写用户要求的风格、数量、时长。',
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
        description: '已接管知识入库语义审核；媒体审核仍处于影子模式。',
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
        key: 'shared-model',
        label: '共享模型',
        mark: 'M',
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
      return `
        <div id="settings-category-panel" class="settings-grid" role="tabpanel" aria-labelledby="${labelledBy}">
          <section class="settings-section">
            <div class="settings-section-head">
              <div class="settings-section-title">${escapeHtml(settingsCategoryDisplayLabel(group.label))}</div>
              ${group.label === '归档' ? `
                <div class="settings-section-actions">
                  <button type="button" class="settings-section-refresh" data-refresh-archive-settings ${state.settingsModal.refreshingArchive || state.settingsModal.cleaningArchiveAll ? 'disabled' : ''}>${state.settingsModal.refreshingArchive ? '刷新中' : '刷新'}</button>
                  <span class="settings-archive-total">总占用 ${escapeHtml(archiveTotal)}</span>
                  <button type="button" class="settings-section-cleanup" data-cleanup-archive-all ${state.settingsModal.cleaningArchiveAll ? 'disabled' : ''}>${state.settingsModal.cleaningArchiveAll ? '清理中' : '一键清理'}</button>
                </div>
              ` : ''}
            </div>
            ${group.fields.map((field) => buildSettingsRowMarkup(field)).join('')}
          </section>
        </div>
      `;
    }

    function buildMultiAgentSettingsMarkup(group, labelledBy) {
      const activeRole = resolveActiveMultiAgentRole();
      const sharedModelReady = isMultiAgentSharedModelConfigured(group.fields);
      return `
        <section id="settings-category-panel" class="multi-agent-settings" role="tabpanel" aria-labelledby="${labelledBy}">
          <div class="multi-agent-hero">
            <div class="multi-agent-hero-copy">
              <div class="multi-agent-eyebrow">AGENT ORCHESTRATION</div>
              <h3 class="multi-agent-title">Multi-Agent 协作设置</h3>
              <p class="multi-agent-summary">Planner 已接入智能分集；知识库 Agent 与 Reviewer 已接入知识入库闭环，Reviewer 的媒体审核能力仍处于影子模式。</p>
            </div>
            <div class="multi-agent-badges" aria-label="当前实现状态">
              <span class="multi-agent-badge is-live">调度基座 · 已接入</span>
              <span class="multi-agent-badge is-live">知识库 Agent · 已接入</span>
              <span class="multi-agent-badge is-live">Reviewer · 知识库已接入</span>
              <span class="multi-agent-badge is-live">Planner · 智能分集已接入</span>
              <span class="multi-agent-badge is-shadow">Reviewer · 媒体审核影子模式</span>
              <span class="multi-agent-badge">共享模型 · ${sharedModelReady ? '已配置' : '待补齐'}</span>
            </div>
          </div>
          ${buildMultiAgentRoleTabsMarkup(activeRole)}
          <div id="multi-agent-role-panel" class="multi-agent-panel" role="tabpanel" aria-labelledby="multi-agent-role-tab-${escapeHtml(activeRole)}" tabindex="0">
            ${buildMultiAgentRolePanel(activeRole, group, sharedModelReady)}
          </div>
        </section>
      `;
    }

    function buildMultiAgentRoleTabsMarkup(activeRole) {
      return `
        <div class="multi-agent-tabs" role="tablist" aria-label="Multi-Agent 角色设置">
          ${multiAgentRoleDefinitions.map((role) => {
            const active = role.key === activeRole;
            return `<button type="button" id="multi-agent-role-tab-${escapeHtml(role.key)}" class="multi-agent-tab${active ? ' active' : ''}" data-agent-settings-role="${escapeHtml(role.key)}" role="tab" aria-selected="${active ? 'true' : 'false'}" aria-controls="multi-agent-role-panel" tabindex="${active ? '0' : '-1'}">${escapeHtml(role.label)}</button>`;
          }).join('')}
        </div>
      `;
    }

    function buildMultiAgentRolePanel(activeRole, group, sharedModelReady) {
      if (activeRole === 'shared-model') {
        return buildMultiAgentSharedModelPanel(group, sharedModelReady);
      }
      if (activeRole === 'overview') {
        return buildMultiAgentOverviewPanel(sharedModelReady);
      }
      const role = multiAgentRoleDefinitions.find((item) => item.key === activeRole);
      return role ? buildMultiAgentDetailPanel(role) : buildMultiAgentOverviewPanel(sharedModelReady);
    }

    function buildMultiAgentOverviewPanel(sharedModelReady) {
      const roles = multiAgentRoleDefinitions.filter((role) => role.key !== 'overview');
      return `
        <div class="multi-agent-role-grid">
          ${roles.map((role) => {
            const shared = role.key === 'shared-model';
            const status = shared ? (sharedModelReady ? '已配置' : '待补齐') : role.status;
            const tone = shared ? (sharedModelReady ? 'live' : 'shadow') : role.tone;
            const description = shared ? '当前所有角色继续复用同一套 AI8video 模型鉴权。' : role.description;
            return `
              <button type="button" class="multi-agent-role-card" data-agent-settings-role="${escapeHtml(role.key)}" aria-label="查看 ${escapeHtml(role.label)} 设置">
                <span class="multi-agent-role-mark" aria-hidden="true">${escapeHtml(role.mark)}</span>
                <span class="multi-agent-role-copy">
                  <span class="multi-agent-role-head"><strong>${escapeHtml(role.label)}</strong><span class="multi-agent-role-status is-${escapeHtml(tone)}">${escapeHtml(status)}</span></span>
                  <span class="multi-agent-role-desc">${escapeHtml(description)}</span>
                </span>
              </button>
            `;
          }).join('')}
        </div>
        <aside class="multi-agent-boundary">
          <strong>当前施工边界</strong>
          <span>角色页先把职责、状态和共享配置讲清楚；尚未接通的 Agent 不提供假开关，也不会悄悄改变用户期望风格。</span>
        </aside>
      `;
    }

    function buildMultiAgentDetailPanel(role) {
      return `
        <div class="multi-agent-detail">
          <div class="multi-agent-detail-heading">
            <span class="multi-agent-role-mark" aria-hidden="true">${escapeHtml(role.mark)}</span>
            <div><div class="multi-agent-role-head"><strong>${escapeHtml(role.label)}</strong><span class="multi-agent-role-status is-${escapeHtml(role.tone)}">${escapeHtml(role.status)}</span></div><p>${escapeHtml(role.description)}</p></div>
          </div>
          <div class="multi-agent-detail-grid">
            <section class="multi-agent-detail-block"><strong>当前职责</strong><ul>${role.responsibilities.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul></section>
            <section class="multi-agent-detail-block"><strong>行为边界</strong><p>${escapeHtml(role.boundary)}</p></section>
          </div>
        </div>
      `;
    }

    function buildMultiAgentSharedModelPanel(group, sharedModelReady) {
      return `
        <div class="multi-agent-shared-note">
          <div><strong>共享核心模型</strong><p>Planner、知识库 Agent 与 Reviewer 当前复用 AI8video 核心模型；Supervisor 不调用模型。</p></div>
          <span class="multi-agent-role-status is-${sharedModelReady ? 'live' : 'shadow'}">${sharedModelReady ? '配置完整' : '等待补齐'}</span>
        </div>
        <div class="settings-grid">
          <section class="settings-section">
            <div class="settings-section-head"><div class="settings-section-title">连接与模型</div></div>
            ${group.fields.map((field) => buildSettingsRowMarkup(field)).join('')}
          </section>
        </div>
      `;
    }

    function resolveActiveMultiAgentRole() {
      const validRoles = multiAgentRoleDefinitions.map((role) => role.key);
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
      if (!multiAgentRoleDefinitions.some((role) => role.key === roleKey)) return;
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
      const trigger = event.target.closest('.multi-agent-tab[role="tab"]');
      if (!trigger || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const tabs = Array.from(trigger.parentElement?.querySelectorAll('.multi-agent-tab[role="tab"]') || []);
      if (!tabs.length) return;
      event.preventDefault();
      const currentIndex = Math.max(0, tabs.indexOf(trigger));
      const nextIndex = event.key === 'Home' ? 0
        : event.key === 'End' ? tabs.length - 1
          : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      selectMultiAgentSettingsRole(tabs[nextIndex].getAttribute('data-agent-settings-role') || 'overview', true);
    });

    const settingsCategoryOrder = ['运行模式', 'TTS', 'AI8video', '文本/视频规划模型', '多模态模型', '图片模型', '视频模型', 'HTML 动效', '归档', '其他'];
    const settingsCategoryAliasMap = {};
