    function videoPreviewEditingControlsMarkup(userGeneratedKey) {
      const encodedKey = escapeHtml(userGeneratedKey);
      const disabled = userGeneratedKey ? '' : 'disabled';
      return `
        <div class="video-preview-html-motion-drawer" data-video-preview-html-motion-drawer>
          <div class="video-preview-html-motion-drawer-slot">
            <button type="button" class="video-preview-html-motion-status" data-video-preview-html-motion-toggle data-video-preview-html-motion-status aria-label="展开任务详情" aria-expanded="false" aria-controls="videoPreviewHtmlMotionDrawer">
              ${videoPreviewIconSvg('chevron')}<span class="video-preview-button-label video-preview-html-motion-summary" data-video-preview-html-motion-summary aria-live="polite" hidden></span>
            </button>
          </div>
          <div id="videoPreviewHtmlMotionDrawer" class="video-preview-html-motion-detail" data-video-preview-html-motion-detail></div>
        </div>
        <div class="video-preview-tts-timeline video-preview-video-timeline" data-video-preview-video-timeline hidden aria-hidden="true">
          <div class="video-preview-tts-timeline-head video-preview-timeline-toolbar">
            <div class="video-preview-timeline-heading">
              <strong>视频裁剪时间轴</strong>
              <span data-video-preview-video-status aria-live="polite">正在读取视频片段</span>
            </div>
            <span class="video-preview-timeline-duration" data-video-preview-video-duration>0.0 秒</span>
            <div class="video-preview-tts-timeline-actions">
              <button type="button" class="video-preview-button video-preview-tts-tool-button" data-video-preview-action="undo-timeline" data-icon="undo" aria-label="没有可撤销的时间轴编辑" title="没有可撤销的时间轴编辑" disabled>${videoPreviewIconSvg('undo')}</button>
              <button type="button" class="video-preview-button video-preview-tts-tool-button" data-video-preview-action="redo-timeline" data-icon="redo" aria-label="没有可重做的时间轴编辑" title="没有可重做的时间轴编辑" disabled>${videoPreviewIconSvg('redo')}</button>
              <button type="button" class="video-preview-button" data-video-preview-action="toggle-background-music" aria-expanded="false">背景音乐</button>
              <button type="button" class="video-preview-button video-preview-tts-tool-button" data-video-preview-video-editor-action data-video-preview-action="toggle-video-seek" data-icon="pointer" aria-label="定位工具" aria-pressed="false" title="开启定位工具">${videoPreviewIconSvg('pointer')}</button>
              <button type="button" class="video-preview-button video-preview-tts-tool-button" data-video-preview-video-editor-action data-video-preview-action="toggle-video-scissors" data-icon="scissors" aria-label="剪刀工具" aria-pressed="false" title="开启剪刀工具">${videoPreviewIconSvg('scissors')}</button>
              <button type="button" class="video-preview-button video-preview-tts-tool-button video-preview-tts-delete-button" data-video-preview-video-editor-action data-video-preview-action="delete-selected-video-chunk" data-icon="trash" aria-label="删除所选视频片段" title="请先点击选择一个视频片段" disabled>${videoPreviewIconSvg('trash')}</button>
              <button type="button" class="video-preview-button" data-video-preview-video-editor-action data-video-preview-action="reset-video-timeline">恢复完整视频</button>
            </div>
          </div>
          <div class="video-preview-tts-chunks video-preview-video-chunks" data-video-preview-video-chunks></div>
        </div>
        <div class="video-preview-background-music-drawer" data-video-preview-background-music-drawer hidden></div>
        <div class="video-preview-tts-timeline" data-video-preview-tts-timeline hidden aria-hidden="true">
          <div class="video-preview-tts-timeline-head video-preview-timeline-toolbar">
            <div class="video-preview-timeline-heading">
              <strong>TTS 配音时间轴</strong>
              <span data-video-preview-tts-status aria-live="polite">正在读取配音</span>
            </div>
            <span class="video-preview-timeline-duration" data-video-preview-tts-duration>0.0 秒</span>
            <div class="video-preview-tts-timeline-actions">
              <button type="button" class="video-preview-button video-preview-tts-tool-button" data-video-preview-tts-editor-action data-video-preview-action="smart-split-tts" data-icon="sparkles" aria-label="智能切块" title="根据音波停顿智能切块">${videoPreviewIconSvg('sparkles')}</button>
              <button type="button" class="video-preview-button video-preview-tts-tool-button" data-video-preview-tts-editor-action data-video-preview-action="toggle-tts-scissors" data-icon="scissors" aria-label="剪刀工具" aria-pressed="false" title="开启剪刀工具">${videoPreviewIconSvg('scissors')}</button>
              <button type="button" class="video-preview-button video-preview-tts-tool-button video-preview-tts-delete-button" data-video-preview-tts-editor-action data-video-preview-action="delete-selected-tts-chunk" data-icon="trash" aria-label="删除所选配音块" title="请先点击选择一个配音块" disabled>${videoPreviewIconSvg('trash')}</button>
              <button type="button" class="video-preview-button" data-video-preview-tts-editor-action data-video-preview-action="export-tts-mp3" title="自定义文件名和保存位置并导出当前时间轴配音">导出 MP3</button>
              <button type="button" class="video-preview-button" data-video-preview-tts-editor-action data-video-preview-action="reset-tts-timeline">恢复完整配音</button>
            </div>
          </div>
          <div class="video-preview-tts-chunks" data-video-preview-tts-chunks></div>
        </div>
        <div class="video-preview-html-motion-timeline" data-video-preview-html-motion-timeline hidden>
          <div class="video-preview-html-motion-timeline-head video-preview-timeline-toolbar">
            <div class="video-preview-timeline-heading">
              <strong>HTML 动效时间轴</strong>
              <span data-video-preview-html-motion-timeline-status aria-live="polite">可切块、删除或拖动</span>
            </div>
            <span class="video-preview-timeline-duration" data-video-preview-html-motion-duration>0.0 秒</span>
            <div class="video-preview-tts-timeline-actions">
              <button type="button" class="video-preview-button video-preview-tts-tool-button" data-video-preview-html-motion-editor-action data-video-preview-action="toggle-html-motion-scissors" data-icon="scissors" aria-label="剪刀工具" aria-pressed="false" title="开启剪刀工具">${videoPreviewIconSvg('scissors')}</button>
              <button type="button" class="video-preview-button video-preview-tts-tool-button video-preview-tts-delete-button" data-video-preview-html-motion-editor-action data-video-preview-action="delete-selected-html-motion-chunk" data-icon="trash" aria-label="删除所选动效片段" title="请先点击选择一个动效片段" disabled>${videoPreviewIconSvg('trash')}</button>
              <button type="button" class="video-preview-button" data-video-preview-html-motion-editor-action data-video-preview-action="reset-html-motion-timeline">恢复完整动效</button>
            </div>
          </div>
          <div class="video-preview-html-motion-chunks" data-video-preview-html-motion-chunks></div>
        </div>
        <div class="video-preview-controls-row">
          <div class="video-preview-control-group">
            <button type="button" class="video-preview-button" data-video-preview-action="edit-video-timeline" data-icon="crop" data-video-user-generated-key="${encodedKey}" aria-expanded="false" title="展开全部时间轴" ${disabled}>${videoPreviewButtonInnerHtml('crop', '裁剪视频')}</button>
            <span class="video-preview-split-button" role="group" aria-label="TTS 配音">
              <button type="button" class="video-preview-button" data-video-preview-action="regenerate-tts" data-icon="mic" data-video-user-generated-key="${encodedKey}" ${disabled}>${videoPreviewButtonInnerHtml('mic', '重新生成TTS配音')}</button>
              <button type="button" class="video-preview-button" data-video-preview-action="edit-tts-text" data-icon="edit" data-video-user-generated-key="${encodedKey}" aria-expanded="false" title="展开台词编辑器" ${disabled}>${videoPreviewButtonInnerHtml('edit', '修改台词')}</button>
            </span>
            <button type="button" class="video-preview-button" data-video-preview-action="regenerate-html-motion" data-icon="sparkles" data-video-user-generated-key="${encodedKey}" ${disabled}>${videoPreviewButtonInnerHtml('sparkles', '重新生成 HTML 动效')}</button>
          </div>
          <div class="video-preview-side-actions">
            <button type="button" class="video-preview-button primary" data-video-preview-action="confirm-burn" data-icon="check" data-video-user-generated-key="${encodedKey}" disabled>${videoPreviewButtonInnerHtml('check', '确认烧录')}</button>
            ${userGeneratedKey ? `<button type="button" class="video-preview-button danger" data-video-preview-action="delete-video" data-icon="trash" data-video-user-generated-key="${encodedKey}">${videoPreviewButtonInnerHtml('trash', '删除视频')}</button>` : ''}
          </div>
        </div>
      `;
    }
