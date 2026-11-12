# NovelRemoteHub · 智笔云枢

通过浏览器或 Android App 管理本地多部小说、阅读正文与设定、远程调度 Antigravity、整理番茄发布素材。

线上入口为 https://scientificexploration.art/novel/ 。Android 安装包可在页面右上角下载。

## 已实现功能

- 多书架与章节搜索、排序、阅读主题、字号调节、前后章切换。
- 浏览设定集，新建作品架构，新增或演进设定，推演大纲与检查设定一致性。
- 本地守护进程轮询任务，远程唤醒 Antigravity，通过已安装的 agentapi 调用用户选择的模型。
- 续写、现有章节精修、平台驳回修复。覆盖原稿的修复会先在本地保存备份。
- 番茄格式清洗、错字与关键词规则预检、问题筛选、正文复制和整书 ZIP 导出。
- 1–4 张二维水墨场景插画。实际调用 Antigravity 图像工具，校验图片、章节依据及云端上传回执后更新画廊。
- 独立生成作者有话说与章节互动问题，可分别编辑、复制、保存，并随章节素材导出。
- Android WebView 外壳、在线版本检查、原图与 ZIP 下载转系统浏览器。

章节评论功能生成供作者使用的互动文案，发布由作者在番茄作家后台完成。规则预检提供本地参考，平台最终审核结果需在平台查看。创作模型名称来自客户端所选配置，实际可用性取决于 Antigravity 账号和客户端。AI 创作需要作者审阅人物、剧情和设定一致性。

## 架构

云端 `cloud_hub.py` 提供 HTTP API、带鉴权的文件读取、任务队列和缓存。本地 `local_bridge.py` 同步 `D:/小说` 的章节与设定，接收任务并运行 Antigravity。`agent_client.py` 自动发现当前语言服务地址和 CSRF，直接调用执行程序，完整传递多行指令。完成回执写入后再同步并确认任务结果。

云端需要 Python 3.10+ 和 Pillow，可选安装 redis Python 包以启用 Redis 缓存。继续使用文件存储及现有 Redis。缓存只操作 `novel:cache:*` 前缀。HTTP 并发限制为 16，写请求串行化，JSON 和文本使用原子替换。任务持久化在 `tasks.json`，领取、进度及终态更新使用同一写锁。

生成图片采用独立任务目录与唯一文件名。元数据最后发布，旧成果仍保留在磁盘，导出只包含当前元数据引用的图片和当前互动文案。作者编辑具有版本冲突检查。

## 使用

1. 在书架选择作品，在章节卷宗点击阅读或番茄。
2. 在操控 AI 页面选择续写、精修、大纲推演或自定义指令，再选择模型并提交。
3. 番茄页面上方选择章节。互动文案可直接输入后保存，也可点击“生成本章互动文案”。
4. 选择 1–4 张后点击“绘制二维水墨插画”。进度在当前页显示，完成后可打开大图或下载本章素材 ZIP。画廊使用轻量 JPEG 预览，下载保留 PNG 原图。
5. 页面会保留当前浏览期间跨章切换的未保存文案。刷新页面前请保存编辑。进行中的素材任务可在刷新后恢复查询。

旧版本文字模板图会标明历史素材，可重新绘制。生成失败或云端版本冲突会显示原因，本地任务目录中的已生成文件可用于恢复上传。

## 本地启动

需要 Windows、Python 3.10+、Pillow，以及已登录且具备所需模型和图像工具的 Antigravity。

在项目目录创建被 Git 忽略的 `.bridge-config.json`，写入私有 `token`。该令牌由服务器登录接口返回。可选 `conversation_id` 用于指定同步到云端的小说历史会话，省略时同步正文和设定。

```powershell
python local_bridge.py
```

`start_daemon.vbs` 提供隐藏后台启动，端口互斥确保单实例。生成任务期间独立线程持续回传心跳。客户端退出后可由桥接器重新启动，重新启动电脑后仍需 Windows 登录并启动守护进程。

## 云端部署

核心文件包含 `cloud_hub.py`、`storage.py`、`novel_manager.py`、`illustration_generator.py`、`agent_client.py`、`index.html` 和 `assets/`。

脚本默认监听 `127.0.0.1:8899`。本机生产 service 通过 `NOVEL_HUB_BIND=172.17.0.1` 适配现有 Docker HTTPS 反向代理，转发 `/novel/`。数据默认保存于 `/root/novel_hub_data`，下载目录为 `/opt/novel_hub/download`。

支持环境变量 `NOVEL_HUB_DATA_DIR`、`NOVEL_HUB_DOWNLOAD_DIR`、`NOVEL_HUB_PORT`、`NOVEL_HUB_BIND` 与 `NOVEL_HUB_PASSWORD`。通过环境变量提供访问口令，省略时在数据目录生成私有 `access_password`。首次启动在数据目录生成 `auth_token`，本地桥接配置需与该服务器令牌对应。重新登录网页获取当前令牌。

发布前备份 `/opt/novel_hub`、数据目录和 service 文件，记录正文及设定摘要。先在隔离端口运行回归，再替换本项目文件并重启 `novel-hub`。验证 HTTPS 页面、授权读取、未授权写入、桥接心跳、下载版本及业务数据摘要。失败时恢复备份文件并重启本项目服务。

## 验证与构建

```powershell
python -m unittest discover -s tests -v
python -m py_compile cloud_hub.py local_bridge.py novel_manager.py illustration_generator.py agent_client.py storage.py
python build_apk.py
```

前端使用随项目提供的 Marked 和 DOMPurify，设定 Markdown 在渲染前清理。真实 Antigravity 绘图需要可用账号及图像工具。Android 构建继续使用原签名文件，版本 1.8.0，versionCode 10。实体手机安装、后台限制和系统下载行为需在实际设备验证。

详细审查、修复方案和本次验收记录见 [修复与优化方案](docs/repair-plan-2026-11-12.md)。
