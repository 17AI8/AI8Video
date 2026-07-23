    function humanizeRecycleBinReason(value) {
      const text = String(value || '').trim();
      const lowered = text.toLowerCase();
      if (!text) return '视频生成失败，请重新生成。';
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
      els.sessionList.innerHTML = '';
      state.sessions.forEach((session) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'session-item' + (session.id === state.activeId ? ' active' : '');
        item.innerHTML = `
          <div class="session-title">${escapeHtml(session.title)}</div>
          <div class="session-sub">${escapeHtml(summarizeSessionSub(session))}</div>
        `;
        item.addEventListener('click', () => {
          state.activeId = session.id;
          render();
        });
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

    function renderMessages() {
      const session = getActiveSession();
      if (stripStaleWelcomeMessages(session)) persistSessions();
      renderClearConversationButton(session);
      const scroller = els.messages.parentElement;
      const distanceFromBottom = Math.max(0, scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight);
      const shouldStickToBottom = distanceFromBottom < 64;
      els.messages.innerHTML = '';
      if (!session.messages.length) {
        els.messages.innerHTML = '<div class="empty">输入数量和要求，比如：2 个，618 活动</div>';
        return;
      }
      session.messages.forEach((message, messageIndex) => {
        const wrap = document.createElement('div');
        wrap.className = 'message'
          + (message.role === 'user' ? ' user' : '')
          + (message.textCleared ? ' text-cleared' : '')
          + (isWelcomeMessage(message) ? ' is-welcome' : '');
        const avatar = message.role === 'user' ? '我' : '讯';
        wrap.innerHTML = `<div class="avatar">${avatar}</div><div class="bubble"></div>`;
        const bubble = wrap.querySelector('.bubble');
        if (message.role === 'user') {
          bubble.innerHTML = `<p>${escapeHtml(message.text)}</p>`;
        } else if (message.error) {
          bubble.innerHTML = `<p>本次请求失败：${escapeHtml(formatNetworkError(message.error))}</p>`;
        } else {
          bubble.innerHTML = renderAssistantPayload(message.payload, {
            sessionId: session.id,
            messageIndex,
            messageCount: session.messages.length,
          });
          bubble.classList.toggle(
            'pending-only',
            bubble.childElementCount === 1 && bubble.firstElementChild?.classList.contains('pending-card'),
          );
        }
        els.messages.appendChild(wrap);
      });
      if (shouldStickToBottom) {
        window.requestAnimationFrame(() => {
          scroller.scrollTop = scroller.scrollHeight;
        });
      }
    }

    function renderClearConversationButton(session = getActiveSession()) {
      if (!els.clearConversationButton) return;
      const count = countTextOnlyMessages(session);
      els.clearConversationButton.disabled = count <= 0;
      els.clearConversationButton.title = count > 0
        ? `清空当前对话窗口中的 ${count} 条对话消息，不影响任务、结果和媒体资源。`
        : '当前没有可清空的对话消息。';
    }

    function countTextOnlyMessages(session) {
      return (session?.messages || []).length;
    }

    function openClearConversationConfirmModal() {
      const session = getActiveSession();
      const count = countTextOnlyMessages(session);
      if (count <= 0) {
        renderClearConversationButton(session);
        return;
      }
      if (els.clearConversationConfirmCount) {
        els.clearConversationConfirmCount.textContent = `将清空 ${count} 条对话消息。`;
      }
      state.clearConversationModal.visible = true;
      els.clearConversationConfirmModal?.classList.remove('hidden');
      window.requestAnimationFrame(() => {
        els.clearConversationConfirmCancelButton?.focus();
      });
    }

    function closeClearConversationConfirmModal() {
      state.clearConversationModal.visible = false;
      els.clearConversationConfirmModal?.classList.add('hidden');
    }

    function clearActiveConversationTextMessages() {
      const session = getActiveSession();
      if (!session) return;
      const before = Array.isArray(session.messages) ? session.messages.length : 0;
      if (before <= 0) {
        renderClearConversationButton(session);
        return;
      }
      session.messages = [];
      session.title = NEW_SESSION_TITLE;
      persistSessions();
      renderMessages();
      renderStatus();
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

    function hasClearableConversationText(message) {
      if (isTextOnlyConversationMessage(message)) return true;
      if (!message || typeof message !== 'object' || message.textCleared) return false;
      const payload = message.payload;
      return !!(payload && typeof payload === 'object' && hasNonTextConversationPayload(payload));
    }
