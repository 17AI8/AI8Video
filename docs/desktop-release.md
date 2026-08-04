# Electron 构建与桌面发行

AI8video 保留互不干扰的 Dev 网页和 Electron 安装包两条通道。

| 通道 | 面向对象 | 运行方式 | 更新方式 |
|---|---|---|---|
| Dev 网页 | 开发者 | 当前源码 + 项目 `.venv` 启动本地 Web | 拉取代码后重启 Dev |
| Electron 安装包 | 普通用户 | 安装包内置冻结 Python 后端与 Node 运行时 | 下载新 DMG/EXE 覆盖安装 |

两条通道可在同一台机器共存。Electron 使用独立实例标识和用户数据目录，不应误连正在开发的 Dev 服务；安装包也不会冻结或改写源码目录。

## Electron 开发

先确保 Python 后端可运行，再启动桌面壳：

```bash
cd desktop/electron
npm ci
npm run dev
```

Electron 负责窗口、本地后端发现/拉起和安装态隔离，不实现短视频业务逻辑。

## 本地构建

在仓库根目录准备 Python、Pi/HyperFrames 和冻结后端：

```bash
python -m pip install ".[ai8video]" "pyinstaller==6.21.0"
npm ci

python -m PyInstaller --noconfirm --clean --distpath build/frozen --workpath build/pyinstaller desktop/runtime/ai8video_backend.spec

python desktop/runtime/stage_release.py --backend-dir build/frozen/ai8video-backend --target desktop/electron/runtime

node desktop/runtime/stage_node_runtime.mjs node_modules desktop/electron/runtime/node_modules
```

构建 macOS DMG：

```bash
cd desktop/electron
npm ci
npm run dist:mac
```

Windows 在对应系统使用：

```bash
cd desktop/electron
npm ci
npm run dist:win
```

打包命令会先运行发行运行时校验，再由 electron-builder 生成制品，最后检查安装包中的冻结后端和 Node 运行时。

发行暂存会移除 Node 依赖中的 source map、TypeScript 声明和 npm `.bin` 命令垫片，避免绝对符号链接进入应用。Electron 只保留简体中文和英文语言包。

## 安装包内容与外部依赖

安装包包含：

- Electron 窗口与 Web 静态资源；
- 冻结的 Python 后端；
- Pi Agent / HyperFrames 所需 Node 运行时；
- 项目授权范围内的中文字体。

安装包不包含项目源码、本地 API Key、`.env`、`mykey.py` 或用户素材。FFmpeg / FFprobe 继续使用用户系统中可用的外部运行时。

## GitHub Actions

`.github/workflows/desktop-release.yml` 支持两种触发方式：

| 触发 | 行为 |
|---|---|
| 推送 `v*` 注解标签 | 构建 macOS ARM64 DMG 与 Windows x64 EXE，并创建或更新对应 Release |
| 手动 `workflow_dispatch` | 构建临时制品；填写现有 `release_tag` 时覆盖该 Release 的附件和说明 |

普通分支推送不会触发大型桌面打包任务。

手动运行未填写 `release_tag` 时，只保留 14 天 Actions 制品。填写标签后，工作流会：

1. 验证标签以 `v` 开头且真实存在；
2. 使用触发提交的完整提交正文生成 Release 说明；
3. 创建或更新目标 Release；
4. 使用 `--clobber` 覆盖同名附件。

通过标签触发且 Release 不存在时，Release 说明来自注解标签正文。因此正式版本必须使用注解标签，并保持模块化说明。

正式安装包从 [GitHub Releases](https://github.com/17AI8/AI8Video/releases) 下载。

## 签名与公证

Secrets 必须按能力成组配置。只配置一部分会中止构建，避免把不完整状态误报为已签名。

| 平台能力 | GitHub Secrets |
|---|---|
| macOS Developer ID 签名 | `MAC_CSC_LINK`、`MAC_CSC_KEY_PASSWORD` |
| Apple 公证 | `APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD`、`APPLE_TEAM_ID` |
| Windows 代码签名 | `WIN_CSC_LINK`、`WIN_CSC_KEY_PASSWORD` |

状态含义：

- macOS 已配置完整证书组：Developer ID 签名，并在配置完整时提交公证、验证装订票据。
- macOS 未配置证书组：生成结构完整的 ad-hoc 签名包，但仍标记为 `unsigned`，不代表 Gatekeeper 信任。
- Windows 已配置证书组：验证安装器、Electron 主程序和冻结后端签名。
- Windows 未配置证书组：三者都必须保持 `NotSigned`，并明确标记 `unsigned`。

检查脚本只输出变量名与配置状态，不输出证书、密码或账号值。

## 发布前最小检查

- `desktop/runtime/verify_release_runtime.mjs` 通过；
- 安装包内冻结后端、Node 运行时和静态资源完整；
- macOS `codesign --verify --deep --strict` 通过；
- 配置公证时 `stapler validate` 与 Gatekeeper 检查通过；
- Windows 签名状态与配置预期一致；
- 提交正文和注解标签正文包含功能、UI/文档、验证与已知边界。

## 当前部署边界

- 桌面后端和 Dev Web 都只监听 `127.0.0.1`。
- Electron 是本地桌面壳，不是公网多用户服务。
- 项目未提供官方 Docker、Compose、Kubernetes 或生产服务器部署。
- 未完成 Developer ID 签名和 Apple 公证的 macOS 制品不能表述为正式受信安装包。
- 本地密钥、用户素材和生成结果不得进入安装包或 Actions Artifact。

架构边界见[架构说明](../ARCHITECTURE.md)，模型与运行时依赖见[配置说明](configuration.md)。
