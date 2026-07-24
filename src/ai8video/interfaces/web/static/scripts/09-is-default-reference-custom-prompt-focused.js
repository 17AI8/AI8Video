    function isDefaultReferenceCustomPromptFocused() {
      const active = document.activeElement;
      return !!active && typeof active.matches === 'function' && active.matches('[data-default-reference-custom-prompt]');
    }

    function buildDefaultReferenceOptionsMarkup(options, effects) {
      const rows = normalizeDefaultReferenceEffects(effects);
      const optionsMarkup = rows.length ? `
        <div class="reference-option-list">
          ${rows.map(({ key, label }) => `
            <label class="reference-option">
              <span>${escapeHtml(label)}</span>
              <input type="checkbox" data-default-reference-option="${escapeHtml(key)}" ${options[key] ? 'checked' : ''}>
            </label>
          `).join('')}
        </div>
      ` : '<div class="empty">暂无参考图设定。</div>';
      const customPrompt = normalizeDefaultReferenceCustomPrompt(state.defaultReferenceImage?.customPrompt);
      return `
        ${optionsMarkup}
        <div class="reference-option-form">
          <label class="reference-option-textarea-field">
            <span>自定义修图提示词</span>
            <textarea
              class="reference-option-textarea"
              data-default-reference-custom-prompt
              placeholder="请输入你想对 AI 修图提的要求">${escapeHtml(customPrompt)}</textarea>
          </label>
        </div>
      `;
    }

    function renderScriptReferenceButton() {
      const button = els.scriptReferenceButton;
      if (!button) return;
      const ref = state.scriptReference || {};
      const item = ref.item || {};
      const enabled = !!ref.enabled && !!item;
      const temporary = state.temporaryScriptKnowledge || null;
      const selecting = !!ref.selecting;
      button.classList.toggle('is-ready', enabled || !!temporary);
      button.classList.toggle('is-open', !!state.scriptReferenceDrawer?.visible);
      button.disabled = selecting;
    button.textContent = '知识库参考';
      button.setAttribute('aria-expanded', state.scriptReferenceDrawer?.visible ? 'true' : 'false');
      if (selecting) {
        button.title = '正在切换知识库参考';
      } else if (temporary && enabled) {
        button.title = `本次已锁定临时知识库，并额外选择：${item.name || item.relativePath || '知识文档'}。`;
      } else if (temporary) {
        button.title = '本次已锁定猜剧本临时知识库。点击后可额外任选一个常驻知识库。';
      } else if (enabled) {
        button.title = `当前知识库参考：${item.name || item.relativePath || '知识文档'}。点击展开列表，可切换或取消。`;
      } else if (ref.error) {
        button.title = `知识库参考设置失败：${ref.error}`;
      } else {
        button.title = '从剧本知识库选择默认参考文档。';
      }
    }

    function renderScriptReferenceDrawer() {
      if (!els.scriptReferenceDrawer || !els.scriptReferenceDrawerBody) return;
      const visible = !!state.scriptReferenceDrawer?.visible;
      els.scriptReferenceDrawer.classList.toggle('open', visible);
      els.scriptReferenceDrawer.setAttribute('aria-hidden', visible ? 'false' : 'true');
      els.scriptReferenceButton?.classList.toggle('is-open', visible);
      els.scriptReferenceButton?.setAttribute('aria-expanded', visible ? 'true' : 'false');
      if (!visible) return;
      const ref = state.scriptReference || {};
      const temporary = state.temporaryScriptKnowledge || null;
      const scripts = Array.isArray(state.scriptReferenceDrawer.items) ? state.scriptReferenceDrawer.items : [];
      const selectedPath = String(ref.item?.relativePath || '');
      const loading = !!state.scriptReferenceDrawer.loading;
      const selecting = !!ref.selecting;
      const error = String(ref.error || '').trim();
      const statusText = selecting
        ? '正在切换知识库参考...'
        : error
          ? `提示：${error}`
          : temporary
            ? '临时知识库已锁定；下方常驻知识库可额外任选 1 个'
            : '选择后会按当前需求检索知识树，必要时回退原文';
      let listMarkup = '';
      if (loading) {
        listMarkup = '<div class="empty">正在读取知识库...</div>';
      } else if (!scripts.length) {
        listMarkup = '<div class="empty">还没有知识库文档。先导入 txt、md 或 docx。</div>';
      } else {
        listMarkup = scripts.map((item) => buildScriptReferenceItemMarkup(item, selectedPath, !!temporary)).join('');
      }
      const temporaryMarkup = temporary ? buildTemporaryScriptKnowledgeReferenceMarkup(temporary) : '';
      els.scriptReferenceDrawerBody.innerHTML = `
        <div class="background-music-head">
          <div class="background-music-status">${escapeHtml(statusText)}</div>
          <div class="background-music-actions">
            <button type="button" class="background-music-add-button" data-add-script-reference>导入文档</button>
          </div>
        </div>
        <div class="background-music-list">
          ${temporaryMarkup}
          ${listMarkup}
        </div>
      `;
    }

    function buildScriptReferenceItemMarkup(item, selectedPath, additional = false) {
      const relativePath = String(item?.relativePath || item?.name || '');
      const selected = !!selectedPath && selectedPath === relativePath;
      const name = String(item?.title || item?.stem || item?.name || relativePath || '知识文档');
      const preview = normalizeMaterialPreview(item?.summary || item?.preview || '暂无摘要');
      const sectionCount = Number(item?.sectionCount || 0);
      return `
        <button type="button" class="script-knowledge-list-card script-reference-knowledge-card${selected ? ' is-active' : ''}" data-select-script-reference="${escapeHtml(relativePath)}" data-script-reference-selected="${selected ? '1' : '0'}">
          <span class="script-knowledge-list-title">${escapeHtml(name)}${selected ? `<span class="material-selected-badge">${additional ? '额外已选择' : '已选择'}</span>` : ''}</span>
          ${buildScriptKnowledgeTagsMarkup(item?.tags || [])}
          <span class="script-knowledge-list-preview">${escapeHtml(preview)}</span>
          <span class="script-knowledge-list-foot">
            <span>${sectionCount ? `${sectionCount} 个叶节点` : '未知识入库'}</span>
            <span>${escapeHtml(formatFileSize(item?.sizeBytes || 0) || '0 B')}</span>
          </span>
        </button>
      `;
    }

    function renderFlowerTextButton() {
      if (state.flowerText?._suppressEntryStatus) return;
      const button = els.flowerTextButton;
      if (!button) return;
      const config = state.flowerText || {};
      const watermark1Ready = !!config.watermarkEnabled && !!normalizeFlowerTextWatermarkImage(config.watermarkImage, state.userMaterials?.flowerWatermarks);
      const watermark2Ready = !!config.watermark2Enabled && !!normalizeFlowerTextWatermarkImage(config.watermark2Image, state.userMaterials?.flowerWatermarks);
      const watermarkReady = watermark1Ready || watermark2Ready;
      const enabled = !!config.enabled && (!!String(config.text || '').trim() || watermarkReady);
      const saving = !!config.saving;
      button.classList.toggle('is-ready', enabled && !saving);
      button.classList.toggle('is-open', !!state.flowerTextDrawer?.visible);
      button.disabled = saving;
      button.textContent = saving ? '保存中' : '花字';
      button.setAttribute('aria-expanded', state.flowerTextDrawer?.visible ? 'true' : 'false');
      if (saving) {
        button.title = '正在保存花字设置';
      } else if (enabled) {
        button.title = '花字已开启。生成归档后会烧录到视频画面。';
      } else if (config.error) {
        button.title = `花字设置失败：${config.error}`;
      } else {
        button.title = '点击展开花字设置。';
      }
    }

    function renderFlowerTextFontPreview(font, className, fallbackText) {
      const previewUrl = String(font?.previewUrl || '').trim();
      const name = String(font?.name || fallbackText || '字体预览').trim();
      if (previewUrl) {
        return `<img class="${escapeHtml(className)}" alt="${escapeHtml(name)}" src="${escapeHtml(previewUrl)}">`;
      }
      return `<span class="${escapeHtml(className)} empty">${escapeHtml(fallbackText || name || '字体预览')}</span>`;
    }

    function positionOpenFlowerTextFontMenu() {
      const picker = els.flowerTextDrawer?.querySelector?.('.flower-text-font-picker[open]');
      positionFlowerTextFontMenu(picker);
    }

    function positionFlowerTextFontMenu(picker) {
      if (!picker?.open) return;
      const summary = picker.querySelector('summary');
      const menu = picker.querySelector('.flower-text-font-menu');
      if (!summary || !menu) return;
      const rect = summary.getBoundingClientRect();
      const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
      const edgeGap = 8;
      const menuGap = 6;
      const width = Math.max(180, Math.min(rect.width, viewportWidth - edgeGap * 2));
      const left = Math.max(edgeGap, Math.min(rect.left, viewportWidth - width - edgeGap));
      const spaceAbove = Math.max(0, rect.top - edgeGap - menuGap);
      const spaceBelow = Math.max(0, viewportHeight - rect.bottom - edgeGap - menuGap);
      const openUp = spaceAbove >= Math.min(260, spaceBelow) || spaceAbove >= spaceBelow;
      const maxHeight = Math.max(180, Math.min(520, openUp ? spaceAbove : spaceBelow));
      const top = openUp ? Math.max(edgeGap, rect.top - menuGap - maxHeight) : Math.min(viewportHeight - edgeGap - maxHeight, rect.bottom + menuGap);
      menu.style.setProperty('--flower-font-menu-left', `${Math.round(left)}px`);
      menu.style.setProperty('--flower-font-menu-width', `${Math.round(width)}px`);
      menu.style.setProperty('--flower-font-menu-top', `${Math.round(top)}px`);
      menu.style.setProperty('--flower-font-menu-max-height', `${Math.round(maxHeight)}px`);
    }

    function scrollSelectedFlowerTextFontIntoView(picker) {
      if (!picker?.open) return;
      positionFlowerTextFontMenu(picker);
      const menu = picker.querySelector('.flower-text-font-menu');
      const selected = picker.querySelector('.flower-text-font-option.selected');
      if (!menu || !selected) return;
      const targetTop = selected.offsetTop - Math.max(0, (menu.clientHeight - selected.offsetHeight) / 2);
      menu.scrollTop = Math.max(0, targetTop);
    }

    function applyFlowerTextFontPickerSelection(fontFamily = state.flowerText?.fontFamily) {
      const availableFonts = normalizeFlowerTextFonts(state.flowerText?.availableFonts);
      const selectedId = normalizeFlowerTextFamily(fontFamily, availableFonts);
      const selectedFont = availableFonts.find((font) => font.id === selectedId) || null;
      const label = selectedFont?.name || '系统默认';
      document.querySelectorAll('.flower-text-font-picker').forEach((picker) => {
        picker.querySelectorAll('[data-flower-text-font-option]').forEach((option) => {
          option.classList.toggle('selected', String(option.getAttribute('data-flower-text-font-option') || '') === selectedId);
        });
        const summary = picker.querySelector('summary');
        if (summary) {
          summary.innerHTML = `
            <span class="flower-text-font-current">${escapeHtml(label)}</span>
            ${renderFlowerTextFontPreview(selectedFont, 'flower-text-font-current-preview', availableFonts.length ? '系统默认字体' : '还没有可用字体')}
          `;
        }
        scrollSelectedFlowerTextFontIntoView(picker);
      });
    }

    function renderFlowerTextDrawer() {
      if (!els.flowerTextDrawer || !els.flowerTextDrawerBody) return;
      const visible = !!state.flowerTextDrawer?.visible;
      els.flowerTextDrawer.classList.toggle('open', visible);
      els.flowerTextDrawer.setAttribute('aria-hidden', visible ? 'false' : 'true');
      els.flowerTextButton?.classList.toggle('is-open', visible);
      els.flowerTextButton?.setAttribute('aria-expanded', visible ? 'true' : 'false');
      if (!visible) return;
      const config = state.flowerText || {};
      const enabled = !!config.enabled;
      const saving = !!config.saving;
      const width = normalizeFlowerTextSide(config.canvasWidth, 9);
      const height = normalizeFlowerTextSide(config.canvasHeight, 16);
      const ratioValue = flowerTextRatioValue(width, height);
      const safeZoneEditing = !!state.htmlMotionSafeZone?.editing;
      const safeZoneSaving = !!state.htmlMotionSafeZone?.saving;
      const safeZone = currentHtmlMotionSafeZone(ratioValue);
      const animationDelaySeconds = normalizeFlowerTextAnimationDelay(config.animationDelaySeconds);
      const animationType = normalizeFlowerTextAnimationType(config.animationType);
      const textColor = normalizeFlowerTextColor(config.textColor, '#ffee43');
      const strokeColor = normalizeFlowerTextColor(config.strokeColor, '#121826');
      const availableFonts = normalizeFlowerTextFonts(config.availableFonts);
      ensureFlowerTextFontFaces(availableFonts);
      const fontFamily = normalizeFlowerTextFamily(config.fontFamily, availableFonts);
      const selectedFont = availableFonts.find((font) => font.id === fontFamily) || null;
      const fontLabel = selectedFont?.name || '系统默认';
      const editorFontFamily = flowerTextEditorFontFamily(selectedFont);
      const fontSize = normalizeFlowerTextPercent(config.fontSize, 16, 6, 28);
      const fontWeight = normalizeFlowerTextWeight(config.fontWeight, 800);
      const strokeWidth = normalizeFlowerTextPercent(config.strokeWidth, 8, 0, 18);
      const position = normalizeFlowerTextPosition(config.position);
      const textX = normalizeFlowerTextCoordinate(config.textX, 50);
      const textY = normalizeFlowerTextCoordinate(config.textY, flowerTextPositionY(position));
      const watermarkImages = Array.isArray(state.userMaterials?.flowerWatermarks) ? state.userMaterials.flowerWatermarks : [];
      fetch('http://127.0.0.1:7352/ingest/a6129daf-2746-4e4a-84ac-54d0dd03e374',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'cad45c'},body:JSON.stringify({sessionId:'cad45c',hypothesisId:'H3',location:'renderFlowerTextDrawer',message:'rendering',data:{watermarkEnabled:config.watermarkEnabled,watermarkImage:config.watermarkImage,watermark2Enabled:config.watermark2Enabled,watermark2Image:config.watermark2Image,watermarkImagesCount:watermarkImages.length},timestamp:Date.now()})}).catch(()=>{});
      const watermarkEnabled = !!config.watermarkEnabled;
      const watermarkImage = normalizeFlowerTextWatermarkImage(config.watermarkImage, watermarkImages);
      const watermarkItem = getFlowerTextWatermarkItem(watermarkImage, watermarkImages);
      const watermarkPreviewUrl = String(watermarkItem?.url || '');
      const watermarkSize = normalizeFlowerTextPercent(config.watermarkSize, 18, 5, 200);
      const watermarkOpacity = normalizeFlowerTextPercent(config.watermarkOpacity, 100, 5, 100);
      const watermarkAnimationDelaySeconds = normalizeFlowerTextAnimationDelay(config.watermarkAnimationDelaySeconds);
      const watermarkAnimationType = normalizeFlowerTextAnimationType(config.watermarkAnimationType);
      const watermarkPosition = normalizeFlowerTextWatermarkPosition(config.watermarkPosition);
      const watermarkX = normalizeFlowerTextCoordinate(config.watermarkX, flowerTextWatermarkPositionX(watermarkPosition));
      const watermarkY = normalizeFlowerTextCoordinate(config.watermarkY, flowerTextWatermarkPositionY(watermarkPosition));
      const watermark2Enabled = !!config.watermark2Enabled;
      const watermark2Image = normalizeFlowerTextWatermarkImage(config.watermark2Image, watermarkImages);
      const watermark2Item = getFlowerTextWatermarkItem(watermark2Image, watermarkImages);
      const watermark2PreviewUrl = String(watermark2Item?.url || '');
      const watermark2Size = normalizeFlowerTextPercent(config.watermark2Size, 18, 5, 200);
      const watermark2Opacity = normalizeFlowerTextPercent(config.watermark2Opacity, 100, 5, 100);
      const watermark2AnimationDelaySeconds = normalizeFlowerTextAnimationDelay(config.watermark2AnimationDelaySeconds);
      const watermark2AnimationType = normalizeFlowerTextAnimationType(config.watermark2AnimationType);
      const watermark2Position = normalizeFlowerTextWatermarkPosition(config.watermark2Position);
      const watermark2X = normalizeFlowerTextCoordinate(config.watermark2X, flowerTextWatermarkPositionX(watermark2Position));
      const watermark2Y = normalizeFlowerTextCoordinate(config.watermark2Y, flowerTextWatermarkPositionY(watermark2Position));
      const previewBackgroundColor = normalizeFlowerTextColor(config.previewBackgroundColor, '#303844');
      const previewBackgroundImageUrl = String(config.previewBackgroundImageUrl || '');
      const previewMaxWidth = flowerTextPreviewWidth(width, height);
      const previewBackgroundStyle = `--flower-text-ratio: ${width} / ${height}; --flower-text-preview-background-color: ${previewBackgroundColor}; --flower-text-preview-background-image: ${previewBackgroundImageUrl ? `url('${escapeHtml(previewBackgroundImageUrl)}')` : 'none'};`;
      const previewFontSize = flowerTextPreviewFontSize(width, height, fontSize);
      const previewUrl = String(config.previewUrl || '');
      const text = String(config.text || '');
      const error = String(config.error || '').trim();
      const notice = String(config.notice || '').trim();
      const status = saving ? '保存中...' : error ? `提示：${error}` : notice || (enabled ? '已开启' : '已关闭');
      els.flowerTextDrawerBody.innerHTML = `
        <div class="flower-text-panel">
          <div class="flower-text-preview-workbench">
            <div class="flower-text-watermark-rail">
              <div class="watermark-segmented-control">
                <label class="watermark-segment-btn${watermarkEnabled ? ' active' : ''}"><span class="watermark-segment-label">添加水印 1</span><input type="checkbox" class="watermark-segment-checkbox" data-flower-watermark-checkbox="1" ${watermarkEnabled ? 'checked' : ''}></label>
                <label class="watermark-segment-btn${watermark2Enabled ? ' active' : ''}"><span class="watermark-segment-label">添加水印 2</span><input type="checkbox" class="watermark-segment-checkbox" data-flower-watermark-checkbox="2" ${watermark2Enabled ? 'checked' : ''}></label>
              </div>
              <div class="flower-text-watermark-control" data-flower-watermark-panel="1">
                <label class="background-music-add-button flower-text-watermark-upload">
                  <span>上传水印图 1</span>
                  <input class="flower-text-watermark-file-input" type="file" data-flower-watermark-file-input="1" accept=".jpg,.jpeg,.png,.webp,.gif,.bmp,image/*">
                </label>
                <div class="flower-text-watermark-preview-box">
                  ${watermarkPreviewUrl ? `<img src="${escapeHtml(watermarkPreviewUrl)}" alt="${escapeHtml(watermarkItem?.name || '水印图片')}">` : '<span>未上传水印</span>'}
                </div>
                <label class="flower-text-style-field flower-text-watermark-size-field flower-text-inline-range-field">
                  <span>水印大小</span>
                  <input type="range" min="5" max="200" step="1" value="${watermarkSize}" data-flower-text-style="watermarkSize" data-flower-watermark-style="1">
                </label>
                <label class="flower-text-style-field flower-text-inline-range-field">
                  <span>水印入场时间</span>
                  <select data-flower-text-style="watermarkAnimationDelaySeconds">
                    <option value="0" ${watermarkAnimationDelaySeconds === 0 ? 'selected' : ''}>立即出现</option>
                    <option value="1" ${watermarkAnimationDelaySeconds === 1 ? 'selected' : ''}>1秒后</option>
                    <option value="3" ${watermarkAnimationDelaySeconds === 3 ? 'selected' : ''}>3秒后</option>
                    <option value="5" ${watermarkAnimationDelaySeconds === 5 ? 'selected' : ''}>5秒后</option>
                    <option value="10" ${watermarkAnimationDelaySeconds === 10 ? 'selected' : ''}>10秒后</option>
                  </select>
                </label>
                <label class="flower-text-style-field flower-text-inline-range-field">
                  <span>出场动画</span>
                  <select data-flower-text-style="watermarkAnimationType">
                    <option value="fade" ${watermarkAnimationType === 'fade' ? 'selected' : ''}>淡入</option>
                    <option value="none" ${watermarkAnimationType === 'none' ? 'selected' : ''}>无动画</option>
                  </select>
                </label>
              </div>
              <div class="flower-text-watermark-control" data-flower-watermark-panel="2">
                <label class="background-music-add-button flower-text-watermark-upload">
                  <span>上传水印图 2</span>
                  <input class="flower-text-watermark-file-input" type="file" data-flower-watermark-file-input="2" accept=".jpg,.jpeg,.png,.webp,.gif,.bmp,image/*">
                </label>
                <div class="flower-text-watermark-preview-box">
                  ${watermark2PreviewUrl ? `<img src="${escapeHtml(watermark2PreviewUrl)}" alt="${escapeHtml(watermark2Item?.name || '水印图片')}">` : '<span>未上传水印</span>'}
                </div>
                <label class="flower-text-style-field flower-text-watermark-size-field flower-text-inline-range-field">
                  <span>水印大小</span>
                  <input type="range" min="5" max="200" step="1" value="${watermark2Size}" data-flower-text-style="watermark2Size" data-flower-watermark-style="2">
                </label>
                <label class="flower-text-style-field flower-text-inline-range-field">
                  <span>水印2入场时间</span>
                  <select data-flower-text-style="watermark2AnimationDelaySeconds">
                    <option value="0" ${watermark2AnimationDelaySeconds === 0 ? 'selected' : ''}>立即出现</option>
                    <option value="1" ${watermark2AnimationDelaySeconds === 1 ? 'selected' : ''}>1秒后</option>
                    <option value="3" ${watermark2AnimationDelaySeconds === 3 ? 'selected' : ''}>3秒后</option>
                    <option value="5" ${watermark2AnimationDelaySeconds === 5 ? 'selected' : ''}>5秒后</option>
                    <option value="10" ${watermark2AnimationDelaySeconds === 10 ? 'selected' : ''}>10秒后</option>
                  </select>
                </label>
                <label class="flower-text-style-field flower-text-inline-range-field">
                  <span>出场动画</span>
                  <select data-flower-text-style="watermark2AnimationType">
                    <option value="fade" ${watermark2AnimationType === 'fade' ? 'selected' : ''}>淡入</option>
                    <option value="none" ${watermark2AnimationType === 'none' ? 'selected' : ''}>无动画</option>
                  </select>
                </label>
              </div>
            </div>
            <div class="flower-text-preview-stack" style="max-width: min(100%, ${previewMaxWidth}px);">
              <div class="flower-text-background-controls" role="group" aria-label="花字预览背景">
                <label class="flower-text-background-button">
                  <span>更换纯色背景</span>
                  <input class="flower-text-background-color-input" type="color" data-flower-background-color-input value="${escapeHtml(previewBackgroundColor)}">
                </label>
                <label class="flower-text-background-button">
                  <span>上传背景图</span>
                  <input class="flower-text-background-file-input" type="file" data-flower-background-file-input accept=".jpg,.jpeg,.png,.webp,.gif,.bmp,image/*">
                </label>
              </div>
              <div id="flowerTextEditorWrap" class="flower-text-editor-wrap${previewUrl ? ' has-render-preview' : ''}${safeZoneEditing ? ' is-html-motion-safe-zone-editing' : ''}" style="${previewBackgroundStyle}">
                <img id="flowerTextRenderedPreview" class="flower-text-rendered-preview" alt="" src="${escapeHtml(previewUrl)}">
                <div id="htmlMotionSafeZoneBox" class="html-motion-safe-zone-box" style="left:${safeZone.x}%;top:${safeZone.y}%;width:${safeZone.width}%;height:${safeZone.height}%;">
                  <span class="html-motion-safe-zone-resize" data-html-motion-safe-zone-resize aria-hidden="true"></span>
                </div>
                <textarea id="flowerTextEditor" class="flower-text-editor" spellcheck="false" rows="1" placeholder="在这里输入要一直显示在视频里的花字" style="left: ${textX}%; top: ${textY}%; color: ${escapeHtml(textColor)}; -webkit-text-stroke: ${Math.max(0, Math.round(previewFontSize * strokeWidth / 100))}px ${escapeHtml(strokeColor)}; font-family: ${escapeHtml(editorFontFamily)}; font-size: ${previewFontSize}px; font-weight: ${fontWeight};">${escapeHtml(text)}</textarea>
                <button id="flowerTextDragHandle" class="flower-text-drag-handle" type="button" aria-label="拖动花字" draggable="true" style="left: ${textX}%; top: ${textY}%;">
                  <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                    <path d="M12 3v18M3 12h18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                    <path d="m12 3-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </button>
                ${watermarkEnabled && watermarkImage ? `<button id="flowerTextWatermarkDragHandle" class="flower-text-watermark-drag-handle" type="button" aria-label="拖动水印" style="left: ${watermarkX}%; top: ${watermarkY}%; --flower-watermark-size: ${watermarkSize}%; --flower-watermark-opacity: ${Math.min(1, Math.max(0.05, watermarkOpacity / 100))};"><img src="${escapeHtml(watermarkPreviewUrl || flowerTextWatermarkUrl(watermarkImage))}" alt="${escapeHtml(watermarkItem?.name || '水印图片')}"></button>` : ''}
                ${watermark2Enabled && watermark2Image ? `<button id="flowerTextWatermark2DragHandle" class="flower-text-watermark-drag-handle" type="button" aria-label="拖动水印2" style="left: ${watermark2X}%; top: ${watermark2Y}%; --flower-watermark-size: ${watermark2Size}%; --flower-watermark-opacity: ${Math.min(1, Math.max(0.05, watermark2Opacity / 100))};"><img src="${escapeHtml(watermark2PreviewUrl || flowerTextWatermarkUrl(watermark2Image))}" alt="${escapeHtml(watermark2Item?.name || '水印图片')}"></button>` : ''}
              </div>
            </div>
          </div>
          <div class="flower-text-settings">
            <label class="generation-mode-toggle">
              <span>烧录花字</span>
              <input type="checkbox" data-flower-text-toggle ${enabled ? 'checked' : ''} ${saving ? 'disabled' : ''}>
            </label>
            <div class="flower-text-ratio">
              <label>
                <span>画面比例</span>
                <select data-flower-text-ratio-select ${saving ? 'disabled' : ''}>
                  <option value="9:16" ${ratioValue === '9:16' ? 'selected' : ''}>9:16</option>
                  <option value="16:9" ${ratioValue === '16:9' ? 'selected' : ''}>16:9</option>
                  <option value="1:1" ${ratioValue === '1:1' ? 'selected' : ''}>1:1</option>
                </select>
              </label>
            </div>
            <div class="flower-text-style-grid">
              <label class="flower-text-style-field full">
                <span>字体</span>
                <details class="flower-text-font-picker">
                  <summary>
                    <span class="flower-text-font-current">${escapeHtml(fontLabel)}</span>
                    ${renderFlowerTextFontPreview(selectedFont, 'flower-text-font-current-preview', availableFonts.length ? '系统默认字体' : '还没有可用字体')}
                  </summary>
                  <div class="flower-text-font-menu">
                    <button type="button" class="flower-text-font-option${fontFamily ? '' : ' selected'}" data-flower-text-font-option="" aria-label="系统默认" title="系统默认" ${saving ? 'disabled' : ''}>
                      <span class="flower-text-font-option-name">系统默认</span>
                      <span class="flower-text-font-option-preview empty">${availableFonts.length ? '系统默认字体' : '还没有可用字体'}</span>
                    </button>
                    ${availableFonts.map((font) => `
                      <button type="button" class="flower-text-font-option${font.id === fontFamily ? ' selected' : ''}" data-flower-text-font-option="${escapeHtml(font.id)}" aria-label="${escapeHtml(font.name)}" title="${escapeHtml(font.name)}" ${saving ? 'disabled' : ''}>
                        <span class="flower-text-font-option-name">${escapeHtml(font.name)}</span>
                        ${renderFlowerTextFontPreview(font, 'flower-text-font-option-preview', font.name)}
                      </button>
                    `).join('')}
                  </div>
                </details>
              </label>
              <label class="flower-text-style-field">
                <span>字色</span>
                ${renderFlowerTextColorControl('textColor', textColor, saving)}
              </label>
              <label class="flower-text-style-field">
                <span>描边</span>
                ${renderFlowerTextColorControl('strokeColor', strokeColor, saving)}
              </label>
              <label class="flower-text-style-field">
                <span>字号</span>
                <input type="range" data-flower-text-style="fontSize" min="6" max="28" value="${fontSize}" ${saving ? 'disabled' : ''}>
              </label>
              <label class="flower-text-style-field">
                <span>文字粗细</span>
                <input type="range" data-flower-text-style="fontWeight" min="300" max="900" step="100" value="${fontWeight}" ${saving ? 'disabled' : ''}>
              </label>
              <label class="flower-text-style-field">
                <span>描边粗细</span>
                <input type="range" data-flower-text-style="strokeWidth" min="0" max="18" value="${strokeWidth}" ${saving ? 'disabled' : ''}>
              </label>
              <label class="flower-text-style-field">
                <span>位置</span>
                <select data-flower-text-style="position" ${saving ? 'disabled' : ''}>
                  <option value="top" ${position === 'top' ? 'selected' : ''}>上</option>
                  <option value="center" ${position === 'center' ? 'selected' : ''}>中</option>
                  <option value="bottom" ${position === 'bottom' ? 'selected' : ''}>下</option>
                </select>
              </label>
            </div>

            <label class="flower-text-style-field">
              <span>动画入场时间</span>
              <select data-flower-text-style="animationDelaySeconds" ${saving ? 'disabled' : ''}>
                <option value="0" ${animationDelaySeconds === 0 ? 'selected' : ''}>立即出现</option>
                <option value="1" ${animationDelaySeconds === 1 ? 'selected' : ''}>1秒后</option>
                <option value="3" ${animationDelaySeconds === 3 ? 'selected' : ''}>3秒后</option>
                <option value="5" ${animationDelaySeconds === 5 ? 'selected' : ''}>5秒后</option>
                <option value="10" ${animationDelaySeconds === 10 ? 'selected' : ''}>10秒后</option>
              </select>
            </label>
            <label class="flower-text-style-field flower-text-inline-range-field">
              <span>出场动画</span>
              <select data-flower-text-style="animationType" ${saving ? 'disabled' : ''}>
                <option value="fade" ${animationType === 'fade' ? 'selected' : ''}>淡入</option>
                <option value="none" ${animationType === 'none' ? 'selected' : ''}>无动画</option>
              </select>
            </label>
            <div id="flowerTextSaveStatus" class="flower-text-status">${escapeHtml(status)}</div>
            <div class="html-motion-safe-zone-setting">
              <span class="html-motion-safe-zone-actions">
                <button type="button" class="html-motion-safe-zone-button${safeZoneEditing ? ' active' : ''}" data-html-motion-safe-zone-toggle>HTML 动效安全区</button>
                ${safeZoneEditing ? `<button type="button" class="html-motion-safe-zone-save" data-html-motion-safe-zone-save ${safeZoneSaving ? 'disabled' : ''}>${safeZoneSaving ? '保存中' : '保存'}</button>` : ''}
              </span>
            </div>
          </div>
        </div>
      `;
      applyFlowerTextEditorStyle();
    }

    function renderGenerationModeButton() {
      const button = els.generationModeButton;
      if (!button) return;
      const mode = state.generationMode || {};
      const enabled = !!mode.concurrentGeneration;
      const saving = !!mode.saving;
      button.classList.toggle('is-ready', enabled);
      button.classList.toggle('is-open', !!state.generationModeDrawer?.visible);
      button.disabled = saving;
      button.textContent = saving ? '保存中' : '并发模式';
      button.setAttribute('aria-expanded', state.generationModeDrawer?.visible ? 'true' : 'false');
      if (saving) {
        button.title = '正在保存并发模式';
      } else if (enabled) {
        button.title = '并发模式已开启。多条视频会一次性提交。';
      } else if (mode.error) {
        button.title = `并发模式保存失败：${mode.error}`;
      } else {
        button.title = '点击展开并发模式设置。';
      }
    }
