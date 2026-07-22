            document.addEventListener('click', (event) => {
              const materialLibraryTrigger = event.target.closest('[data-show-user-materials]');
              if (!materialLibraryTrigger) return;
              const materialLibraryAddButton = document.getElementById('materialLibraryAddButton');
              if (!materialLibraryAddButton) return;
              materialLibraryAddButton.dataset.addUserMaterial = materialLibraryTrigger.getAttribute('data-show-user-materials') || 'image';
            }, true);

    const RESET_SESSIONS = new URLSearchParams(window.location.search).get('fresh') === '1';
    const BRAND_NAME = 'AI8video';
    const BRAND_SLUG = 'ai8video';
    const BRAND_DISPLAY_SLUG = '批量创作助手';
    const NEW_SESSION_TITLE = `新的${BRAND_NAME}会话`;
    const WAITING_REPLY_TITLE = `等待${BRAND_NAME} 回复`;
    const SESSION_STORAGE_KEY = `${BRAND_SLUG}-chat-sessions`;
    const SESSION_STORAGE_MAX_CHARS = 900000;
    const SESSION_STORAGE_OMIT_KEYS = new Set([
      'imageBase64', 'audioBase64', 'videoBase64', 'base64', 'dataUrl', 'dataURL',
      'raw', 'rawResponse', 'requestBody', 'responseBody', 'stdout', 'stderr', 'html', 'css',
    ]);

    const SUPERVISOR_CONFIG_STORAGE_KEY = `${BRAND_SLUG}-supervisor-config-draft`;
    const SUPERVISOR_ACTION_STORAGE_KEY = `${BRAND_SLUG}-supervisor-admin-result`;
    const HOT_RADAR_SNAPSHOT_STORAGE_KEY = `${BRAND_SLUG}-hot-radar-snapshot`;
    const HOT_RADAR_VIEW_STATE_STORAGE_KEY = `${BRAND_SLUG}-hot-radar-view-state`;
    const HOT_RADAR_COLUMN_COUNT_STORAGE_KEY = `${BRAND_SLUG}-hot-radar-column-count`;
    const VIDEO_PREVIEW_EXTENSION_STORAGE_KEY = `${BRAND_SLUG}-video-preview-extension-state`;
    const HOT_RADAR_SNAPSHOT_MAX_AGE_MS = 24 * 60 * 60 * 1000;
    // 旧名称只存在于浏览器缓存兼容边界；迁移成功后只写新 key。
    const LEGACY_BRAND_SLUGS = ['AI8miniVideo', 'ai8minivideo'];
    const STORAGE_MIGRATIONS = [
      { key: SESSION_STORAGE_KEY, suffix: 'chat-sessions', aliases: ['ai8minivideo-sessions'] },
      { key: SUPERVISOR_CONFIG_STORAGE_KEY, suffix: 'supervisor-config-draft' },
      { key: SUPERVISOR_ACTION_STORAGE_KEY, suffix: 'supervisor-admin-result' },
      { key: HOT_RADAR_SNAPSHOT_STORAGE_KEY, suffix: 'hot-radar-snapshot' },
      { key: HOT_RADAR_VIEW_STATE_STORAGE_KEY, suffix: 'hot-radar-view-state' },
      { key: HOT_RADAR_COLUMN_COUNT_STORAGE_KEY, suffix: 'hot-radar-column-count' },
      { key: VIDEO_PREVIEW_EXTENSION_STORAGE_KEY, suffix: 'video-preview-extension-state' },
    ].map((item) => ({
      ...item,
      legacyKeys: [
        ...LEGACY_BRAND_SLUGS.map((slug) => `${slug}-${item.suffix}`),
        ...(item.aliases || []),
      ],
    }));
    migrateLegacyBrowserStorage();
    const WELCOME_PAYLOAD = {
      text: `我是${BRAND_NAME}。把提示词或多集剧本直接发我。多集剧本记得写目标集数；参考图可以下一句再给。如果暂时不用参考图，直接回复“不用参考图”。\n如果要批量跑量，也可以直接说“今天先跑两条商务风”，再把候选内容逐行发我，或者一次性发“候选：A；B；C”。`,
      stage: 'collecting',
      awaiting: null,
      draft: null,
      result: null,
      summary: null,
    };



















    const state = {
      sessions: RESET_SESSIONS ? [] : loadSessions(),
      activeId: null,
      health: null,
      assets: [],
      userGeneratedResults: [],
      recycleBin: { count: 0, items: [], root: '' },
      deletedUserGeneratedKeys: [],
      deletedUserGeneratedJobIds: [],


      userMaterials: { images: [], scripts: [], flowerWatermarks: [], imageCount: 0, scriptCount: 0, flowerWatermarkCount: 0 },
      viralBreakdown: {
        root: '',
        itemCount: 0,
        sizeBytes: 0,
        sizeLabel: '0 B',
        archiveDisplay: '0 个视频 · 0 B',
        items: [],
        selectedVideoKey: '',
        intervalSeconds: 1,
        targetRatio: '16:9',
        loading: false,
        uploading: false,
        frameProcessing: false,
        transcriptProcessing: false,
        transcriptDrafts: {},
        scriptGuessProcessing: false,
        scriptGuessDrafts: {},
        error: '',
        notice: '',
      },
      hotRadar: {
        requestSeq: 0,
        loading: false,
        summarizing: false,
        promptBuilding: false,
        sources: [],
        categories: {},
        selectedCategory: '',
        selectedSourceId: '',
        keyword: '',
        items: [],
        selectedTopicId: '',
        summaryText: '',
        promptText: '',
        notice: '',
        error: '',
        updatedAt: '',
        errors: [],
        sourceDrafts: [],
        columnCount: loadHotRadarColumnCount(),
        ...loadHotRadarSnapshot(),
        ...loadHotRadarViewState(),
      },
      backgroundMusic: { enabled: false, name: '', items: [], volumePercent: 28, preserveOriginalAudio: true, uploading: false, selecting: false, error: '' },
      backgroundMusicDrawer: {
        visible: false,
        loading: false,
      },
      defaultReferenceImage: {
        enabled: false,
        item: null,
        selecting: false,
        error: '',
        options: {},
        customPrompt: '',
        effectDefinitions: [],
      },
      defaultReferenceDrawer: {
        visible: false,
        loading: false,
        customPromptSaveTimer: null,
        customPromptComposing: false,
      },
      scriptReference: {
        enabled: false,
        item: null,
        selecting: false,
        error: '',
      },
      scriptReferenceDrawer: {
        visible: false,
        loading: false,
      },
      flowerText: {
        enabled: false,
        text: '',
        canvasWidth: 9,
        canvasHeight: 16,
        textColor: '#ffee43',
        strokeColor: '#121826',
        fontFamily: '',
        availableFonts: [],
        fontSize: 16,
        fontWeight: 800,
        strokeWidth: 8,
        position: 'center',
        textX: 50,
        textY: 50,
        animationDelaySeconds: 0,
        animationType: 'fade',
        watermarkEnabled: false,
        watermarkImage: '',
        watermarkSize: 18,
        watermarkOpacity: 100,
        watermarkAnimationDelaySeconds: 0,
        watermarkAnimationType: 'fade',
        watermarkPosition: 'bottom-right',
        watermarkX: 92,
        watermarkY: 92,
        watermark2Enabled: false,
        watermark2Image: '',
        watermark2Size: 18,
        watermark2Opacity: 100,
        watermark2AnimationDelaySeconds: 0,
        watermark2AnimationType: 'fade',
        watermark2Position: 'bottom-left',
        watermark2X: 8,
        watermark2Y: 92,
        previewBackgroundColor: '#303844',
        previewBackgroundImage: '',
        previewBackgroundImageUrl: '',
        backgroundUploading: false,
        saving: false,
        error: '',
        notice: '',
        autoSaveTimer: null,
        autoSaveSeq: 0,
        previewTimer: null,
        previewSeq: 0,
        previewUrl: '',
        drag: null,
      },
      flowerTextDrawer: {
        visible: false,
        loading: false,
      },
      generationMode: {
        concurrentGeneration: false,
        saving: false,
        error: '',
      },
      generationModeDrawer: {
        visible: false,
        loading: false,
      },
      htmlMotionOverlay: {
        enabled: false,
        qualityRetryCount: 5,
        beatIntervalSeconds: 5,
        smartBeatInterval: false,
        saving: false,
        error: '',
        runtime: null,
        safeZones: {},
      },
      narrationReview: {
        reviewCount: 2,
        saving: false,
      },
      htmlMotionSafeZone: {
        editing: false,
        saving: false,
        draft: null,
        drag: null,
      },
      htmlMotionOverlayDrawer: {
        visible: false,
        loading: false,
      },
      batchAlerts: [],
      batchReports: [],
      authSettings: null,
      localTts: null,
      videoModelSettings: null,
      busy: false,
      generationProgress: null,
      generationProgressTimer: null,
      progressModal: {
        visible: false,
      },
      resultModal: {
        visible: false,
      },
      videoPreviewModal: {
        visible: false,
        playlist: [],
        index: 0,
        htmlMotionTaskId: '',
        htmlMotionPollTimer: null,
        htmlMotionRequestSeq: 0,
        htmlMotionSubmitting: false,
        htmlMotionCancelRequested: false,
      },
      // Survives modal close so in-flight HTML motion can resume on reopen.
      htmlMotionJobs: {},
      settingsModal: {
        visible: false,
        activeCategory: 'AI8video',
        revealedSecrets: {},
        savingVideoModel: false,
        pullingVideoModels: false,
        pullingAuthModelEnvName: '',
        authModelCatalogs: {},
        videoModelCatalog: [],
        videoModelAttempts: [],
        videoMergeMode: 'none',
        videoInlinePanel: '',
        videoModelError: '',
        videoModelNotice: '',
        videoModelRowError: '',
        videoModelRowNotice: '',
        videoModelRowNoticeTimer: null,
        savingVideoModelField: '',
        localTtsPreviewLoading: false,
        localTtsPreviewAudio: null,
        localTtsPreviewSignature: '',
        localTtsPreviewUrl: '',
        refreshingArchive: false,
        regeneratingPreviews: false,
        cleaningArchiveArtifactKind: '',
        saveBadgeTimer: null,
        htmlMotionSaveTimer: null,
        narrationReviewSaveTimer: null,
      },
      videoParamsModal: {
        visible: false,
        autoSaveTimer: null,
        saveStatusTimer: null,
        autoSaveSeq: 0,
      },
      systemPromptModal: {
        visible: false,
        loading: false,
        saving: false,
        error: '',
        notice: '',
        payload: null,
        draft: '',
        autoSaveTimer: null,
        autoSaveSeq: 0,
      },
      preflight: {
        running: false,
        report: null,
        error: '',
      },
      supervisorAdminResult: loadSupervisorAdminResult(),
      supervisorModal: {
        visible: false,
        mode: 'write',
        submitting: false,
        error: '',
        draftSource: '',
      },
      clearConversationModal: {
        visible: false,
      },
      materialModal: {
        visible: false,
        kind: 'image',
      },
      scriptKnowledge: {
        requestSeq: 0,
        loading: false,
        syncing: false,
        saving: false,
        query: '',
        items: [],
        status: null,
        selectedId: 0,
        detail: null,
        resetDetailScroll: false,
        error: '',
      },
      recycleBinModal: {
        visible: false,
        renderedSignature: '',
        selectedFolders: [],
        deleting: false,
        restoringFolder: '',
      },



    };
    let flowerTextFontStyleEl = null;
    let flowerTextFontFaceSignature = '';
    let flowerTextSavePipeline = Promise.resolve();
    let scriptKnowledgeSearchTimer = null;
    const pendingPollTimers = new Map();
    const pendingPollInflight = new Set();
    const pendingCancelInflight = new Set();
    const collectingSyncTimers = new Map();
    const collectingSyncInflight = new Set();
    const collectingSyncSeen = new Map();

    const els = {
      shell: document.querySelector('.shell'),
      brandName: document.getElementById('brandName'),
      brandSlug: document.getElementById('brandSlug'),
      progressPanel: document.getElementById('progressPanel'),
      settingsEntryButton: document.getElementById('settingsEntryButton'),
      mobileSettingsEntryButton: document.getElementById('mobileSettingsEntryButton'),
      systemPromptButton: document.getElementById('systemPromptButton'),
      backgroundMusicButton: document.getElementById('backgroundMusicButton'),
      defaultReferenceButton: document.getElementById('defaultReferenceButton'),
      scriptReferenceButton: document.getElementById('scriptReferenceButton'),
      flowerTextButton: document.getElementById('flowerTextButton'),
      generationModeButton: document.getElementById('generationModeButton'),
      htmlMotionOverlayButton: document.getElementById('htmlMotionOverlayButton'),
      imageMaterialList: document.getElementById('imageMaterialList'),
      scriptMaterialList: document.getElementById('scriptMaterialList'),
      recycleBinList: document.getElementById('recycleBinList'),
      assistantToolsList: document.getElementById('assistantToolsList'),
      materialMentionPicker: document.getElementById('materialMentionPicker'),
      supervisorPanel: document.getElementById('supervisorPanel'),
      batchAlertList: document.getElementById('batchAlertList'),
      batchReportList: document.getElementById('batchReportList'),
      sessionList: document.getElementById('sessionList'),
      statusBar: document.getElementById('statusBar'),
      messages: document.getElementById('messages'),
      clearConversationButton: document.getElementById('clearConversationButton'),
      clearConversationConfirmModal: document.getElementById('clearConversationConfirmModal'),
      clearConversationConfirmCloseButton: document.getElementById('clearConversationConfirmCloseButton'),
      clearConversationConfirmCancelButton: document.getElementById('clearConversationConfirmCancelButton'),
      clearConversationConfirmSubmitButton: document.getElementById('clearConversationConfirmSubmitButton'),
      clearConversationConfirmCount: document.getElementById('clearConversationConfirmCount'),
      composer: document.getElementById('composer'),
      messageInput: document.getElementById('messageInput'),
      messageEditor: document.getElementById('messageEditor'),
      sendButton: document.getElementById('sendButton'),
      assetList: document.getElementById('assetList'),
      metrics: document.getElementById('metrics'),
      progressModal: document.getElementById('progressModal'),
      progressModalTitle: document.getElementById('progressModalTitle'),
      progressModalCancelSlot: document.getElementById('progressModalCancelSlot'),
      progressModalSub: document.getElementById('progressModalSub'),
      progressModalBody: document.getElementById('progressModalBody'),
      progressModalCloseButton: document.getElementById('progressModalCloseButton'),
      resultModal: document.getElementById('resultModal'),
      resultModalTitle: document.getElementById('resultModalTitle'),
      resultModalSub: document.getElementById('resultModalSub'),
      resultModalBody: document.getElementById('resultModalBody'),
      resultModalRefreshButton: document.getElementById('resultModalRefreshButton'),
      resultModalOpenFolderButton: document.getElementById('resultModalOpenFolderButton'),
      resultModalCloseButton: document.getElementById('resultModalCloseButton'),
      videoPreviewModal: document.getElementById('videoPreviewModal'),
      videoPreviewTitle: document.getElementById('videoPreviewTitle'),
      videoPreviewSub: document.getElementById('videoPreviewSub'),
      videoPreviewBody: document.getElementById('videoPreviewBody'),
      videoPreviewCloseButton: document.getElementById('videoPreviewCloseButton'),
      settingsModal: document.getElementById('settingsModal'),
      settingsModalSub: document.getElementById('settingsModalSub'),
      settingsSaveBadge: document.getElementById('settingsSaveBadge'),
      settingsModalBody: document.getElementById('settingsModalBody'),
      settingsModalCloseButton: document.getElementById('settingsModalCloseButton'),
      videoParamsModal: document.getElementById('videoParamsModal'),
      videoParamsModalBody: document.getElementById('videoParamsModalBody'),
      videoParamsModalCloseButton: document.getElementById('videoParamsModalCloseButton'),
      systemPromptDrawer: document.getElementById('systemPromptDrawer'),
      systemPromptDrawerBody: document.getElementById('systemPromptDrawerBody'),
      backgroundMusicDrawer: document.getElementById('backgroundMusicDrawer'),
      backgroundMusicDrawerBody: document.getElementById('backgroundMusicDrawerBody'),
      defaultReferenceDrawer: document.getElementById('defaultReferenceDrawer'),
      defaultReferenceDrawerBody: document.getElementById('defaultReferenceDrawerBody'),
      scriptReferenceDrawer: document.getElementById('scriptReferenceDrawer'),
      scriptReferenceDrawerBody: document.getElementById('scriptReferenceDrawerBody'),
      flowerTextDrawer: document.getElementById('flowerTextDrawer'),
      flowerTextDrawerBody: document.getElementById('flowerTextDrawerBody'),
      generationModeDrawer: document.getElementById('generationModeDrawer'),
      generationModeDrawerBody: document.getElementById('generationModeDrawerBody'),
      htmlMotionOverlayDrawer: document.getElementById('htmlMotionOverlayDrawer'),
      htmlMotionOverlayDrawerBody: document.getElementById('htmlMotionOverlayDrawerBody'),
      systemPromptModal: document.getElementById('systemPromptModal'),
      systemPromptModalSub: document.getElementById('systemPromptModalSub'),
      systemPromptModalBody: document.getElementById('systemPromptModalBody'),
      systemPromptModalCloseButton: document.getElementById('systemPromptModalCloseButton'),
      materialLibraryModal: document.getElementById('materialLibraryModal'),
      materialLibraryTitle: document.getElementById('materialLibraryTitle'),
      materialLibrarySub: document.getElementById('materialLibrarySub'),
      materialLibraryWall: document.getElementById('materialLibraryWall'),
      materialLibraryAddButton: document.getElementById('materialLibraryAddButton'),
      materialLibraryOpenFolderButton: document.getElementById('materialLibraryOpenFolderButton'),
      materialLibraryCloseButton: document.getElementById('materialLibraryCloseButton'),
      scriptKnowledgeToolbar: document.getElementById('scriptKnowledgeToolbar'),
      scriptKnowledgeSearchInput: document.getElementById('scriptKnowledgeSearchInput'),
      scriptKnowledgeSearchClearButton: document.getElementById('scriptKnowledgeSearchClearButton'),
      scriptKnowledgeStatus: document.getElementById('scriptKnowledgeStatus'),
      scriptKnowledgeSyncButton: document.getElementById('scriptKnowledgeSyncButton'),
      recycleBinModal: document.getElementById('recycleBinModal'),
      recycleBinTitle: document.getElementById('recycleBinTitle'),
      recycleBinSub: document.getElementById('recycleBinSub'),
      recycleBinWall: document.getElementById('recycleBinWall'),
      recycleBinSelectAllButton: document.getElementById('recycleBinSelectAllButton'),
      recycleBinBatchDeleteButton: document.getElementById('recycleBinBatchDeleteButton'),
      recycleBinOpenFolderButton: document.getElementById('recycleBinOpenFolderButton'),
      recycleBinCloseButton: document.getElementById('recycleBinCloseButton'),








      userMaterialUploadInput: document.getElementById('userMaterialUploadInput'),
      backgroundMusicUploadInput: document.getElementById('backgroundMusicUploadInput'),
      localTtsVoiceCloneUploadInput: document.getElementById('localTtsVoiceCloneUploadInput'),
      supervisorConfigModal: document.getElementById('supervisorConfigModal'),
      supervisorConfigForm: document.getElementById('supervisorConfigForm'),
      supervisorConfigTitle: document.getElementById('supervisorConfigTitle'),
      supervisorConfigSub: document.getElementById('supervisorConfigSub'),
      supervisorConfigCloseButton: document.getElementById('supervisorConfigCloseButton'),
      supervisorConfigCancelButton: document.getElementById('supervisorConfigCancelButton'),
      supervisorConfigSubmitButton: document.getElementById('supervisorConfigSubmitButton'),
      supervisorConfigNote: document.getElementById('supervisorConfigNote'),
      supervisorConfigError: document.getElementById('supervisorConfigError'),
      supervisorScheduleTimesInput: document.getElementById('supervisorScheduleTimesInput'),
      supervisorTargetPassCountInput: document.getElementById('supervisorTargetPassCountInput'),
      supervisorStyleHintInput: document.getElementById('supervisorStyleHintInput'),
      supervisorPollSecondsInput: document.getElementById('supervisorPollSecondsInput'),
      supervisorMinPassRateInput: document.getElementById('supervisorMinPassRateInput'),
      supervisorLowPassRunsInput: document.getElementById('supervisorLowPassRunsInput'),
      supervisorAutoBuildSeedInput: document.getElementById('supervisorAutoBuildSeedInput'),
    };
