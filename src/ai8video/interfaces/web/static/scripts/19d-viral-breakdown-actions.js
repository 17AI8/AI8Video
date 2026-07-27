    function beginViralBreakdownUpload() {
      if (isViralBreakdownBusy()) return;
      const input = document.getElementById('viralBreakdownUploadInput');
      if (!input) return;
      input.click();
    }

    async function uploadViralBreakdownVideos(files) {
      if (isViralBreakdownBusy() || !Array.from(files || []).length) return;
      const formData = new FormData();
      Array.from(files || []).forEach((file) => formData.append('files', file, file.name));
      state.viralBreakdown.uploading = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在上传视频...';
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/upload', {
          method: 'POST',
          body: formData,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '上传爆款拆解视频失败');
        }
        state.viralBreakdown.notice = Array.isArray(data?.saved) && data.saved.length
          ? `已上传 ${data.saved.length} 个视频`
          : '没有新增视频';
        await refreshViralBreakdownWorkspace({ keepSelection: false });
      } finally {
        state.viralBreakdown.uploading = false;
        renderViralBreakdownWorkbench();
      }
    }

    async function openViralBreakdownFolder(trigger) {
      const previous = trigger?.textContent || '打开文件夹';
      if (trigger) trigger.textContent = '打开中...';
      const res = await fetch('/api/open-viral-breakdown-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        if (trigger) {
          trigger.textContent = '打开失败';
          setTimeout(() => { trigger.textContent = previous; }, 1600);
        }
        throw new Error('open viral breakdown folder failed');
      }
      if (trigger) {
        trigger.textContent = '已打开';
        setTimeout(() => { trigger.textContent = previous; }, 1200);
      }
    }

    async function processSelectedViralBreakdownFrames() {
      if (isViralBreakdownBusy()) return;
      const currentItem = getSelectedViralBreakdownItem();
      if (!currentItem?.videoKey) return;
      closeViralBreakdownVideoMenu();
      state.viralBreakdown.frameProcessing = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在按设定间隔截图并拼接宫格图...';
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/process-frames', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            videoKey: currentItem.videoKey,
            intervalSeconds: Number(state.viralBreakdown.intervalSeconds || 1),
            targetRatio: String(state.viralBreakdown.targetRatio || '16:9'),
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '拆解画面失败');
        }
        state.viralBreakdown.notice = `已完成 ${Number(data?.frameCount || 0) || 0} 张截图，并拼成 ${String(data?.targetRatio || state.viralBreakdown.targetRatio)}`;
        await refreshViralBreakdownWorkspace({ keepSelection: true });
      } finally {
        state.viralBreakdown.frameProcessing = false;
        renderViralBreakdownWorkbench();
      }
    }

    async function analyzeSelectedViralBreakdownShotLanguage() {
      if (isViralBreakdownBusy()) return;
      const currentItem = getSelectedViralBreakdownItem();
      if (
        !currentItem?.videoKey
        || !hasViralBreakdownFrames(currentItem)
        || !hasViralBreakdownTranscript(currentItem)
      ) return;
      const requestedVideoKey = String(currentItem.videoKey || '');
      closeViralBreakdownVideoMenu();
      state.viralBreakdown.shotLanguageProcessing = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在分析代表帧的镜头语言...';
      activateViralBreakdownTab('shot-language');
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/analyze-shot-language', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ videoKey: currentItem.videoKey }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '分析镜头语言失败');
        }
        await refreshViralBreakdownWorkspace({ keepSelection: true });
        if (String(state.viralBreakdown.selectedVideoKey || '') !== requestedVideoKey) return;
        const frameCount = Number(data?.inputFrameCount || data?.selectedFrames?.length || 0);
        const batchCount = Number(data?.imageBatchCount || 0);
        state.viralBreakdown.notice = frameCount
          ? `镜头语言分析完成，已全量分析 ${frameCount} 张截图（${batchCount} 批）`
          : '镜头语言分析完成';
      } finally {
        state.viralBreakdown.shotLanguageProcessing = false;
        renderViralBreakdownWorkbench();
      }
    }

    async function transcribeSelectedViralBreakdownVideo() {
      if (isViralBreakdownBusy()) return;
      const currentItem = getSelectedViralBreakdownItem();
      if (!currentItem?.videoKey || !hasViralBreakdownFrames(currentItem)) return;
      closeViralBreakdownVideoMenu();
      state.viralBreakdown.transcriptProcessing = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在调用 Whisper 识别台词...';
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/transcribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            videoKey: currentItem.videoKey,
            model: 'base',
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '分析台词失败');
        }
        state.viralBreakdown.transcriptDrafts = {
          ...(state.viralBreakdown.transcriptDrafts || {}),
          [String(currentItem.videoKey || '')]: String(data?.text || ''),
        };
        state.viralBreakdown.notice = data?.text ? '台词识别完成' : '没有识别到可用台词';
        await refreshViralBreakdownWorkspace({ keepSelection: true });
      } finally {
        state.viralBreakdown.transcriptProcessing = false;
        renderViralBreakdownWorkbench();
      }
    }

    async function guessSelectedViralBreakdownScript(options = {}) {
      if (isViralBreakdownBusy()) return;
      const resumeFrom = String(options.resumeFrom || 'full').trim() === 'tree' ? 'tree' : 'full';
      const currentItem = getSelectedViralBreakdownItem();
      if (
        !currentItem?.videoKey
        || !hasCurrentViralBreakdownShotLanguage(currentItem)
        || hasUnsavedViralBreakdownTranscript(currentItem)
      ) return;
      const transcriptTextFromItem = String(currentItem?.transcriptText || '');
      const transcriptText = getViralBreakdownTranscriptDraft(currentItem.videoKey, transcriptTextFromItem);
      const normalizedVideoKey = String(currentItem.videoKey || '');
      closeViralBreakdownVideoMenu();
      let failedStage = resumeFrom === 'tree' ? 'tree' : 'skeleton';
      let streamedScriptText = '';
      state.viralBreakdown.error = '';
      state.viralBreakdown.scriptResumeStage = '';
      activateViralBreakdownTab('script');
      try {
        if (resumeFrom === 'tree') {
          streamedScriptText = String(
            getViralBreakdownScriptGuessDraft(normalizedVideoKey)
            || currentItem?.scriptDraft?.scriptText
            || '',
          ).trim();
          if (!streamedScriptText) {
            failedStage = 'skeleton';
            throw new Error('还没有可用剧本骨架，请先重新生成骨架');
          }
          state.viralBreakdown.scriptGuessProcessing = false;
          state.viralBreakdown.scriptTreeProcessing = true;
          state.viralBreakdown.notice = '正在从断点继续：用已有骨架和可用分析资料调用知识库 Agent 建树...';
          activateViralBreakdownScriptSubTab('tree');
          renderViralBreakdownWorkbench();
        } else {
          state.viralBreakdown.scriptGuessProcessing = true;
          state.viralBreakdown.scriptTreeProcessing = false;
          state.viralBreakdown.notice = '正在把镜头语言和可选台词发给多模态，生成剧本骨架...';
          state.viralBreakdown.scriptGuessDrafts = {
            ...(state.viralBreakdown.scriptGuessDrafts || {}),
            [normalizedVideoKey]: '',
          };
          setViralBreakdownScriptTreeDraft(normalizedVideoKey, null);
          activateViralBreakdownScriptSubTab('skeleton');
          renderViralBreakdownWorkbench();
          try {
            await persistViralBreakdownScriptDraft(normalizedVideoKey, {
              scriptText: '',
              clearTree: true,
            });
          } catch (error) {
            console.warn(error);
          }
          streamedScriptText = await streamViralBreakdownScriptGuess(normalizedVideoKey, transcriptText);
          if (!streamedScriptText) {
            throw new Error('多模态没有返回可用剧本骨架，请检查多模态是否支持图片输入与文本输出');
          }
          try {
            await persistViralBreakdownScriptDraft(normalizedVideoKey, {
              scriptText: streamedScriptText,
              clearTree: true,
            });
          } catch (error) {
            console.warn(error);
          }
          failedStage = 'tree';
          state.viralBreakdown.scriptGuessProcessing = false;
          state.viralBreakdown.scriptTreeProcessing = true;
          state.viralBreakdown.notice = '骨架已完成，正在用剧本骨架和可用分析资料调用知识库 Agent 建树...';
          activateViralBreakdownScriptSubTab('tree');
          renderViralBreakdownWorkbench();
        }
        await buildViralBreakdownScriptTreeFromText(
          normalizedVideoKey,
          streamedScriptText,
          transcriptText,
        );
        state.viralBreakdown.scriptResumeStage = '';
        state.viralBreakdown.error = '';
        state.viralBreakdown.notice = '临时知识树已生成；可点「存入知识库」，或不存仅留在本窗';
      } catch (error) {
        console.error(error);
        state.viralBreakdown.scriptResumeStage = failedStage;
        state.viralBreakdown.error = friendlyViralBreakdownScriptError(error, failedStage);
        state.viralBreakdown.notice = '';
      } finally {
        state.viralBreakdown.scriptGuessProcessing = false;
        state.viralBreakdown.scriptTreeProcessing = false;
        renderViralBreakdownWorkbench();
      }
    }

    function friendlyViralBreakdownScriptError(error, stage) {
      const raw = String(error?.message || error || '').trim();
      const lowered = raw.toLowerCase();
      const transport = /连接中断|ended prematurely|connection reset|timeout|超时|upstream/i.test(raw)
        || /prematurely|econnreset|timed out/.test(lowered);
      if (stage === 'tree') {
        if (transport) {
          return '知识库建树时模型连接中断了。剧本骨架已保留，可从建树步骤重试。';
        }
        return raw ? `知识库建树失败：${raw}` : '知识库建树失败，可从建树步骤重试。';
      }
      if (transport) {
        return '生成剧本骨架时模型连接中断了，请重试。';
      }
      return raw || '猜剧本失败，请重试。';
    }

    async function retryViralBreakdownScriptFromBreakpoint() {
      const stage = String(state.viralBreakdown.scriptResumeStage || '').trim();
      if (stage === 'tree') {
        await guessSelectedViralBreakdownScript({ resumeFrom: 'tree' });
        return;
      }
      await guessSelectedViralBreakdownScript({ resumeFrom: 'full' });
    }

    function syncViralBreakdownScriptResumeAvailability() {
      if (state.viralBreakdown.scriptGuessProcessing || state.viralBreakdown.scriptTreeProcessing) return;
      const currentItem = getSelectedViralBreakdownItem();
      if (!currentItem?.videoKey) return;
      const scriptText = String(
        getViralBreakdownScriptGuessDraft(currentItem.videoKey)
        || currentItem?.scriptDraft?.scriptText
        || '',
      ).trim();
      const treeDraft = getViralBreakdownScriptTreeDraft(currentItem.videoKey);
      const hasTree = !!(treeDraft?.tree && Array.isArray(treeDraft.leaves) && treeDraft.leaves.length);
      if (scriptText && !hasTree) {
        state.viralBreakdown.scriptResumeStage = 'tree';
        if (!state.viralBreakdown.error && !state.viralBreakdown.notice) {
          state.viralBreakdown.notice = '剧本骨架已就绪，可从知识库建树继续。';
        }
        return;
      }
      if (state.viralBreakdown.scriptResumeStage === 'tree' && hasTree) {
        state.viralBreakdown.scriptResumeStage = '';
      }
    }

    async function streamViralBreakdownScriptGuess(videoKey, transcriptText) {
      const res = await fetch('/api/viral-breakdown/guess-script?stream=1', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          videoKey,
          text: transcriptText,
        }),
      });
      if (!res.ok) {
        const errorPayload = await res.json().catch(() => ({}));
        throw new Error(errorPayload?.error || '猜剧本失败');
      }
      if (!res.body) {
        throw new Error('当前浏览器不支持流式读取');
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let streamedScriptText = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        streamedScriptText += decoder.decode(value, { stream: true });
        state.viralBreakdown.scriptGuessDrafts = {
          ...(state.viralBreakdown.scriptGuessDrafts || {}),
          [videoKey]: streamedScriptText,
        };
        const scriptGuessEditor = document.querySelector('#viralBreakdownScriptGuessPane .viral-breakdown-script-guess-editor');
        const scriptGuessMeta = document.getElementById('viralBreakdownScriptGuessMeta');
        if (scriptGuessEditor instanceof HTMLTextAreaElement) {
          scriptGuessEditor.value = streamedScriptText;
          scriptGuessEditor.scrollTop = scriptGuessEditor.scrollHeight;
        }
        if (scriptGuessMeta) {
          scriptGuessMeta.textContent = streamedScriptText ? `${streamedScriptText.length} 字 · 生成中` : '生成中';
        }
      }
      const trailingText = decoder.decode();
      if (trailingText) streamedScriptText += trailingText;
      state.viralBreakdown.scriptGuessDrafts = {
        ...(state.viralBreakdown.scriptGuessDrafts || {}),
        [videoKey]: streamedScriptText,
      };
      return streamedScriptText;
    }

    async function saveSelectedViralBreakdownTranscript() {
      if (isViralBreakdownBusy()) return;
      const currentItem = getSelectedViralBreakdownItem();
      if (!currentItem?.videoKey) return;
      const normalizedVideoKey = String(currentItem.videoKey || '');
      const transcriptText = getViralBreakdownTranscriptDraft(normalizedVideoKey, currentItem.transcriptText || '');
      state.viralBreakdown.transcriptSaving = true;
      state.viralBreakdown.error = '';
      state.viralBreakdown.notice = '正在保存台词...';
      renderViralBreakdownWorkbench();
      try {
        const res = await fetch('/api/viral-breakdown/save-transcript', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            videoKey: normalizedVideoKey,
            text: transcriptText,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data?.error || '保存台词失败');
        }
        currentItem.transcriptText = String(data?.text || transcriptText);
        currentItem.transcriptJsonKey = String(data?.transcriptJsonKey || currentItem.transcriptJsonKey || '');
        currentItem.transcriptTextKey = String(data?.transcriptTextKey || currentItem.transcriptTextKey || '');
        state.viralBreakdown.transcriptDrafts = {
          ...(state.viralBreakdown.transcriptDrafts || {}),
          [normalizedVideoKey]: currentItem.transcriptText,
        };
        state.viralBreakdown.notice = '台词已保存';
      } finally {
        state.viralBreakdown.transcriptSaving = false;
        renderViralBreakdownWorkbench();
      }
    }
