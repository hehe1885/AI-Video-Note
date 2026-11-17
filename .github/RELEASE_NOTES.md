# 1.0.3

- 支持同一页面最多同时提交并处理 3 个笔记任务（`MAX_CONCURRENT_TASKS`，范围 1～3），可在「历史任务」中切换查看任一任务进度并随时取消。
- 新增本地语音转写槽位串行控制（`MAX_CONCURRENT_ASR`，默认 1），多任务并发时避免本地 GPU 显存与内存争抢；多任务需核对字幕时按提交顺序排队弹窗。
- 修复任务取消、异常状态同步、Agent 自动托管竞态及字幕校对弹窗清理等稳定性问题。
- 优化「历史任务」列表默认高度为 3 条展示，超出部分下滑浏览。

- Support up to 3 concurrent note tasks per page (`MAX_CONCURRENT_TASKS`, range 1–3), switch between tasks in History to inspect live progress, and cancel running tasks inline.
- Serialize local speech transcription workers via `MAX_CONCURRENT_ASR` (default 1) to avoid GPU/RAM contention, and queue pending subtitle reviews strictly in submission order.
- Fix stability issues around task cancellation, state polling, Agent auto-processing races, and subtitle review modal cleanup.
- Adjust the Recent History list height to show 3 items by default with smooth scrolling for more.

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
