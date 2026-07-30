import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';


const [sourceArg, targetArg] = process.argv.slice(2);
if (!sourceArg || !targetArg) {
  throw new Error('用法：node stage_node_runtime.mjs <node_modules> <target>');
}

const sourceRoot = path.resolve(sourceArg);
const targetRoot = path.resolve(targetArg);
const hyperframesCli = path.join(sourceRoot, 'hyperframes', 'dist', 'cli.js');

async function pathExists(target) {
  try {
    await fs.access(target);
    return true;
  } catch {
    return false;
  }
}

async function copyProductionModules() {
  if (!await pathExists(hyperframesCli)) {
    throw new Error(`HyperFrames CLI 不存在：${hyperframesCli}`);
  }
  await fs.rm(targetRoot, { recursive: true, force: true });
  await fs.cp(sourceRoot, targetRoot, {
    recursive: true,
    filter(source) {
      const name = path.basename(source);
      return name !== '.DS_Store' && name !== '.cache';
    },
  });
}

async function pruneOnnxPlatforms() {
  const binRoot = path.join(targetRoot, 'onnxruntime-node', 'bin');
  if (!await pathExists(binRoot)) return;
  for (const napiEntry of await fs.readdir(binRoot, { withFileTypes: true })) {
    if (!napiEntry.isDirectory() || !/^napi-v\d+$/.test(napiEntry.name)) continue;
    const binariesRoot = path.join(binRoot, napiEntry.name);
    for (const platform of await fs.readdir(binariesRoot)) {
      const platformRoot = path.join(binariesRoot, platform);
      if (platform !== process.platform) {
        await fs.rm(platformRoot, { recursive: true, force: true });
        continue;
      }
      for (const architecture of await fs.readdir(platformRoot)) {
        if (architecture !== process.arch) {
          await fs.rm(path.join(platformRoot, architecture), { recursive: true, force: true });
        }
      }
    }
  }
}

async function directoryStats(root) {
  let bytes = 0;
  let files = 0;
  for (const entry of await fs.readdir(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) {
      const nested = await directoryStats(target);
      bytes += nested.bytes;
      files += nested.files;
    } else if (entry.isFile()) {
      bytes += (await fs.stat(target)).size;
      files += 1;
    }
  }
  return { bytes, files };
}

await copyProductionModules();
await pruneOnnxPlatforms();
const stats = await directoryStats(targetRoot);
process.stdout.write(JSON.stringify({
  target: targetRoot,
  platform: process.platform,
  arch: process.arch,
  files: stats.files,
  mebibytes: Number((stats.bytes / 1024 / 1024).toFixed(2)),
}) + '\n');
