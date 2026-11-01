# AI Video Note

[中文](README.md) · **English** · [Support development on Ko-fi](https://ko-fi.com/hehe1885)

A local desktop tool that turns video and audio into notes. Submit a video link or local file, extract subtitles or transcribe speech, then use AI to review, organize and export the result. Switch between Chinese and English using the control in the top-right corner.

## Features

- Bilibili, YouTube and Douyin links, plus local video and audio. Platform availability depends on network access, sign-in requirements and platform rules.
- Reliable original-language subtitles first, with local speech recognition when subtitles are unavailable. Supports Whisper and Chinese Paraformer models.
- Detailed, faithful and concise note styles, with timestamps, video screenshots and transcript review.
- Configurable model APIs and collaboration through Agents, MCP or the included Skill. Agent workflows require an available external client.
- Markdown, Word, PDF, HTML, PNG, TXT, JSON and Evernote ENEX exports, with support for Obsidian folders.
- Segmented processing and saved progress for long tasks, history, and regeneration using existing materials.
- Output file shortcuts, storage statistics and audio/video cache cleanup.
- Windows system tray, optional startup with Windows, Chinese/English UI and light/dark themes.

## Windows installer (recommended)

Download `AI-Video-Note-VERSION-Setup-x64.exe` from the repository's GitHub Releases, choose English or Chinese, and follow the wizard. The installer includes Python and the required runtime dependencies, so a separate Python installation is unnecessary. Speech recognition models are downloaded as needed; no personal settings or account data are included.

The default location is `%LOCALAPPDATA%\Programs\AI Video Note`. Installation is per user and does not require administrator privileges. You can choose another writable folder. A Start menu entry is created, with an optional desktop shortcut.

Settings, history, notes and models default to `workspace/` inside the installation folder. Before upgrading, quit through the system tray, then run the new installer and reuse the same folder. Only program files are replaced; the workspace and custom data folders are preserved during both updates and uninstalling.

## Running the source on Windows

1. Install **64-bit Python 3.11–3.14** and make sure `py` or `python` is available.
2. Download and extract the source, or clone the repository into a writable folder.
3. Double-click `start.bat`. On first launch it creates `.venv`, installs Python dependencies, and checks or downloads the Node.js runtime needed for YouTube. Internet access is required.
4. In the browser interface, select English or Chinese and configure your own AI connection.
5. If speech recognition is needed, download a model using model management. **No installed models are included in the source.**
6. Submit a link or local media file, choose the output options and start processing.

The service listens only on the local computer by default, using `127.0.0.1`. The launcher looks for an available port starting at `8765`; use the address it opens. Closing the browser can leave the app running in the system tray. Use the tray menu to quit.

If dependency installation fails, run `repair.bat` or use the environment check and repair controls in software settings. Initial setup, model downloads and online media downloads require internet access. Local speech recognition can run offline after model installation. Whether note generation needs the internet depends on the selected AI connection.

### Command line and development

In Windows PowerShell:

```powershell
# Prepare the environment and start; -Foreground displays logs in the terminal
.\start.ps1 -Foreground

# Stop a service managed by the startup scripts
.\stop.ps1
```

On other systems, create a Python virtual environment, install the dependencies and run the service manually. Windows tray integration, native dialogs and GPU installation helpers depend on the corresponding platform. Main source entry points:

```bash
python -m venv .venv
# After activating the virtual environment:
python -m pip install -r backend/requirements.txt
python launcher.py --console
```

YouTube downloads need Node.js 20 or newer. Formula images in some exports require a local Edge or Chrome installation. GPU acceleration also requires compatible drivers and runtime components; follow the instructions in the app.

## Models and AI configuration

Local speech recognition and the model used to write notes are separate settings. The former transcribes audio; the latter reviews and writes content. Select your models, CPU/GPU backend and AI service in the app.

The API workflow uses your own API key and may incur provider charges. An Agent workflow processes materials through the connected client. It does not require a note-generation API key in this app, but that client must have a working model configured.

When a platform requires Bilibili or Douyin sign-in, use your own account. Do not upload cookies, API keys or exported personal settings to a public repository.

## Data and privacy

This source distribution starts with a clean state. It **does not include the author's tasks, media, notes, account sessions, API keys, personal preferences, logs, installed models or Python environment**.

- User data defaults to `workspace/`; models default to `workspace/_model_cache/`. Custom folders can be configured in the app.
- `.venv/` and `.runtime/` are generated during use and are not distributed with the source.
- Local speech recognition runs on your computer. When you select an online AI service or Agent, relevant text and selected images or other materials are sent to that service or client. A local interface does not mean every processing step is offline.
- `.env.example` contains example values only. If needed, copy it to `.env` and configure it yourself. Do not commit real settings.
- `.gitignore` excludes the workspace, environments, models, logs and common credential files. Review your own staged changes before sharing.

Before cleaning an existing installation, quit the app and back up anything you need. The workspace may contain notes and original uploads. Custom model, download and export folders may also be outside the project.

## Tests and builds

```bash
python -m pip install -r backend/requirements-dev.txt
python -m pytest
node scripts/check_frontend.cjs
```

On Windows, build a portable executable with the following command. Build dependencies and compatible C++ runtime libraries are required.

```powershell
.\scripts\build_exe.ps1 -Python .\.venv\Scripts\python.exe

# Build a bilingual installer (also requires Inno Setup 7 or 6 and Simplified Chinese messages)
.\scripts\build_installer.ps1 -Python .\.venv\Scripts\python.exe
```

The repository includes cross-platform test workflows and a Windows release workflow. Pushing a `v*` tag that matches the version in `launcher.py` triggers verification, a build and a GitHub Release. Running the release workflow manually only creates build artifacts for testing. Prebuilt executables are not included in the source tree.

## Project layout

| Directory | Contents |
| --- | --- |
| `backend/` | Local service, tasks, speech recognition, AI processing and exports |
| `frontend/` | Browser UI, Chinese/English text and local static assets |
| `skills/AI-Video-Note/` | Agent Skill and helper scripts |
| `scripts/` | Environment setup, checks and build tools |
| `tests/` | Backend and frontend regression checks |
| `sources/` | Application icons |

## Support development

If this project is useful to you, you can buy me a coffee at [Ko-fi: hehe1885](https://ko-fi.com/hehe1885). Bug reports and contributions are welcome too.

## License and acknowledgments

This project uses the [MIT License](LICENSE) and retains the existing copyright notice. Open-source dependencies, models and platform content have their own licenses and terms. Notices for bundled third-party resources are retained with those resources.
