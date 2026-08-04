    async function openCurrentBurnedVideoInFolder(button) {
      const userGeneratedKey = currentVideoPreviewUserGeneratedKey();
      if (!userGeneratedKey) throw new Error('当前预览没有可定位的视频文件');
      const idleLabel = '在文件夹中打开';
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = '正在打开…';
      try {
        const res = await fetch('/api/user-generated-results/open-burned-in-folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ userGeneratedKey }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data?.ok === false) {
          throw new Error(data?.error || '打开烧录结果失败');
        }
        button.textContent = '已在文件夹中选中';
        window.setTimeout(() => {
          button.textContent = idleLabel;
        }, 1200);
        return data;
      } finally {
        button.disabled = false;
        button.setAttribute('aria-busy', 'false');
        if (button.textContent === '正在打开…') button.textContent = idleLabel;
      }
    }
