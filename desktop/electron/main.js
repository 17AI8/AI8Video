'use strict';

const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Menu,
  nativeImage,
  shell,
} = require('electron');

const BRAND_NAME = 'AI8video';
const SETTINGS_BASENAME = '.ai8video-electron-settings.json';
// 旧名称只存在于设置与环境变量兼容边界；保存时始终使用新名称。
const LEGACY_SETTINGS_BASENAME = '.ai8minivideo-electron-settings.json';
const LEGACY_ENV_PREFIX = 'AI8MINIVIDEO_';
const DEFAULT_PORT_START = 18720;
const DEFAULT_PORT_END = 18820;
const HEALTH_PATH = '/api/health';
const APP_ICON_PATH = path.join(__dirname, 'icons', 'icon.png');

let mainWindow = null;
let managedBackendChild = null;
let managedBackendPort = 0;
let bootInFlight = null;

function resolveAppIcon() {
  if (!fs.existsSync(APP_ICON_PATH)) return null;
  const icon = nativeImage.createFromPath(APP_ICON_PATH);
  return icon.isEmpty() ? null : icon;
}

function settingsPath() {
  return path.join(app.getPath('home'), SETTINGS_BASENAME);
}

function legacySettingsPath() {
  return path.join(app.getPath('home'), LEGACY_SETTINGS_BASENAME);
}

function normalizeSettings(raw = {}) {
  const value = raw && typeof raw === 'object' ? raw : {};
  return {
    pythonPath: String(value.pythonPath || value.python_path || '').trim(),
    projectDir: String(value.projectDir || value.project_dir || '').trim(),
    lastPort: Number.parseInt(String(value.lastPort || value.last_port || ''), 10) || 0,
    lastUrl: String(value.lastUrl || value.last_url || '').trim(),
  };
}

function loadSettings() {
  const current = readSettingsFile(settingsPath());
  if (current) return current;
  const legacy = readSettingsFile(legacySettingsPath());
  if (!legacy) return normalizeSettings();
  try {
    saveSettings(legacy);
  } catch {
    // 迁移写入失败不影响本次继续使用旧设置。
  }
  return legacy;
}

function saveSettings(rawSettings) {
  const settings = normalizeSettings(rawSettings);
  fs.writeFileSync(settingsPath(), JSON.stringify(settings, null, 2), 'utf8');
  return settings;
}

function readSettingsFile(filePath) {
  try {
    return normalizeSettings(JSON.parse(fs.readFileSync(filePath, 'utf8')));
  } catch {
    return null;
  }
}

function productEnvironmentValue(name) {
  const current = String(process.env[name] || '').trim();
  if (current) return current;
  const legacyName = name.startsWith('AI8VIDEO_')
    ? `${LEGACY_ENV_PREFIX}${name.slice('AI8VIDEO_'.length)}`
    : name;
  return String(process.env[legacyName] || '').trim();
}

