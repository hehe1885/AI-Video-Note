# AI Video Note

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-1F6FEB?style=flat-square" alt="简体中文" height="36"></a>
  &nbsp;
  <a href="README.en.md"><img src="https://img.shields.io/badge/English-64748B?style=flat-square" alt="English" height="36"></a>
  &nbsp;
  <a href="https://ko-fi.com/hehe1885"><img src="https://img.shields.io/badge/Ko--fi-%E6%94%AF%E6%8C%81%E5%BC%80%E5%8F%91-FF5E5B?style=flat-square&amp;logo=kofi&amp;logoColor=white" alt="通过 Ko-fi 支持开发" height="36"></a>
</p>

把视频和音频整理成笔记的本地桌面工具。提交视频链接或本地文件，提取字幕或识别语音，再由 AI 校对、整理和导出。界面支持中文与英文，可在右上角切换。

## 功能

- 支持 Bilibili、YouTube、抖音链接，以及本地视频和音频；平台可用性受网络、登录状态和平台规则影响。
- 优先使用可靠的原文字幕，缺失时使用本地语音识别；支持 Whisper 和中文 Paraformer 模型。
- 提供翔实笔记、忠实复原、精简摘要等风格，支持时间线、视频截图与转写校对。
- 支持配置大模型 API，也可通过 Agent、MCP 或配套 Skill 协作生成笔记；Agent 路线需要可用的外部客户端。
- 导出 Markdown、Word、PDF、HTML、PNG、TXT、JSON 和 Evernote ENEX；支持 Obsidian 目录。
- 长任务分段处理、保存进度和历史记录，支持复用材料重新生成。
- 提供输出文件快捷操作、存储统计和音视频缓存清理。
- Windows 系统托盘、可选开机启动、中英文界面及明暗主题。

## Windows 安装版（推荐）

在仓库的 GitHub Releases 中下载 `AI-Video-Note-版本号-Setup-x64.exe`，选择中文或英文并按向导安装。安装版自带 Python 和必要运行依赖，无需事先安装 Python；语音识别模型按需下载，不附带个人配置或账号数据。

默认安装到 `%LOCALAPPDATA%\Programs\AI Video Note`，只为当前用户安装，无需管理员权限，也可以自行选择其他可写目录。开始菜单会创建入口，桌面快捷方式可选。

配置、历史、笔记和模型默认位于安装目录下的 `workspace/`。升级前从系统托盘退出软件，运行新版安装包并沿用原目录；安装包只替换程序文件，保留整个工作区和自定义数据目录。卸载也会保留这些个人数据。

## Windows 源码版快速开始

1. 安装 **64 位 Python 3.11–3.14**，确保 `py` 或 `python` 可用。
2. 下载并解压本仓库源码，或使用 Git 克隆到可写目录。
3. 双击 `start.bat`。首次启动会创建 `.venv`、安装 Python 依赖，并检查或下载 YouTube 所需的 Node.js 运行组件，需要联网。
4. 在浏览器打开的软件界面中选择中文或 English，配置自己的 AI 通道。
5. 如需语音识别，在模型管理中下载所需模型。**源码不附带任何已安装模型。**
6. 提交链接或本地媒体，选择输出选项并开始处理。

默认仅监听本机 `127.0.0.1`；启动器默认从 `8765` 端口开始寻找可用端口，以实际打开的地址为准。关闭浏览器后软件仍可在托盘运行，退出请使用托盘菜单。

依赖安装失败时可运行 `repair.bat`，或在软件设置中使用环境检查与修复。首次安装、下载模型和下载视频需要网络；本地语音识别在模型安装后可离线运行。生成笔记是否联网取决于所选 AI 通道。

### 命令行与开发

在 Windows PowerShell 中：

```powershell
# 准备环境并启动；可加 -Foreground 在终端查看日志
.\start.ps1 -Foreground

# 停止由启动脚本管理的服务
.\stop.ps1
```

其他系统可自行创建 Python 虚拟环境并安装依赖，然后运行服务；Windows 托盘、原生窗口和 GPU 安装向导等功能依赖相应平台。源码中的主要入口：

```bash
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -r backend/requirements.txt
python launcher.py --console
```

YouTube 下载需要 Node.js 20 或更新版本。部分导出中的公式图片需要本机 Edge 或 Chrome；GPU 加速还需要兼容的驱动和运行组件，可通过软件内提示配置。

## 模型与 AI 配置

本地语音识别模型与笔记大模型是两类配置：前者负责识别音频，后者负责校对和写作。可在软件中选择适合自己的模型、CPU/GPU 后端以及 AI 服务。

API 路线使用你自己的 API Key，可能产生服务商费用。Agent 路线由所连接的客户端处理材料，不要求在本软件中填写笔记 API Key，但需要该客户端已配置可用模型。

需要 Bilibili 或抖音登录时，使用自己的账号完成登录。不要把 Cookie、API Key 或导出的设置发到公共仓库。

## 数据与隐私

这个源码发布包从空白状态开始，**不包含作者的任务、媒体、笔记、账号登录状态、API Key、个人偏好、日志、已安装模型或 Python 环境**。

- 默认用户数据位于 `workspace/`，模型默认位于 `workspace/_model_cache/`；自定义目录可在软件中配置。
- `.venv/` 和 `.runtime/` 在使用时生成，不随源码分发。
- 本地语音识别在本机执行。选择在线 AI 或 Agent 服务时，相应的文字与选中的图片等材料会发送给所选服务或客户端；“本地界面”不表示所有处理都离线。
- 仓库提供的 `.env.example` 只有示例值。需要时复制为 `.env` 并自行配置，不要提交真实配置。
- `.gitignore` 已排除工作区、环境、模型、日志和常见凭据文件；分享前仍应检查自己的 Git 暂存内容。

需要清理自己的安装时，先退出软件并备份需要的文件。工作区可能包含笔记和原始上传文件；自定义的模型、下载和导出目录也可能位于项目之外。

## 测试与打包

```bash
python -m pip install -r backend/requirements-dev.txt
python -m pytest
node scripts/check_frontend.cjs
```

Windows 可使用以下命令生成便携可执行文件（需要相应构建依赖和 C++ 运行库）：

```powershell
.\scripts\build_exe.ps1 -Python .\.venv\Scripts\python.exe

# 制作中英文安装包（还需 Inno Setup 7 或 6 与简体中文语言文件）
.\scripts\build_installer.ps1 -Python .\.venv\Scripts\python.exe
```

仓库包含跨平台测试工作流和 Windows 发布工作流。推送与 `launcher.py` 中版本号一致的 `v*` 标签，会触发验证、构建和 GitHub Release；手动运行发布工作流只构建供测试的附件。源码不包含预构建的可执行文件。

## 项目结构

| 目录 | 内容 |
| --- | --- |
| `backend/` | 本地服务、任务、识别、AI 整理与导出 |
| `frontend/` | 浏览器界面、中英文文案及本地静态资源 |
| `skills/AI-Video-Note/` | Agent Skill 与调用脚本 |
| `scripts/` | 环境准备、检查与打包工具 |
| `tests/` | 后端与前端回归检查 |
| `sources/` | 应用图标 |

## 支持开发

如果这个项目对你有帮助，欢迎通过 [Ko-fi：hehe1885](https://ko-fi.com/hehe1885) 请我喝杯咖啡。也欢迎反馈问题和提交改进。

## 许可证与致谢

本项目使用 [MIT License](LICENSE)，保留原有版权声明。所用开源依赖、模型和平台内容遵循各自许可证及使用条款；第三方资源的声明随对应资源保留。
