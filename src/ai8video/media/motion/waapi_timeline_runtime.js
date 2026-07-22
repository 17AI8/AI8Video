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
    if (item.kind === 'entrance') initializeEntrance(node, from);
    const staggerSeconds = number(item.to && item.to.stagger, 0);
    const animation = node.animate([from, to], {
      duration: Math.max(1, number(item.duration, 0.001) * 1000),
      delay: Math.max(0, (number(item.at, 0) + staggerSeconds * index) * 1000),
      easing: easing(item.to && item.to.ease),
      fill: 'forwards',
      iterations: 1,
    });
    animation.pause();
    animation.currentTime = 0;
    return animation;
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
      nodes.forEach((node, index) => animations.push(createAnimation(node, item, index)));
    }
    global.__ai8MotionPlan = plan;
    global.__ai8MotionAnimations = animations;
    return animations;
  }

  global.AI8WaapiTimeline = Object.freeze({ mount });
})(window);