function candidateProjectDirs(explicit = '') {
  return [
    explicit,
    productEnvironmentValue('AI8VIDEO_ROOT'),
    path.resolve(__dirname, '..', '..'),
    process.cwd(),
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .map((item) => path.resolve(item));
}

function resolveProjectDir(explicit = '') {
  return candidateProjectDirs(explicit).find((directory) => (
    fs.existsSync(path.join(directory, 'src', 'ai8video', 'interfaces', 'web', 'app.py'))
  )) || '';
}

function resolvePythonPath(projectDir, explicit = '') {
  const virtualEnvPython = projectDir
    ? path.join(projectDir, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    : '';
  const candidates = [
    explicit,
    productEnvironmentValue('AI8VIDEO_PYTHON'),
    virtualEnvPython,
    process.platform === 'win32' ? 'python' : 'python3',
    'python',
  ];
  return candidates.find((candidate) => {
    const value = String(candidate || '').trim();
    if (!value) return false;
    return !(path.isAbsolute(value) || value.includes(path.sep)) || fs.existsSync(value);
  }) || '';
}

function findCertifiBundle(projectDir) {
  const libDir = path.join(projectDir, '.venv', 'lib');
  try {
    const versions = fs.readdirSync(libDir).filter((name) => /^python\d+\.\d+$/.test(name)).sort().reverse();
    for (const version of versions) {
      const candidate = path.join(libDir, version, 'site-packages', 'certifi', 'cacert.pem');
      if (fs.existsSync(candidate)) return candidate;
    }
  } catch {
    return '';
  }
  return '';
}

function backendEnvironment(projectDir) {
  const env = { ...process.env, PYTHONUNBUFFERED: '1' };
  const sourceRoot = path.join(projectDir, 'src');
  env.PYTHONPATH = [sourceRoot, env.PYTHONPATH].filter(Boolean).join(path.delimiter);
  const certifiBundle = findCertifiBundle(projectDir);
  if (certifiBundle) {
    env.SSL_CERT_FILE = certifiBundle;
    env.REQUESTS_CA_BUNDLE = certifiBundle;
  }
  return env;
}

function localWorkbenchUrl(port) {
  return `http://127.0.0.1:${port}/`;
}

function checkHealth(port) {
  return new Promise((resolve) => {
    const request = http.get(`${localWorkbenchUrl(port).replace(/\/$/, '')}${HEALTH_PATH}`, (response) => {
      response.resume();
      resolve((response.statusCode || 0) >= 200 && (response.statusCode || 0) < 300);
    });
    request.setTimeout(1200, () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

function canBindPort(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once('error', () => resolve(false));
    server.listen(port, '127.0.0.1', () => server.close(() => resolve(true)));
  });
}

async function findHealthyBackend(preferredPort = 0) {
  const ports = preferredPort > 0 ? [preferredPort] : [];
  for (let port = DEFAULT_PORT_START; port <= DEFAULT_PORT_END; port += 1) {
    if (!ports.includes(port)) ports.push(port);
  }
  for (const port of ports) {
    if (await checkHealth(port)) return { port, url: localWorkbenchUrl(port) };
  }
  return null;
}

async function findAvailablePort() {
  for (let port = DEFAULT_PORT_START; port <= DEFAULT_PORT_END; port += 1) {
    if (await canBindPort(port)) return port;
  }
  throw new Error(`没有找到可用端口，请检查 ${DEFAULT_PORT_START}-${DEFAULT_PORT_END}。`);
}

async function waitForBackend(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await checkHealth(port)) return true;
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  return false;
}

function stopManagedBackend() {
  if (!managedBackendChild) return;
  try {
    managedBackendChild.kill();
  } catch {
    // 退出过程中后端可能已经自行结束。
  }
  managedBackendChild = null;
  managedBackendPort = 0;
}

function spawnBackend(projectDir, pythonPath, port) {
  const child = spawn(pythonPath, ['-m', 'ai8video.interfaces.web.app', '--port', String(port)], {
    cwd: projectDir,
    env: backendEnvironment(projectDir),
    stdio: 'ignore',
    detached: false,
  });
  child.once('exit', () => {
    if (managedBackendChild === child) {
      managedBackendChild = null;
      managedBackendPort = 0;
    }
  });
  managedBackendChild = child;
  managedBackendPort = port;
}

async function ensureBackend(rawSettings = {}) {
  const settings = normalizeSettings({ ...loadSettings(), ...rawSettings });
  const healthy = await findHealthyBackend(settings.lastPort);
  if (healthy) return persistBackendSettings(settings, healthy, true);
  const projectDir = resolveProjectDir(settings.projectDir);
  if (!projectDir) throw new Error('没有找到 AI8video 项目目录，请先选择项目目录。');
  const pythonPath = resolvePythonPath(projectDir, settings.pythonPath);
  if (!pythonPath) throw new Error('没有找到可用的 Python 解释器，请先选择 Python。');
  const port = await findAvailablePort();
  spawnBackend(projectDir, pythonPath, port);
  if (!await waitForBackend(port)) {
    stopManagedBackend();
    throw new Error(`本地服务在 30 秒内没有准备好，目标端口 ${port}。`);
  }
  return persistBackendSettings({ ...settings, projectDir, pythonPath }, { port, url: localWorkbenchUrl(port) }, false);
}

function persistBackendSettings(settings, backend, reused) {
  const saved = saveSettings({ ...settings, lastPort: backend.port, lastUrl: backend.url });
  return { reused, settings: saved, port: backend.port, url: backend.url };
}

function isTrustedWindowUrl(targetUrl = '') {
  try {
    const parsed = new URL(String(targetUrl || '').trim());
    if (parsed.protocol === 'http:' && ['127.0.0.1', 'localhost', '::1'].includes(parsed.hostname)) return true;
    if (parsed.protocol !== 'file:') return false;
    const staticRoot = pathToFileURL(`${path.join(__dirname, 'static')}${path.sep}`).href;
    return parsed.href.startsWith(staticRoot);
  } catch {
    return false;
  }
}

function routeExternalUrl(targetUrl = '') {
  try {
    const parsed = new URL(String(targetUrl || '').trim());
    if (['http:', 'https:'].includes(parsed.protocol)) shell.openExternal(parsed.href).catch(() => {});
  } catch {
    // 忽略无效外链。
  }
}

function createMainWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) return mainWindow;
  const icon = resolveAppIcon();
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    show: false,
    title: BRAND_NAME,
    backgroundColor: '#f5f7fb',
    autoHideMenuBar: true,
    ...(icon ? { icon } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  bindWindowNavigation(mainWindow);
  mainWindow.once('ready-to-show', () => mainWindow?.show());
  mainWindow.on('closed', () => { mainWindow = null; });
  return mainWindow;
}

function bindWindowNavigation(window) {
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isTrustedWindowUrl(url)) window.loadURL(url).catch(() => {});
    else routeExternalUrl(url);
    return { action: 'deny' };
  });
  const guard = (event, url) => {
    if (isTrustedWindowUrl(url)) return;
    event.preventDefault();
    routeExternalUrl(url);
  };
  window.webContents.on('will-navigate', guard);
  window.webContents.on('will-redirect', guard);
}

