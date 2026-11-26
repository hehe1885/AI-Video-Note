# 1.0.4

- 修复 Windows 安装版在 CPU 或 GPU 转写长音频后，因字幕结果过大而一直停在「语音转写」的问题。
- 转写结果读取失败时，任务会显示错误并回收转写进程。
- 取消转写后会限时回收无响应的子进程，释放后续任务的转写槽位。
- 修复模型断点续传、B 站分 P 截图错配，以及 Obsidian 笔记更改编码后无法删除任务的问题。

- Fix a hang in the Windows installer build when CPU or GPU transcription returns a large transcript.
- Show an error and clean up the transcription process if the result cannot be read.
- Reap unresponsive transcription workers after cancellation so queued tasks can start.
- Fix model download resumption, screenshot routing for Bilibili parts, and task deletion after an Obsidian note changes encoding.

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
