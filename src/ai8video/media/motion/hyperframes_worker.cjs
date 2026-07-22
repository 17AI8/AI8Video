#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { spawn, execFileSync } = require('child_process');

let active = null;

function emit(event) {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

function textTail(value, limit = 320) {
  const text = String(value || '').trim();
  if (!text) return '';
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).slice(-4).join(' | ').slice(-limit);
}

function errorMessage(error) {
  return textTail(error?.message || error, 320) || error?.name || 'HyperFrames Worker 未知错误';
}

function validateTask(task) {
  const taskId = String(task.taskId || '').trim();
  const rawCompositionDir = String(task.compositionDir || '').trim();
  const rawOutput = String(task.output || '').trim();
  const rawCliPath = String(task.cliPath || '').trim();
  const compositionDir = path.resolve(rawCompositionDir);
  const output = path.resolve(rawOutput);
  const cliPath = path.resolve(rawCliPath);
  if (!taskId || !rawCompositionDir || !fs.existsSync(compositionDir) || !fs.statSync(compositionDir).isDirectory()) {
    throw new Error('compositionDir 不存在或不是目录');
  }
  const relativeOutput = path.relative(compositionDir, output);
  if (!relativeOutput || relativeOutput.startsWith('..') || path.isAbsolute(relativeOutput)) {
    throw new Error('Worker 输出路径必须位于 compositionDir 内');
  }
  if (!rawOutput || !rawCliPath || !fs.existsSync(cliPath)) throw new Error('HyperFrames CLI 或输出路径不存在');
  return {
    ...task,
    taskId,
    compositionDir,
    output,
    cliPath,
    timeoutMs: Math.max(1000, Number(task.timeoutMs) || 300000),
  };
}

function killProcessGroup(child) {
  if (!child || !child.pid) return;
  try {
    if (process.platform !== 'win32') process.kill(-child.pid, 'SIGTERM');
    else execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F']);
  } catch (_) {}
  setTimeout(() => {
    try {
      if (process.platform !== 'win32') process.kill(-child.pid, 'SIGKILL');
      else execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F']);
    } catch (_) {}
  }, 1500).unref();
}

function parseCheckOutput(output) {
  const text = String(output || '');
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try {
    const payload = JSON.parse(text.slice(start, end + 1));
    return payload && typeof payload === 'object' ? payload : null;
  } catch (_) {
    return null;
  }
}

function checkFindings(payload) {
  const warnings = [];
  const fatal = [];
  for (const section of ['lint', 'runtime', 'layout', 'motion', 'contrast']) {
    const findings = payload?.[section]?.findings;
    if (!Array.isArray(findings)) continue;
    for (const finding of findings) {
      if (!finding || String(finding.severity || '').toLowerCase() !== 'error') continue;
      const message = String(finding.message || finding.detail || finding.rule || finding.code || '').trim();
      if (!message) continue;
      if (['layout', 'motion'].includes(section)) warnings.push(`${section}: ${message}`);
      else fatal.push(`${section}: ${message}`);
    }
  }
  return { warnings, fatal };
}

function runCommand(task, phase, args, deadline) {
  return new Promise((resolve, reject) => {
    const nodeBin = process.env.AI8VIDEO_NODE_BIN || process.execPath;
    const child = spawn(nodeBin, [task.cliPath, ...args], {
      cwd: task.compositionDir,
      detached: process.platform !== 'win32',
      stdio: ['ignore', 'pipe', 'pipe'],
      env: process.env,
    });
    active.child = child;
    let stdout = '';
    let stderr = '';
    let settled = false;
    let abortError = null;
    let abortTimer = null;
    const remaining = Math.max(1000, deadline - Date.now());
    const finishReject = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (abortTimer) clearTimeout(abortTimer);
      reject(Object.assign(abortError || new Error(`${phase} 失败`), { stdout, stderr }));
    };
    const requestAbort = (error) => {
      if (settled || abortError) return;
      abortError = error;
      killProcessGroup(child);
      // 给 Chrome/Puppeteer 一个短暂的 close 窗口，避免 Node worker 退出后遗留子进程。
      abortTimer = setTimeout(finishReject, 1800);
    };
    const timer = setTimeout(() => {
      requestAbort(Object.assign(new Error(`${phase} 超时`), { code: 'TIMEOUT' }));
    }, remaining);
    const push = (stream, chunk) => {
      const value = String(chunk || '');
      if (stream === 'stdout') stdout += value;
      else stderr += value;
      emit({
        taskId: task.taskId,
        phase,
        status: 'running',
        message: textTail(value) || `${phase} 进行中`,
      });
    };
    child.stdout.on('data', (chunk) => push('stdout', chunk));
    child.stderr.on('data', (chunk) => push('stderr', chunk));
    child.once('error', (error) => {
      if (settled) return;
      if (abortError) return finishReject();
      settled = true;
      clearTimeout(timer);
      reject(Object.assign(error, { stdout, stderr }));
    });
    child.once('close', (code, signal) => {
      if (settled) return;
      if (abortError) return finishReject();
      settled = true;
      clearTimeout(timer);
      resolve({ code, signal, stdout, stderr });
    });
  });
}

