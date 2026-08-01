    function animateModelProfileDrawer(card, expanded) {
      const summary = card.querySelector('[data-toggle-model-profile]');
      const collapse = card.querySelector('.model-profile-collapse');
      const inner = card.querySelector('.model-profile-collapse-inner');
      const form = card.querySelector('[data-model-profile-form]');
      if (!collapse || !inner) return Promise.resolve();
      const startHeight = collapse.getBoundingClientRect().height;
      collapse.getAnimations().forEach((animation) => animation.cancel());
      inner.getAnimations().forEach((animation) => animation.cancel());
      if (expanded) {
        card.classList.remove('is-collapsing');
        card.classList.add('is-expanded');
      } else {
        card.classList.add('is-collapsing');
      }
      summary?.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      if (expanded) collapse.setAttribute('aria-hidden', 'false');
      if (form) {
        if (expanded) form.removeAttribute('inert');
      }
      const endHeight = expanded ? inner.scrollHeight : 0;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || typeof collapse.animate !== 'function') {
        card.classList.toggle('is-expanded', expanded);
        card.classList.remove('is-collapsing');
        if (form && !expanded) form.setAttribute('inert', '');
        collapse.setAttribute('aria-hidden', expanded ? 'false' : 'true');
        collapse.style.height = expanded ? 'auto' : '0px';
        collapse.style.opacity = '';
        inner.style.transform = '';
        return Promise.resolve();
      }
      collapse.style.height = `${startHeight}px`;
      const animation = collapse.animate([
        { height: `${startHeight}px`, opacity: startHeight > 0 ? 1 : 0 },
        { height: `${endHeight}px`, opacity: expanded ? 1 : 0 },
      ], {
        duration: 260,
        easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
        fill: 'forwards',
      });
      const innerAnimation = inner.animate([
        { transform: expanded ? `translateY(-${endHeight}px)` : 'translateY(0)' },
        { transform: expanded ? 'translateY(0)' : `translateY(-${startHeight}px)` },
      ], {
        duration: 260,
        easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
        fill: 'forwards',
      });
      return new Promise((resolve) => {
        animation.onfinish = () => {
          card.classList.toggle('is-expanded', expanded);
          card.classList.remove('is-collapsing');
          if (form && !expanded) form.setAttribute('inert', '');
          collapse.setAttribute('aria-hidden', expanded ? 'false' : 'true');
          collapse.style.height = expanded ? 'auto' : '0px';
          collapse.style.opacity = '';
          inner.style.transform = '';
          innerAnimation.cancel();
          animation.cancel();
          resolve();
        };
      });
    }

    async function resizeSettingsModalAfterDrawer(modal, lockedHeight, panel, lockedPanelHeight) {
      if (!modal) return;
      await new Promise((resolve) => setTimeout(resolve, 60));
      if (panel) panel.style.height = 'auto';
      modal.style.height = 'auto';
      const targetPanelHeight = panel?.getBoundingClientRect().height || 0;
      const targetHeight = modal.getBoundingClientRect().height;
      if (panel && lockedPanelHeight) panel.style.height = `${lockedPanelHeight}px`;
      modal.style.height = `${lockedHeight}px`;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        if (panel) {
          panel.style.height = '';
          panel.style.overflow = '';
        }
        modal.style.height = '';
        modal.style.overflow = '';
        return;
      }
      const options = { duration: 180, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' };
      const animations = [];
      if (Math.abs(targetHeight - lockedHeight) >= 1) {
        animations.push(modal.animate([
          { height: `${lockedHeight}px` },
          { height: `${targetHeight}px` },
        ], options));
      }
      if (panel && lockedPanelHeight && Math.abs(targetPanelHeight - lockedPanelHeight) >= 1) {
        animations.push(panel.animate([
          { height: `${lockedPanelHeight}px` },
          { height: `${targetPanelHeight}px` },
        ], options));
      }
      await Promise.all(animations.map((animation) => animation.finished.catch(() => {})));
      if (panel) {
        panel.style.height = '';
        panel.style.overflow = '';
      }
      modal.style.height = '';
      modal.style.overflow = '';
    }

    document.addEventListener('click', async (event) => {
      const forceCancelTrigger = event.target.closest('[data-force-cancel-session]');
      if (forceCancelTrigger) {
        event.preventDefault();
        const forceCancelIndex = forceCancelTrigger.hasAttribute('data-force-cancel-index')
          ? forceCancelTrigger.getAttribute('data-force-cancel-index')
          : undefined;
        await forceCancelPendingSession(
          forceCancelTrigger.getAttribute('data-force-cancel-session') || state.activeId,
          forceCancelIndex
        );
        return;
      }
      const progressTrigger = event.target.closest('[data-show-progress-modal]');
      if (progressTrigger) {
        event.preventDefault();
        openProgressModal();
        return;
      }
      const resultTrigger = event.target.closest('[data-show-result-modal]');
      if (resultTrigger) {
        event.preventDefault();
        openResultModal();
        return;
      }
      const batchMergeTrigger = event.target.closest('[data-toggle-agent-batch-merge]');
      if (batchMergeTrigger) {
        event.preventDefault();
        toggleAgentResultBatchMergeMode();
        return;
      }
      const agentBatchSelection = event.target.closest('.agent-video-results [data-result-batch-merge-select]');
      if (agentBatchSelection) {
        event.preventDefault();
        event.stopPropagation();
        toggleAgentResultBatchMergeSelection(agentBatchSelection.dataset.resultBatchMergeSelect);
        return;
      }
      const agentBatchConfirm = event.target.closest('[data-confirm-agent-batch-merge]');
      if (agentBatchConfirm) {
        event.preventDefault();
        await confirmAgentResultBatchMerge();
        return;
      }
      const fullscreenVideoTrigger = event.target.closest('[data-fullscreen-video]');
      if (fullscreenVideoTrigger) {
        event.preventDefault();
        const playlist = buildVideoPreviewPlaylist(fullscreenVideoTrigger);
        const playlistIndex = Math.max(0, playlist.findIndex((item) => item.trigger === fullscreenVideoTrigger));
        const currentItem = playlist[playlistIndex] || videoPreviewOptionsFromTrigger(fullscreenVideoTrigger);
        openVideoPreviewModal({
          ...currentItem,
          playlist,
          playlistIndex,
        });
        return;
      }
      const settingsTabTrigger = event.target.closest('[data-settings-category]');
      if (settingsTabTrigger) {
        event.preventDefault();
        const category = settingsTabTrigger.getAttribute('data-settings-category') || 'AI8video';
        state.settingsModal.activeCategory = category === '模型设置'
          ? state.settingsModal.activeModelCategory || '文本/视频规划模型'
          : category;
        state.settingsModal.videoModelError = '';
        state.settingsModal.videoModelNotice = '';
        state.settingsModal.videoInlinePanel = '';
        renderSettingsModal();
        return;
      }
      const modelSettingsTabTrigger = event.target.closest('[data-model-settings-category]');
      if (modelSettingsTabTrigger) {
        event.preventDefault();
        const category = modelSettingsTabTrigger.getAttribute('data-model-settings-category') || '文本/视频规划模型';
        state.settingsModal.activeCategory = category;
        state.settingsModal.activeModelCategory = category;
        state.settingsModal.videoModelError = '';
        state.settingsModal.videoModelNotice = '';
        state.settingsModal.videoInlinePanel = '';
        renderSettingsModal();
        return;
      }
      const switchModelProfileTrigger = event.target.closest('[data-switch-model-profile]');
      if (switchModelProfileTrigger) {
        event.preventDefault();
        event.stopPropagation();
        if (switchModelProfileTrigger.getAttribute('aria-checked') === 'true') return;
        const [category, profileId] = String(switchModelProfileTrigger.getAttribute('data-switch-model-profile') || '').split(':');
        try {
          await activateModelProfileFromSettings(category, profileId);
        } catch (error) {
          window.alert(error?.message || '切换模型配置失败');
        }
        return;
      }
      const duplicateModelProfileTrigger = event.target.closest('[data-duplicate-model-profile]');
      if (duplicateModelProfileTrigger) {
        event.preventDefault();
        event.stopPropagation();
        const [category, profileId] = String(duplicateModelProfileTrigger.getAttribute('data-duplicate-model-profile') || '').split(':');
        try {
          await duplicateModelProfileFromSettings(category, profileId);
        } catch (error) {
          window.alert(error?.message || '复制模型配置失败');
        }
        return;
      }
      const toggleModelProfileTrigger = event.target.closest('[data-toggle-model-profile]');
      if (toggleModelProfileTrigger) {
        event.preventDefault();
        const [category, profileId] = String(toggleModelProfileTrigger.getAttribute('data-toggle-model-profile') || '').split(':');
        const shouldExpand = toggleModelProfileTrigger.getAttribute('aria-expanded') !== 'true';
        state.settingsModal.expandedModelProfiles = {
          ...(state.settingsModal.expandedModelProfiles || {}),
          [category]: shouldExpand ? profileId : '',
        };
        const list = toggleModelProfileTrigger.closest('.model-profile-list');
        if (!list || list.dataset.drawerAnimating === 'true') return;
        const modal = toggleModelProfileTrigger.closest('.material-modal-panel');
        const settingsPanel = toggleModelProfileTrigger.closest('#settings-category-panel');
        const modelSettingsPanel = toggleModelProfileTrigger.closest('#model-settings-panel');
        const lockedModalHeight = modal?.getBoundingClientRect().height || 0;
        const lockedPanelHeight = settingsPanel?.getBoundingClientRect().height || 0;
        if (modal && lockedModalHeight) {
          modal.style.height = `${lockedModalHeight}px`;
          modal.style.overflow = 'hidden';
        }
        if (settingsPanel && lockedPanelHeight) {
          settingsPanel.style.height = `${lockedPanelHeight}px`;
          settingsPanel.style.overflow = 'hidden';
        }
        modelSettingsPanel?.classList.add('is-drawer-animating');
        list.dataset.drawerAnimating = 'true';
        const drawerAnimations = [];
        Array.from(list.querySelectorAll('.model-profile-card')).forEach((card) => {
          const summary = card.querySelector('[data-toggle-model-profile]');
          const isTarget = shouldExpand
            && summary?.getAttribute('data-toggle-model-profile') === `${category}:${profileId}`;
          const isExpanded = summary?.getAttribute('aria-expanded') === 'true';
          if (isTarget !== isExpanded) drawerAnimations.push(animateModelProfileDrawer(card, isTarget));
        });
        await Promise.all(drawerAnimations);
        await resizeSettingsModalAfterDrawer(
          modal,
          lockedModalHeight,
          settingsPanel,
          lockedPanelHeight,
        );
        modelSettingsPanel?.classList.remove('is-drawer-animating');
        delete list.dataset.drawerAnimating;
        return;
      }
      const createModelProfileTrigger = event.target.closest('[data-create-model-profile]');
      if (createModelProfileTrigger) {
        event.preventDefault();
        try {
          await createModelProfileFromSettings(createModelProfileTrigger.getAttribute('data-create-model-profile') || '');
        } catch (error) {
          window.alert(error?.message || '新建模型配置失败');
        }
        return;
      }
      const activateModelProfileTrigger = event.target.closest('[data-activate-model-profile]');
      if (activateModelProfileTrigger) {
        event.preventDefault();
        const [category, profileId] = String(activateModelProfileTrigger.getAttribute('data-activate-model-profile') || '').split(':');
        try {
          await activateModelProfileFromSettings(category, profileId);
        } catch (error) {
          window.alert(error?.message || '切换模型配置失败');
        }
        return;
      }
      const deleteModelProfileTrigger = event.target.closest('[data-delete-model-profile]');
      if (deleteModelProfileTrigger) {
        event.preventDefault();
        const [category, profileId] = String(deleteModelProfileTrigger.getAttribute('data-delete-model-profile') || '').split(':');
        try {
          await deleteModelProfileFromSettings(category, profileId);
        } catch (error) {
          window.alert(error?.message || '删除模型配置失败');
        }
        return;
      }
      const refreshArchiveTrigger = event.target.closest('[data-refresh-archive-settings]');
      if (refreshArchiveTrigger) {
        event.preventDefault();
        await refreshArchiveSettings();
        return;
      }
      const cleanupArchiveAllTrigger = event.target.closest('[data-cleanup-archive-all]');
      if (cleanupArchiveAllTrigger) {
        event.preventDefault();
        try {
          await cleanupAllArchiveArtifacts(cleanupArchiveAllTrigger);
        } catch (error) {
          console.error(error);
          window.alert(error?.message || '一键清理归档失败');
        }
        return;
      }
      const openArchiveArtifactTrigger = event.target.closest('[data-open-archive-artifact]');
      if (openArchiveArtifactTrigger) {
        event.preventDefault();
        try {
          await openArchiveArtifactFolder(openArchiveArtifactTrigger, openArchiveArtifactTrigger.getAttribute('data-open-archive-artifact') || '');
        } catch (error) {
          console.error(error);
          window.alert(error?.message || '打开中间产物目录失败');
        }
        return;
      }
      const cleanupArchiveArtifactTrigger = event.target.closest('[data-cleanup-archive-artifact]');
      if (cleanupArchiveArtifactTrigger) {
        event.preventDefault();
        try {
          await cleanupArchiveArtifact(cleanupArchiveArtifactTrigger, cleanupArchiveArtifactTrigger.getAttribute('data-cleanup-archive-artifact') || '');
        } catch (error) {
          console.error(error);
          window.alert(error?.message || '清理中间产物失败');
        }
        return;
      }
      const toggleSettingSecretTrigger = event.target.closest('[data-toggle-setting-secret]');
      if (toggleSettingSecretTrigger) {
        event.preventDefault();
        const envName = toggleSettingSecretTrigger.getAttribute('data-toggle-setting-secret') || '';
        state.settingsModal.revealedSecrets = {
          ...(state.settingsModal.revealedSecrets || {}),
          [envName]: !isSettingsSecretVisible(envName),
        };
        renderSettingsModal();
        return;
      }
      const videoParamsTrigger = event.target.closest('[data-open-video-params]');
      if (videoParamsTrigger) {
        event.preventDefault();
        openVideoParamsModal();
        return;
      }
      const smartBeatTrigger = event.target.closest('[data-html-motion-smart-beat]');
      if (smartBeatTrigger) {
        event.preventDefault();
        await saveHtmlMotionSmartBeatInterval(!state.htmlMotionOverlay?.smartBeatInterval);
        return;
      }
      const videoMergeTrigger = event.target.closest('[data-video-merge-mode]');
      if (videoMergeTrigger) {
        event.preventDefault();
        const mode = normalizeVideoMergeMode(videoMergeTrigger.getAttribute('data-video-merge-mode'));
        const previousMode = normalizeVideoMergeMode(state.settingsModal.videoMergeMode);
        state.settingsModal.videoMergeMode = mode;
        renderSettingsModal();
        try {
          const res = await fetch('/api/video-merge-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mergeMode: mode }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || data?.ok === false) {
            throw new Error(data?.error || '视频合并设置保存失败');
          }
          state.settingsModal.videoMergeMode = normalizeVideoMergeMode(data?.mergeMode);
          await refreshAuthSettings();
        } catch (error) {
          state.settingsModal.videoMergeMode = previousMode;
          state.settingsModal.videoModelNotice = '';
          state.settingsModal.videoModelError = error?.message || '视频合并设置保存失败';
        }
        renderSettingsModal();
        return;
      }
      const pullVideoModelsTrigger = event.target.closest('[data-pull-video-models]');
      if (pullVideoModelsTrigger) {
        event.preventDefault();
        await pullVideoModelCatalog();
        return;
      }
      const pullAuthModelsTrigger = event.target.closest('[data-pull-auth-models]');
      if (pullAuthModelsTrigger) {
        event.preventDefault();
        await pullAuthModelCatalog(pullAuthModelsTrigger.getAttribute('data-pull-auth-models') || '');
        return;
      }
      const closeVideoParamsTrigger = event.target.closest('[data-close-video-params]');
      if (closeVideoParamsTrigger) {
        event.preventDefault();
        closeVideoParamsModal();
        return;
      }
      const smartSplitPlanToggle = event.target.closest('[data-smart-split-plan-toggle]');
      if (smartSplitPlanToggle) {
        event.preventDefault();
        const node = smartSplitPlanToggle.closest('.smart-split-plan-node');
        const open = !node?.classList.contains('is-open');
        node?.classList.toggle('is-open', open);
        node?.setAttribute('aria-expanded', open ? 'true' : 'false');
        return;
      }
      const smartSplitPlanEdit = event.target.closest('[data-smart-split-plan-edit]');
      if (smartSplitPlanEdit) {
        event.preventDefault();
        setSmartSplitPromptEditing(smartSplitPlanEdit.closest('.smart-split-plan-node'), true);
        return;
      }
      const smartSplitPlanSave = event.target.closest('[data-smart-split-plan-save]');
      if (smartSplitPlanSave) {
        event.preventDefault();
        await saveSmartSplitPrompt(smartSplitPlanSave);
        return;
      }
      const guideActionTrigger = event.target.closest('[data-guide-action-kind]');
      if (guideActionTrigger) {
        event.preventDefault();
        await handleGuideAction(
          guideActionTrigger.getAttribute('data-guide-action-kind') || '',
          guideActionTrigger.getAttribute('data-guide-action-value') || '',
          guideActionTrigger
        );
        return;
      }
      const trigger = event.target.closest('[data-open-folder]');
      if (trigger) {
        event.preventDefault();
        const archiveKey = trigger.getAttribute('data-open-folder') || '';
        const localPath = trigger.getAttribute('data-local-path') || '';
        try {
          await openArchiveFolder(archiveKey, localPath, trigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const coverFolderTrigger = event.target.closest('[data-open-user-generated-cover-folder]');
      if (coverFolderTrigger) {
        event.preventDefault();
        try {
          await openUserGeneratedCoverFolder(coverFolderTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const localTtsFolderTrigger = event.target.closest('[data-open-local-tts-folder]');
      if (localTtsFolderTrigger) {
        event.preventDefault();
        try {
          await fetch('/api/open-local-tts-folder', { method: 'POST' });
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const localTtsPreviewTrigger = event.target.closest('[data-local-tts-preview]');
      if (localTtsPreviewTrigger) {
        event.preventDefault();
        await previewLocalTtsVoice();
        return;
      }
      const localTtsCloneUploadTrigger = event.target.closest('[data-add-local-tts-voice-clone]');
      if (localTtsCloneUploadTrigger) {
        event.preventDefault();
        beginLocalTtsVoiceCloneUpload();
        return;
      }
      const localTtsCloneFolderTrigger = event.target.closest('[data-open-local-tts-voice-clone-folder]');
      if (localTtsCloneFolderTrigger) {
        event.preventDefault();
        try {
          await openLocalTtsVoiceCloneFolder(localTtsCloneFolderTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const previewFolderTrigger = event.target.closest('[data-open-user-generated-preview-folder]');
      if (previewFolderTrigger) {
        event.preventDefault();
        try {
          await openUserGeneratedPreviewFolder(previewFolderTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const regeneratePreviewTrigger = event.target.closest('[data-regenerate-user-generated-previews]');
      if (regeneratePreviewTrigger) {
        event.preventDefault();
        try {
          await regenerateUserGeneratedPreviews(regeneratePreviewTrigger);
        } catch (error) {
          console.error(error);
          window.alert(error?.message || '重新生成预览图失败');
        }
        return;
      }
      const recycleBinRestoreTrigger = event.target.closest('[data-restore-recycle-bin-folder]');
      if (recycleBinRestoreTrigger) {
        event.preventDefault();
        await restoreRecycleBinTask(recycleBinRestoreTrigger);
        return;
      }
      const recycleBinFolderTrigger = event.target.closest('[data-open-user-recycle-bin-folder]');
      if (recycleBinFolderTrigger) {
        event.preventDefault();
        try {
          await openUserRecycleBinFolder(recycleBinFolderTrigger);
          await refreshRecycleBin();
          renderRecycleBin();
          renderRecycleBinModal();
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const materialFolderTrigger = event.target.closest('[data-open-user-material-folder]');
      if (materialFolderTrigger) {
        event.preventDefault();
        const kind = materialFolderTrigger.getAttribute('data-open-user-material-folder') || 'root';
        try {
          await openUserMaterialFolder(kind, materialFolderTrigger);
          await refreshUserMaterials();
          renderUserMaterials();
          renderMaterialMentionPicker();
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const materialAddTrigger = event.target.closest('[data-add-user-material]');
      if (materialAddTrigger) {
        event.preventDefault();
        beginUserMaterialUpload(materialAddTrigger.getAttribute('data-add-user-material') || 'image');
        return;
      }
      const materialLibraryTrigger = event.target.closest('[data-show-user-materials]');
      if (materialLibraryTrigger) {
        event.preventDefault();
        openMaterialLibraryModal(materialLibraryTrigger.getAttribute('data-show-user-materials') || 'image');
        return;
      }
      const materialDeleteTrigger = event.target.closest('[data-delete-user-material-kind]');
      if (materialDeleteTrigger) {
        event.preventDefault();
        event.stopPropagation();
        try {
          await deleteUserMaterial(
            materialDeleteTrigger.getAttribute('data-delete-user-material-kind') || 'image',
            materialDeleteTrigger.getAttribute('data-delete-user-material-path') || '',
            materialDeleteTrigger.getAttribute('data-delete-user-material-name') || '',
            materialDeleteTrigger,
          );
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const recycleBinTrigger = event.target.closest('[data-show-recycle-bin]');
      if (recycleBinTrigger) {
        event.preventDefault();
        await refreshRecycleBin();
        openRecycleBinModal();
        renderRecycleBin();
        return;
      }


      const viralBreakdownTrigger = event.target.closest('[data-open-viral-breakdown-entry]');
      if (viralBreakdownTrigger) {
        event.preventDefault();
        openViralBreakdownModal();
        return;
      }
      const hotRadarEntryTrigger = event.target.closest('[data-open-hot-radar-entry]');
      if (hotRadarEntryTrigger) {
        event.preventDefault();
        openHotRadarModal();
        return;
      }


















































      const scriptReferenceLibraryTrigger = event.target.closest('[data-select-script-reference-library]');
      if (scriptReferenceLibraryTrigger) {
        event.preventDefault();
        await selectScriptReference(scriptReferenceLibraryTrigger.getAttribute('data-select-script-reference-library') || '');
        closeMaterialLibraryModal();
        return;
      }
      const materialPickTrigger = event.target.closest('[data-pick-material]');
      if (materialPickTrigger) {
        event.preventDefault();
        pickMaterialMention(materialPickTrigger.getAttribute('data-pick-material') || '');
        return;
      }
      const reportTrigger = event.target.closest('[data-open-report]');
      if (reportTrigger) {
        event.preventDefault();
        const reportPath = reportTrigger.getAttribute('data-open-report') || '';
        try {
          await openBatchReport(reportPath, reportTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const alertTrigger = event.target.closest('[data-open-alert]');
      if (alertTrigger) {
        event.preventDefault();
        const alertPath = alertTrigger.getAttribute('data-open-alert') || '';
        try {
          await openBatchAlert(alertPath, alertTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const stateTrigger = event.target.closest('[data-open-supervisor-state]');
      if (stateTrigger) {
        event.preventDefault();
        try {
          await openBatchSupervisorState(stateTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const adminStateTrigger = event.target.closest('[data-open-supervisor-admin-state]');
      if (adminStateTrigger) {
        event.preventDefault();
        try {
          await openBatchSupervisorAdminState(adminStateTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const livePreflightTrigger = event.target.closest('[data-run-live-preflight]');
      if (livePreflightTrigger) {
        event.preventDefault();
        try {
          await runLivePreflight(livePreflightTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const lockTrigger = event.target.closest('[data-open-supervisor-lock]');
      if (lockTrigger) {
        event.preventDefault();
        try {
          await openBatchSupervisorLock(lockTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const deploymentTrigger = event.target.closest('[data-open-supervisor-deployment]');
      if (deploymentTrigger) {
        event.preventDefault();
        try {
          await openBatchSupervisorDeployment(deploymentTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const seedFileTrigger = event.target.closest('[data-open-seed-file]');
      if (seedFileTrigger) {
        event.preventDefault();
        try {
          await openBatchSeedFile(seedFileTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const buildSeedFileTrigger = event.target.closest('[data-build-seed-file]');
      if (buildSeedFileTrigger) {
        event.preventDefault();
        try {
          await buildBatchSeedFile(buildSeedFileTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const writeDeploymentTrigger = event.target.closest('[data-write-supervisor-deployment]');
      if (writeDeploymentTrigger) {
        event.preventDefault();
        try {
          await writeBatchSupervisorDeployment(writeDeploymentTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const installDeploymentTrigger = event.target.closest('[data-install-supervisor-deployment]');
      if (installDeploymentTrigger) {
        event.preventDefault();
        try {
          await installBatchSupervisorDeployment(installDeploymentTrigger);
        } catch (error) {
          console.error(error);
        }
        return;
      }
      const uninstallDeploymentTrigger = event.target.closest('[data-uninstall-supervisor-deployment]');
      if (uninstallDeploymentTrigger) {
        event.preventDefault();
        try {
          await uninstallBatchSupervisorDeployment(uninstallDeploymentTrigger);
        } catch (error) {
          console.error(error);
        }
      }
    });
