import fs from 'node:fs';
import process from 'node:process';


const PLATFORM_CONFIG = {
  mac: {
    label: 'macOS',
    signing: ['CSC_LINK', 'CSC_KEY_PASSWORD'],
    notarization: ['APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID'],
  },
  win: {
    label: 'Windows',
    signing: ['WIN_CSC_LINK', 'WIN_CSC_KEY_PASSWORD'],
    notarization: [],
  },
};

function hasValue(name) {
  return typeof process.env[name] === 'string' && process.env[name].trim().length > 0;
}

function inspectGroup(names) {
  const configured = names.filter(hasValue);
  const missing = names.filter((name) => !hasValue(name));
  return {
    configured,
    missing,
    complete: names.length > 0 && missing.length === 0,
    absent: configured.length === 0,
  };
}

function appendFileFromEnvironment(name, lines) {
  const target = process.env[name];
  if (!target) return;
  fs.appendFileSync(target, `${lines.join('\n')}\n`, 'utf8');
}

function writeOutputs(result) {
  appendFileFromEnvironment('GITHUB_OUTPUT', [
    `signing=${String(result.signing)}`,
    `notarization=${String(result.notarization)}`,
    `status=${result.status}`,
    `csc_identity_auto_discovery=${String(result.signing)}`,
  ]);
}

function writeSummary(config, result) {
  const signing = result.signing ? '已启用' : '未配置';
  const notarization = result.notarization ? '已启用' : '未配置';
  appendFileFromEnvironment('GITHUB_STEP_SUMMARY', [
    '## 桌面发行签名状态',
    '',
    `- 平台：${config.label}`,
    `- 制品标记：\`${result.status}\``,
    `- 代码签名：${signing}`,
    `- Apple 公证：${config.notarization.length > 0 ? notarization : '不适用'}`,
  ]);
}

function fail(platform, group, details) {
  process.stderr.write(`${JSON.stringify({
    platform,
    error: 'incomplete-signing-config',
    group,
    configured: details.configured,
    missing: details.missing,
  })}\n`);
  process.exitCode = 1;
}

function resolveResult(platform, config) {
  const signing = inspectGroup(config.signing);
  if (!signing.absent && !signing.complete) {
    fail(platform, 'signing', signing);
    return null;
  }

  const notarization = inspectGroup(config.notarization);
  if (!notarization.absent && !notarization.complete) {
    fail(platform, 'notarization', notarization);
    return null;
  }
  if (notarization.complete && !signing.complete) {
    fail(platform, 'signing-required-for-notarization', signing);
    return null;
  }

  const status = platform === 'mac' && notarization.complete
    ? 'signed-notarized'
    : signing.complete ? 'signed' : 'unsigned';
  return {
    platform,
    status,
    signing: signing.complete,
    notarization: notarization.complete,
  };
}

const platform = process.argv[2];
const config = PLATFORM_CONFIG[platform];
if (!config) {
  process.stderr.write(`仅支持签名配置检查：${Object.keys(PLATFORM_CONFIG).join(', ')}\n`);
  process.exitCode = 2;
} else {
  const result = resolveResult(platform, config);
  if (result) {
    writeOutputs(result);
    writeSummary(config, result);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  }
}
