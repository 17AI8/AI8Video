    function smartImageSafeSource(value) {
      const source = String(value || '');
      return /^data:image\/(?:png|jpe?g|webp);base64,/i.test(source)
        || source.startsWith('/smart-image-results/')
        || source.startsWith('/user-materials/images/');
    }

    function smartImageSerializableSource(source) {
      if (!source) return null;
      return {
        ...source,
        edits: smartImageCloneEdits(source.edits),
      };
    }

    function smartImageSerializableResult(result) {
      return {
        ...result,
        jobId: String(result?.jobId || ''),
        edits: smartImageCloneEdits(result.edits),
      };
    }

    function smartImageResultPersistenceKey(result) {
      const url = String(result?.url || '');
      return url.startsWith('/smart-image-results/') ? url : String(result?.id || '');
    }

    function smartImageSerializableStringList(values, limit = 128) {
      return [...new Set((Array.isArray(values) ? values : [])
        .map((value) => String(value || '').trim())
        .filter((value) => value && value.length <= 1000))].slice(-limit);
    }

    function smartImageLibraryRelativePath(value) {
      return String(value || '').trim().replace(/\\/g, '/').replace(/^\/+/, '').slice(0, 1000);
    }

    function smartImageRecentLibraryTimestamp(value) {
      const timestamp = Date.parse(String(value || ''));
      return Number.isFinite(timestamp) ? timestamp : 0;
    }

    function smartImageSerializableRecentLibraryHistory(history) {
      const latestByPath = new Map();
      (Array.isArray(history) ? history : []).forEach((entry, order) => {
        const path = smartImageLibraryRelativePath(typeof entry === 'string' ? entry : entry?.path);
        if (!path) return;
        const selectedAt = String(typeof entry === 'string' ? '' : entry?.selectedAt || '').slice(0, 64);
        const candidate = { path, selectedAt, order };
        const current = latestByPath.get(path);
        const candidateTime = smartImageRecentLibraryTimestamp(candidate.selectedAt);
        const currentTime = smartImageRecentLibraryTimestamp(current?.selectedAt);
        if (!current || candidateTime > currentTime) latestByPath.set(path, candidate);
      });
      return [...latestByPath.values()]
        .sort((a, b) => smartImageRecentLibraryTimestamp(b.selectedAt) - smartImageRecentLibraryTimestamp(a.selectedAt) || a.order - b.order)
        .slice(0, SMART_IMAGE_RECENT_LIBRARY_LIMIT)
        .map(({ path, selectedAt }) => ({ path, selectedAt }));
    }

    function smartImageSerializableJob(job) {
      const total = smartImageClamp(job.total || 1, 1, 8);
      const successful = smartImageClamp(job.successful ?? job.done ?? (job.status === 'done' ? total : 0), 0, total);
      return {
        id: String(job.id || smartImageId('job')),
        prompt: String(job.prompt || '').slice(0, 2000),
        presetId: String(job.presetId || 'custom'),
        presetLabel: String(job.presetLabel || '自定义修图').slice(0, 80),
        total,
        successful,
        remaining: smartImageClamp(job.remaining ?? (total - successful), 0, total),
        status: String(job.status || 'error'),
        error: String(job.error || '').slice(0, 500),
        createdAt: String(job.createdAt || new Date().toISOString()),
        resultIds: smartImageSerializableStringList(job.resultIds, 64),
      };
    }

    function smartImageSessionResults(results) {
      return (Array.isArray(results) ? results : [])
        .slice(-64)
        .filter((item) => item?.id && smartImageSafeSource(item.url))
        .map(smartImageSerializableResult);
    }

    function smartImageBindSessionHierarchy(session) {
      const deletedResultKeys = smartImageSerializableStringList(session?.deletedResultKeys, 256);
      const deletedJobIds = smartImageSerializableStringList(session?.deletedJobIds, 128);
      const deletedResultSet = new Set(deletedResultKeys);
      const deletedJobSet = new Set(deletedJobIds);
      const results = smartImageSessionResults(session?.results)
        .filter((result) => !deletedResultSet.has(smartImageResultPersistenceKey(result)));
      const resultById = new Map(results.map((result) => [result.id, result]));
      const jobs = (Array.isArray(session?.jobs) ? session.jobs : [])
        .slice(-24)
        .map(smartImageSerializableJob)
        .filter((job) => !deletedJobSet.has(job.id));

      if (results.length && !jobs.length) {
        const firstResult = results[0];
        jobs.push(smartImageSerializableJob({
          id: `job-history-${smartImageStableHash(`${session?.sourceRelativePath || session?.sourceName || ''}|${firstResult.id}`)}`,
          prompt: firstResult.prompt || session?.prompt || SMART_IMAGE_DEFAULT_PROMPT,
          presetId: firstResult.presetId || 'custom',
          presetLabel: '历史修图任务',
          total: Math.min(8, results.length),
          successful: Math.min(8, results.length),
          remaining: 0,
          status: 'done',
          createdAt: firstResult.createdAt || session?.updatedAt,
          resultIds: results.map((result) => result.id),
        }));
      }

      const jobById = new Map(jobs.map((job) => [job.id, job]));
      const claimedResultIds = new Set();
      jobs.forEach((job) => {
        job.resultIds = job.resultIds.filter((resultId) => resultById.has(resultId) && !claimedResultIds.has(resultId));
        job.resultIds.forEach((resultId) => claimedResultIds.add(resultId));
      });
      results.forEach((result) => {
        const job = jobById.get(String(result.jobId || ''));
        if (!job || claimedResultIds.has(result.id)) return;
        job.resultIds.push(result.id);
        claimedResultIds.add(result.id);
      });

      let unassignedResults = results.filter((result) => !claimedResultIds.has(result.id));
      jobs.forEach((job) => {
        const expected = Math.max(0, Number(job.successful || 0));
        const needed = Math.max(0, expected - job.resultIds.length);
        const assigned = unassignedResults.splice(0, needed);
        assigned.forEach((result) => {
          job.resultIds.push(result.id);
          claimedResultIds.add(result.id);
        });
      });
      if (unassignedResults.length && jobs.length) {
        const latestJob = jobs[jobs.length - 1];
        unassignedResults.forEach((result) => {
          latestJob.resultIds.push(result.id);
          claimedResultIds.add(result.id);
        });
        unassignedResults = [];
      }

      jobs.forEach((job) => {
        job.resultIds.forEach((resultId) => {
          const result = resultById.get(resultId);
          if (result) result.jobId = job.id;
        });
        job.successful = smartImageClamp(Math.max(job.successful, job.resultIds.length), 0, job.total);
        job.remaining = smartImageClamp(job.remaining, 0, Math.max(0, job.total - job.successful));
      });

      let selectedJobId = jobs.some((job) => job.id === session?.selectedJobId)
        ? String(session.selectedJobId)
        : '';
      if (!selectedJobId && session?.selectedResultId) {
        selectedJobId = jobs.find((job) => job.resultIds.includes(session.selectedResultId))?.id || '';
      }
      if (!selectedJobId) {
        selectedJobId = [...jobs].reverse().find((job) => job.resultIds.length)?.id || jobs.at(-1)?.id || '';
      }
      const selectedJob = jobById.get(selectedJobId) || null;
      const selectedResultId = selectedJob?.resultIds.includes(session?.selectedResultId)
        ? String(session.selectedResultId)
        : (selectedJob?.resultIds[0] || '');
      return { results, jobs, selectedJobId, selectedResultId, deletedResultKeys, deletedJobIds };
    }

    function smartImageSerializableSession(session) {
      const hierarchy = smartImageBindSessionHierarchy(session);
      const selectedPresetId = SMART_IMAGE_PRESETS.some((item) => item.id === session?.selectedPresetId)
        ? String(session.selectedPresetId)
        : 'custom';
      return {
        sourceName: String(session?.sourceName || '').slice(0, 120),
        sourceRelativePath: String(session?.sourceRelativePath || '').slice(0, 1000),
        sourceEdits: smartImageCloneEdits(session?.sourceEdits),
        results: hierarchy.results,
        jobs: hierarchy.jobs,
        selectedJobId: hierarchy.selectedJobId,
        selectedResultId: hierarchy.selectedResultId,
        deletedResultKeys: hierarchy.deletedResultKeys,
        deletedJobIds: hierarchy.deletedJobIds,
        selectedPresetId,
        prompt: String(session?.prompt || SMART_IMAGE_DEFAULT_PROMPT).slice(0, 2000),
        batchCount: smartImageClamp(session?.batchCount || 1, 1, 8),
        viewMode: ['result', 'source', 'compare'].includes(session?.viewMode) ? session.viewMode : (hierarchy.selectedResultId ? 'result' : 'source'),
        comparePosition: smartImageClamp(session?.comparePosition ?? 50, 0, 100),
        updatedAt: String(session?.updatedAt || new Date().toISOString()),
      };
    }

    function smartImageSerializableSessions(sessions) {
      return Object.fromEntries(Object.entries(sessions && typeof sessions === 'object' ? sessions : {})
        .filter(([sourceKey, session]) => sourceKey && session && typeof session === 'object')
        .map(([sourceKey, session]) => [String(sourceKey), smartImageSerializableSession(session)]));
    }

    function smartImageRecentHistoryFromProject(project, savedAt = '') {
      const explicit = smartImageSerializableRecentLibraryHistory(project?.recentLibraryHistory);
      if (explicit.length) return explicit;
      const candidates = [];
      const currentPath = smartImageLibraryRelativePath(project?.source?.sourceRelativePath);
      if (currentPath) candidates.push({ path: currentPath, selectedAt: String(savedAt || new Date().toISOString()) });
      Object.values(project?.sourceSessions && typeof project.sourceSessions === 'object' ? project.sourceSessions : {})
        .filter((session) => session && typeof session === 'object')
        .sort((a, b) => smartImageRecentLibraryTimestamp(b.updatedAt) - smartImageRecentLibraryTimestamp(a.updatedAt))
        .forEach((session) => {
          const path = smartImageLibraryRelativePath(session.sourceRelativePath);
          if (path) candidates.push({ path, selectedAt: String(session.updatedAt || '') });
        });
      return smartImageSerializableRecentLibraryHistory(candidates);
    }

    function smartImageRememberRecentLibrarySelection(relativePath, selectedAt = new Date().toISOString()) {
      const path = smartImageLibraryRelativePath(relativePath);
      if (!path) return false;
      AI8SmartImage.state.recentLibraryHistory = smartImageSerializableRecentLibraryHistory([
        { path, selectedAt },
        ...AI8SmartImage.state.recentLibraryHistory,
      ]);
      return true;
    }

    function smartImageCurrentSourceSession() {
      const source = AI8SmartImage.state.source;
      if (!source) return null;
      return {
        sourceName: source.sourceName,
        sourceRelativePath: source.sourceRelativePath,
        sourceEdits: source.edits,
        results: AI8SmartImage.state.results,
        jobs: AI8SmartImage.state.jobs,
        selectedJobId: AI8SmartImage.state.selectedJobId,
        selectedResultId: AI8SmartImage.state.selectedResultId,
        deletedResultKeys: AI8SmartImage.state.deletedResultKeys,
        deletedJobIds: AI8SmartImage.state.deletedJobIds,
        selectedPresetId: AI8SmartImage.state.selectedPresetId,
        prompt: AI8SmartImage.state.prompt,
        batchCount: AI8SmartImage.state.batchCount,
        viewMode: AI8SmartImage.state.viewMode,
        comparePosition: AI8SmartImage.state.comparePosition,
        updatedAt: new Date().toISOString(),
      };
    }

    function smartImageRememberSourceSession() {
      const source = AI8SmartImage.state.source;
      const session = smartImageCurrentSourceSession();
      if (!source || !session) return '';
      const sourceKey = smartImageEnsureSourceKey(source);
      AI8SmartImage.state.sourceSessions = {
        ...AI8SmartImage.state.sourceSessions,
        [sourceKey]: smartImageSerializableSession(session),
      };
      return sourceKey;
    }

    function smartImageActivateSourceSession(source) {
      const sourceKey = smartImageEnsureSourceKey(source);
      const existing = AI8SmartImage.state.sourceSessions?.[sourceKey];
      AI8SmartImage.state.source = source;
      if (!existing) {
        AI8SmartImage.state.results = [];
        AI8SmartImage.state.jobs = [];
        AI8SmartImage.state.selectedJobId = '';
        AI8SmartImage.state.selectedResultId = '';
        AI8SmartImage.state.deletedResultKeys = [];
        AI8SmartImage.state.deletedJobIds = [];
        AI8SmartImage.state.selectedPresetId = 'natural';
        AI8SmartImage.state.prompt = SMART_IMAGE_DEFAULT_PROMPT;
        AI8SmartImage.state.batchCount = 1;
        AI8SmartImage.state.viewMode = 'source';
        AI8SmartImage.state.comparePosition = 50;
        return false;
      }
      const session = smartImageSerializableSession(existing);
      AI8SmartImage.state.sourceSessions[sourceKey] = session;
      source.edits = smartImageCloneEdits(session.sourceEdits);
      AI8SmartImage.state.results = session.results;
      AI8SmartImage.state.jobs = restoreSmartImageJobs(session.jobs, source);
      AI8SmartImage.state.selectedJobId = session.selectedJobId;
      AI8SmartImage.state.selectedResultId = session.selectedResultId;
      AI8SmartImage.state.deletedResultKeys = session.deletedResultKeys;
      AI8SmartImage.state.deletedJobIds = session.deletedJobIds;
      AI8SmartImage.state.selectedPresetId = session.selectedPresetId;
      AI8SmartImage.state.prompt = session.prompt;
      AI8SmartImage.state.batchCount = session.batchCount;
      AI8SmartImage.state.viewMode = session.selectedResultId ? session.viewMode : 'source';
      AI8SmartImage.state.comparePosition = session.comparePosition;
      return true;
    }

    function smartImageProjectPayload() {
      smartImageRememberSourceSession();
      return {
        product: 'AI8video AI 智能修图',
        version: AI8SmartImage.version,
        savedAt: new Date().toISOString(),
        project: {
          source: smartImageSerializableSource(AI8SmartImage.state.source),
          results: AI8SmartImage.state.results.map(smartImageSerializableResult),
          jobs: AI8SmartImage.state.jobs.slice(-24).map(smartImageSerializableJob),
          selectedJobId: AI8SmartImage.state.selectedJobId,
          selectedResultId: AI8SmartImage.state.selectedResultId,
          deletedResultKeys: smartImageSerializableStringList(AI8SmartImage.state.deletedResultKeys, 256),
          deletedJobIds: smartImageSerializableStringList(AI8SmartImage.state.deletedJobIds, 128),
          selectedPresetId: AI8SmartImage.state.selectedPresetId,
          prompt: AI8SmartImage.state.prompt,
          batchCount: AI8SmartImage.state.batchCount,
          viewMode: AI8SmartImage.state.viewMode,
          comparePosition: AI8SmartImage.state.comparePosition,
          exportFormat: AI8SmartImage.state.exportFormat,
          exportQuality: AI8SmartImage.state.exportQuality,
          sourceSessions: smartImageSerializableSessions(AI8SmartImage.state.sourceSessions),
          recentLibraryHistory: smartImageSerializableRecentLibraryHistory(AI8SmartImage.state.recentLibraryHistory),
        },
      };
    }

    function smartImageLegacyEdits(node) {
      return {
        brightness: Number(node?.filter?.brightness ?? 100),
        contrast: Number(node?.filter?.contrast ?? 100),
        saturation: Number(node?.filter?.saturation ?? 100),
        rotation: Number(node?.rotation || 0),
        flipX: !!node?.flipX,
        ratio: String(node?.cropRatio || 'original'),
      };
    }

    function smartImageLegacySource(node, batchItems) {
      const firstItem = batchItems[0] || null;
      const dataUrl = String(node.originalDataUrl || firstItem?.originalDataUrl || firstItem?.dataUrl || node.dataUrl || '');
      const source = {
        id: smartImageId('source'),
        name: String(node.name || node.sourceName || '上次修图图片').slice(0, 80),
        sourceName: String(node.sourceName || node.name || '上次修图图片').slice(0, 120),
        mime: dataUrl.startsWith('data:image/jpeg') ? 'image/jpeg' : dataUrl.startsWith('data:image/webp') ? 'image/webp' : 'image/png',
        size: 0,
        width: Number(firstItem?.width || node.width || 1),
        height: Number(firstItem?.height || node.height || 1),
        dataUrl,
        sourceRelativePath: String(node.sourceRelativePath || ''),
        edits: smartImageLegacyEdits(node),
      };
      source.sourceKey = smartImageSourceKey(source);
      return source;
    }

    function smartImageLegacyResults(items, node, source, project, savedAt) {
      return items.slice(1).filter((item) => smartImageSafeSource(item?.dataUrl)).map((item, index) => ({
        id: smartImageId('result'),
        url: String(item.dataUrl),
        fileName: `${source.name}-AI修图-${index + 1}.png`,
        model: String(node.modelName || ''),
        prompt: String(project.modelPrompt || SMART_IMAGE_DEFAULT_PROMPT),
        presetId: 'custom',
        createdAt: savedAt || new Date().toISOString(),
        width: Number(item.width || source.width),
        height: Number(item.height || source.height),
        edits: smartImageCloneEdits({ rotation: item.rotation || 0, flipX: !!item.flipX, ratio: item.cropRatio || 'original' }),
      }));
    }

    function smartImageSessionFromProject(project, savedAt = '') {
      return {
        sourceName: project.source?.sourceName,
        sourceRelativePath: project.source?.sourceRelativePath,
        sourceEdits: project.source?.edits,
        results: project.results,
        jobs: project.jobs,
        selectedJobId: project.selectedJobId,
        selectedResultId: project.selectedResultId,
        deletedResultKeys: project.deletedResultKeys,
        deletedJobIds: project.deletedJobIds,
        selectedPresetId: project.selectedPresetId,
        prompt: project.prompt,
        batchCount: project.batchCount,
        viewMode: project.viewMode,
        comparePosition: project.comparePosition,
        updatedAt: savedAt || new Date().toISOString(),
      };
    }

    function migrateSmartImageProject(payload) {
      let migrated = payload;
      let project = payload?.project;
      if (!project || typeof project !== 'object') throw new Error('不是有效的智能修图项目');
      if (project.source === undefined) {
        const legacyNode = Array.isArray(project.nodes) ? project.nodes.find((node) => node?.type === 'image') : null;
        if (!legacyNode) {
          migrated = {
            ...payload,
            project: {
              source: null,
              results: [],
              jobs: [],
              selectedJobId: '',
              selectedResultId: '',
              deletedResultKeys: [],
              deletedJobIds: [],
              selectedPresetId: 'natural',
              prompt: String(project.modelPrompt || SMART_IMAGE_DEFAULT_PROMPT),
              batchCount: Number(project.modelBatchCount || 1),
              sourceSessions: {},
              recentLibraryHistory: [],
            },
          };
        } else {
          const batchItems = Array.isArray(legacyNode.batchItems) ? legacyNode.batchItems : [];
          const source = smartImageLegacySource(legacyNode, batchItems);
          const results = smartImageLegacyResults(batchItems, legacyNode, source, project, payload.savedAt);
          migrated = {
            ...payload,
            project: {
              source,
              results,
              jobs: [],
              selectedJobId: '',
              selectedResultId: results[0]?.id || '',
              deletedResultKeys: [],
              deletedJobIds: [],
              selectedPresetId: 'custom',
              prompt: String(project.modelPrompt || SMART_IMAGE_DEFAULT_PROMPT),
              batchCount: Number(project.modelBatchCount || 1),
              viewMode: results.length ? 'result' : 'source',
              comparePosition: 50,
              exportFormat: 'png',
              exportQuality: 92,
              sourceSessions: {},
              recentLibraryHistory: [],
            },
          };
        }
      }
      project = migrated.project;
      if (!project.sourceSessions || typeof project.sourceSessions !== 'object' || Array.isArray(project.sourceSessions)) {
        project.sourceSessions = {};
      }
      if (project.source) {
        const sourceKey = smartImageEnsureSourceKey(project.source);
        if (!project.sourceSessions[sourceKey]) {
          project.sourceSessions[sourceKey] = smartImageSerializableSession(smartImageSessionFromProject(project, migrated.savedAt));
        }
      }
      project.recentLibraryHistory = smartImageRecentHistoryFromProject(project, migrated.savedAt);
      return { ...migrated, version: AI8SmartImage.version, project };
    }

    function validateSmartImageProject(payload) {
      const project = payload?.project;
      if (!project || typeof project !== 'object') throw new Error('不是有效的智能修图项目');
      if (project.source) {
        if (!smartImageSafeSource(project.source.dataUrl)) throw new Error('上次画布中的原图地址无效');
        project.source.edits = smartImageCloneEdits(project.source.edits);
        smartImageEnsureSourceKey(project.source);
      }
      project.results = smartImageSessionResults(project.results);
      if (!Array.isArray(project.jobs)) project.jobs = [];
      project.sourceSessions = smartImageSerializableSessions(project.sourceSessions);
      if (project.source) {
        const sourceKey = smartImageEnsureSourceKey(project.source);
        if (!project.sourceSessions[sourceKey]) {
          project.sourceSessions[sourceKey] = smartImageSerializableSession(smartImageSessionFromProject(project, payload.savedAt));
        }
      }
      project.recentLibraryHistory = smartImageRecentHistoryFromProject(project, payload.savedAt);
      return payload;
    }

    function restoreSmartImageJobs(jobs, source) {
      const allowedStatuses = new Set(['done', 'partial', 'error']);
      return (Array.isArray(jobs) ? jobs : []).slice(-24).filter((job) => job?.id).map((job) => {
        const saved = smartImageSerializableJob(job);
        const interrupted = ['queued', 'running'].includes(job.status);
        return {
          ...saved,
          source,
          status: interrupted ? 'error' : (allowedStatuses.has(saved.status) ? saved.status : 'error'),
          error: interrupted ? '上次关闭时任务尚未完成，请手动重试' : saved.error,
          done: saved.successful,
          attemptDone: 0,
          attemptTotal: saved.remaining || saved.total,
        };
      });
    }

    function restoreSmartImageState(payload) {
      const project = validateSmartImageProject(migrateSmartImageProject(payload)).project;
      AI8SmartImage.state.sourceSessions = smartImageSerializableSessions(project.sourceSessions);
      AI8SmartImage.state.recentLibraryHistory = smartImageSerializableRecentLibraryHistory(project.recentLibraryHistory);
      AI8SmartImage.state.exportFormat = ['png', 'jpeg', 'webp'].includes(project.exportFormat) ? project.exportFormat : 'png';
      AI8SmartImage.state.exportQuality = smartImageClamp(project.exportQuality || 92, 60, 100);
      if (project.source) {
        smartImageActivateSourceSession(project.source);
      } else {
        AI8SmartImage.state.source = null;
        AI8SmartImage.state.results = [];
        AI8SmartImage.state.jobs = [];
        AI8SmartImage.state.selectedJobId = '';
        AI8SmartImage.state.selectedResultId = '';
        AI8SmartImage.state.deletedResultKeys = [];
        AI8SmartImage.state.deletedJobIds = [];
        AI8SmartImage.state.selectedPresetId = 'natural';
        AI8SmartImage.state.prompt = SMART_IMAGE_DEFAULT_PROMPT;
        AI8SmartImage.state.batchCount = 1;
        AI8SmartImage.state.viewMode = 'source';
        AI8SmartImage.state.comparePosition = 50;
      }
    }

    async function saveSmartImageProject() {
      if (AI8SmartImage.state.saveTimer) clearTimeout(AI8SmartImage.state.saveTimer);
      AI8SmartImage.state.saveTimer = null;
      try {
        const payload = smartImageProjectPayload();
        const response = await fetch('/api/smart-image-editor/project', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error('保存失败');
        localStorage.removeItem(SMART_IMAGE_PROJECT_KEY);
        setSmartImageSaveState(`已保存 ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`, 'saved');
        return true;
      } catch {
        try { localStorage.setItem(SMART_IMAGE_PROJECT_KEY, JSON.stringify(smartImageProjectPayload())); } catch {}
        setSmartImageSaveState('自动保存失败', 'error');
        return false;
      }
    }

    function scheduleSmartImageSave() {
      if (AI8SmartImage.state.saveTimer) clearTimeout(AI8SmartImage.state.saveTimer);
      setSmartImageSaveState('待保存', 'pending');
      AI8SmartImage.state.saveTimer = setTimeout(saveSmartImageProject, 500);
    }

    async function restoreSmartImageProject() {
      try {
        const response = await fetch('/api/smart-image-editor/project', { cache: 'no-store' });
        let payload = response.ok ? await response.json() : null;
        if (!payload) {
          const raw = localStorage.getItem(SMART_IMAGE_PROJECT_KEY);
          if (raw) payload = JSON.parse(raw);
        }
        if (!payload) {
          AI8SmartImage.render();
          return;
        }
        restoreSmartImageState(payload);
        AI8SmartImage.render();
        await saveSmartImageProject();
        setSmartImageStatus(AI8SmartImage.state.source ? '已恢复上次修图任务' : '导入一张图片开始修图', 'success');
      } catch {
        AI8SmartImage.state.source = null;
        AI8SmartImage.state.results = [];
        AI8SmartImage.state.jobs = [];
        AI8SmartImage.state.selectedJobId = '';
        AI8SmartImage.state.selectedResultId = '';
        AI8SmartImage.state.deletedResultKeys = [];
        AI8SmartImage.state.deletedJobIds = [];
        AI8SmartImage.state.recentLibraryHistory = [];
        AI8SmartImage.render();
        setSmartImageStatus('旧画布无法完整迁移，已打开全新工作台；原图片文件未被删除', 'error');
      }
    }

    function smartImageCanvasBlob(canvas, mime = 'image/png', quality = .92) {
      if (mime === 'image/png') return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
      return new Promise((resolve) => canvas.toBlob(resolve, mime, quality));
    }

    function smartImageDrawCover(context, image, width, height) {
      const sourceRatio = image.naturalWidth / Math.max(1, image.naturalHeight);
      const targetRatio = width / Math.max(1, height);
      let sx = 0; let sy = 0; let sw = image.naturalWidth; let sh = image.naturalHeight;
      if (sourceRatio > targetRatio) {
        sw = image.naturalHeight * targetRatio;
        sx = (image.naturalWidth - sw) / 2;
      } else if (sourceRatio < targetRatio) {
        sh = image.naturalWidth / targetRatio;
        sy = (image.naturalHeight - sh) / 2;
      }
      context.drawImage(image, sx, sy, sw, sh, -width / 2, -height / 2, width, height);
    }

    async function renderSmartImageAsset(asset, format = AI8SmartImage.state.exportFormat) {
      if (!asset) throw new Error('请先选择要导出的图片');
      const image = await smartImageLoadElement(smartImageAssetSource(asset));
      const edits = smartImageCloneEdits(asset.edits);
      const ratio = smartImageRatioValue(edits.ratio, { width: image.naturalWidth, height: image.naturalHeight });
      let width = image.naturalWidth;
      let height = Math.max(1, Math.round(width / ratio));
      if (height > image.naturalHeight && edits.ratio !== 'original') {
        height = image.naturalHeight;
        width = Math.max(1, Math.round(height * ratio));
      }
      const scale = Math.min(1, SMART_IMAGE_MAX_EDGE / Math.max(width, height));
      width = Math.max(1, Math.round(width * scale));
      height = Math.max(1, Math.round(height * scale));
      const rotation = ((Number(edits.rotation || 0) % 360) + 360) % 360;
      const rotated = rotation % 180 !== 0;
      const canvas = document.createElement('canvas');
      canvas.width = rotated ? height : width;
      canvas.height = rotated ? width : height;
      const context = canvas.getContext('2d');
      if (format === 'jpeg') {
        context.fillStyle = '#ffffff';
        context.fillRect(0, 0, canvas.width, canvas.height);
      }
      context.save();
      context.translate(canvas.width / 2, canvas.height / 2);
      context.rotate((rotation * Math.PI) / 180);
      context.scale(edits.flipX ? -1 : 1, 1);
      context.filter = `brightness(${edits.brightness}%) contrast(${edits.contrast}%) saturate(${edits.saturation}%)`;
      smartImageDrawCover(context, image, width, height);
      context.restore();
      return canvas;
    }

    function downloadSmartImageBlob(blob, fileName) {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1200);
    }

    function smartImageSafeName() {
      const source = AI8SmartImage.state.source?.name || '智能修图';
      return String(source).replace(/[\\/:*?"<>|]+/g, '-').trim().slice(0, 70) || '智能修图';
    }

    async function exportSmartImageCurrent() {
      try {
        const asset = smartImageActiveAsset();
        if (!asset) throw new Error('请先导入图片');
        const format = AI8SmartImage.state.exportFormat;
        const mime = { png: 'image/png', jpeg: 'image/jpeg', webp: 'image/webp' }[format];
        const extension = format === 'jpeg' ? 'jpg' : format;
        setSmartImageStatus(`正在生成 ${extension.toUpperCase()} 副本…`);
        const canvas = await renderSmartImageAsset(asset, format);
        const blob = await smartImageCanvasBlob(canvas, mime, AI8SmartImage.state.exportQuality / 100);
        if (!blob) throw new Error('浏览器无法生成导出文件');
        downloadSmartImageBlob(blob, `${smartImageSafeName()}-智能修图.${extension}`);
        setSmartImageStatus(`${extension.toUpperCase()} 已导出，原图未改动`, 'success');
      } catch (error) {
        setSmartImageStatus(error?.message || '导出失败', 'error');
      }
    }

    Object.assign(AI8SmartImage, {
      projectPayload: smartImageProjectPayload,
      saveProject: saveSmartImageProject,
      scheduleSave: scheduleSmartImageSave,
      restoreProject: restoreSmartImageProject,
      rememberSourceSession: smartImageRememberSourceSession,
      activateSourceSession: smartImageActivateSourceSession,
      normalizeRecentLibraryHistory: smartImageSerializableRecentLibraryHistory,
      rememberRecentLibrarySelection: smartImageRememberRecentLibrarySelection,
      canvasBlob: smartImageCanvasBlob,
      renderAsset: renderSmartImageAsset,
      exportCurrent: exportSmartImageCurrent,
      downloadBlob: downloadSmartImageBlob,
    });
