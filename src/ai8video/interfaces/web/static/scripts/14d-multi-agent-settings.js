    const agentArchitectureSections = [
      { key: 'overview', label: '总览' },
      { key: 'main-agent', label: 'Main Agent' },
      { key: 'composite-tools', label: '复合工具' },
      { key: 'runtime', label: 'Runtime' },
      { key: 'standard-mode', label: '标准模式' },
      { key: 'special-capabilities', label: '专项能力' },
    ];
    const agentArchitectureConfigSections = [
      { key: 'model-binding', label: '模型绑定' },
    ];
    const agentCompositeToolDetails = {
      prepare_video_plan: {
        title: '准备视频方案',
        description: '把用户目标解析成可追踪的视频计划，复用现有规划流水线。',
        tag: '规划',
        tone: 'runtime',
      },
      review_video_plan: {
        title: '审核视频方案',
        description: '审核并修正待生成方案，在提交生成前形成明确结论。',
        tag: '审核',
        tone: 'runtime',
      },
      generate_video_batch: {
        title: '提交批量生成',
        description: '按用户明确数量提交生成；额外付费重试必须先取得批准。',
        tag: '成本动作',
        tone: 'cost',
      },
      inspect_generation_result: {
        title: '检查生成结果',
        description: '读取终态观察，区分成功、失败与部分成功，不负责持续轮询。',
        tag: '终态',
        tone: 'runtime',
      },
      archive_and_deliver: {
        title: '归档并交付',
        description: '整理已确认结果并交付，不自动发布到外部平台。',
        tag: '交付动作',
        tone: 'delivery',
      },
      task_user: {
        title: '询问用户',
        description: '遇到实质歧义、额外成本或部分成功取舍时暂停并等待用户。',
        tag: '暂停等待',
        tone: 'wait',
      },
    };
    const agentSpecialCapabilityDefinitions = [
      {
        agentId: 'knowledge-base',
        title: '知识建树',
        description: '把单份文档规划为可检索的知识树，并由程序确定性提取原文。',
        boundary: '属于知识入库功能，不参与主对话调度。',
      },
      {
        agentId: 'reviewer',
        title: '知识审核',
        description: '审核知识叶子的原子性、覆盖度、层级和检索价值。',
        boundary: '只审核知识入库，不代表 Agent 模式的视频方案审核。',
      },
      {
        agentId: 'viral-shot-language',
        title: '镜头语言分析',
        description: '从代表帧和识别台词中提取构图、动作、节奏与视觉钩子。',
        boundary: '属于爆款拆解功能，不触发视频生成。',
      },
      {
        agentId: 'viral-script-reconstruction',
        title: '剧本重建',
        description: '融合镜头语言证据与识别台词，恢复可复用的剧本框架。',
        boundary: '只消费已有拆解结果，不改原视频或主对话状态。',
      },
    ];
    const agentModelBindingCategories = [
      { key: 'llm', label: '文本规划' },
      { key: 'multimodal', label: '多模态' },
      { key: 'image', label: '图片' },
      { key: 'video', label: '视频' },
    ];

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
        return buildAgentArchitectureSettingsMarkup(group, labelledBy);
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
        return buildModelSettingsPanelMarkup(groups, group, labelledBy);
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

    function currentAgentArchitecture() {
      const payload = state.authSettings?.agentArchitecture || {};
      const compositeTools = Array.isArray(payload.compositeTools) && payload.compositeTools.length
        ? payload.compositeTools.map((name) => String(name || '').trim()).filter(Boolean)
        : Object.keys(agentCompositeToolDetails);
      return {
        enabled: payload.enabled !== false && state.agentModeEnabled !== false,
        controller: String(payload.controller || 'AI8VideoMainAgent'),
        decisionPolicy: String(payload.decisionPolicy || 'key_nodes'),
        runtimeOwner: String(payload.runtimeOwner || 'python'),
        compositeTools,
        standardMode: payload.standardMode || {
          controller: 'AI8VideoConversationController',
          isolated: true,
        },
        modelBinding: payload.modelBinding || {
          strategy: 'first_message_snapshot',
          source: 'model_profiles',
        },
      };
    }

    function buildAgentArchitectureSettingsMarkup(group, labelledBy) {
      const activeSection = resolveActiveAgentSettingsSection();
      return `
        <section id="settings-category-panel" class="multi-agent-settings agent-architecture-settings" role="tabpanel" aria-labelledby="${labelledBy}">
          <nav class="multi-agent-nav agent-architecture-nav" aria-label="Agent 架构">
            <div class="multi-agent-nav-group" role="tablist" aria-label="运行架构">
              ${agentArchitectureSections.map((section) => buildAgentArchitectureNavItemMarkup(section, activeSection)).join('')}
            </div>
            <div class="multi-agent-nav-group is-config" role="tablist" aria-label="绑定配置">
              ${agentArchitectureConfigSections.map((section) => buildAgentArchitectureNavItemMarkup(section, activeSection, true)).join('')}
            </div>
          </nav>
          <div id="agent-settings-section-panel" class="multi-agent-panel agent-architecture-panel" role="tabpanel" aria-labelledby="agent-settings-section-tab-${escapeHtml(activeSection)}" tabindex="0">
            ${buildAgentArchitecturePanel(activeSection, group)}
          </div>
        </section>
      `;
    }

    function buildAgentArchitectureNavItemMarkup(section, activeSection, isConfig = false) {
      const active = section.key === activeSection;
      const status = agentArchitectureSectionStatus(section.key);
      const className = `multi-agent-nav-item${isConfig ? ' is-config' : ''}${active ? ' active' : ''}`;
      return `
        <button type="button" id="agent-settings-section-tab-${escapeHtml(section.key)}" class="${className}" data-agent-settings-section="${escapeHtml(section.key)}" role="tab" aria-selected="${active ? 'true' : 'false'}" aria-controls="agent-settings-section-panel" tabindex="${active ? '0' : '-1'}">
          <span>${escapeHtml(section.label)}</span>
          ${status ? `<span class="multi-agent-nav-status is-${escapeHtml(status.tone)}" title="${escapeHtml(status.label)}" aria-label="${escapeHtml(status.label)}"></span>` : ''}
        </button>
      `;
    }

    function agentArchitectureSectionStatus(sectionKey) {
      const architecture = currentAgentArchitecture();
      if (sectionKey === 'overview') return null;
      if (sectionKey === 'main-agent') {
        return architecture.enabled
          ? { label: 'Agent 模式已启用', tone: 'live' }
          : { label: 'Agent 模式已关闭', tone: 'shadow' };
      }
      if (sectionKey === 'composite-tools') {
        return architecture.compositeTools.length
          ? { label: `${architecture.compositeTools.length} 个复合工具`, tone: 'live' }
          : { label: '没有可用工具', tone: 'shadow' };
      }
      if (sectionKey === 'runtime') {
        return { label: '确定性执行层', tone: architecture.enabled ? 'live' : 'neutral' };
      }
      if (sectionKey === 'standard-mode') {
        return { label: '原有工作流保持独立', tone: 'neutral' };
      }
      if (sectionKey === 'special-capabilities') {
        const count = agentSpecialCapabilityDefinitions.filter((item) => enabledAgentSkills(item.agentId).length).length;
        return count
          ? { label: `${count} 项专项能力`, tone: 'live' }
          : { label: '专项能力未启用', tone: 'neutral' };
      }
      if (sectionKey === 'model-binding') {
        const summaries = agentModelProfileSummaries();
        const readyCount = summaries.filter((item) => item.ready).length;
        return {
          label: `${readyCount}/${summaries.length} 类模型就绪`,
          tone: readyCount === summaries.length ? 'live' : 'shadow',
        };
      }
      return null;
    }

    function buildAgentArchitecturePanel(activeSection, group) {
      if (activeSection === 'main-agent') return buildMainAgentPanel();
      if (activeSection === 'composite-tools') return buildAgentCompositeToolsPanel();
      if (activeSection === 'runtime') return buildAgentRuntimePanel();
      if (activeSection === 'standard-mode') return buildStandardModePanel();
      if (activeSection === 'special-capabilities') return buildAgentSpecialCapabilitiesPanel();
      if (activeSection === 'model-binding') return buildAgentModelBindingPanel(group);
      return buildAgentArchitectureOverviewPanel();
    }

    function agentStatusMarkup(label, tone = 'live') {
      return `<span class="multi-agent-role-status is-${escapeHtml(tone)}">${escapeHtml(label)}</span>`;
    }

    function buildAgentArchitectureOverviewPanel() {
      const architecture = currentAgentArchitecture();
      const flow = [
        {
          section: 'main-agent',
          title: 'Main Agent 决策',
          description: '只在规划、审核、终态、确认和交付等关键节点选择下一步。',
          status: architecture.enabled ? '已启用' : '已关闭',
          tone: architecture.enabled ? 'live' : 'shadow',
        },
        {
          section: 'composite-tools',
          title: '调用复合工具',
          description: '通过类型化高层动作进入既有能力，不直接操作文件、网络或底层模型。',
          status: `${architecture.compositeTools.length} 个工具`,
          tone: 'live',
        },
        {
          section: 'runtime',
          title: 'Runtime 确定性执行',
          description: '负责提交、轮询、下载、后处理、恢复与归档，运行中不反复调用模型。',
          status: '确定性',
          tone: 'neutral',
        },
        {
          section: 'runtime',
          title: '关键事件重新唤醒',
          description: '只有审核结论、用户输入、生成终态或交付结果才重新交给 Main Agent。',
          status: '事件驱动',
          tone: 'live',
        },
      ];
      return `
        <div class="multi-agent-overview agent-architecture-overview">
          <header class="agent-overview-head">
            <div>
              <span class="agent-overview-eyebrow">当前运行架构</span>
              <h3>单一 Main Agent，关键节点决策</h3>
              <p>Agent 负责判断下一步，Runtime 负责把已决定的动作稳定执行完成。</p>
            </div>
            ${agentStatusMarkup(architecture.enabled ? 'Agent 模式可用' : 'Agent 模式已关闭', architecture.enabled ? 'live' : 'shadow')}
          </header>
          <div class="agent-summary-pills" aria-label="架构摘要">
            <span>Main Agent</span>
            <span>${escapeHtml(String(architecture.compositeTools.length))} 个复合工具</span>
            <span>关键节点决策</span>
            <span>标准模式隔离</span>
          </div>
          <ol class="multi-agent-flow agent-runtime-flow" aria-label="Agent 运行循环">
            ${flow.map((item, index) => `
              <li>
                <button type="button" class="multi-agent-flow-item" data-agent-settings-section="${escapeHtml(item.section)}">
                  <span class="multi-agent-flow-index" aria-hidden="true">${index + 1}</span>
                  <span class="multi-agent-flow-copy">
                    <strong>${escapeHtml(item.title)}</strong>
                    <span>${escapeHtml(item.description)}</span>
                  </span>
                  <span class="multi-agent-role-flags">${agentStatusMarkup(item.status, item.tone)}</span>
                </button>
              </li>
            `).join('')}
          </ol>
          <section class="agent-mode-boundary" aria-label="模式边界">
            <article class="agent-mode-card is-agent">
              <div class="agent-mode-card-head">
                <div><span>Agent 模式</span><strong>Main Agent + Runtime</strong></div>
                ${agentStatusMarkup('关键节点', 'live')}
              </div>
              <p>使用对话绑定模型和 Agent 运行账本，执行路径独立于标准模式。</p>
              <button type="button" data-agent-settings-section="main-agent">查看 Agent 边界</button>
            </article>
            <article class="agent-mode-card is-standard">
              <div class="agent-mode-card-head">
                <div><span>标准模式</span><strong>原有确定性工作流</strong></div>
                ${agentStatusMarkup('保持原样', 'neutral')}
              </div>
              <p>继续使用原来的会话控制器、意图判断和视频流水线，不被 Agent 改写。</p>
              <button type="button" data-agent-settings-section="standard-mode">查看模式边界</button>
            </article>
          </section>
          <p class="multi-agent-footnote">两种模式只共享媒体资源和配置来源；对话执行模式与运行状态分别维护。</p>
        </div>
      `;
    }

    function buildMainAgentPanel() {
      const architecture = currentAgentArchitecture();
      return `
        <div class="multi-agent-detail">
          <header class="multi-agent-detail-head">
            <div class="multi-agent-detail-copy">
              <div class="multi-agent-role-head">
                <div>
                  <span class="agent-overview-eyebrow">决策层</span>
                  <h3>AI8Video Main Agent</h3>
                </div>
                <span class="multi-agent-role-flags">${agentStatusMarkup(architecture.enabled ? '已启用' : '已关闭', architecture.enabled ? 'live' : 'shadow')}</span>
              </div>
              <p>围绕用户明确的视频目标做少量决策，每次只选择一个高层工具。</p>
            </div>
          </header>
          <div class="agent-fact-grid" aria-label="Main Agent 运行事实">
            <div class="agent-fact"><span>控制器</span><strong>${escapeHtml(architecture.controller)}</strong></div>
            <div class="agent-fact"><span>决策策略</span><strong>关键节点</strong></div>
            <div class="agent-fact"><span>高层工具</span><strong>${escapeHtml(String(architecture.compositeTools.length))} 个</strong></div>
          </div>
          <div class="multi-agent-detail-grid">
            <section class="multi-agent-detail-block">
              <h4>负责什么</h4>
              <ul>
                <li>理解用户目标并选择下一项高层动作。</li>
                <li>根据审核结论、生成终态和交付状态重新决策。</li>
                <li>在实质歧义、额外成本或部分成功取舍时询问用户。</li>
              </ul>
            </section>
            <section class="multi-agent-detail-block">
              <h4>明确边界</h4>
              <ul>
                <li>不直接访问文件、Shell、网络或外部平台。</li>
                <li>不负责后台轮询、下载和后处理。</li>
                <li>不增加用户未要求的数量、付费重试或外部发布。</li>
              </ul>
            </section>
          </div>
          <p class="multi-agent-footnote">工具状态和 Python 运行账本是事实真值；模型回复不能覆盖未确认的业务状态。</p>
        </div>
      `;
    }

    function buildAgentCompositeToolsPanel() {
      const architecture = currentAgentArchitecture();
      return `
        <div class="multi-agent-detail">
          <header class="multi-agent-detail-head">
            <div class="multi-agent-detail-copy">
              <div class="multi-agent-role-head">
                <div>
                  <span class="agent-overview-eyebrow">高层动作</span>
                  <h3>复合工具</h3>
                </div>
                <span class="multi-agent-role-flags">${agentStatusMarkup(`${architecture.compositeTools.length} 个已接入`, architecture.compositeTools.length ? 'live' : 'shadow')}</span>
              </div>
              <p>Main Agent 只调用这些受控入口；每个入口内部复用现有确定性能力。</p>
            </div>
          </header>
          <ol class="agent-tool-grid" aria-label="Main Agent 复合工具">
            ${architecture.compositeTools.map((toolName, index) => {
              const details = agentCompositeToolDetails[toolName] || {
                title: toolName,
                description: '由 Runtime 提供的受控高层动作。',
                tag: '工具',
                tone: 'runtime',
              };
              return `
                <li class="agent-tool-card">
                  <div class="agent-tool-card-head">
                    <span class="agent-tool-index" aria-hidden="true">${index + 1}</span>
                    <span class="agent-tool-tag is-${escapeHtml(details.tone)}">${escapeHtml(details.tag)}</span>
                  </div>
                  <strong>${escapeHtml(details.title)}</strong>
                  <code>${escapeHtml(toolName)}</code>
                  <p>${escapeHtml(details.description)}</p>
                </li>
              `;
            }).join('')}
          </ol>
          <p class="multi-agent-footnote">工具列表来自当前后端架构数据；页面不再把 Planner、Reviewer 等内部能力伪装成并列自治 Agent。</p>
        </div>
      `;
    }

    function buildAgentRuntimePanel() {
      return `
        <div class="multi-agent-detail">
          <header class="multi-agent-detail-head">
            <div class="multi-agent-detail-copy">
              <div class="multi-agent-role-head">
                <div>
                  <span class="agent-overview-eyebrow">执行层</span>
                  <h3>Runtime</h3>
                </div>
                <span class="multi-agent-role-flags">${agentStatusMarkup('确定性执行', 'neutral')}</span>
              </div>
              <p>接管长耗时和可重复的执行工作，让 Main Agent 在 pending 期间保持静默。</p>
            </div>
          </header>
          <div class="multi-agent-detail-grid">
            <section class="multi-agent-detail-block">
              <h4>执行职责</h4>
              <ul>
                <li>提交生成任务并维护真实运行状态。</li>
                <li>后台轮询、下载、后处理、恢复和归档。</li>
                <li>把成功、失败和部分成功整理为结构化终态观察。</li>
              </ul>
            </section>
            <section class="multi-agent-detail-block">
              <h4>运行边界</h4>
              <ul>
                <li>pending 期间不反复请求模型决定是否继续轮询。</li>
                <li>不擅自扩大生成数量、重试次数或外部副作用。</li>
                <li>所有状态变更写入 Python 会话和任务账本。</li>
              </ul>
            </section>
          </div>
          <section class="agent-runtime-events" aria-label="重新决策事件">
            <h4>这些事件才会重新交给 Main Agent</h4>
            <div>
              <span>方案审核结论</span>
              <span>用户确认或补充</span>
              <span>生成终态成功</span>
              <span>终态失败或部分成功</span>
              <span>归档与交付结果</span>
            </div>
          </section>
        </div>
      `;
    }

    function buildStandardModePanel() {
      const architecture = currentAgentArchitecture();
      const standardController = String(architecture.standardMode?.controller || 'AI8VideoConversationController');
      return `
        <div class="multi-agent-detail">
          <header class="multi-agent-detail-head">
            <div class="multi-agent-detail-copy">
              <div class="multi-agent-role-head">
                <div>
                  <span class="agent-overview-eyebrow">独立执行入口</span>
                  <h3>标准模式保持原有工作流</h3>
                </div>
                <span class="multi-agent-role-flags">${agentStatusMarkup('保持原样', 'neutral')}</span>
              </div>
              <p>Agent 升级不会重写标准模式的对话控制、意图判断或视频流水线。</p>
            </div>
          </header>
          <div class="agent-mode-comparison">
            <article>
              <span>标准模式</span>
              <strong>${escapeHtml(standardController)}</strong>
              <ul>
                <li>沿用原有 IntentAgent 和确定性 Pipeline。</li>
                <li>继续使用原来的确认卡、分集和生成流程。</li>
                <li>不会创建或复用 Agent Run。</li>
              </ul>
            </article>
            <article>
              <span>Agent 模式</span>
              <strong>AI8VideoMainAgent</strong>
              <ul>
                <li>使用 Main Agent、复合工具和 Agent Run。</li>
                <li>只在关键业务节点重新决策。</li>
                <li>Runtime 执行长耗时确定性任务。</li>
              </ul>
            </article>
          </div>
          <section class="agent-shared-boundary">
            <div>
              <span>共享范围</span>
              <strong>媒体资源与配置来源</strong>
            </div>
            <p>图片、脚本、生成结果、回收站和模型配置可以共享；对话模式、消息状态和运行账本互不覆盖。</p>
          </section>
          <p class="multi-agent-footnote">对话开始执行后模式会锁定；切换新建模式只影响下一次新建的对话。</p>
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

    function buildAgentSpecialCapabilitiesPanel() {
      return `
        <div class="multi-agent-detail">
          <header class="multi-agent-detail-head">
            <div class="multi-agent-detail-copy">
              <div class="multi-agent-role-head">
                <div>
                  <span class="agent-overview-eyebrow">功能域能力</span>
                  <h3>专项能力</h3>
                </div>
                <span class="multi-agent-role-flags">${agentStatusMarkup('不属于主调度链', 'neutral')}</span>
              </div>
              <p>知识入库和爆款拆解中的模型能力独立展示，不再冒充 Main Agent 的并列同事。</p>
            </div>
          </header>
          <div class="agent-capability-grid">
            ${agentSpecialCapabilityDefinitions.map((capability) => {
              const skills = enabledAgentSkills(capability.agentId);
              const runtimeActive = skills.some((skill) => !!skill.runtimeActive);
              const status = skills.length
                ? (runtimeActive ? '执行能力' : '专项 Skill')
                : '未启用';
              const tone = skills.length ? 'live' : 'neutral';
              return `
                <article class="agent-capability-card">
                  <header>
                    <div>
                      <strong>${escapeHtml(capability.title)}</strong>
                      <span>${escapeHtml(capability.agentId)}</span>
                    </div>
                    ${agentStatusMarkup(status, tone)}
                  </header>
                  <p>${escapeHtml(capability.description)}</p>
                  ${skills.length ? `
                    <ul>
                      ${skills.map((skill) => `
                        <li>
                          <code>${escapeHtml(skill.name || '')}</code>
                          <span>${escapeHtml(skill.description || '暂无说明')}</span>
                        </li>
                      `).join('')}
                    </ul>
                  ` : '<p class="agent-capability-empty">当前没有启用的 Skill。</p>'}
                  <small>${escapeHtml(capability.boundary)}</small>
                </article>
              `;
            }).join('')}
          </div>
          <p class="multi-agent-footnote">内置 Skill 仍由原功能模块使用；本页只纠正它们在产品架构中的层级和命名。</p>
        </div>
      `;
    }

    function agentModelProfileSummaries() {
      const profiles = state.authSettings?.modelProfiles || {};
      return agentModelBindingCategories.map((category) => {
        const bucket = profiles?.[category.key] || {};
        const items = Array.isArray(bucket.profiles) ? bucket.profiles : [];
        const active = items.find((item) => item?.id === bucket.activeId) || null;
        const ready = !!(
          active
          && String(active.model || '').trim()
          && String(active.baseUrl || '').trim()
          && active.hasApiKey
        );
        return { ...category, active, ready };
      });
    }

    function buildAgentModelBindingPanel(group) {
      const architecture = currentAgentArchitecture();
      const summaries = agentModelProfileSummaries();
      const readyCount = summaries.filter((item) => item.ready).length;
      return `
        <div class="multi-agent-detail agent-model-binding-detail">
          <header class="multi-agent-detail-head">
            <div class="multi-agent-detail-copy">
              <div class="multi-agent-role-head">
                <div>
                  <span class="agent-overview-eyebrow">对话模型快照</span>
                  <h3>模型绑定</h3>
                </div>
                <span class="multi-agent-role-flags">${agentStatusMarkup(`${readyCount}/${summaries.length} 类就绪`, readyCount === summaries.length ? 'live' : 'shadow')}</span>
              </div>
              <p>对话在第一条消息提交时固化当前模型配置快照；之后切换全局当前配置，不会改写已运行对话。</p>
            </div>
          </header>
          <div class="agent-model-profile-grid" aria-label="当前模型配置">
            ${summaries.map((item) => `
              <article class="agent-model-profile-card${item.ready ? ' is-ready' : ''}">
                <div>
                  <span>${escapeHtml(item.label)}</span>
                  ${agentStatusMarkup(item.ready ? '已就绪' : '待补齐', item.ready ? 'live' : 'shadow')}
                </div>
                <strong>${escapeHtml(item.active?.name || '未启用配置')}</strong>
                <code>${escapeHtml(item.active?.model || '尚未填写模型')}</code>
              </article>
            `).join('')}
          </div>
          <div class="agent-model-actions">
            <div>
              <strong>模型连接在“模型设置”统一维护</strong>
              <span>标准模式与 Agent 模式可以读取同一配置来源，但各自保存独立的对话运行状态。</span>
            </div>
            <button type="button" class="settings-action-button" data-settings-category="模型设置">打开模型设置</button>
          </div>
          <section class="settings-panel is-embedded agent-legacy-model-settings" aria-label="兼容模型回退">
            <header class="settings-panel-head">
              <div class="agent-legacy-model-title">
                <h3 class="settings-section-title">兼容模型回退</h3>
                <p>保留 mykey.py 作为旧配置回退；它不代表所有能力共享同一个自治 Agent 模型。</p>
              </div>
              <span class="settings-archive-total">${escapeHtml(architecture.modelBinding?.source || 'model_profiles')}</span>
            </header>
            <div class="settings-row-list">
              ${group.fields.map((field) => buildSettingsRowMarkup(field)).join('')}
            </div>
          </section>
        </div>
      `;
    }

    function resolveActiveAgentSettingsSection() {
      const validSections = [...agentArchitectureSections, ...agentArchitectureConfigSections].map((item) => item.key);
      const activeSection = String(state.settingsModal.activeAgentSection || 'overview');
      if (validSections.includes(activeSection)) return activeSection;
      state.settingsModal.activeAgentSection = 'overview';
      return 'overview';
    }

    function settingsCategoryDisplayLabel(label) {
      return label === 'AI8video' ? 'Agent 架构' : label;
    }

    function selectAgentSettingsSection(sectionKey, focusTab = false) {
      const validItems = [...agentArchitectureSections, ...agentArchitectureConfigSections];
      if (!validItems.some((item) => item.key === sectionKey)) return;
      state.settingsModal.activeAgentSection = sectionKey;
      renderSettingsModal();
      if (!focusTab) return;
      requestAnimationFrame(() => document.getElementById(`agent-settings-section-tab-${sectionKey}`)?.focus());
    }

    document.addEventListener('click', (event) => {
      const trigger = event.target.closest('[data-agent-settings-section]');
      if (!trigger) return;
      event.preventDefault();
      selectAgentSettingsSection(trigger.getAttribute('data-agent-settings-section') || 'overview');
    });

    document.addEventListener('keydown', (event) => {
      const trigger = event.target.closest('.agent-architecture-nav .multi-agent-nav-item[role="tab"][data-agent-settings-section]');
      if (!trigger) return;
      const keys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'];
      if (!keys.includes(event.key)) return;
      const root = trigger.closest('.agent-architecture-nav');
      const tabs = Array.from(root?.querySelectorAll('.multi-agent-nav-item[role="tab"][data-agent-settings-section]') || []);
      if (!tabs.length) return;
      event.preventDefault();
      const currentIndex = Math.max(0, tabs.indexOf(trigger));
      const delta = (event.key === 'ArrowDown' || event.key === 'ArrowRight') ? 1 : -1;
      const nextIndex = event.key === 'Home' ? 0
        : event.key === 'End' ? tabs.length - 1
          : (currentIndex + delta + tabs.length) % tabs.length;
      selectAgentSettingsSection(tabs[nextIndex].getAttribute('data-agent-settings-section') || 'overview', true);
    });

    const settingsCategoryOrder = ['运行模式', 'TTS', 'AI8video', '文本/视频规划模型', '多模态模型', '图片模型', '视频模型', '图床', 'HTML 动效', '归档', '其他'];
    const settingsCategoryAliasMap = {};