async function showLocalPage(fileName, query = {}) {
  if (!mainWindow) return;
  await mainWindow.loadFile(path.join(__dirname, 'static', fileName), { query });
}

async function bootClient(rawSettings = {}) {
  if (bootInFlight) return bootInFlight;
  bootInFlight = (async () => {
    await showLocalPage('loading.html', { message: '正在启动 AI8video 本地工作台...' });
    try {
      const result = await ensureBackend(rawSettings);
      await mainWindow?.loadURL(result.url);
      if (process.env.ELECTRON_DEVTOOLS === '1') mainWindow?.webContents.openDevTools({ mode: 'detach' });
      return { ok: true, ...result };
    } catch (error) {
      const message = error?.message || String(error);
      await showLocalPage('setup.html', { error: message });
      return { ok: false, error: message };
    } finally {
      bootInFlight = null;
    }
  })();
  return bootInFlight;
}

function buildMenu() {
  const template = [{
    label: BRAND_NAME,
    submenu: [
      { label: '重新连接工作台', click: () => bootClient(loadSettings()).catch(() => {}) },
      { label: '重新加载页面', accelerator: 'CmdOrCtrl+R', click: () => mainWindow?.webContents.reload() },
      { type: 'separator' },
      { role: 'quit', label: `退出 ${BRAND_NAME}` },
    ],
  }];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

ipcMain.handle('desktop:get-config', () => {
  const settings = loadSettings();
  const projectDir = resolveProjectDir(settings.projectDir);
  return {
    ...settings,
    detectedProjectDir: projectDir,
    detectedPythonPath: resolvePythonPath(projectDir, settings.pythonPath),
    managedBackendPort,
  };
});

ipcMain.handle('desktop:pick-python', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openFile'] });
  return result.canceled ? '' : (result.filePaths[0] || '');
});

ipcMain.handle('desktop:pick-project-dir', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openDirectory'] });
  return result.canceled ? '' : (result.filePaths[0] || '');
});

ipcMain.handle('desktop:start-backend', async (_event, settings) => {
  const result = await ensureBackend(settings || {});
  await mainWindow?.loadURL(result.url);
  return { ok: true, ...result };
});

ipcMain.handle('desktop:open-external', (_event, targetUrl) => {
  routeExternalUrl(targetUrl);
  return { ok: true };
});

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) app.quit();

app.on('second-instance', () => {
  createMainWindow();
  if (!mainWindow?.isVisible()) mainWindow?.show();
  mainWindow?.focus();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', stopManagedBackend);

app.on('activate', () => {
  createMainWindow();
  if (!mainWindow?.isVisible()) mainWindow?.show();
  mainWindow?.focus();
});

app.whenReady().then(async () => {
  const icon = resolveAppIcon();
  if (process.platform === 'darwin' && app.dock && icon) app.dock.setIcon(icon);
  createMainWindow();
  buildMenu();
  await bootClient(loadSettings());
});
