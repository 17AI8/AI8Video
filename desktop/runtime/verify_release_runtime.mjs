import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';


const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const runtimeRoot = path.resolve(scriptDir, '..', 'electron', 'runtime');
const backendName = process.platform === 'win32' ? 'ai8video-backend.exe' : 'ai8video-backend';
const requiredFiles = [
  path.join(runtimeRoot, 'backend', backendName),
  path.join(runtimeRoot, 'node_modules', 'hyperframes', 'dist', 'cli.js'),
  path.join(runtimeRoot, 'licenses', 'FONT_LICENSES.md'),
];

const missing = requiredFiles.filter((target) => !fs.existsSync(target));
if (missing.length) {
  throw new Error(`发布运行时尚未准备完成：\n${missing.join('\n')}`);
}

const selfCheckHome = fs.mkdtempSync(path.join(os.tmpdir(), 'ai8video-runtime-check-'));
const selfCheck = spawnSync(requiredFiles[0], ['--runtime-self-check'], {
  encoding: 'utf8',
  env: {
    ...process.env,
    AI8VIDEO_HOME: selfCheckHome,
    AI8VIDEO_DISABLE_MYKEY: '1',
  },
  timeout: 60_000,
});
fs.rmSync(selfCheckHome, { recursive: true, force: true });
if (selfCheck.error || selfCheck.status !== 0) {
  const detail = selfCheck.error?.message || selfCheck.stderr || `退出码 ${selfCheck.status}`;
  throw new Error(`冻结运行时自检失败：${detail}`);
}

process.stdout.write(`发布运行时检查通过：${runtimeRoot}\n`);
