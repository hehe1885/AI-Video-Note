# 1.0.2

- 本地视频和音频导入取消应用大小上限，保留分块写入。
- 修复 GPU 自动探测和转写子进程设备选择，实时记录设备、精度及回退原因。
- 增加后台结构化详细日志，网页保留简洁日志，默认保留 2 天并自动脱敏。
- 修复历史本地任务从头重跑：复用原始素材并重新识别，处理期间显示文件名。
- 修复正常取消任务后的异常日志。

- Remove the application size cap for local media imports while retaining chunked writes.
- Fix automatic GPU detection and worker routing; report device, precision and fallback immediately.
- Add redacted structured background logs, retain concise web logs, and default to two-day retention.
- Restart local history tasks from preserved media and keep the filename visible.
- Handle normal transcription cancellation without spurious exception reports.

## 中文

AI Video Note：把视频和音频整理成笔记的本地桌面工具，支持中英文界面。

- 视频链接与本地媒体输入、字幕提取和本地语音识别。
- API / Agent 笔记生成、时间线、截图及多种导出格式。
- 长任务进度保存、历史记录、材料复用和缓存管理。

首次使用请自行配置 AI 通道，并按需下载语音识别模型。发布包不包含个人配置、登录凭据、历史任务或预装模型。安装说明见仓库中的 `README.md`。

[通过 Ko-fi 支持开发](https://ko-fi.com/hehe1885)

## English

AI Video Note is a local desktop tool for turning video and audio into notes, with Chinese and English interfaces.

- Video links and local media, subtitle extraction and local speech recognition.
- API / Agent note generation, timestamps, screenshots and multiple export formats.
- Saved progress for long tasks, history, material reuse and cache management.

Configure your own AI connection and download speech recognition models as needed. The distribution contains no personal settings, account credentials, historical tasks or preinstalled models. See `README.en.md` in the repository for setup instructions.

[Support development on Ko-fi](https://ko-fi.com/hehe1885)
