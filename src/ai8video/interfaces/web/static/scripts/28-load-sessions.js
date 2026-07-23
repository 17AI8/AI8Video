    function loadSessions() {
      try {
        const primary = localStorage.getItem(SESSION_STORAGE_KEY);
        if (!primary) return [];
        const parsed = JSON.parse(primary || '[]');
        const schemaMigration = migrateLegacyVideoSchema(parsed);
        const brandingMigration = migrateStoredSessionBranding(schemaMigration.value);
        if (schemaMigration.changed || brandingMigration.changed) {
          localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(brandingMigration.sessions));
        }
        return brandingMigration.sessions;
      } catch {
        return [];
      }
    }

    function replaceLegacyBrandText(value) {
      if (typeof value !== 'string') return value;
      return value.replace(/AI8[ _.-]*mini[ _.-]*video/gi, BRAND_NAME);
    }

    function migrateStoredSessionBranding(value) {
      if (!Array.isArray(value)) return { sessions: [], changed: false };
      let changed = false;
      const sessions = value.map((session) => {
        if (!session || typeof session !== 'object') return session;
        const title = replaceLegacyBrandText(session.title);
        const messages = Array.isArray(session.messages)
          ? session.messages.map((message) => migrateAssistantMessageBranding(message))
          : session.messages;
        const messagesChanged = Array.isArray(session.messages)
          && messages.some((message, index) => message !== session.messages[index]);
        if (title !== session.title || messagesChanged) {
          changed = true;
          return { ...session, title, messages };
        }
        return session;
      });
      return { sessions, changed };
    }

    function migrateAssistantMessageBranding(message) {
      if (!message || typeof message !== 'object' || message.role === 'user') return message;
      const text = replaceLegacyBrandText(replaceLegacyAssistantSemantics(message.text));
      const payload = migrateAssistantPayloadBranding(message.payload);
      if (text === message.text && payload === message.payload) return message;
      return { ...message, text, payload };
    }

    function migrateAssistantPayloadBranding(payload) {
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return payload;
      const text = replaceLegacyBrandText(replaceLegacyAssistantSemantics(payload.text));
      const reply = payload.reply && typeof payload.reply === 'object'
        ? { ...payload.reply, text: replaceLegacyBrandText(replaceLegacyAssistantSemantics(payload.reply.text)) }
        : payload.reply;
      if (text === payload.text && reply?.text === payload.reply?.text) return payload;
      return { ...payload, text, reply };
    }

    function migrateLegacyBrowserStorage() {
      try {
        STORAGE_MIGRATIONS.forEach((entry) => {
          let currentValue = localStorage.getItem(entry.key);
          if (currentValue === null) {
            const legacyKey = entry.legacyKeys.find((key) => localStorage.getItem(key) !== null);
            if (legacyKey) {
              currentValue = localStorage.getItem(legacyKey);
              localStorage.setItem(entry.key, currentValue);
            }
          }
          if (currentValue !== null) {
            entry.legacyKeys.forEach((key) => localStorage.removeItem(key));
          }
        });
      } catch (error) {
        console.warn('旧版浏览器缓存迁移失败，将继续使用当前会话', error);
      }
    }

    function removeProductStorageEntry(storageKey) {
      localStorage.removeItem(storageKey);
      const migration = STORAGE_MIGRATIONS.find((entry) => entry.key === storageKey);
      migration?.legacyKeys.forEach((key) => localStorage.removeItem(key));
    }

    function loadHotRadarColumnCount() {
      try {
        return localStorage.getItem(HOT_RADAR_COLUMN_COUNT_STORAGE_KEY) === '2' ? 2 : 1;
      } catch {
        return 1;
      }
    }

    function loadHotRadarViewState() {
      try {
        const value = JSON.parse(localStorage.getItem(HOT_RADAR_VIEW_STATE_STORAGE_KEY) || '{}');
        return {
          selectedSourceId: String(value?.selectedSourceId || ''),
          selectedTopicId: String(value?.selectedTopicId || ''),
          keyword: String(value?.keyword || ''),
        };
      } catch {
        return {};
      }
    }

    function persistHotRadarViewState(hotRadar) {
      const value = {
        selectedSourceId: String(hotRadar?.selectedSourceId || ''),
        selectedTopicId: String(hotRadar?.selectedTopicId || ''),
        keyword: String(hotRadar?.keyword || ''),
      };
      try {
        localStorage.setItem(HOT_RADAR_VIEW_STATE_STORAGE_KEY, JSON.stringify(value));
      } catch {
        // 本地存储不可用时不影响当前页面内的热点选择。
      }
    }

    function loadHotRadarSnapshot() {
      try {
        const raw = localStorage.getItem(HOT_RADAR_SNAPSHOT_STORAGE_KEY);
        const snapshot = raw ? JSON.parse(raw) : null;
        const savedAt = Number(snapshot?.savedAt || 0);
        const items = Array.isArray(snapshot?.items) ? snapshot.items : [];
        if (!items.length || Date.now() - savedAt > HOT_RADAR_SNAPSHOT_MAX_AGE_MS) return {};
        return {
          sources: Array.isArray(snapshot.sources) ? snapshot.sources : [],
          categories: snapshot.categories && typeof snapshot.categories === 'object' ? snapshot.categories : {},
          items,
          selectedTopicId: String(snapshot.selectedTopicId || items[0]?.id || ''),
          updatedAt: String(snapshot.updatedAt || ''),
          errors: Array.isArray(snapshot.errors) ? snapshot.errors : [],
          fetchRouteLabel: String(snapshot.fetchRouteLabel || '公开数据源'),
          realDataAvailable: true,
          notice: `已恢复上次保存的 ${items.length} 条热点，点击“刷新”获取最新热榜`,
        };
      } catch {
        return {};
      }
    }

    function persistHotRadarSnapshot(hotRadar) {
      if (hotRadar.selectedSourceId || hotRadar.selectedCategory || hotRadar.keyword || !hotRadar.items.length) return;
      const snapshot = {
        savedAt: Date.now(),
        sources: hotRadar.sources,
        categories: hotRadar.categories,
        items: hotRadar.items,
        selectedTopicId: hotRadar.selectedTopicId,
        updatedAt: hotRadar.updatedAt,
        errors: hotRadar.errors,
        fetchRouteLabel: hotRadar.fetchRouteLabel,
      };
      try {
        localStorage.setItem(HOT_RADAR_SNAPSHOT_STORAGE_KEY, JSON.stringify(snapshot));
      } catch {
        // 本地存储不可用时仍保留当前页面内的热点结果。
      }
    }

    function sessionStorageReplacer(aggressive = false) {
      const maxStringLength = aggressive ? 4000 : 16000;
      const maxArrayLength = aggressive ? 16 : 48;
      return (key, value) => {
        if (SESSION_STORAGE_OMIT_KEYS.has(key)) return undefined;
        if (typeof value === 'string') {
          if (/^(data:|blob:)/i.test(value)) return undefined;
          return value.length > maxStringLength ? value.slice(0, maxStringLength) : value;
        }
        if (Array.isArray(value) && value.length > maxArrayLength) {
          return value.slice(0, maxArrayLength);
        }
        return value;
      };
    }

    function sessionsForStorage(sessionLimit, messageLimit) {
      const sessions = state.sessions.slice(0, sessionLimit);
      const active = getActiveSession();
      if (active && !sessions.some((item) => item.id === active.id)) {
        sessions.splice(Math.max(0, sessions.length - 1), 1, active);
      }
      return sessions.map((session) => ({
        id: session.id,
        title: session.title,
        messages: (session.messages || []).slice(-messageLimit),
      }));
    }

    function serializeSessionsForStorage(sessionLimit, messageLimit, aggressive) {
      return JSON.stringify(
        sessionsForStorage(sessionLimit, messageLimit),
        sessionStorageReplacer(aggressive),
      );
    }

    function tryPersistSessionSnapshot(serialized) {
      if (!serialized || serialized.length > SESSION_STORAGE_MAX_CHARS) return false;
      try {
        localStorage.setItem(SESSION_STORAGE_KEY, serialized);
        const migration = STORAGE_MIGRATIONS.find((entry) => entry.key === SESSION_STORAGE_KEY);
        migration?.legacyKeys.forEach((key) => localStorage.removeItem(key));
        return true;
      } catch (error) {
        console.warn('会话缓存空间不足，正在自动精简', error);
        return false;
      }
    }

    function persistSessions() {
      const candidates = [
        [8, 80, false],
        [5, 50, true],
        [3, 30, true],
        [1, 20, true],
      ];
      for (const [sessionLimit, messageLimit, aggressive] of candidates) {
        const serialized = serializeSessionsForStorage(sessionLimit, messageLimit, aggressive);
        if (tryPersistSessionSnapshot(serialized)) return true;
      }
      try {
        localStorage.removeItem(SESSION_STORAGE_KEY);
      } catch (error) {
        console.warn('会话缓存清理失败', error);
      }
      return false;
    }














































































































































































































































































