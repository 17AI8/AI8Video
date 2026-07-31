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
    return {
      animation,
      baseDelay: delaySeconds * 1000,
      kind: String(item.kind || ''),
      localDelay: number(item.localAt, delaySeconds - number(node.closest('.hf-scene')?.dataset.start, 0)) * 1000,
      sceneIndex: sceneSourceIndex(node.closest('.hf-scene')),
    };
  }

  function sceneSourceIndex(scene) {
    return number(scene?.dataset.timelineSourceIndex, number(scene?.id?.replace('hf-scene-', ''), 0) - 1);
  }

  function sceneForSourceIndex(sourceIndex) {
    return Array.from(document.querySelectorAll('.hf-scene'))
      .find((scene) => sceneSourceIndex(scene) === sourceIndex) || null;
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
    updateChunks(Array.from(document.querySelectorAll('.hf-scene')).map((scene, index) => ({
      sourceIndex: sceneSourceIndex(scene),
      startSeconds: number(scene.dataset.start, 0),
      durationSeconds: number(scene.dataset.duration, 0.1),
      index,
    })));
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
    const windowsBySource = global.__ai8MotionChunkWindows;
    global.__ai8MotionCurrentTime = current;
    if (global.__ai8MotionManageSceneVisibility) {
      for (const scene of document.querySelectorAll('.hf-scene')) {
        const sourceIndex = sceneSourceIndex(scene);
        const windows = windowsBySource instanceof Map ? windowsBySource.get(sourceIndex) || [] : null;
        scene.hidden = Array.isArray(windows) && !windows.some((window) => current >= window.start && current < window.end);
      }
    }
    for (const record of global.__ai8MotionRecords || []) {
      const windows = windowsBySource instanceof Map ? windowsBySource.get(record.sceneIndex) || [] : null;
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
  }

  function updateChunks(chunks) {
    if (!Array.isArray(chunks)) return;
    const windowsBySource = new Map();
    chunks.forEach((chunk, index) => {
      const sourceIndex = number(chunk && chunk.sourceIndex, number(chunk && chunk.index, index));
      const scene = sceneForSourceIndex(sourceIndex);
      const original = number(scene?.dataset.originalStart, number(scene?.dataset.start, 0));
      const start = Math.max(0, number(chunk.startSeconds, original));
      const duration = Math.max(0.1, number(chunk.durationSeconds, number(chunk.endSeconds, start) - start));
      const sourceStart = number(chunk.sourceStartSeconds, number(scene?.dataset.sourceStart, original));
      const originalSourceStart = number(chunk.originalSourceStartSeconds, sourceStart);
      const windows = windowsBySource.get(sourceIndex) || [];
      windows.push({ start, end: start + duration, originalStart: original, sourceStart, originalSourceStart });
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
    global.__ai8MotionChunkWindows = windowsBySource;
    seek(global.__ai8MotionCurrentTime || 0);
  }

  global.addEventListener('message', (event) => {
    if (event.data?.type !== 'ai8-motion-preview') return;
    global.__ai8MotionManageSceneVisibility = true;
    updateChunks(event.data.chunks);
    seek(event.data.currentTime);
  });
  global.addEventListener('resize', fitPreviewViewport);

  global.AI8WaapiTimeline = Object.freeze({ mount, seek, updateChunks, fitPreviewViewport });
})(window);
