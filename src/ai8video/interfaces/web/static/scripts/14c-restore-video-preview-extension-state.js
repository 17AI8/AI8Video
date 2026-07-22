    function restoreVideoPreviewExtensionState(video, userGeneratedKey, button) {
      const savedState = loadVideoPreviewExtensionStates()[String(userGeneratedKey || '').trim()];
      if (!savedState?.active || !video || !button) return;
      const restoredVideoUrl = String(savedState.rightVideoUrl || savedState.videoUrl || '').trim();
      const restoredVideoKey = String(
        savedState.rightVideoKey
        || savedState.userGeneratedKey
        || restoredVideoUrl.replace(/^\/user-generated-results\//, ''),
      ).trim();
      const renderSavedFrame = async () => {
        video.pause();
        await prepareVideoExtensionPreview(userGeneratedKey, button, savedState);
        if (restoredVideoUrl) {
          setVideoPreviewExtensionVideo(restoredVideoUrl, restoredVideoKey);
          return;
        }
        const frameKey = String(savedState.frameKey || '').trim();
        if (!frameKey) return;
        try {
          const res = await fetch('/api/user-generated-results/extension-video/batch-status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ frames: [{ frameKey }] }),
          });
          const data = await res.json().catch(() => ({}));
          const restored = Array.isArray(data.videos) ? data.videos[0] : null;
          if (res.ok && data.ok && restored?.status === 'completed') {
            setVideoPreviewExtensionVideo(restored.videoUrl, restored.userGeneratedKey);
          }
        } catch (_) {
          // 只读恢复失败时保留现有截图，不触发生成。
        }
      };
      const seekSavedFrame = () => {
        video.pause();
        const targetTime = Math.max(0, Math.min(Number(savedState.frameTime || 0), video.duration || Infinity));
        if (Math.abs(video.currentTime - targetTime) < 0.04) {
          void renderSavedFrame();
          return;
        }
        video.addEventListener('seeked', () => void renderSavedFrame(), { once: true });
        video.currentTime = targetTime;
      };
      if (video.readyState >= 2) seekSavedFrame();
      else video.addEventListener('loadeddata', seekSavedFrame, { once: true });
    }
