





























































































    function pill(text, kind) {
      return `<span class="pill ${kind}">${escapeHtml(text)}</span>`;
    }

    function buildMediaUrl(archiveKey) {
      if (!archiveKey) return '';
      return '/media/' + encodeURI(String(archiveKey)).replace(/#/g, '%23');
    }

    function buildUserGeneratedMediaUrl(relativeKey) {
      if (!relativeKey) return '';
      return '/user-generated-results/' + encodeURI(String(relativeKey)).replace(/#/g, '%23');
    }

    function deriveUserGeneratedKeyFromMediaUrl(value) {
      const raw = String(value || '').trim();
      if (!raw) return '';
      let path = raw;
      try {
        path = new URL(raw, window.location.origin).pathname;
      } catch (_error) {
        path = raw.split('?')[0].split('#')[0];
      }
      const prefix = '/user-generated-results/';
      if (!path.startsWith(prefix)) return '';
      return decodeURIComponent(path.slice(prefix.length).replace(/^\/+/, ''));
    }

    function resolveRemoteArchiveUrl(value) {
      const text = String(value || '').trim();
      if (!/^https?:\/\//i.test(text)) return '';
      return text;
    }

    function mediaKeyBasename(value) {
      const text = decodeURIComponent(String(value || '').split('?')[0]).trim();
      if (!text) return '';
      return text.split('/').filter(Boolean).pop() || '';
    }

    function findUserGeneratedMirror(item) {
      const results = Array.isArray(state.userGeneratedResults) ? state.userGeneratedResults : [];
      const requestedKey = String(item?.userGeneratedKey || '').trim();
      if (requestedKey) {
        return results.find((result) => result.userGeneratedKey === requestedKey) || null;
      }
      const candidates = [
        item?.jobId,
        item?.archiveKey,
        item?.archiveUrl,
        item?.archiveLocalPath,
        item?.localVideoPath,
        item?.videoUrl,
      ].map(mediaKeyBasename).filter(Boolean);
      if (!candidates.length) return null;
      return results.find((result) => {
        const resultNames = [
          result?.jobId,
          result?.userGeneratedKey,
          result?.archiveKey,
          result?.archiveUrl,
          result?.archiveLocalPath,
          result?.localVideoPath,
          result?.videoUrl,
        ].map(mediaKeyBasename).filter(Boolean);
        return resultNames.some((name) => candidates.includes(name));
      }) || null;
    }

    function itemRequiresUserGeneratedMirror(item) {
      return Boolean(
        item?.userGeneratedKey
        || item?.archiveKey
        || item?.archiveLocalPath
        || item?.archiveStatus === 'archived'
        || item?.archiveBackend === 'local'
      );
    }

    function resolvePlayableVideoSrc(item) {
      const userGeneratedMirror = findUserGeneratedMirror(item);
      if (userGeneratedMirror?.userGeneratedKey) {
        return buildUserGeneratedMediaUrl(userGeneratedMirror.userGeneratedKey);
      }
      if (itemRequiresUserGeneratedMirror(item)) {
        return '';
      }
      const remoteUrl = resolveRemoteArchiveUrl(item?.archiveUrl);
      if (remoteUrl) {
        return remoteUrl;
      }
      if (item?.archiveStatus === 'archived' && item?.archiveBackend === 'local' && item?.archiveKey) {
        return buildMediaUrl(item.archiveKey);
      }
      if (item?.videoUrl && !String(item.videoUrl).includes('example.invalid')) {
        return item.videoUrl;
      }
      return '';
    }

    function resolvePlayableCoverSrc(item) {
      const userGeneratedMirror = findUserGeneratedMirror(item);
      if (userGeneratedMirror?.userGeneratedCoverKey) {
        return buildUserGeneratedMediaUrl(userGeneratedMirror.userGeneratedCoverKey);
      }
      if (userGeneratedMirror?.userGeneratedKey) {
        const mirrorCoverKey = deriveLocalCoverKey(userGeneratedMirror.userGeneratedKey);
        if (mirrorCoverKey) {
          return buildUserGeneratedMediaUrl(mirrorCoverKey);
        }
      }
      if (itemRequiresUserGeneratedMirror(item)) {
        return '';
      }
      const derivedUserCoverKey = deriveLocalCoverKey(item?.userGeneratedKey);
      if (derivedUserCoverKey) {
        return buildUserGeneratedMediaUrl(derivedUserCoverKey);
      }
      const remoteUrl = resolveRemoteArchiveUrl(item?.archiveCoverUrl);
      if (remoteUrl) {
        return remoteUrl;
      }
      if (item?.archiveStatus === 'archived' && item?.archiveBackend === 'local' && item?.archiveCoverKey) {
        return buildMediaUrl(item.archiveCoverKey);
      }
      const derivedArchiveCoverKey = item?.archiveStatus === 'archived' && item?.archiveBackend === 'local'
        ? deriveLocalCoverKey(item?.archiveKey)
        : '';
      if (derivedArchiveCoverKey) {
        return buildMediaUrl(derivedArchiveCoverKey);
      }
      if (item?.coverImageUrl && !String(item.coverImageUrl).includes('example.invalid')) {
        return item.coverImageUrl;
      }
      return '';
    }

    function resolvePlayablePreviewSrc(item) {
      const userGeneratedMirror = findUserGeneratedMirror(item);
      if (userGeneratedMirror?.userGeneratedPreviewKey) {
        return buildUserGeneratedMediaUrl(userGeneratedMirror.userGeneratedPreviewKey);
      }
      if (userGeneratedMirror?.userGeneratedKey) {
        const mirrorPreviewKey = deriveLocalPreviewKey(userGeneratedMirror.userGeneratedKey);
        if (mirrorPreviewKey) {
          return buildUserGeneratedMediaUrl(mirrorPreviewKey);
        }
      }
      const directPreviewKey = String(item?.userGeneratedPreviewKey || '').trim();
      if (directPreviewKey) {
        return buildUserGeneratedMediaUrl(directPreviewKey);
      }
      return '';
    }

    function deriveLocalCoverKey(videoKey) {
      const raw = String(videoKey || '').trim();
      if (!raw || !/\.(mp4|mov|m4v)$/i.test(raw)) return '';
      const parts = raw.split('/');
      const videoIndex = parts.lastIndexOf('video');
      if (videoIndex < 0) return '';
      parts[videoIndex] = 'cover';
      const name = parts[parts.length - 1] || '';
      parts[parts.length - 1] = name.replace(/\.(mp4|mov|m4v)$/i, '.jpg');
      return parts.join('/');
    }

    function deriveLocalPreviewKey(videoKey) {
      const raw = String(videoKey || '').trim();
      if (!raw || !/\.(mp4|mov|m4v)$/i.test(raw)) return '';
      const parts = raw.split('/');
      const videoIndex = parts.lastIndexOf('video');
      if (videoIndex >= 0) {
        parts[videoIndex] = 'preview';
      } else {
        parts.unshift('preview');
      }
      const name = parts[parts.length - 1] || '';
      parts[parts.length - 1] = name.replace(/\.(mp4|mov|m4v)$/i, '.jpg');
      return parts.join('/');
    }

    function buildMediaFsPath(archiveBackend, archiveKey, fallbackPath) {
      if (archiveBackend && archiveBackend !== 'local') {
        return fallbackPath || '';
      }
      if (fallbackPath) {
        return fallbackPath;
      }
      if (archiveKey && state.health?.archiveLocalDir) {
        return `${String(state.health.archiveLocalDir).replace(/\/$/, '')}/${String(archiveKey).replace(/^\//, '')}`;
      }
      return '';
    }

    async function openArchiveFolder(archiveKey, localPath, trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-archive-dir', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ archiveKey, localPath }),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open archive dir failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openUserMaterialFolder(kind, trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-user-material-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind }),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open user material folder failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openUserGeneratedResultsFolder(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-user-generated-results-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open user generated results folder failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openUserGeneratedCoverFolder(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-user-generated-cover-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open user generated cover folder failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openUserGeneratedPreviewFolder(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-user-generated-preview-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open user generated preview folder failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openArchiveArtifactFolder(trigger, kind) {
      const previous = trigger?.textContent || '打开文件夹';
      if (trigger) {
        trigger.textContent = '打开中...';
        trigger.disabled = true;
      }
      try {
        const res = await fetch('/api/archive-artifacts/open', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw buildRequestError(data);
        }
        if (trigger) {
          trigger.textContent = '已打开';
          setTimeout(() => {
            trigger.textContent = previous;
            trigger.disabled = false;
          }, 1200);
        }
      } catch (error) {
        if (trigger) {
          trigger.textContent = '打开失败';
          setTimeout(() => {
            trigger.textContent = previous;
            trigger.disabled = false;
          }, 1600);
        }
        throw error;
      }
    }

    function archiveArtifactCleanupConfirmText(kind) {
      return {
        covers: '只会删除没有对应结果视频的孤儿封面。确定清理吗？',
        'tts-output': '会删除 TTS 输出目录里的配音音频中间产物，已有结果视频不会被删除。确定清理吗？',
        'merge-temp': '会清空视频合并临时媒体目录，已有结果视频不会被删除。确定清理吗？',
        'reference-temp': '会删除参考图图生图临时结果，素材库里的参考图不会被删除。确定清理吗？',
        'extension-archive': '会删除不在生成结果列表中展示的延长视频归档及其封面、预览图。确定清理吗？',
        'extension-frames': '会删除延长功能保存的截帧、修图结果和状态文件。确定清理吗？',
        'html-motion-work': '会删除失败后保留的 HTML 动效诊断工作目录。确定清理吗？',
        'html-motion-reviews': '会删除 HTML 动效审核候选缓存，成品视频不会被删除。确定清理吗？',
        'restored-metadata': '只会删除没有对应结果视频的恢复元数据。确定清理吗？',
        'result-junk': '只会删除结果目录中的系统杂项文件。确定清理吗？',
        manifests: '只会删除没有对应结果视频的孤儿归档元数据。确定清理吗？',
        'asset-index': '会从资产索引中压缩移除已经没有结果视频的孤儿记录。确定清理吗？',
        'recycle-bin': '会清空失败任务回收站里的媒体和记录。确定清理吗？',
      }[String(kind || '')] || '确定清理这个中间产物分类吗？';
    }

    async function refreshArchiveCleanupViews(data) {
      state.archiveArtifacts = data.archiveArtifacts || state.archiveArtifacts || null;
      await refreshAuthSettings();
      await refreshUserGeneratedResults();
      await refreshRecycleBin();
      renderStatus();
      renderProgress();
      renderProgressModal();
      renderResultModal();
      renderRecycleBin();
      renderRecycleBinModal();
    }

    async function cleanupArchiveArtifact(trigger, kind) {
      if (!kind) return;
      if (!window.confirm(archiveArtifactCleanupConfirmText(kind))) return;
      const previous = trigger?.textContent || '清理';
      state.settingsModal.cleaningArchiveArtifactKind = kind;
      state.settingsModal.videoModelError = '';
      state.settingsModal.videoModelNotice = '';
      renderSettingsModal();
      try {
        const res = await fetch('/api/archive-artifacts/cleanup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw buildRequestError(data);
        }
        const deletedCount = Number(data.deletedCount || data.removedCount || 0);
        state.settingsModal.videoModelNotice = deletedCount
          ? `已清理 ${deletedCount} 项`
          : '没有需要清理的项目';
        await refreshArchiveCleanupViews(data);
      } finally {
        state.settingsModal.cleaningArchiveArtifactKind = '';
        renderSettingsModal();
        if (trigger) trigger.textContent = previous;
      }
    }

    async function cleanupAllArchiveArtifacts(trigger) {
      const confirmed = window.confirm('会清理归档中的中间产物、孤儿记录和失败任务；仍在生成结果中展示的视频、有效封面和预览图不会被删除。确定继续吗？');
      if (!confirmed) return;
      state.settingsModal.cleaningArchiveAll = true;
      renderSettingsModal();
      try {
        const res = await fetch('/api/archive-artifacts/cleanup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind: 'all' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw buildRequestError(data);
        await refreshArchiveCleanupViews(data);
      } finally {
        state.settingsModal.cleaningArchiveAll = false;
        renderSettingsModal();
      }
    }

    async function openUserRecycleBinFolder(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-user-recycle-bin-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open user recycle bin folder failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }
