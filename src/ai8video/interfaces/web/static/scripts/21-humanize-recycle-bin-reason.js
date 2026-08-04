    const messageBubbleEnterUntil = new Map();

    function humanizeRecycleBinReason(value) {
      const text = String(value || '').trim();
      const lowered = text.toLowerCase();
      if (!text) return '视频生成失败，请重新生成。';
      if (lowered.includes('exceeded 30 redirects') || lowered.includes('too many redirects')) {
        return '模型服务连接异常，接口发生循环跳转。请稍后重试，或检查 API Key 服务状态。';
      }
      if (text.includes('_mix_video') || text.includes('preserve_original_audio_override') || lowered.includes('mix_background_music')) {
        return '视频后处理失败，背景音乐或原声音轨合成没有完成。请重新生成，或先关闭背景音乐后再试。';
      }
      if (text.includes('花字') || lowered.includes('text overlay') || lowered.includes('overlay')) {
        return '花字处理失败，视频已经保留在这里。请调整花字设置后重新生成。';
      }
      if (lowered.includes('no module named pil') || lowered.includes('pillow')) {
        return '花字处理失败，缺少图片渲染组件。请先关闭花字或补齐本机组件后重试。';
      }
      if (lowered.includes('ffmpeg not found')) {
        return '视频后处理失败，本机没有找到 FFmpeg。请检查视频处理环境后重试。';
      }
      if (lowered.includes('timeout') || lowered.includes('timed out') || text.includes('超时')) {
        return '视频生成等待超时。请稍后刷新结果，或重新生成。';
      }
      if ((text.includes('上游') && text.includes('失败')) || text.includes('生成未成功') || text.includes('生成状态')) {
        return '视频生成没有成功，请重新生成这一条。';
      }
      if (looksTechnicalError(text)) {
        return '视频处理失败，请重新生成这一条。';
      }
      return text;
    }

    function humanizeAssistantError(value) {
      const text = String(value || '').trim();
      const lowered = text.toLowerCase();
      if (lowered.includes('insufficient_user_quota') || text.includes('资源不足')) {
        return '视频服务当前可用额度不足，本轮视频没有提交成功。\n请稍后重试，或取消后回到上一步。';
      }
      if (lowered.includes('fail_to_fetch_task')) {
        return '视频服务暂时无法创建生成任务，本轮视频没有提交成功。\n请稍后重试，或取消后回到上一步。';
      }
      const networkInterrupted = lowered.includes('unexpected_eof_while_reading')
        || lowered.includes('ssleoferror')
        || lowered.includes('connection reset')
        || lowered.includes('connection aborted')
        || lowered.includes('remote end closed connection');
      if (networkInterrupted) {
        return '网络连接出现短暂波动，本次请求没有完成。\n请稍后重试，或取消后回到上一步。';
      }
      if (lowered.includes('max retries exceeded') || lowered.includes('connectionpool')) {
        return '暂时无法连接模型服务，本次请求没有完成。\n请检查网络后重试，或取消后回到上一步。';
      }
      if (lowered.includes('timeout') || lowered.includes('timed out') || text.includes('超时')) {
        return '模型服务响应超时，本次请求没有完成。\n请稍后重试，或取消后回到上一步。';
      }
      if (lowered.includes('exceeded 30 redirects') || lowered.includes('too many redirects')) {
        return '模型服务连接异常，接口发生循环跳转。请稍后重试，或检查 API Key 服务状态。';
      }
      if (lowered.includes('ssl module is not available') || lowered.includes("can't connect to https url")) {
        return '本机安全连接组件不可用，暂时无法连接视频服务。请修复本机 Python 的 HTTPS 支持后再试。';
      }
      return text || '本次任务未完成，请稍后重试。';
    }

    function looksTechnicalError(value) {
      return /traceback|typeerror|runtimeerror|exception|unexpected keyword|_[a-z0-9]+\(/i.test(String(value || ''));
    }

    function buildMaterialItemMarkup(item, options = {}) {
      const selectable = options.selectable !== false;
      const selected = !!options.selected;
      const name = getMaterialMentionName(item);
      const pickAttr = selectable ? `data-pick-material="${escapeHtml(name)}"` : '';
      const selectedBadge = selected ? '<span class="material-selected-badge">已选择</span>' : '';
      const className = `material-option${selected ? ' selected' : ''}`;
      const preview = normalizeMaterialPreview(item.preview || '');
      const meta = getMaterialOptionMeta(item, name);
      if (item.kind === 'image') {
        return `
          <button type="button" class="${className}" ${pickAttr}>
            ${item.url ? `<img class="material-option-thumb" src="${escapeHtml(item.url)}" alt="">` : '<span class="material-option-thumb">图</span>'}
            <span>
              <span class="material-title-row">
                <span class="material-title">@${escapeHtml(name)}</span>
                ${selectedBadge}
              </span>
              ${meta ? `<span class="material-meta">${escapeHtml(meta)}</span>` : ''}
            </span>
          </button>
        `;
      }
      return `
        <button type="button" class="${className}" ${pickAttr}>
          <span class="material-option-thumb">文</span>
          <span>
            <span class="material-title-row">
              <span class="material-title">@${escapeHtml(name)}</span>
              ${selectedBadge}
            </span>
            ${meta ? `<span class="material-meta">${escapeHtml(meta)}</span>` : ''}
            ${preview ? `<span class="material-option-preview">${escapeHtml(preview)}</span>` : ''}
          </span>
        </button>
      `;
    }

    function normalizeMaterialPreview(value) {
      return String(value || '').replace(/\s+/g, ' ').trim();
    }

    function getMaterialOptionMeta(item, name) {
      const meta = String(item?.relativePath || item?.name || '').trim();
      const title = String(name || '').trim();
      if (!meta || meta === title || meta === String(item?.name || '').trim()) return '';
      return meta;
    }

    function getAllUserMaterials() {
      const materials = state.userMaterials || {};
      return [...(materials.images || []), ...(materials.scripts || [])];
    }

    function getSelectedMaterialNameSet() {
      const selected = new Set();
      for (const match of getMessageEditorText().matchAll(/@([^\s@，。；;：:、,]+)/g)) {
        const name = (match[1] || '').trim();
        if (name) selected.add(name);
      }
      return selected;
    }

    function getCurrentMentionQuery() {
      const editor = els.messageEditor;
      const value = getMessageEditorText();
      const caret = getEditorCaretOffset(editor);
      const before = value.slice(0, caret);
      const match = before.match(/@([^\s@，。；;：:、,]*)$/);
      if (!match) return null;
      return { query: match[1] || '', start: caret - match[0].length, end: caret };
    }

    function renderMaterialMentionPicker() {
      const picker = els.materialMentionPicker;
      if (!picker) return;
      const mention = getCurrentMentionQuery();
      if (!mention) {
        hideMaterialMentionPicker();
        return;
      }
      const query = mention.query.trim().toLowerCase();
      const matches = getAllUserMaterials()
        .filter((item) => {
          const mentionName = String(getMaterialMentionName(item)).toLowerCase();
          const stem = String(item.stem || '').toLowerCase();
          const filename = String(item.name || '').toLowerCase();
          return !query || mentionName.includes(query) || stem.includes(query) || filename.includes(query);
        })
        .slice(0, 6);
      if (!matches.length) {
        picker.innerHTML = '<div class="empty">没有匹配素材，先放到左侧素材文件夹。</div>';
        picker.classList.remove('hidden');
        return;
      }
      const selectedNames = getSelectedMaterialNameSet();
      picker.innerHTML = matches.map((item) => {
        const name = String(getMaterialMentionName(item));
        return buildMaterialItemMarkup(item, { selectable: true, selected: selectedNames.has(name) });
      }).join('');
      picker.classList.remove('hidden');
    }

    function hideMaterialMentionPicker() {
      if (els.materialMentionPicker) {
        els.materialMentionPicker.classList.add('hidden');
      }
    }

    function pickMaterialMention(name) {
      if (!name) return;
      const mention = getCurrentMentionQuery();
      const fallbackOffset = getMessageEditorText().length;
      const start = mention ? mention.start : fallbackOffset;
      const end = mention ? mention.end : fallbackOffset;
      replaceEditorTextRangeWithMaterialToken(start, end, name);
      syncMessageInputFromEditor();
      if (state.materialModal.visible) {
        closeMaterialLibraryModal();
        hideMaterialMentionPicker();
      } else {
        renderMaterialMentionPicker();
      }
    }

    function syncMessageInputFromEditor() {
      if (!els.messageInput) return;
      els.messageInput.value = getMessageEditorText();
    }

    function clearMessageEditor() {
      if (els.messageEditor) {
        els.messageEditor.textContent = '';
      }
      if (els.messageInput) {
        els.messageInput.value = '';
      }
    }

    function setComposerDraft(text, { submit = false } = {}) {
      const value = String(text || '').trim();
      renderMessageEditorFromText(value, value.length);
      els.messageEditor?.focus();
      if (submit && value) {
        els.composer.requestSubmit();
      }
    }

    function getMessageEditorText() {
      const editor = els.messageEditor;
      if (!editor) return '';
      return Array.from(editor.childNodes).map(nodeToEditorText).join('').replace(/\u00a0/g, ' ');
    }

    function nodeToEditorText(node) {
      if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || '';
      if (node.nodeType !== Node.ELEMENT_NODE) return '';
      const element = node;
      if (element.dataset?.materialMention) {
        return `@${element.dataset.materialMention}`;
      }
      if (element.tagName === 'BR') return '\n';
      const children = Array.from(element.childNodes).map(nodeToEditorText).join('');
      if (element.tagName === 'DIV' || element.tagName === 'P') return `${children}\n`;
      return children;
    }

    function getEditorCaretOffset(editor) {
      const selection = window.getSelection();
      if (!editor || !selection || !selection.rangeCount) return getMessageEditorText().length;
      const range = selection.getRangeAt(0);
      if (!editor.contains(range.startContainer)) return getMessageEditorText().length;
      const before = range.cloneRange();
      before.selectNodeContents(editor);
      before.setEnd(range.startContainer, range.startOffset);
      return rangeFragmentToEditorText(before.cloneContents()).length;
    }

    function rangeFragmentToEditorText(fragment) {
      return Array.from(fragment.childNodes).map(nodeToEditorText).join('').replace(/\u00a0/g, ' ');
    }

    function replaceEditorTextRangeWithMaterialToken(start, end, name) {
      const editor = els.messageEditor;
      if (!editor) return;
      const value = getMessageEditorText();
      const nextValue = `${value.slice(0, start)}@${name} ${value.slice(end)}`;
      renderMessageEditorFromText(nextValue, start + name.length + 2);
      editor.focus();
    }

    function renderMessageEditorFromText(text, caretOffset) {
      const editor = els.messageEditor;
      if (!editor) return;
      editor.replaceChildren();
      const value = String(text || '');
      const knownNames = getKnownMaterialNames().sort((a, b) => b.length - a.length);
      let index = 0;
      for (const match of value.matchAll(/@([^\s@，。；;：:、,]+)/g)) {
        const name = match[1] || '';
        const materialName = knownNames.find((known) => known === name);
        const start = match.index || 0;
        const end = start + match[0].length;
        if (!materialName) continue;
        appendEditorText(value.slice(index, start));
        appendMaterialToken(materialName);
        index = end;
      }
      appendEditorText(value.slice(index));
      syncMessageInputFromEditor();
      setEditorCaretOffset(Math.min(caretOffset ?? value.length, getMessageEditorText().length));
    }

    function appendEditorText(text) {
      if (!text) return;
      els.messageEditor.appendChild(document.createTextNode(text));
    }

    function appendMaterialToken(name) {
      const token = document.createElement('span');
      token.className = 'material-mention-token';
      token.contentEditable = 'false';
      token.dataset.materialMention = name;
      token.textContent = `@${name}`;
      els.messageEditor.appendChild(token);
    }

    function getKnownMaterialNames() {
      return getAllUserMaterials()
        .map((item) => String(getMaterialMentionName(item)).trim())
        .filter(Boolean);
    }

    function setEditorCaretOffset(targetOffset) {
      const editor = els.messageEditor;
      const selection = window.getSelection();
      if (!editor || !selection) return;
      const point = findEditorCaretPoint(editor, targetOffset);
      const range = document.createRange();
      range.setStart(point.node, point.offset);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
    }

    function findEditorCaretPoint(root, targetOffset) {
      let currentOffset = 0;
      for (const node of Array.from(root.childNodes)) {
        const text = nodeToEditorText(node);
        const nextOffset = currentOffset + text.length;
        if (targetOffset <= nextOffset) {
          if (node.nodeType === Node.TEXT_NODE) {
            return { node, offset: Math.max(0, targetOffset - currentOffset) };
          }
          const textNode = document.createTextNode('');
          if (targetOffset <= currentOffset) {
            root.insertBefore(textNode, node);
          } else {
            root.insertBefore(textNode, node.nextSibling);
          }
          return { node: textNode, offset: 0 };
        }
        currentOffset = nextOffset;
      }
      return { node: root, offset: root.childNodes.length };
    }

    function extractMaterialMentionNames(text) {
      const known = new Set(getAllUserMaterials().map((item) => String(item.stem || item.name || '')));
      const names = [];
      for (const match of String(text || '').matchAll(/@([^\s@，。；;：:、,]+)/g)) {
        const name = match[1].trim();
        if (name && (known.has(name) || known.size === 0) && !names.includes(name)) {
          names.push(name);
        }
      }
      return names;
    }

    function renderSessions() {
      if (!els.sessionList) return;
      els.sessionList.innerHTML = '';
      const onlyConversation = state.sessions.length <= 1;
      state.sessions.forEach((session) => {
        const item = document.createElement('div');
        item.className = 'session-item' + (session.id === state.activeId ? ' active' : '');
        const deleteDisabled = onlyConversation
          || state.conversationSyncing
          || conversationIsBusy(session)
          || session.canDelete === false;
        item.innerHTML = `
          <button type="button" class="session-select" data-select-conversation="${escapeHtml(session.id)}">
            <span class="session-title">${escapeHtml(session.title || NEW_SESSION_TITLE)}</span>
            <span class="session-meta-row">
              <span class="session-mode-badge" data-mode="${escapeHtml(session.executionMode === 'agent' ? 'agent' : 'workflow')}">${escapeHtml(conversationModeLabel(session))}</span>
              <span class="session-state-label">${escapeHtml(conversationLifecycleLabel(session))}</span>
            </span>
          </button>
          <button type="button" class="session-delete-button" data-delete-conversation="${escapeHtml(session.id)}" aria-label="删除对话 ${escapeHtml(session.title || NEW_SESSION_TITLE)}" title="${deleteDisabled ? (onlyConversation ? '至少保留一个对话' : '运行中的对话不能删除') : '删除对话；不会删除视频、素材和任务记录'}" ${deleteDisabled ? 'disabled' : ''}>
            <span class="session-delete-icon" aria-hidden="true"></span>
          </button>
        `;
        els.sessionList.appendChild(item);
      });
    }

    function renderBatchReports() {
      const groups = groupReportsByDay(state.batchReports || []);
      if (!groups.length) {
        els.batchReportList.innerHTML = '<div class="empty">批量日报会在正式批跑后出现在这里。</div>';
        return;
      }
      els.batchReportList.innerHTML = `
        <div class="report-group-list">
          ${groups.map((group) => `
            <div class="report-day-group">
              <div class="report-day">${escapeHtml(group.date)}</div>
              ${group.items.map((item) => buildBatchReportCardMarkup(item)).join('')}
            </div>
          `).join('')}
        </div>
      `;
    }

    function renderSupervisorStatus() {
      const health = state.health;
      if (!health) {
        els.supervisorPanel.innerHTML = '<div class="empty">值守状态读取中。</div>';
        return;
      }
      els.supervisorPanel.innerHTML = buildSupervisorCardMarkup(health);
    }

    function renderBatchAlerts() {
      const groups = groupAlertsByDay(state.batchAlerts || []);
      if (!groups.length) {
        els.batchAlertList.innerHTML = '<div class="empty">当前没有新的异常告警。</div>';
        return;
      }
      els.batchAlertList.innerHTML = `
        <div class="report-group-list">
          ${groups.map((group) => `
            <div class="report-day-group">
              <div class="report-day">${escapeHtml(group.date)}</div>
              ${group.items.map((item) => buildBatchAlertCardMarkup(item)).join('')}
            </div>
          `).join('')}
        </div>
      `;
    }

    function getActiveConversationAwaiting(session) {
      const lastMessage = session?.messages?.at?.(-1);
      if (lastMessage?.role !== 'assistant' || lastMessage.error) return '';
      return String(lastMessage.payload?.awaiting || '').trim();
    }

    function manualTailFrameCardKey(card) {
      const action = card?.querySelector('[data-tail-frame-continue]');
      const preview = card?.querySelector('.result-notify-preview img');
      const batchId = String(action?.dataset.generationBatchId || '').trim();
      const videoIndex = String(action?.dataset.tailFrameContinue || '').trim();
      const previewPath = String(preview?.getAttribute('src') || '').trim().split(/[?#]/u, 1)[0];
      return batchId && videoIndex && previewPath ? `${batchId}:${videoIndex}:${previewPath}` : '';
    }

    function collectStableManualTailFrameCards() {
      return new Map(Array.from(els.messages.querySelectorAll('.manual-tail-frame')).map((card) => [
        manualTailFrameCardKey(card),
        card,
      ]).filter(([key]) => key));
    }

    function restoreStableManualTailFrameCards(cards) {
      els.messages.querySelectorAll('.manual-tail-frame').forEach((card) => {
        const previous = cards.get(manualTailFrameCardKey(card));
        if (previous) card.replaceWith(previous);
      });
    }

    function collectStableProgressResultCards() {
      const cards = new Map();
      els.messages.querySelectorAll('.message').forEach((message) => {
        const messageIndex = String(message.dataset.messageIndex || '').trim();
        message.querySelectorAll('.agent-video-results-primary .result-notify-card').forEach((card, cardIndex) => {
          cards.set(`${messageIndex}:${cardIndex}`, {
            card,
            markup: card.outerHTML,
          });
        });
      });
      return cards;
    }

    function restoreStableProgressResultCards(cards) {
      els.messages.querySelectorAll('.message').forEach((message) => {
        const messageIndex = String(message.dataset.messageIndex || '').trim();
        message.querySelectorAll('.agent-video-results-primary .result-notify-card').forEach((card, cardIndex) => {
          const previous = cards.get(`${messageIndex}:${cardIndex}`);
          if (previous?.markup === card.outerHTML) card.replaceWith(previous.card);
        });
      });
    }

    function renderMessages() {
      const session = getActiveSession();
      if (!session) {
        els.messages.dataset.renderedSessionId = '';
        els.messages.innerHTML = `<div class="empty">${escapeHtml(state.conversationError || '正在准备对话，请稍候。')}</div>`;
        return;
      }
      repairRecoveredSmartSplitFailure(session);
      if (stripStaleWelcomeMessages(session)) persistSessions();
      const sessionId = String(session?.id || '');
      const renderedSessionId = String(els.messages.dataset.renderedSessionId || '');
      const renderedMessageCount = renderedSessionId === sessionId
        ? Array.from(els.messages.children).filter((node) => node.classList.contains('message')).length
        : 0;
      const bubbleEnterNow = Date.now();
      messageBubbleEnterUntil.forEach((until, key) => {
        if (until <= bubbleEnterNow) messageBubbleEnterUntil.delete(key);
      });
      els.messages.dataset.renderedSessionId = sessionId;
      const scroller = els.messages.parentElement;
      const distanceFromBottom = Math.max(0, scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight);
      const shouldStickToBottom = distanceFromBottom < 64;
      const stableTailFrameCards = collectStableManualTailFrameCards();
      const stableProgressResultCards = collectStableProgressResultCards();
      els.messages.innerHTML = '';
      if (!session.messages.length) {
        els.messages.innerHTML = '<div class="empty">输入数量和要求，比如：2 个，618 活动</div>';
        return;
      }
      const activeAwaiting = getActiveConversationAwaiting(session);
      session.messages.forEach((message, messageIndex) => {
        const wrap = document.createElement('div');
        wrap.dataset.messageIndex = String(messageIndex);
        wrap.className = 'message'
          + (message.role === 'user' ? ' user' : '')
          + (message.textCleared ? ' text-cleared' : '')
          + (isWelcomeMessage(message) ? ' is-welcome' : '');
        const bubbleEnterKey = `${sessionId}:${messageIndex}`;
        if (renderedSessionId !== sessionId || messageIndex >= renderedMessageCount) {
          messageBubbleEnterUntil.set(bubbleEnterKey, bubbleEnterNow + 340);
        }
        if ((messageBubbleEnterUntil.get(bubbleEnterKey) || 0) > bubbleEnterNow) {
          wrap.classList.add('is-bubble-entering');
        }
        const avatar = message.role === 'user' ? '我' : 'AI8video';
        wrap.innerHTML = `<div class="avatar">${avatar}</div><div class="bubble"></div>`;
        const bubble = wrap.querySelector('.bubble');
        if (message.role === 'user') {
          bubble.innerHTML = `<p>${escapeHtml(message.text)}</p>`;
        } else if (message.error) {
          bubble.innerHTML = renderAssistantPayload({
            text: formatNetworkError(message.error),
            meta: { operation: 'error' },
          }, {
            sessionId: session.id,
            messageIndex,
            messageCount: session.messages.length,
            activeAwaiting,
          });
        } else {
          bubble.innerHTML = renderAssistantPayload(message.payload, {
            sessionId: session.id,
            messageIndex,
            messageCount: session.messages.length,
            activeAwaiting,
          });
          syncAssistantBubbleLayoutClasses(bubble);
        }
        els.messages.appendChild(wrap);
      });
      restoreStableManualTailFrameCards(stableTailFrameCards);
      restoreStableProgressResultCards(stableProgressResultCards);
      recoverLegacySmartSplitPlans(session);
      if (shouldStickToBottom) {
        window.requestAnimationFrame(() => {
          scroller.scrollTop = scroller.scrollHeight;
        });
      }
    }

    function isTextOnlyConversationMessage(message) {
      if (!message || typeof message !== 'object') return false;
      if (message.role === 'user') {
        return !!String(message.text || '').trim() && !message.payload && !message.error;
      }
      if (message.error && !message.payload) return true;
      const payload = message.payload;
      if (!payload || typeof payload !== 'object') {
        return !!String(message.text || '').trim();
      }
      if (hasNonTextConversationPayload(payload)) return false;
      return !!String(payload.text || '').trim();
    }
