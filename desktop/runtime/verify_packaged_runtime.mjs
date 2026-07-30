import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';


const distRoot = path.resolve(process.argv[2] || path.join('desktop', 'electron', 'dist'));
const backendName = process.platform === 'win32' ? 'ai8video-backend.exe' : 'ai8video-backend';

function candidateRuntimeRoots() {
  if (process.platform === 'darwin') {
    return ['mac-arm64', 'mac-x64', 'mac'].map((directory) => (
      path.join(distRoot, directory, 'AI8video.app', 'Contents', 'Resources', 'runtime')
    ));
  }
  if (process.platform === 'win32') {
    return [path.join(distRoot, 'win-unpacked', 'resources', 'runtime')];
  }
  return [];
}

function requiredFiles(runtimeRoot) {
  return [
    path.join(runtimeRoot, 'backend', backendName),
    path.join(runtimeRoot, 'node_modules', 'hyperframes', 'dist', 'cli.js'),
    path.join(runtimeRoot, 'licenses', 'FONT_LICENSES.md'),
  ];
}

const runtimeRoot = candidateRuntimeRoots().find((candidate) => (
  requiredFiles(candidate).every((target) => fs.existsSync(target))
));
if (!runtimeRoot) {
  throw new Error(`安装包缺少完整运行时：${distRoot}`);
}

process.stdout.write(`安装包运行时检查通过：${runtimeRoot}\n`);
