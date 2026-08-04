(function installAI8WaapiTimeline(global) {
  'use strict';

  const EASINGS = {
    linear: 'linear',
    'power1.out': 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
    'power2.out': 'cubic-bezier(0.16, 1, 0.3, 1)',
    'power3.out': 'cubic-bezier(0.16, 1, 0.3, 1)',
    'power2.in': 'cubic-bezier(0.55, 0.06, 0.68, 0.19)',
    'expo.out': 'cubic-bezier(0.16, 1, 0.3, 1)',
    'circ.out': 'cubic-bezier(0, 0.55, 0.45, 1)',
    'sine.inout': 'cubic-bezier(0.37, 0, 0.63, 1)',
  };
  const textPositionState = new WeakMap();

  function number(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function hasOwn(state, key) {
    return Object.prototype.hasOwnProperty.call(state, key);
  }

  function transformValue(state) {
    const keys = ['x', 'y', 'scale', 'scaleX', 'scaleY', 'rotation'];
    if (!keys.some((key) => hasOwn(state, key))) return null;
    const x = number(state.x, 0);
    const y = number(state.y, 0);
    const rotation = number(state.rotation, 0);
    const scale = number(state.scale, 1);
    const scaleX = scale * number(state.scaleX, 1);
    const scaleY = scale * number(state.scaleY, 1);
    return `translate(${x}px, ${y}px) rotate(${rotation}deg) scale(${scaleX}, ${scaleY})`;
  }

  function keyframe(rawState) {
    const state = rawState && typeof rawState === 'object' ? rawState : {};
    const frame = {};
    const transform = transformValue(state);
    if (transform !== null) frame.transform = transform;
    if (hasOwn(state, 'autoAlpha')) frame.opacity = number(state.autoAlpha, 1);
    if (hasOwn(state, 'opacity')) frame.opacity = number(state.opacity, 1);
    if (hasOwn(state, 'color')) frame.color = String(state.color);
    if (hasOwn(state, 'backgroundColor')) frame.backgroundColor = String(state.backgroundColor);
    if (hasOwn(state, 'borderRadius')) frame.borderRadius = `${number(state.borderRadius, 0)}px`;
    if (hasOwn(state, 'transformOrigin')) frame.transformOrigin = String(state.transformOrigin);
    return frame;
  }

  function easing(value) {
    const normalized = String(value || 'linear').trim().toLowerCase();
    if (normalized.startsWith('back.out')) return 'cubic-bezier(0.34, 1.56, 0.64, 1)';
    return EASINGS[normalized] || 'cubic-bezier(0.25, 0.1, 0.25, 1)';
  }

  function initializeEntrance(node, frame) {
    for (const [property, value] of Object.entries(frame)) {
      node.style[property] = String(value);
    }
  }

  function createAnimation(node, item, index) {
    const from = keyframe(item.from);
    const to = keyframe(item.to);
    if (item.static) {
      if (item.kind !== 'exit' && item.kind !== 'scene-end') initializeEntrance(node, to);
      return null;
    }
    if (item.kind === 'entrance') initializeEntrance(node, from);
    const staggerSeconds = number(item.to && item.to.stagger, 0);
    const delaySeconds = number(item.at, 0) + staggerSeconds * index;
    const animation = node.animate([from, to], {
      duration: Math.max(1, number(item.duration, 0.001) * 1000),
      delay: delaySeconds * 1000,
      easing: easing(item.to && item.to.ease),
      fill: 'forwards',
      iterations: 1,
    });
    animation.pause();
    animation.currentTime = 0;
    const scene = node.closest('.hf-scene');
    return {
      animation,
      baseDelay: delaySeconds * 1000,
      kind: String(item.kind || ''),
      localDelay: number(item.localAt, delaySeconds - number(scene?.dataset.start, 0)) * 1000,
      sceneIndex: sceneSourceIndex(scene),
      chunkId: sceneChunkId(scene),
    };
  }

  function sceneSourceIndex(scene) {
    return number(scene?.dataset.timelineSourceIndex, number(scene?.id?.replace('hf-scene-', ''), 0) - 1);
  }

  function sceneChunkId(scene, fallbackIndex) {
    const value = String(scene?.dataset.chunkId || '').trim();
    const index = Number.isInteger(fallbackIndex)
      ? fallbackIndex
      : Math.max(0, number(scene?.dataset.trackIndex, 1) - 1);
    return value || `html-motion-chunk-${index + 1}`;
  }

  function sceneForSourceIndex(sourceIndex) {
    return Array.from(document.querySelectorAll('.hf-scene'))
      .find((scene) => sceneSourceIndex(scene) === sourceIndex) || null;
  }

  function sceneForChunkId(chunkId) {
    const normalized = String(chunkId || '').trim();
    if (!normalized) return null;
    return Array.from(document.querySelectorAll('.hf-scene'))
      .find((scene) => sceneChunkId(scene) === normalized) || null;
  }

  function textPositionTarget(scene) {
    return scene?.querySelector('.hf-copy')
      || scene?.querySelector('.hf-card-content')
      || scene?.querySelector('h1, h2, h3, p, small')
      || null;
  }

  function normalizedTextPosition(value) {
    const x = number(value?.x, NaN);
    const y = number(value?.y, NaN);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return {
      x: Math.min(100, Math.max(0, x)),
      y: Math.min(100, Math.max(0, y)),
    };
  }

  function storedSceneTextPosition(scene) {
    return normalizedTextPosition({ x: scene?.dataset.textX, y: scene?.dataset.textY });
  }

  function previewRootGeometry() {
    const root = document.getElementById('root');
    if (!root) return null;
    const rect = root.getBoundingClientRect();
    const width = Math.max(1, number(root.dataset.width, root.offsetWidth));
    const height = Math.max(1, number(root.dataset.height, root.offsetHeight));
    return {
      root,
      rect,
      width,
      height,
      scaleX: Math.max(0.0001, rect.width / width),
      scaleY: Math.max(0.0001, rect.height / height),
    };
  }

  function targetBaseMetrics(target, geometry) {
    let state = textPositionState.get(target);
    if (!state) {
      state = { inlineTranslate: target.style.translate || '' };
      textPositionState.set(target, state);
    }
    const activeTranslate = target.style.translate;
    target.style.translate = state.inlineTranslate;
    const rect = target.getBoundingClientRect();
    target.style.translate = activeTranslate;
    return {
      centerX: (rect.left + rect.width / 2 - geometry.rect.left) / geometry.scaleX,
      centerY: (rect.top + rect.height / 2 - geometry.rect.top) / geometry.scaleY,
      width: rect.width / geometry.scaleX,
      height: rect.height / geometry.scaleY,
    };
  }

  function applySceneTextPosition(scene, rawPosition) {
    const target = textPositionTarget(scene);
    const geometry = previewRootGeometry();
    const position = normalizedTextPosition(rawPosition);
    if (!target || !geometry || !position) return false;
    const base = targetBaseMetrics(target, geometry);
    const desiredX = position.x / 100 * geometry.width;
    const desiredY = position.y / 100 * geometry.height;
    target.style.translate = `${desiredX - base.centerX}px ${desiredY - base.centerY}px`;
    scene.dataset.textX = position.x.toFixed(3);
    scene.dataset.textY = position.y.toFixed(3);
    return true;
  }

  function clearSceneTextPosition(scene) {
    const target = textPositionTarget(scene);
    const state = target ? textPositionState.get(target) : null;
    if (target) target.style.translate = state?.inlineTranslate || '';
    if (scene) {
      delete scene.dataset.textX;
      delete scene.dataset.textY;
    }
  }

  function currentSceneTextPosition(scene) {
    const target = textPositionTarget(scene);
    const geometry = previewRootGeometry();
    if (!target || !geometry) return null;
    const rect = target.getBoundingClientRect();
    return normalizedTextPosition({
      x: (rect.left + rect.width / 2 - geometry.rect.left) / geometry.rect.width * 100,
      y: (rect.top + rect.height / 2 - geometry.rect.top) / geometry.rect.height * 100,
    });
  }

  function clampSceneTextPosition(scene, rawPosition) {
    const target = textPositionTarget(scene);
    const geometry = previewRootGeometry();
    const position = normalizedTextPosition(rawPosition);
    if (!target || !geometry || !position) return position;
    const rect = target.getBoundingClientRect();
    const halfWidthPercent = rect.width / geometry.rect.width * 50;
    const halfHeightPercent = rect.height / geometry.rect.height * 50;
    return {
      x: Math.min(100 - halfWidthPercent, Math.max(halfWidthPercent, position.x)),
      y: Math.min(100 - halfHeightPercent, Math.max(halfHeightPercent, position.y)),
    };
  }

  function applyStoredTextPositions() {
    document.querySelectorAll('.hf-scene').forEach((scene) => {
      const position = storedSceneTextPosition(scene);
      if (position && !scene.hidden) applySceneTextPosition(scene, position);
    });
  }

  function applyChunkTextPositions() {
    (global.__ai8MotionChunks || []).forEach((chunk) => {
      const scene = sceneForChunkId(chunk?.chunkId)
        || sceneForSourceIndex(number(chunk?.sourceIndex, chunk?.index));
      if (!scene || scene.hidden) return;
      const position = normalizedTextPosition(chunk?.textPosition);
      if (position) applySceneTextPosition(scene, position);
      else clearSceneTextPosition(scene);
    });
  }

  function chunksForIds(ids) {
    const selected = new Set(Array.isArray(ids) ? ids.map(String) : []);
    return (global.__ai8MotionChunks || []).filter((chunk) => selected.has(String(chunk?.chunkId || '')));
  }

  function scenesForChunkIds(ids) {
    const scenes = new Set();
    chunksForIds(ids).forEach((chunk) => {
      const exact = sceneForChunkId(chunk.chunkId);
      if (exact) scenes.add(exact);
      else {
        const fallback = sceneForSourceIndex(number(chunk.sourceIndex, chunk.index));
        if (fallback) scenes.add(fallback);
      }
    });
    return [...scenes];
  }

  function syncTextPositionEditingState() {
    const editableScenes = scenesForChunkIds(global.__ai8MotionEditableChunkIds)
      .filter((scene) => !scene.hidden && textPositionTarget(scene));
    document.querySelectorAll('.hf-scene').forEach((scene) => {
      const target = textPositionTarget(scene);
      const editable = editableScenes.includes(scene);
      scene.dataset.ai8TextEditable = editable ? 'true' : 'false';
      target?.classList.toggle('ai8-motion-text-edit-target', editable);
      target?.classList.toggle('is-ai8-motion-text-editable', editable);
      if (!editable) target?.classList.remove('is-ai8-motion-text-dragging');
    });
    const signature = editableScenes.map((scene) => sceneChunkId(scene)).sort().join('|');
    if (signature === global.__ai8MotionEditableSignature) return;
    global.__ai8MotionEditableSignature = signature;
    global.parent?.postMessage({
      type: 'ai8-motion-editability',
      editable: editableScenes.length > 0,
      anchorChunkIds: editableScenes.map((scene) => sceneChunkId(scene)),
    }, '*');
  }

  function bindTextPositionEditing() {
    document.querySelectorAll('.hf-scene').forEach((scene) => {
      const target = textPositionTarget(scene);
      if (!target || target.dataset.ai8TextPositionBound === 'true') return;
      target.dataset.ai8TextPositionBound = 'true';
      target.addEventListener('pointerdown', (event) => beginTextPositionDrag(event, scene, target));
    });
  }

  function beginTextPositionDrag(event, anchorScene, target) {
    if (event.button !== 0 || anchorScene.dataset.ai8TextEditable !== 'true') return;
    const geometry = previewRootGeometry();
    const start = currentSceneTextPosition(anchorScene);
    if (!geometry || !start) return;
    const selectedScenes = scenesForChunkIds(global.__ai8MotionSelectedChunkIds);
    if (!selectedScenes.includes(anchorScene)) selectedScenes.push(anchorScene);
    const previous = new Map(selectedScenes.map((scene) => [scene, storedSceneTextPosition(scene)]));
    const originX = event.clientX;
    const originY = event.clientY;
    let moved = false;
    event.preventDefault();
    event.stopPropagation();
    target.setPointerCapture(event.pointerId);
    target.classList.add('is-ai8-motion-text-dragging');
    global.parent?.postMessage({ type: 'ai8-motion-text-drag-start' }, '*');
    const move = (moveEvent) => {
      const deltaX = moveEvent.clientX - originX;
      const deltaY = moveEvent.clientY - originY;
      if (!moved && Math.hypot(deltaX, deltaY) < 2) return;
      moved = true;
      const next = clampSceneTextPosition(anchorScene, {
        x: start.x + deltaX / geometry.rect.width * 100,
        y: start.y + deltaY / geometry.rect.height * 100,
      });
      selectedScenes.forEach((scene) => applySceneTextPosition(scene, next));
    };
    const end = (endEvent) => {
      target.removeEventListener('pointermove', move);
      target.removeEventListener('pointerup', end);
      target.removeEventListener('pointercancel', end);
      target.removeEventListener('lostpointercapture', end);
      target.classList.remove('is-ai8-motion-text-dragging');
      if (endEvent.type !== 'pointerup') {
        previous.forEach((position, scene) => {
          if (position) applySceneTextPosition(scene, position);
          else clearSceneTextPosition(scene);
        });
        return;
      }
      if (!moved) return;
      const position = currentSceneTextPosition(anchorScene);
      if (!position) return;
      global.parent?.postMessage({
        type: 'ai8-motion-text-position-change',
        anchorChunkId: sceneChunkId(anchorScene),
        selectedChunkIds: global.__ai8MotionSelectedChunkIds || [],
        position,
      }, '*');
    };
    target.addEventListener('pointermove', move);
    target.addEventListener('pointerup', end);
    target.addEventListener('pointercancel', end);
    target.addEventListener('lostpointercapture', end);
  }

  function mount(plan) {
    const animations = [];
    const items = Array.isArray(plan && plan.animations) ? plan.animations : [];
    for (const item of items) {
      if (!item || typeof item.target !== 'string') continue;
      let nodes = [];
      try {
        nodes = Array.from(document.querySelectorAll(item.target));
      } catch (_) {
        continue;
      }
      nodes.forEach((node, index) => {
        const record = createAnimation(node, item, index);
        if (record) animations.push(record);
      });
    }
    global.__ai8MotionPlan = plan;
    global.__ai8MotionAnimations = animations.map((entry) => entry.animation);
    global.__ai8MotionRecords = animations;
    fitPreviewViewport();
    bindTextPositionEditing();
    applyStoredTextPositions();
    updateChunks(Array.from(document.querySelectorAll('.hf-scene')).map((scene, index) => ({
      chunkId: sceneChunkId(scene, index),
      sourceIndex: sceneSourceIndex(scene),
      startSeconds: number(scene.dataset.start, 0),
      durationSeconds: number(scene.dataset.duration, 0.1),
      textPosition: storedSceneTextPosition(scene),
      index,
    })));
    global.document?.fonts?.ready?.then(() => {
      fitPreviewViewport();
      applyStoredTextPositions();
      syncTextPositionEditingState();
    });
    global.parent?.postMessage({ type: 'ai8-motion-ready' }, '*');
    return global.__ai8MotionAnimations;
  }

  function fitPreviewViewport() {
    const root = document.getElementById('root');
    if (!root) return;
    const width = Math.max(1, number(root.dataset.width, root.offsetWidth));
    const height = Math.max(1, number(root.dataset.height, root.offsetHeight));
    const scale = Math.min(global.innerWidth / width, global.innerHeight / height);
    root.style.position = 'absolute';
    root.style.left = `${Math.max(0, (global.innerWidth - width * scale) / 2)}px`;
    root.style.top = `${Math.max(0, (global.innerHeight - height * scale) / 2)}px`;
    root.style.transformOrigin = 'top left';
    root.style.transform = scale < 0.999 ? `scale(${scale})` : '';
  }

  function seek(seconds) {
    const current = Math.max(0, number(seconds, 0));
    const time = current * 1000;
    const windowsById = global.__ai8MotionChunkWindowsById;
    const windowsBySource = global.__ai8MotionChunkWindows;
    global.__ai8MotionCurrentTime = current;
    if (global.__ai8MotionManageSceneVisibility) {
      for (const scene of document.querySelectorAll('.hf-scene')) {
        const sourceIndex = sceneSourceIndex(scene);
        const exact = windowsById instanceof Map ? windowsById.get(sceneChunkId(scene)) || [] : [];
        const windows = exact.length
          ? exact
          : windowsBySource instanceof Map ? windowsBySource.get(sourceIndex) || [] : null;
        scene.hidden = Array.isArray(windows) && !windows.some((window) => current >= window.start && current < window.end);
      }
    }
    applyChunkTextPositions();
    for (const record of global.__ai8MotionRecords || []) {
      const exact = windowsById instanceof Map ? windowsById.get(record.chunkId) || [] : [];
      const windows = exact.length
        ? exact
        : windowsBySource instanceof Map ? windowsBySource.get(record.sceneIndex) || [] : null;
      const active = Array.isArray(windows)
        ? windows.find((window) => current >= window.start && current < window.end)
        : null;
      if (active) {
        const sourceOffset = Math.max(0, number(active.sourceStart, 0) - number(active.originalSourceStart, 0));
        const delay = record.kind === 'scene-end'
          ? active.end * 1000
          : active.start * 1000 + record.localDelay - sourceOffset * 1000;
        record.animation.effect.updateTiming({ delay });
      }
      record.animation.currentTime = time;
    }
    syncTextPositionEditingState();
  }

  function updateChunks(chunks) {
    if (!Array.isArray(chunks)) return;
    const windowsById = new Map();
    const windowsBySource = new Map();
    chunks.forEach((chunk, index) => {
      const chunkId = String(chunk?.chunkId || `html-motion-chunk-${index + 1}`);
      const sourceIndex = number(chunk && chunk.sourceIndex, number(chunk && chunk.index, index));
      const scene = sceneForChunkId(chunkId) || sceneForSourceIndex(sourceIndex);
      const original = number(scene?.dataset.originalStart, number(scene?.dataset.start, 0));
      const start = Math.max(0, number(chunk.startSeconds, original));
      const duration = Math.max(0.1, number(chunk.durationSeconds, number(chunk.endSeconds, start) - start));
      const sourceStart = number(chunk.sourceStartSeconds, number(scene?.dataset.sourceStart, original));
      const originalSourceStart = number(chunk.originalSourceStartSeconds, sourceStart);
      const window = { start, end: start + duration, originalStart: original, sourceStart, originalSourceStart };
      const idWindows = windowsById.get(chunkId) || [];
      idWindows.push(window);
      windowsById.set(chunkId, idWindows);
      const windows = windowsBySource.get(sourceIndex) || [];
      windows.push(window);
      windowsBySource.set(sourceIndex, windows);
    });
    for (const [sourceIndex, windows] of windowsBySource) {
      windows.sort((left, right) => left.start - right.start);
      const scene = sceneForSourceIndex(sourceIndex);
      if (scene) {
        scene.dataset.timelineSourceIndex = String(sourceIndex);
        scene.dataset.originalStart = String(windows[0]?.originalStart || 0);
      }
    }
    global.__ai8MotionChunks = chunks;
    global.__ai8MotionChunkWindowsById = windowsById;
    global.__ai8MotionChunkWindows = windowsBySource;
    seek(global.__ai8MotionCurrentTime || 0);
  }

  global.addEventListener('message', (event) => {
    if (event.data?.type !== 'ai8-motion-preview') return;
    global.__ai8MotionManageSceneVisibility = true;
    global.__ai8MotionSelectedChunkIds = Array.isArray(event.data.selectedChunkIds)
      ? event.data.selectedChunkIds.map(String)
      : [];
    global.__ai8MotionEditableChunkIds = Array.isArray(event.data.editableChunkIds)
      ? event.data.editableChunkIds.map(String)
      : [];
    updateChunks(event.data.chunks);
    seek(event.data.currentTime);
  });
  global.addEventListener('resize', () => {
    fitPreviewViewport();
    applyStoredTextPositions();
    syncTextPositionEditingState();
  });

  global.AI8WaapiTimeline = Object.freeze({ mount, seek, updateChunks, fitPreviewViewport });
})(window);
