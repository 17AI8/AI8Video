    function getViralBreakdownTranscriptSegments(videoKey, fallbackSegments = []) {
      const key = String(videoKey || '').trim();
      const drafts = state.viralBreakdown.transcriptSegmentDrafts || {};
      const source = Object.prototype.hasOwnProperty.call(drafts, key) ? drafts[key] : fallbackSegments;
      return Array.isArray(source) ? source.map((segment) => ({ ...segment })) : [];
    }

    function transcriptSegmentSignature(segments) {
      return JSON.stringify((Array.isArray(segments) ? segments : []).map((segment) => ({
        start: Number(segment.start || 0),
        end: Number(segment.end || 0),
        text: String(segment.text || ''),
        deleted: !!segment.deleted,
        audioUrl: String(segment.audioUrl || ''),
      })));
    }

    function hasViralBreakdownTranscriptSegmentChanges(item) {
      if (!item?.videoKey) return false;
      const drafts = state.viralBreakdown.transcriptSegmentDrafts || {};
      const key = String(item.videoKey || '');
      if (!Object.prototype.hasOwnProperty.call(drafts, key)) return false;
      return transcriptSegmentSignature(drafts[key]) !== transcriptSegmentSignature(item.transcriptSegments);
    }

    function formatViralBreakdownTime(seconds) {
      const value = Math.max(0, Number(seconds) || 0);
      const minutes = Math.floor(value / 60);
      const remainder = value - minutes * 60;
      return `${String(minutes).padStart(2, '0')}:${remainder.toFixed(1).padStart(4, '0')}`;
    }

    function buildTranscriptChunkMarkup(segment) {
      const index = Number(segment.index);
      const deleted = !!segment.deleted;
      const audioReady = !!segment.audioUrl;
      return `
        <article class="viral-transcript-chunk${deleted ? ' is-deleted' : ''}" data-transcript-chunk-index="${index}">
          <div class="viral-transcript-chunk-top">
            <button type="button" class="viral-transcript-drag-handle" data-transcript-drag-handle="${index}" title="拖拽换位" aria-label="拖拽换位">⠿</button>
            <button type="button" class="viral-transcript-time-button" data-transcript-seek="${Number(segment.start) || 0}" data-transcript-end="${Number(segment.end) || 0}" data-transcript-index="${index}" title="播放该时间段">
              <span aria-hidden="true">▶</span>${formatViralBreakdownTime(segment.start)} → ${formatViralBreakdownTime(segment.end)}
            </button>
            <span class="viral-transcript-audio-state${audioReady ? ' is-ready' : ''}">${audioReady ? '已重新配音' : ''}</span>
            <button type="button" class="viral-transcript-chunk-action" data-transcript-tts="${index}" ${deleted ? 'disabled' : ''}>重新配音</button>
            <button type="button" class="viral-transcript-chunk-action is-danger" data-transcript-delete="${index}">${deleted ? '恢复' : '删除'}</button>
          </div>
          <textarea rows="2" spellcheck="false" data-transcript-text="${index}" placeholder="${deleted ? '该时间槽已留空' : '输入该时间段台词'}">${escapeHtml(deleted ? '' : String(segment.text || ''))}</textarea>
        </article>`;
    }

    function buildViralBreakdownTranscriptTreeMarkup(segments) {
      const groups = new Map();
      segments.forEach((segment, index) => {
        const minute = Math.floor((Number(segment.start) || 0) / 60);
        if (!groups.has(minute)) groups.set(minute, []);
        groups.get(minute).push({ ...segment, index });
      });
      const branches = [...groups.entries()].map(([minute, items], groupIndex) => `
        <section class="viral-transcript-preview-group ${groupIndex === 0 ? 'is-open' : ''}">
          <button type="button" class="viral-transcript-preview-group-toggle" data-transcript-tree-toggle>
            <span class="script-knowledge-tree-chevron" aria-hidden="true"></span>
            <strong>${formatViralBreakdownTime(minute * 60)}—${formatViralBreakdownTime((minute + 1) * 60)}</strong>
            <span>${items.length} 段</span>
          </button>
          <div class="viral-transcript-preview-group-body">${items.map(buildTranscriptChunkMarkup).join('')}</div>
        </section>`).join('');
      return `<div class="viral-transcript-tree viral-transcript-preview">${branches}</div>`;
    }

    function commitViralBreakdownTranscriptDraft(videoKey, segments) {
      const key = String(videoKey || '');
      const next = segments.map((segment) => ({ ...segment }));
      const text = next.filter((segment) => !segment.deleted).map((segment) => String(segment.text || '').trim()).filter(Boolean).join('\n');
      state.viralBreakdown.transcriptSegmentDrafts = { ...(state.viralBreakdown.transcriptSegmentDrafts || {}), [key]: next };
      state.viralBreakdown.transcriptDrafts = { ...(state.viralBreakdown.transcriptDrafts || {}), [key]: text };
      document.getElementById('viralBreakdownTranscriptUnsaved')?.classList.remove('hidden');
      const saveButton = document.getElementById('viralBreakdownSaveTranscriptButton');
      if (saveButton) {
        saveButton.disabled = false;
        saveButton.textContent = '保存台词';
      }
    }

    function moveViralBreakdownTranscriptContent(segments, fromIndex, toIndex) {
      const payloads = segments.map((segment) => ({
        text: String(segment.text || ''),
        deleted: !!segment.deleted,
        audioUrl: String(segment.audioUrl || ''),
      }));
      const [moved] = payloads.splice(fromIndex, 1);
      payloads.splice(toIndex, 0, moved);
      return segments.map((slot, index) => ({ ...slot, ...payloads[index] }));
    }

    function bindTranscriptChunkDrag(pane, videoKey, segments) {
      pane.querySelectorAll('[data-transcript-chunk-index]').forEach((chunk) => {
        const handle = chunk.querySelector('[data-transcript-drag-handle]');
        handle.onpointerdown = (event) => beginViralBreakdownTranscriptDrag(event, pane, chunk, videoKey, segments);
        handle.onmousedown = (event) => beginViralBreakdownTranscriptDrag(event, pane, chunk, videoKey, segments);
      });
    }

    function beginViralBreakdownTranscriptDrag(event, pane, chunk, videoKey, segments) {
      if (chunk.dataset.dragActive === 'true') return;
      event.preventDefault();
      chunk.dataset.dragActive = 'true';
      chunk.classList.add('is-dragging');
      const fromIndex = Number(chunk.dataset.transcriptChunkIndex);
      const pointerMode = String(event.type).startsWith('pointer');
      const moveEvent = pointerMode ? 'pointermove' : 'mousemove';
      const endEvents = pointerMode ? ['pointerup', 'pointercancel'] : ['mouseup'];
      let toIndex = fromIndex;
      const move = (moveDetails) => {
        const target = document.elementFromPoint(moveDetails.clientX, moveDetails.clientY)?.closest('[data-transcript-chunk-index]');
        if (!target) return;
        toIndex = Number(target.dataset.transcriptChunkIndex);
        pane.querySelectorAll('.is-drop-target').forEach((item) => item.classList.remove('is-drop-target'));
        target.classList.add('is-drop-target');
      };
      const end = () => {
        window.removeEventListener(moveEvent, move);
        endEvents.forEach((name) => window.removeEventListener(name, end));
        pane.querySelectorAll('.is-drop-target').forEach((item) => item.classList.remove('is-drop-target'));
        chunk.classList.remove('is-dragging');
        delete chunk.dataset.dragActive;
        if (!Number.isInteger(toIndex) || fromIndex === toIndex) return;
        commitViralBreakdownTranscriptDraft(videoKey, moveViralBreakdownTranscriptContent(segments, fromIndex, toIndex));
        renderViralBreakdownWorkbench();
      };
      window.addEventListener(moveEvent, move);
      endEvents.forEach((name) => window.addEventListener(name, end));
    }

    async function regenerateViralBreakdownTranscriptChunk(videoKey, segments, index, button) {
      const segment = segments[index];
      const text = String(segment?.text || '').trim();
      if (!text || button.disabled) return;
      button.disabled = true;
      button.textContent = '配音中...';
      try {
        const res = await fetch('/api/viral-breakdown/transcript-segment-tts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ videoKey, text, start: segment.start, end: segment.end }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) throw new Error(data?.error || '单段配音失败');
        segments[index] = { ...segment, audioUrl: String(data.audioUrl || ''), deleted: false };
        commitViralBreakdownTranscriptDraft(videoKey, segments);
        renderViralBreakdownWorkbench();
      } catch (error) {
        window.alert(error?.message || '单段配音失败');
        button.disabled = false;
        button.textContent = '重新配音';
      }
    }

    function bindViralBreakdownTranscriptEditor(pane, item, segments) {
      pane.querySelectorAll('[data-transcript-tree-toggle]').forEach((button) => {
        button.onclick = () => button.closest('.viral-transcript-preview-group')?.classList.toggle('is-open');
      });
      pane.querySelectorAll('[data-transcript-text]').forEach((editor) => {
        editor.oninput = () => {
          const index = Number(editor.dataset.transcriptText);
          const deleted = !String(editor.value || '').trim();
          segments[index] = { ...segments[index], text: editor.value, deleted, audioUrl: '' };
          const chunk = editor.closest('.viral-transcript-chunk');
          chunk?.classList.toggle('is-deleted', deleted);
          const ttsButton = chunk?.querySelector('[data-transcript-tts]');
          const deleteButton = chunk?.querySelector('[data-transcript-delete]');
          if (ttsButton) ttsButton.disabled = deleted;
          if (deleteButton) deleteButton.textContent = deleted ? '恢复' : '删除';
          commitViralBreakdownTranscriptDraft(item.videoKey, segments);
        };
      });
      bindViralBreakdownTranscriptActions(pane, item, segments);
      bindTranscriptChunkDrag(pane, item.videoKey, segments);
    }

    function bindViralBreakdownTranscriptActions(pane, item, segments) {
      pane.querySelectorAll('[data-transcript-seek]').forEach((button) => {
        button.onclick = () => {
          const video = document.querySelector('#viralBreakdownOriginalPane video');
          const segment = segments[Number(button.dataset.transcriptIndex)];
          if (video instanceof HTMLVideoElement && segment) playViralBreakdownTranscriptRange(video, segment, button);
        };
      });
      pane.querySelectorAll('[data-transcript-tts]').forEach((button) => {
        button.onclick = () => regenerateViralBreakdownTranscriptChunk(item.videoKey, segments, Number(button.dataset.transcriptTts), button);
      });
      pane.querySelectorAll('[data-transcript-delete]').forEach((button) => {
        button.onclick = () => {
          const index = Number(button.dataset.transcriptDelete);
          const deleted = !segments[index]?.deleted;
          segments[index] = { ...segments[index], text: deleted ? '' : String(item.transcriptSegments?.[index]?.text || ''), deleted, audioUrl: '' };
          commitViralBreakdownTranscriptDraft(item.videoKey, segments);
          renderViralBreakdownWorkbench();
        };
      });
    }

    function playViralBreakdownTranscriptRange(video, segment, button) {
      video.__viralTranscriptFinish?.();
      const start = Number(segment.start) || 0;
      const end = Number(segment.end) || start;
      const previousMuted = video.muted;
      const audio = segment.audioUrl ? new Audio(segment.audioUrl) : null;
      document.querySelectorAll('.viral-transcript-chunk.is-playing').forEach((item) => item.classList.remove('is-playing'));
      const chunk = button.closest('.viral-transcript-chunk');
      chunk?.classList.add('is-playing');
      const finish = () => {
        video.pause();
        video.currentTime = start;
        video.muted = previousMuted;
        audio?.pause();
        chunk?.classList.remove('is-playing');
        window.clearInterval(video.__viralTranscriptRangeTimer);
        video.__viralTranscriptRangeTimer = null;
        video.__viralTranscriptAudio = null;
        video.__viralTranscriptFinish = null;
      };
      video.currentTime = start;
      video.muted = !!audio || previousMuted;
      video.__viralTranscriptAudio = audio;
      video.__viralTranscriptFinish = finish;
      video.__viralTranscriptRangeTimer = window.setInterval(() => {
        if (video.currentTime >= end - 0.03) finish();
      }, 25);
      video.play().catch(finish);
      audio?.play().catch(finish);
    }

    function renderViralBreakdownTranscriptTree({ pane, item, displayText, meta, saveButton }) {
      if (!item?.videoKey) {
        pane.innerHTML = '<div class="viral-breakdown-empty">点击“分析台词”后，这里会显示 Whisper 识别到的文本。</div>';
        return;
      }
      const segments = getViralBreakdownTranscriptSegments(item.videoKey, item.transcriptSegments);
      if (segments.length) {
        pane.innerHTML = buildViralBreakdownTranscriptTreeMarkup(segments);
        bindViralBreakdownTranscriptEditor(pane, item, segments);
        return;
      }
      pane.innerHTML = displayText
        ? `<div class="viral-transcript-legacy-note">当前文本没有可用时间轴，重新“分析台词”即可生成。</div><textarea class="viral-breakdown-text-output viral-breakdown-text-editor" spellcheck="false">${escapeHtml(displayText)}</textarea>`
        : '<div class="viral-breakdown-empty">点击“分析台词”后，这里会显示带时间轴的 Whisper 台词。</div>';
    }
