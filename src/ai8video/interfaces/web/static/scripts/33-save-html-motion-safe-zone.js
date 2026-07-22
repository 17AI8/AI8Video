    async function saveHtmlMotionSafeZone() {
      const ratio = flowerTextRatioValue(state.flowerText?.canvasWidth, state.flowerText?.canvasHeight);
      const zone = currentHtmlMotionSafeZone(ratio);
      state.htmlMotionSafeZone = { ...(state.htmlMotionSafeZone || {}), saving: true };
      renderFlowerTextDrawer();
      try {
        const res = await fetch('/api/html-motion-safe-zone', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ aspectRatio: ratio, safeZone: zone }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw new Error(data?.error || 'HTML 动效安全区保存失败');
        state.htmlMotionOverlay = {
          ...(state.htmlMotionOverlay || {}),
          safeZones: { ...(state.htmlMotionOverlay?.safeZones || {}), [ratio]: data.safeZone },
        };
        state.htmlMotionSafeZone = { editing: false, saving: false, draft: null, drag: null };
      } catch (error) {
        state.htmlMotionSafeZone = { ...(state.htmlMotionSafeZone || {}), saving: false };
        state.flowerText = { ...(state.flowerText || {}), error: error?.message || String(error) };
      }
      renderFlowerTextDrawer();
    }

    function flowerTextPreviewWidth(width, height) {
      const w = normalizeFlowerTextSide(width, 9);
      const h = normalizeFlowerTextSide(height, 16);
      return Math.round(Math.min(520, Math.max(180, 400 * (w / h))));
    }

    function flowerTextPreviewTargetSize(width, height) {
      const w = normalizeFlowerTextSide(width, 9);
      const h = normalizeFlowerTextSide(height, 16);
      if (w === 16 && h === 9) return { width: 720, height: 405 };
      if (w === 1 && h === 1) return { width: 720, height: 720 };
      return { width: 405, height: 720 };
    }

    function flowerTextPreviewFontSize(width, height, fontSize) {
      const w = normalizeFlowerTextSide(width, 9);
      const h = normalizeFlowerTextSide(height, 16);
      const shortestSide = Math.min(flowerTextPreviewWidth(w, h), 400);
      return Math.round(Math.min(54, Math.max(6, shortestSide * normalizeFlowerTextPercent(fontSize, 16, 6, 28) / 100)));
    }

    function normalizeFlowerTextPercent(value, fallback, minimum, maximum) {
      const number = Number.parseInt(String(value ?? ''), 10);
      if (!Number.isFinite(number)) return fallback;
      return Math.min(maximum, Math.max(minimum, number));
    }

    function normalizeFlowerTextWeight(value, fallback = 800) {
      const number = Math.round(Number.parseFloat(String(value ?? '')) / 100) * 100;
      if (!Number.isFinite(number)) return fallback;
      return Math.min(900, Math.max(300, number));
    }

    function normalizeFlowerTextAnimationDelay(value) {
      const number = Number.parseInt(String(value ?? ''), 10);
      return [0, 1, 3, 5, 10].includes(number) ? number : 0;
    }

    function normalizeFlowerTextAnimationType(value) {
      return String(value || '').trim() === 'none' ? 'none' : 'fade';
    }

    function normalizeFlowerTextCoordinate(value, fallback) {
      const number = Number.parseInt(String(value ?? ''), 10);
      if (!Number.isFinite(number)) return fallback;
      return Math.min(95, Math.max(5, number));
    }

    function normalizeFlowerTextColor(value, fallback) {
      let text = String(value || '').trim();
      if (/^#[0-9a-fA-F]{3}$/.test(text)) {
        text = `#${text.slice(1).split('').map((char) => char + char).join('')}`;
      }
      if (!/^#[0-9a-fA-F]{6}$/.test(text)) return fallback;
      return text.toLowerCase();
    }

    function normalizeFlowerTextFonts(value) {
      if (!Array.isArray(value)) return [];
      const seen = new Set();
      return value.map((item) => {
        const id = String(item?.id || '').trim();
        if (!id || id.startsWith('.') || id.includes('..') || seen.has(id)) return null;
        seen.add(id);
        return {
          id,
          name: String(item?.name || id.split('/').pop() || id).trim(),
          fontUrl: String(item?.fontUrl || '').trim(),
          previewUrl: String(item?.previewUrl || '').trim(),
        };
      }).filter(Boolean);
    }

    function flowerTextHash(value) {
      let hash = 2166136261;
      const text = String(value || '');
      for (let index = 0; index < text.length; index += 1) {
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return (hash >>> 0).toString(36);
    }

    function flowerTextCssString(value) {
      return String(value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, ' ');
    }

    function flowerTextFontCssName(font) {
      return `FlowerTextFont-${flowerTextHash(font?.id || font?.fontUrl || '')}`;
    }

    function flowerTextFontFormat(fontUrl) {
      const path = String(fontUrl || '').split('?')[0].toLowerCase();
      if (path.endsWith('.ttf')) return 'truetype';
      if (path.endsWith('.otf')) return 'opentype';
      if (path.endsWith('.ttc')) return 'truetype-collection';
      return 'opentype';
    }

    function ensureFlowerTextFontFaces(fonts) {
      const available = normalizeFlowerTextFonts(fonts).filter((font) => font.fontUrl);
      const signature = available.map((font) => `${font.id}|${font.fontUrl}`).join('\n');
      if (signature === flowerTextFontFaceSignature) return;
      flowerTextFontFaceSignature = signature;
      if (!flowerTextFontStyleEl) {
        flowerTextFontStyleEl = document.createElement('style');
        flowerTextFontStyleEl.id = 'flowerTextDynamicFonts';
        document.head.appendChild(flowerTextFontStyleEl);
      }
      flowerTextFontStyleEl.textContent = available.map((font) => {
        const family = flowerTextCssString(flowerTextFontCssName(font));
        const url = flowerTextCssString(font.fontUrl);
        return `@font-face{font-family:"${family}";src:url("${url}") format("${flowerTextFontFormat(font.fontUrl)}");font-display:swap;}`;
      }).join('\n');
    }

    function flowerTextEditorFontFamily(font) {
      if (!font?.fontUrl) return 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      return `"${flowerTextCssString(flowerTextFontCssName(font))}", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    }

    function normalizeFlowerTextFamily(value, fonts) {
      const id = String(value || '').trim();
      if (!id) return '';
      const available = normalizeFlowerTextFonts(fonts);
      return available.some((font) => font.id === id) ? id : '';
    }

    function normalizeFlowerTextWatermarkImage(value, images) {
      const id = normalizeUserMaterialImageRelativePath(value);
      if (!id) return '';
      // 素材列表刷新有时会晚一步；前端只保留安全路径，文件是否真实存在交给后端确认。
      return id;
    }

    function normalizeUserMaterialImageRelativePath(value) {
      const text = String(value || '').trim().replace(/\\/g, '/').replace(/^\/+/, '');
      if (!text || text.startsWith('.')) return '';
      const parts = text.split('/');
      if (parts.some((part) => !part || part === '.' || part === '..')) return '';
      if (!/\.(jpe?g|png|webp|gif|bmp)$/i.test(parts[parts.length - 1] || '')) return '';
      return parts.join('/');
    }

    function userMaterialImageUrl(relativePath) {
      const cleanPath = normalizeUserMaterialImageRelativePath(relativePath);
      if (!cleanPath) return '';
      return `/user-materials/images/${cleanPath.split('/').map((part) => encodeURIComponent(part)).join('/')}`;
    }

    function flowerTextWatermarkUrl(relativePath) {
      const cleanPath = normalizeUserMaterialImageRelativePath(relativePath);
      if (!cleanPath) return '';
      return `/user-materials/flower-watermarks/${cleanPath.split('/').map((part) => encodeURIComponent(part)).join('/')}`;
    }

    function getFlowerTextWatermarkItem(value, images) {
      const relativePath = normalizeUserMaterialImageRelativePath(value);
      if (!relativePath) return null;
      const available = Array.isArray(images) ? images : [];
      const item = available.find((entry) => normalizeUserMaterialImageRelativePath(entry?.relativePath || entry?.name || '') === relativePath);
      if (item) {
        return {
          ...item,
          relativePath,
          name: item.name || extractFileName(relativePath) || '水印图片',
          url: item.url || flowerTextWatermarkUrl(relativePath),
        };
      }
      return {
        kind: 'flower-watermark',
        relativePath,
        name: extractFileName(relativePath) || '水印图片',
        url: flowerTextWatermarkUrl(relativePath),
      };
    }

    function normalizeUploadedFlowerWatermarkItems(items) {
      return (Array.isArray(items) ? items : [])
        .map((item) => {
          const relativePath = normalizeUserMaterialImageRelativePath(item?.relativePath || item?.name || '');
          if (!relativePath) return null;
          return {
            ...(item || {}),
            kind: 'flower-watermark',
            relativePath,
            name: String(item?.name || extractFileName(relativePath) || '水印图片'),
            url: flowerTextWatermarkUrl(relativePath),
          };
        })
        .filter(Boolean);
    }

    function normalizeUploadedImageMaterialItems(items) {
      return (Array.isArray(items) ? items : [])
        .map((item) => {
          const relativePath = normalizeUserMaterialImageRelativePath(item?.relativePath || item?.name || '');
          if (!relativePath) return null;
          return {
            ...(item || {}),
            kind: 'image',
            relativePath,
            name: String(item?.name || extractFileName(relativePath) || '水印图片'),
            url: userMaterialImageUrl(relativePath),
          };
        })
        .filter(Boolean);
    }

    function rememberUploadedImageMaterials(items) {
      const uploaded = normalizeUploadedImageMaterialItems(items);
      if (!uploaded.length) return;
      const existing = Array.isArray(state.userMaterials?.images) ? state.userMaterials.images : [];
      const uploadedPaths = new Set(uploaded.map((item) => item.relativePath));
      const images = [
        ...uploaded,
        ...existing.filter((item) => !uploadedPaths.has(normalizeUserMaterialImageRelativePath(item?.relativePath || item?.name || ''))),
      ];
      state.userMaterials = {
        ...(state.userMaterials || {}),
        images,
        imageCount: Math.max(Number(state.userMaterials?.imageCount || 0) || 0, images.length),
      };
    }

    function rememberUploadedFlowerWatermarks(items) {
      const uploaded = normalizeUploadedFlowerWatermarkItems(items);
      if (!uploaded.length) return;
      state.userMaterials = {
        ...(state.userMaterials || {}),
        flowerWatermarks: uploaded,
        flowerWatermarkCount: uploaded.length,
      };
    }

    async function ensureFlowerWatermarkLibraryReady() {
      if (state.userMaterials?.flowerWatermarkDir) return true;
      try {
        await refreshUserMaterials();
      } catch (error) {
        throw new Error('花字水印库加载失败，请稍后重试');
      }
      if (!state.userMaterials?.flowerWatermarkDir) {
        throw new Error('花字水印库还未加载，请重启AI8video 后再上传水印');
      }
      return true;
    }

    function normalizeFlowerTextPosition(value) {
      const text = String(value || '').trim().toLowerCase();
      if (['top', 'center', 'bottom'].includes(text)) return text;
      return 'center';
    }

    function normalizeFlowerTextWatermarkPosition(value) {
      const text = String(value || '').trim().toLowerCase();
      if (['top-left', 'top-right', 'bottom-left', 'bottom-right', 'center'].includes(text)) return text;
      return 'bottom-right';
    }

    function flowerTextWatermarkPositionX(value) {
      const position = normalizeFlowerTextWatermarkPosition(value);
      if (position === 'top-left' || position === 'bottom-left') return 8;
      if (position === 'top-right' || position === 'bottom-right') return 92;
      return 50;
    }

    function flowerTextWatermarkPositionY(value) {
      const position = normalizeFlowerTextWatermarkPosition(value);
      if (position === 'top-left' || position === 'top-right') return 8;
      if (position === 'bottom-left' || position === 'bottom-right') return 92;
      return 50;
    }

    function flowerTextPositionY(value) {
      const position = normalizeFlowerTextPosition(value);
      if (position === 'top') return 18;
      if (position === 'bottom') return 82;
      return 50;
    }

    function flowerTextStylePatch(key, value) {
      if (key === 'textColor') return { textColor: normalizeFlowerTextColor(value, '#ffee43') };
      if (key === 'strokeColor') return { strokeColor: normalizeFlowerTextColor(value, '#121826') };
      if (key === 'fontFamily') return { fontFamily: normalizeFlowerTextFamily(value, state.flowerText?.availableFonts) };
      if (key === 'fontSize') return { fontSize: normalizeFlowerTextPercent(value, 16, 6, 28) };
      if (key === 'fontWeight') return { fontWeight: normalizeFlowerTextWeight(value, 800) };
      if (key === 'strokeWidth') return { strokeWidth: normalizeFlowerTextPercent(value, 8, 0, 18) };
      if (key === 'animationDelaySeconds') return { animationDelaySeconds: normalizeFlowerTextAnimationDelay(value) };
      if (key === 'animationType') return { animationType: normalizeFlowerTextAnimationType(value) };
      if (key === 'animationType') return { animationType: normalizeFlowerTextAnimationType(value) };
      if (key === 'watermarkImage') return { watermarkImage: normalizeFlowerTextWatermarkImage(value, state.userMaterials?.flowerWatermarks) };
      if (key === 'watermarkSize') return { watermarkSize: normalizeFlowerTextPercent(value, 18, 5, 200) };
      if (key === 'watermark2Size') return { watermark2Size: normalizeFlowerTextPercent(value, 18, 5, 200) };
      if (key === 'watermarkOpacity') return { watermarkOpacity: normalizeFlowerTextPercent(value, 100, 5, 100) };
      if (key === 'watermarkAnimationDelaySeconds') return { watermarkAnimationDelaySeconds: normalizeFlowerTextAnimationDelay(value) };
      if (key === 'watermarkAnimationType') return { watermarkAnimationType: normalizeFlowerTextAnimationType(value) };
      if (key === 'watermarkAnimationType') return { watermarkAnimationType: normalizeFlowerTextAnimationType(value) };
      if (key === 'watermarkAnimationType') return { watermarkAnimationType: normalizeFlowerTextAnimationType(value) };
      if (key === 'watermark2Opacity') return { watermark2Opacity: normalizeFlowerTextPercent(value, 100, 5, 100) };
      if (key === 'watermark2AnimationDelaySeconds') return { watermark2AnimationDelaySeconds: normalizeFlowerTextAnimationDelay(value) };
      if (key === 'watermark2AnimationType') return { watermark2AnimationType: normalizeFlowerTextAnimationType(value) };
      if (key === 'watermark2AnimationType') return { watermark2AnimationType: normalizeFlowerTextAnimationType(value) };
      if (key === 'watermark2AnimationType') return { watermark2AnimationType: normalizeFlowerTextAnimationType(value) };
      if (key === 'watermarkPosition') return { watermarkPosition: normalizeFlowerTextWatermarkPosition(value) };
      if (key === 'watermark2Position') return { watermark2Position: normalizeFlowerTextWatermarkPosition(value) };
      if (key === 'position') {
        const position = normalizeFlowerTextPosition(value);
        return { position, textY: flowerTextPositionY(position) };
      }
      return null;
    }

    async function openBackgroundMusicFolder(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-background-music-folder', { method: 'POST' });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open background music folder failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openLocalTtsVoiceCloneFolder(trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-local-tts-voice-clone-folder', { method: 'POST' });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open local tts voice clone folder failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function uploadBackgroundMusic(file) {
      state.backgroundMusic = {
        ...(state.backgroundMusic || {}),
        uploading: true,
        error: '',
      };
      renderBackgroundMusicButton();
      try {
        const formData = new FormData();
        formData.append('file', file, file.name);
        const res = await fetch('/api/background-music', {
          method: 'POST',
          body: formData,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || '背景音乐上传失败');
        }
        state.backgroundMusic = {
          ...data,
          uploading: false,
          error: '',
        };
      } catch (error) {
        state.backgroundMusic = {
          ...(state.backgroundMusic || {}),
          uploading: false,
          error: error?.message || String(error),
        };
      } finally {
        renderBackgroundMusicButton();
        renderBackgroundMusicDrawer();
      }
    }

    async function uploadLocalTtsVoiceClone(file) {
      state.localTts = {
        ...(state.localTts || {}),
        cloneUploading: true,
        error: '',
      };
      renderSettingsModal();
      try {
        const formData = new FormData();
        formData.append('file', file, file.name);
        const res = await fetch('/api/local-tts/voice-clone', {
          method: 'POST',
          body: formData,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw new Error(data?.error || '音色克隆样本上传失败');
        }
        state.localTts = {
          ...data,
          cloneUploading: false,
          error: '',
        };
        await refreshAuthSettings();
      } catch (error) {
        state.localTts = {
          ...(state.localTts || {}),
          cloneUploading: false,
          error: error?.message || String(error),
        };
        state.settingsModal.videoModelError = error?.message || String(error);
      } finally {
        renderSettingsModal();
      }
    }

    async function uploadUserMaterials(kind, files) {
      const formData = new FormData();
      const normalizedKind = kind === 'script' ? 'script' : kind === 'flower-watermark' ? 'flower-watermark' : 'image';
      formData.append('kind', normalizedKind);
      files.forEach((file) => formData.append('files', file, file.name));
      const res = await fetch('/api/upload-user-material', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || 'upload user material failed');
      }
      return data;
    }

    async function openBatchReport(reportPath, trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-batch-report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reportPath }),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open batch report failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

    async function openBatchAlert(alertPath, trigger) {
      const previous = trigger.textContent;
      trigger.textContent = '打开中...';
      const res = await fetch('/api/open-batch-alert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alertPath }),
      });
      if (!res.ok) {
        trigger.textContent = '打开失败';
        setTimeout(() => { trigger.textContent = previous; }, 1600);
        throw new Error('open batch alert failed');
      }
      trigger.textContent = '已打开';
      setTimeout(() => { trigger.textContent = previous; }, 1200);
    }

