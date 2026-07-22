# AI8video Electron 桌面壳

这个目录提供 Windows 与 macOS 的可选 Electron 客户端。它只负责：

- 发现、启动或复用本机 Python Web 服务；
- 在受限的 Electron 窗口里加载本地工作台；
- 保存项目目录、Python 路径与最近端口。

短视频生成、热点聚合和本地资产管理仍由 Python 服务负责。

## 开发运行

```bash
cd desktop/electron
npm install
npm run dev
```

默认扫描 `127.0.0.1:18720-18820`。如未找到健康服务，会使用项目虚拟环境里的 Python 启动 `ai8video.interfaces.web.app`。

## 打包

```bash
npm run dist:mac
npm run dist:win
```

打包产物写入 `desktop/electron/dist/`，不应提交到仓库。
