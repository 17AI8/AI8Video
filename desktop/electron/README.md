# AI8video Electron 客户端

这个目录提供 Windows 与 macOS 的 Electron 客户端，并同时支持开发态与安装态：

- 开发态发现、启动或复用当前源码目录中的 Python Web 服务；
- 安装态启动随 DMG / EXE 分发的冻结 Python 后端；
- 在受限的 Electron 窗口里加载本地工作台；
- 将安装态用户数据保存在 Electron 独立数据目录中；
- 用运行实例标识隔离安装态与 Dev 网页，避免误连同机的另一个服务。

短视频生成、热点聚合和本地资产管理仍由 Python 服务负责。

## 开发运行

```bash
cd desktop/electron
npm ci
npm run dev
```

默认扫描 `127.0.0.1:18720-18820`。如未找到健康服务，会使用项目虚拟环境里的 Python 启动 `ai8video.interfaces.web.app`。

## 打包

不能直接对空运行时执行 `dist:*`。先在仓库根目录冻结 Python 后端并暂存发行依赖：

```bash
python -m pip install ".[ai8video]" "pyinstaller==6.21.0"
npm ci
python -m PyInstaller --noconfirm --clean \
  --distpath build/frozen \
  --workpath build/pyinstaller \
  desktop/runtime/ai8video_backend.spec
python desktop/runtime/stage_release.py \
  --backend-dir build/frozen/ai8video-backend \
  --target desktop/electron/runtime
node desktop/runtime/stage_node_runtime.mjs \
  node_modules desktop/electron/runtime/node_modules
npm ci --prefix desktop/electron
npm --prefix desktop/electron run dist:mac
```

Windows 在 Windows 主机上将最后一条命令改为 `dist:win`。打包产物写入 `desktop/electron/dist/`，暂存运行时位于 `desktop/electron/runtime/`；两者都不应提交到仓库。

GitHub Actions 支持手动构建；`v*` 标签会在构建成功后自动创建或更新 Release。当前安装包未配置 Apple Developer ID 或 Windows Authenticode 证书签名。
