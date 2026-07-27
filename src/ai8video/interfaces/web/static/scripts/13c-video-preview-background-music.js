    function videoPreviewBackgroundMusicDrawer() {
      return els.videoPreviewBody?.querySelector('[data-video-preview-background-music-drawer]');
    }

    function videoPreviewBackgroundMusicAudio() {
      return els.videoPreviewBody?.querySelector('[data-video-preview-background-music-audio]');
    }

    function selectedVideoPreviewBackgroundMusic() {
      const selectedId = String(state.backgroundMusic?.selectedId || '');
      return (state.backgroundMusic?.items || []).find((item) => String(item?.id || '') === selectedId) || null;
    }

    function positionVideoPreviewBackgroundMusicDrawer() {
      const drawer = videoPreviewBackgroundMusicDrawer();
      const controls = drawer?.closest('.video-preview-controls');
      const button = controls?.querySelector('[data-video-preview-action="toggle-background-music"]');
      if (!drawer || !controls || !button || drawer.hidden) return;
      const controlsRect = controls.getBoundingClientRect();
      const buttonRect = button.getBoundingClientRect();
      drawer.style.right = `${Math.max(0, controlsRect.right - buttonRect.right)}px`;
      drawer.style.bottom = `${Math.max(0, controlsRect.bottom - buttonRect.top + 8)}px`;
    }

    function renderVideoPreviewBackgroundMusicDrawer() {
      const drawer = videoPreviewBackgroundMusicDrawer();
      if (!drawer) return;
      const open = Boolean(state.videoPreviewModal?.backgroundMusicDrawerOpen);
      const items = Array.isArray(state.backgroundMusic?.items) ? state.backgroundMusic.items : [];
      drawer.hidden = !open;
      drawer.innerHTML = open ? `
        <div class="video-preview-background-music-head">
          <strong>背景音乐</strong>
          <span>${state.backgroundMusic?.enabled ? `当前：${escapeHtml(state.backgroundMusic.name || '已选择')}` : '当前未选择'}</span>
        </div>
        <div class="video-preview-background-music-list">
          ${items.length ? items.map((item) => `
            <button type="button" class="video-preview-background-music-item ${item.selected ? 'is-selected' : ''}" data-video-preview-background-music-id="${escapeHtml(item.id || '')}">
              <span>${escapeHtml(item.name || item.sourceName || '背景音乐')}</span>
              <small>${item.selected ? '正在使用，再次点击取消' : '点击选择并试听'}</small>
            </button>
          `).join('') : '<span class="video-preview-background-music-empty">主界面背景音乐标签页暂无音乐</span>'}
        </div>
      ` : '';
      positionVideoPreviewBackgroundMusicDrawer();
      drawer.querySelectorAll('[data-video-preview-background-music-id]').forEach((button) => {
        button.addEventListener('click', async () => {
          const id = button.getAttribute('data-video-preview-background-music-id') || '';
          if (id === String(state.backgroundMusic?.selectedId || '')) await clearBackgroundMusicSelection();
          else await selectBackgroundMusic(id);
          renderVideoPreviewBackgroundMusicDrawer();
          syncVideoPreviewBackgroundMusicSource();
        });
      });
    }

    async function toggleVideoPreviewBackgroundMusicDrawer() {
      if (!state.videoPreviewModal) return;
      const open = !state.videoPreviewModal.backgroundMusicDrawerOpen;
      state.videoPreviewModal.backgroundMusicDrawerOpen = open;
      const button = els.videoPreviewBody?.querySelector('[data-video-preview-action="toggle-background-music"]');
      button?.setAttribute('aria-expanded', String(open));
      if (open) await refreshBackgroundMusic();
      renderVideoPreviewBackgroundMusicDrawer();
      syncVideoPreviewBackgroundMusicSource();
    }

    function syncVideoPreviewBackgroundMusicPosition(video, audio) {
      if (!video || !audio || !Number.isFinite(audio.duration) || audio.duration <= 0) return;
      const target = video.currentTime % audio.duration;
      if (Math.abs(audio.currentTime - target) > 0.2) audio.currentTime = target;
    }

    function syncVideoPreviewBackgroundMusicSource() {
      const video = els.videoPreviewBody?.querySelector('.video-preview-large');
      let audio = videoPreviewBackgroundMusicAudio();
      const selected = selectedVideoPreviewBackgroundMusic();
      if (!video || !selected?.previewUrl) {
        audio?.pause();
        audio?.remove();
        return;
      }
      if (!audio) {
        audio = document.createElement('audio');
        audio.dataset.videoPreviewBackgroundMusicAudio = '';
        audio.loop = true;
        audio.preload = 'auto';
        els.videoPreviewBody?.appendChild(audio);
      }
      const nextSrc = new URL(selected.previewUrl, window.location.href).href;
      if (audio.src !== nextSrc) audio.src = nextSrc;
      audio.volume = Math.max(0, Math.min(1, Number(state.backgroundMusic?.volume ?? 0.28)));
      syncVideoPreviewBackgroundMusicPosition(video, audio);
      if (!video.paused) void audio.play().catch(() => {});
    }

    function bindVideoPreviewBackgroundMusic(video) {
      if (!video) return;
      const sync = () => {
        syncVideoPreviewBackgroundMusicSource();
        syncVideoPreviewBackgroundMusicPosition(video, videoPreviewBackgroundMusicAudio());
      };
      video.addEventListener('play', sync);
      video.addEventListener('pause', () => videoPreviewBackgroundMusicAudio()?.pause());
      video.addEventListener('seeking', sync);
      video.addEventListener('timeupdate', sync);
      syncVideoPreviewBackgroundMusicSource();
    }

    function stopVideoPreviewBackgroundMusic() {
      const audio = videoPreviewBackgroundMusicAudio();
      audio?.pause();
      audio?.remove();
    }
