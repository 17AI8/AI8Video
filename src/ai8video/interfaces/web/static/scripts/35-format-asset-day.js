    function formatAssetDay(value) {
      if (!value) return '未标记日期';
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return '未标记日期';
      return parsed.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
    }

    function formatAssetTime(value) {
      if (!value) return '时间未知';
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return '时间未知';
      return parsed.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
    }

    function formatReportDay(value) {
      if (!value) return '未标记日期';
      if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) {
        return String(value).replace(/-/g, '/');
      }
      return formatAssetDay(value);
    }

    function formatReportTime(value) {
      if (!value) return '时间未知';
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return '时间未知';
      return parsed.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
    }

    function formatPendingTime(value) {
      if (!value) return '时间未知';
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return String(value);
      return parsed.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    }

    function extractFileName(value) {
      const raw = String(value || '').trim();
      if (!raw) return '';
      const parts = raw.split('/');
      return parts[parts.length - 1] || raw;
    }

    function renderParagraphs(text) {
      return String(text)
        .split(/\n+/)
        .filter(Boolean)
        .map((line) => `<p>${escapeHtml(line)}</p>`)
        .join('');
    }

    function formatFileSize(value) {
      const bytes = Number(value || 0);
      if (!Number.isFinite(bytes) || bytes <= 0) return '';
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    }

    function escapeHtml(value) {
      return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function cleanDisplayText(value, fallback = '') {
      const raw = String(value ?? '').replace(/\s+/g, ' ').trim();
      if (!raw) return fallback;
      const repaired = repairUtf8Mojibake(raw);
      return repaired || fallback;
    }

    function repairUtf8Mojibake(value) {
      let text = String(value ?? '');
      for (let i = 0; i < 3 && /[ÃÂÀ-ÿ]/.test(text); i += 1) {
        const decoded = decodeLatin1Utf8Text(text);
        if (!decoded || decoded === text || mojibakeRepairScore(decoded) <= mojibakeRepairScore(text)) break;
        text = decoded;
      }
      return text;
    }

    function mojibakeRepairScore(value) {
      const text = String(value ?? '');
      const cjk = (text.match(/[\u3400-\u9fff]/g) || []).length;
      const chinesePunctuation = (text.match(/[，。？！、：“”《》]/g) || []).length;
      const mojibakeMarkers = (text.match(/[ÃÂ]|[àáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ]/gi) || []).length;
      const replacementMarkers = (text.match(/\uFFFD/g) || []).length;
      return cjk * 3 + chinesePunctuation - mojibakeMarkers * 2 - replacementMarkers * 4;
    }

    function decodeLatin1Utf8Text(value) {
      const text = String(value ?? '');
      try {
        const bytes = Uint8Array.from(Array.from(text, (char) => char.charCodeAt(0) & 0xff));
        return new TextDecoder('utf-8', { fatal: false }).decode(bytes);
      } catch {
        return text;
      }
    }

    (() => {
      const syncBrandSlugLabel = () => {
        const brandSlug = document.getElementById('brandSlug');
        if (brandSlug) brandSlug.textContent = '批量创作助手';
      };
      syncBrandSlugLabel();
      window.addEventListener('DOMContentLoaded', syncBrandSlugLabel, { once: true });
      window.addEventListener('load', syncBrandSlugLabel, { once: true });
    })();
