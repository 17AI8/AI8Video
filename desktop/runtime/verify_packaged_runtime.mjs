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

function isWithin(root, target) {
  return target === root || target.startsWith(`${root}${path.sep}`);
}

function inspectRuntimeSymlinks(runtimeRoot) {
  const absoluteRoot = path.resolve(runtimeRoot);
  const canonicalRoot = fs.realpathSync(runtimeRoot);
  const broken = [];
  const unsafe = [];

  function visit(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        visit(target);
        continue;
      }
      if (!entry.isSymbolicLink()) continue;

      const relativeTarget = path.relative(runtimeRoot, target);
      const linkValue = fs.readlinkSync(target);
      const resolvedTarget = path.resolve(path.dirname(target), linkValue);
      let unsafeLink = path.isAbsolute(linkValue) || !isWithin(absoluteRoot, resolvedTarget);
      try {
        unsafeLink ||= !isWithin(canonicalRoot, fs.realpathSync(target));
      } catch {
        broken.push(relativeTarget);
      }
      if (unsafeLink) unsafe.push(relativeTarget);
    }
  }

  visit(runtimeRoot);
  return { broken, unsafe };
}

const runtimeRoot = candidateRuntimeRoots().find((candidate) => (
  requiredFiles(candidate).every((target) => fs.existsSync(target))
));
if (!runtimeRoot) {
  throw new Error(`安装包缺少完整运行时：${distRoot}`);
}

const executableShimRoot = path.join(runtimeRoot, 'node_modules', '.bin');
if (fs.existsSync(executableShimRoot)) {
  throw new Error(`安装包不应包含 npm .bin 命令垫片：${executableShimRoot}`);
}

const symlinks = inspectRuntimeSymlinks(runtimeRoot);
if (symlinks.broken.length > 0) {
  throw new Error(`安装包包含失效符号链接：${symlinks.broken.join(', ')}`);
}
if (symlinks.unsafe.length > 0) {
  throw new Error(`安装包包含指向运行时外部的符号链接：${symlinks.unsafe.join(', ')}`);
}

process.stdout.write(`安装包运行时检查通过：${runtimeRoot}\n`);