async function runRender(rawTask) {
  const task = validateTask(rawTask);
  const deadline = Date.now() + task.timeoutMs;
  emit({ taskId: task.taskId, phase: 'preparing', status: 'running', message: '准备 HyperFrames composition' });
  const check = await runCommand(task, 'checking', [
    'check', task.compositionDir, '--json', '--no-contrast', '--at-transitions',
  ], deadline);
  if (active.cancelled) throw Object.assign(new Error('Worker 已取消'), { code: 'CANCELLED' });
  const checkPayload = parseCheckOutput(`${check.stdout}\n${check.stderr}`);
  const findingState = checkFindings(checkPayload);
  if (findingState.fatal.length || (check.code !== 0 && !checkPayload)) {
    throw Object.assign(new Error(`HyperFrames 预检失败：${textTail(findingState.fatal.join(' | ') || check.stderr || check.stdout)}`), {
      code: 'CHECK_FAILED',
      checkWarnings: findingState.warnings,
      checkFindings: findingState.fatal,
    });
  }
  if (check.code !== 0 || findingState.warnings.length) {
    emit({
      taskId: task.taskId,
      phase: 'checking',
      status: 'warning',
      message: findingState.warnings.join(' | ') || 'HyperFrames motion/layout 存在可修复告警',
      warnings: findingState.warnings,
    });
  }
  if (active.cancelled) throw Object.assign(new Error('Worker 已取消'), { code: 'CANCELLED' });
  emit({ taskId: task.taskId, phase: 'rendering', status: 'running', message: '启动 HyperFrames render' });
  const render = await runCommand(task, 'rendering', [
    'render', task.compositionDir, '--composition', 'index.html', '--format', 'webm',
    '--output', task.output, '--workers', '1', '--browser-timeout', '45', '--quiet',
  ], deadline);
  if (render.code !== 0 || !fs.existsSync(task.output) || fs.statSync(task.output).size <= 0) {
    throw Object.assign(new Error(`HyperFrames render 失败：${textTail(render.stderr || render.stdout)}`), {
      code: 'RENDER_FAILED',
      stdout: render.stdout,
      stderr: render.stderr,
    });
  }
  emit({
    taskId: task.taskId,
    phase: 'rendering',
    status: 'succeeded',
    message: 'HyperFrames 输出已生成',
    output: task.output,
    warnings: findingState.warnings,
  });
}

function cancelActive() {
  if (!active) return;
  active.cancelled = true;
  killProcessGroup(active.child);
}

let exitTimer = null;
function exitWhenIdle(code = 0) {
  if (exitTimer) return;
  const deadline = Date.now() + 2500;
  const poll = () => {
    if (!active || Date.now() >= deadline) {
      process.exit(code);
      return;
    }
    exitTimer = setTimeout(poll, 50);
    exitTimer.unref();
  };
  poll();
}

async function handleMessage(message) {
  if (!message || typeof message !== 'object') return;
  if (message.type === 'cancel') {
    cancelActive();
    return;
  }
  if (message.type === 'shutdown') {
    cancelActive();
    exitWhenIdle(0);
    return;
  }
  if (message.type !== 'render') return;
  if (active) {
    emit({ taskId: String(message.taskId || ''), phase: 'worker', status: 'failed', message: 'Worker 正在处理另一个任务', code: 'BUSY' });
    return;
  }
  const taskId = String(message.taskId || '').trim();
  active = { taskId, child: null, cancelled: false };
  try {
    await runRender(message);
    if (active.cancelled) throw Object.assign(new Error('Worker 已取消'), { code: 'CANCELLED' });
    emit({ taskId, phase: 'completed', status: 'succeeded', message: 'HyperFrames Worker 完成' });
  } catch (error) {
    const code = active.cancelled ? 'CANCELLED' : (error.code || 'WORKER_FAILED');
    emit({
      taskId,
      phase: code === 'CANCELLED' ? 'cancelled' : 'failed',
      status: code === 'CANCELLED' ? 'cancelled' : (code === 'TIMEOUT' ? 'timeout' : 'failed'),
      message: errorMessage(error),
      code,
      checkWarnings: error.checkWarnings || [],
      checkFindings: error.checkFindings || [],
    });
  } finally {
    active = null;
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
function handleSignal() {
  cancelActive();
  exitWhenIdle(143);
}
process.on('SIGTERM', handleSignal);
process.on('SIGINT', handleSignal);
input.on('line', (line) => {
  try {
    const message = JSON.parse(line);
    void handleMessage(message);
  } catch (error) {
    emit({ taskId: '', phase: 'worker', status: 'failed', message: errorMessage(error), code: 'INVALID_JSON' });
  }
});
input.on('close', () => {
  if (active) {
    cancelActive();
    exitWhenIdle(143);
  } else {
    process.exit(0);
  }
});
