let visionCapability = 'unknown';
let visionCheckTimer = null;
let visionCheckToken = 0;
let lastVisionIdentity = '';
let visionPreference = false;
let cropPreference = false;
let visionForceRefresh = false;
const selectedRecentTaskIds = new Set();
let deletableRecentTaskIds = [];
let historyDeletionBusy = false;
let lastRecentTitleKey = "";
const API_BASE = '/api';

async function loadVideoCodecCapabilities(refresh=false) {
    const select=byId('videoEncoding'), status=byId('videoCodecStatus'), retry=byId('videoCodecRetry');
    if (!select || !status) return;
    const tr=text=>window.AIVideoNoteI18n?.translate(text)||text;
    const preferred=select.value==='size' || prefs.global.video_encoding==='size';
    select.disabled=true;
    select.options[1].disabled=true;
    select.value='compatibility';
    window.videoCodecAvailable=false;
    status.textContent=tr('正在检测 HEVC 硬件编解码能力…');
    if(retry) retry.disabled=true;
    try {
        const response=await fetchWithTimeout(`${API_BASE}/video-codecs?refresh=${refresh}`,{cache:'no-store'},35000);
        const data=await readResponse(response,'编码能力检测失败');
        window.videoCodecAvailable=!!data.available;
        select.options[1].disabled=!data.available;
        if(data.available && preferred) select.value='size';
        const hardware=data.available?'HEVC 硬件编解码可用。':'未验证到可用的 HEVC 硬件编解码，可能是硬件不支持或驱动不可用；体积优先已禁用。';
        const extension=data.hevc_extension===true?'已安装 HEVC 扩展。':data.hevc_extension===false?'未安装 HEVC 扩展；Windows 自带播放器可能无法播放 H.265，可安装 HEVC Video Extensions。软件截图使用内置解码器，不依赖该扩展。':'无法确认 HEVC 扩展是否安装；软件截图不依赖该扩展。';
        status.textContent=tr(hardware)+' '+tr(extension);
    } catch(error) {
        status.textContent=tr('编码能力检测失败，体积优先已禁用；可点击重新检测。');
    } finally {
        select.disabled=false;
        if(retry) retry.disabled=false;
    }
}
const POLL_DELAY_MS = 2000;
const POLL_TIMEOUT_MS = 12000;
const MAX_POLL_ERRORS = 5;
const CUSTOM_MODEL_ID = '__custom__';
// 档案下拉里自定义档案的取值前缀（内置 Provider 直接用 provider 名）。
const CUSTOM_PROFILE_PREFIX = 'custom:';
// 所有偏好集中在一个命名空间对象里写入，"浏览器存储中没有任何密钥"因此是
// 单个函数（persistPrefs）的性质，而不是 13 个散装键的逐处审查结果。
const PREFS_KEY = 'vtn_prefs';
const PREFS_SCHEMA = 3;
const PREFS_DEBOUNCE_MS = 250;
// 旧版本每个设置一个散装键，仅用于一次性迁移，读取后立即删除。
const LEGACY_PREFERENCE_KEYS = [
    'llm_provider',
    'llm_model_id',
    'llm_model',
    'custom_base_url',
    'custom_model_name',
    'whisper_model',
    'screenshot_interval',
    'include_screenshots',
    'use_gpu', 'gpu_backend',
    'summary_style', 'ai_commentary', 'ai_commentary_position', 'note_language', 'timeline_mode', 'source_link_position', 'text_detail',
    'processing_mode',
    'reasoning_effort',
    'theme'
];
const LEGACY_WHISPER_CONFIRM_PREFIX = 'whisper_confirm_';
// 字段名命中即拒绝写入浏览器存储（API Key 只允许待在当前页面内存中）。
const SECRET_FIELD_PATTERN = /key|token|secret|sessdata|jct|buvid|authorization|cookie/i;
const LEGACY_SENSITIVE_KEYS = [
    'custom_api_key',
    'bili_sessdata',
    'bili_jct',
    'bili_buvid3',
    'deepseek_api_key',
    'openai_api_key',
    'qwen_api_key',
    'zhipu_api_key',
    'moonshot_api_key',
    'save_api_key',
    'llm_api_key'
];

const PROVIDER_CONFIG = {
    deepseek: {
        name: 'DeepSeek',
        baseUrl: 'https://api.deepseek.com',
        defaultModel: 'deepseek-flash',
        models: [
            ['deepseek-flash', 'DeepSeek V4.1 Flash'],
            ['deepseek-v4-pro', 'DeepSeek V4 Pro'],
            // 旧名仍可调用（官方已把它并到 V4.1-Flash 并按 Flash 计价），留着是为了
            // 不砸掉已存过这个 ID 的档案：下拉里找不到值会跳去「手动输入」并清空输入框
            ['deepseek-v4-flash', 'DeepSeek V4 Flash（旧名 · 已并入 V4.1）']
        ]
    },
    openai: {
        name: 'OpenAI',
        baseUrl: 'https://api.openai.com/v1',
        defaultModel: 'gpt-5.6-terra',
        models: [
            ['gpt-5.6-sol', 'GPT-5.6 Sol'],
            ['gpt-5.6-terra', 'GPT-5.6 Terra'],
            ['gpt-5.6-luna', 'GPT-5.6 Luna']
        ]
    },
    glm: {
        name: '智谱 GLM',
        baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
        defaultModel: 'glm-4.7-flash',
        models: [
            ['glm-5.3', 'GLM-5.3'],
            ['glm-5.2', 'GLM-5.2'],
            ['glm-4.7-flash', 'GLM-4.7-Flash（免费）'],
            ['glm-4.5-flash', 'GLM-4.5-Flash（已下线 · 自动转 4.7）']
        ]
    },
    qwen: {
        name: '通义千问',
        baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        defaultModel: 'qwen3.7-plus',
        models: [
            ['qwen3.8-max', 'Qwen3.8 Max'],
            ['qwen3.7-plus', 'Qwen3.7 Plus'],
            ['qwen3.8-flash', 'Qwen3.8 Flash'],
            ['qwen3.7-max', 'Qwen3.7 Max（旧版）'],
            ['qwen3.7-flash', 'Qwen3.7 Flash（旧版）']
        ]
    },
    moonshot: {
        name: '月之暗面 Kimi',
        baseUrl: 'https://api.moonshot.cn/v1',
        defaultModel: 'kimi-k3',
        models: [
            ['kimi-k3', 'Kimi K3'],
            ['kimi-k2.6', 'Kimi K2.6'],
            ['kimi-k2.7-code-highspeed', 'Kimi K2.7 Code Highspeed']
        ]
    },
    custom: {
        name: '自定义接口',
        baseUrl: '',
        defaultModel: CUSTOM_MODEL_ID,
        models: []
    }
};

const LEGACY_PROVIDER_MAP = {
    deepseek: 'deepseek',
    openai: 'openai',
    openai_gpt4: 'openai',
    openai_gpt35: 'openai',
    qwen: 'qwen',
    glm: 'glm',
    moonshot: 'moonshot',
    custom: 'custom'
};

let currentTaskId = null;
// Retain the task's materials until the user chooses a different source.
let sourceTaskId = null;
let sourceFilename = '';
let currentTaskStatus = '';
let currentMarkdown = '';
let currentHtml = '';
// 后端下发的下载名（含视频标题与 B 站分 P 名），空串时回退成时间戳名
let currentDownloadName = '';
let pollTimer = null;
let pollErrorCount = 0;
let isSubmitting = false;
let isTaskActive = false;
let isDownloading = false;
let isCancelling = false;
let cancellationTaskId = null;
let taskViewSequence = 0;
// 结果区当前展示的是字幕稿还是笔记：标题、按钮文案与进度步数都跟着它走
let isTranscriptTask = false;
let elapsedTimer = null;
let elapsedBaseSeconds = 0;
let elapsedSyncedAt = 0;
let prefs = defaultPrefs();
let persistTimer = null;
let persistJob = null;

document.addEventListener('DOMContentLoaded', async () => {
    window.__aiVideoNoteReady = false;
    await loadWorkspacePreferences();
    // 每一步单独容错：即使浏览器里残留旧版缓存的 HTML（元素缺失），
    // 也只影响对应功能，不会让整页按钮全部失效。
    safeStep(removeLegacySecrets, '清理旧数据');
    safeStep(initPreferences, '读取偏好设置');
    safeStep(bindEvents, '绑定页面事件');
    bindListener('videoEncoding','change',persistGlobals);
    bindListener('videoCodecRetry','click',()=>loadVideoCodecCapabilities(true));
    safeStep(toggleSourceType, '初始化来源切换');
    safeStep(applyServiceState, '显示本机服务地址');
    safeStep(applyMcpAccess, '初始化 MCP 接入信息');
    loadAppVersion();
    loadVideoCodecCapabilities();
    loadRecentTasks(true);
    const linkedTask = new URLSearchParams(location.search).get("task");
    if (linkedTask) openRecentTask(linkedTask);
    // 启动自检更新：晚几秒再发，别跟首屏请求抢路；失败静默，托盘里有手动入口
    window.setTimeout(runStartupUpdateCheck, 2500);
    window.__aiVideoNoteReady = true;
    window.dispatchEvent(new Event('preferences-ready'));
});

function safeStep(action, label) {
    try {
        action();
    } catch (error) {
        console.error(`[AI Video Note] ${label} 初始化失败：`, error);
    }
}

function applyServiceState() {
    // 地址由页面自己填：静态 HTML 里写死 localhost:8000 会在第二个实例（8001 起）上说谎，
    // 而 location 在页面加载时就已知，不用等健康检查回来。
    const element = byId('serviceState');
    if (element && window.location.host) {
        element.textContent = `本机服务 · ${window.location.host}`;
    }
}

function renderAgentAccessState(state) {
    const element = byId('agentConnectionState');
    if (!element) return;
    const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
    const lines = [];
    const addLine = (text, active) => lines.push({text, active: Boolean(active)});
    for (const client of (state.clients || []).slice(-3)) {
        const status = client.connected ? '已连接' : client.recent ? '最近调用' : '已识别，当前空闲';
        addLine(`MCP · ${client.name} · ${tr(status)}`, client.connected);
    }
    if (state.unknown_connected || (!state.clients?.length && (state.recent || state.active_requests > 0))) {
        addLine(`MCP · ${tr('未知客户端')} · ${tr(state.connected > 0 ? '已连接' : '最近调用')}`, state.connected > 0);
    }
    for (const client of (state.skill_clients || []).slice(-3)) {
        const name = client.name || tr('未知 Agent（未提供名称）');
        const status = client.active_requests > 0 ? '正在调用' : client.recent ? '最近调用' : '已识别，当前空闲';
        addLine(`Skill · ${name} · ${tr(status)}`, client.active_requests > 0);
    }
    const local = state.local_agents?.[state.local_agents.length - 1];
    if (local) addLine(`${tr('本机命令行')} · ${local.name} · ${tr(local.running ? '正在处理' : '已完成调用')}`, local.running);
    if (!lines.length) {
        element.textContent = tr('尚未检测到 Agent 调用');
        return;
    }
    element.replaceChildren(...lines.map((line, index) => {
        const row = document.createElement('span');
        row.className = line.active ? 'agent-access-line active' : 'agent-access-line';
        row.textContent = (index ? '\n' : '') + line.text;
        return row;
    }));
}

async function refreshMcpConnectionState() {
    const element = byId('mcpConnectionState');
    if (!element || document.hidden) return;
    try {
        const response = await fetch(`${API_BASE}/mcp/activity`, { cache: 'no-store' });
        if (!response.ok) throw new Error('Unavailable');
        const state = await response.json();
        renderAgentAccessState(state);
        const clients = Array.isArray(state.clients) ? state.clients : [];
        const names = (items) => items.map(c => c.name + (c.version ? ` (${c.version})` : '')).join('、');
        const connectedClients = clients.filter(c => c.connected);
        const recentClients = clients.filter(c => !c.connected && c.recent !== false);
        const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
        const connected = state.connected > 0;
        element.classList.toggle('connected', connected || Boolean(state.recent));
        if (connected) {
            const label = names(connectedClients);
            element.textContent = label
                ? tr('正在连接：') + label + (state.unknown_connected ? '、' + tr('未知客户端') : '')
                : 'MCP 正在连接（未知客户端）';
        } else if (recentClients.length) {
            element.textContent = tr('最近活跃：') + names(recentClients);
        } else if (clients.length) {
            element.textContent = tr('已识别客户端：') + names(clients) + tr('（按需连接，当前空闲）');
        } else if (state.local_agents?.length) {
            const local = state.local_agents[state.local_agents.length - 1];
            element.classList.toggle('connected', local.running);
            element.textContent = tr(local.running ? '本机命令行正在处理：' : '本机命令行已处理任务：') + local.name + tr('（不通过 MCP 连接）');
        } else {
            element.textContent = state.active_requests > 0 ? '客户端正在访问 MCP'
                : state.recent ? '客户端最近活跃（过去 1 分钟）'
                : state.last_activity ? '暂无持续连接（HTTP 客户端按需访问）' : '未检测到客户端连接';
        }
    } catch {
        const overview = byId('agentConnectionState');
        if (overview) overview.textContent = window.AIVideoNoteI18n?.translate('暂时无法读取 Agent 接入状态') || '暂时无法读取 Agent 接入状态';
        element.classList.remove('connected');
        element.textContent = '暂时无法读取 MCP 连接状态';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    refreshMcpConnectionState();
    window.setInterval(refreshMcpConnectionState, 5000);
});

async function loadAppVersion() {
    try {
        const response = await fetch(`${API_BASE}/health`, { cache: 'no-store' });
        const data = await readResponse(response, '读取版本失败');
        if (data.version) {
            // 端口跟着版本一起露出来：本机同时跑着两个实例、或用户开的是上一个实例
            // 留下的地址时，这是唯一能一眼对上的东西
            const port = window.location.port ? ` · 端口 ${window.location.port}` : '';
            byId('appVersion').textContent = `v${data.version}${port}`;
        }
        const downloadDefault = byId('videoDownloadDefault');
        if (data.video_download_default_directory) {
            const path = document.createElement('span');
            path.setAttribute('data-no-i18n', '');
            path.textContent = data.video_download_default_directory;
            downloadDefault.replaceChildren(document.createTextNode('默认位置：'), path,
                document.createTextNode('（视频文件集中保存）'));
        } else {
            downloadDefault.textContent = '默认位置：workspace 下的 video 文件夹（重启软件后可显示完整路径）';
        }
        applyMcpAccess(data.dependencies?.mcp_streamable_http ?? data.dependencies?.mcp_sse);
    } catch {
        // 保留 HTML 中的构建版本，服务短暂未就绪不影响页面使用。
    }
}

function mcpSseUrl() {
    return `${location.origin}/mcp/${byId("mcpTransport")?.value === "sse" ? "sse" : "http"}`;
}

function mcpJsonConfig() {
    return JSON.stringify(
        { mcpServers: { "AI-Video-Note": { type: byId("mcpTransport")?.value === "sse" ? "sse" : "http", url: mcpSseUrl() } } },
        null,
        2
    );
}

// 只有后端明确说 mcp 没挂上才提示"连不通"；老后端没这个字段时按可用显示。
function applyMcpAccess(mcpSse) {
    const url = byId('mcpSseUrl');
    if (url) url.textContent = mcpSseUrl();
    const unavailable = byId('mcpUnavailable');
    if (unavailable) unavailable.hidden = mcpSse !== false;
    // 芯片和提示条都要等后端确认 MCP 真挂上了才出现，否则等于承诺一个连不通的地址
    const chip = byId('mcpStatusChip');
    if (chip) chip.hidden = mcpSse !== true;
}

async function copyTextToClipboard(text, successLabel) {
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
        } else {
            copyTextFallback(text);
        }
        showToast(successLabel, 'success');
    } catch {
        try {
            copyTextFallback(text);
            showToast(successLabel, 'success');
        } catch (error) {
            showToast(`复制失败：${error.message}`, 'error');
        }
    }
}

function bindEvents() {
    initSelectPickers();
    bindListener('generationMode', 'change', () => { persistGlobals(); updateGenerationSettings(); scheduleVisionCheck(); });
    bindListener('copyAgentPacketBtn', 'click', copyAgentPacket);
    bindListener('importAgentResultBtn', 'click', importAgentResult);
    bindListener('resetConfigBtn', 'click', resetPreferences);
    bindListener('submitBtn', 'click', () => startSummary({
        resumeCurrent: Boolean(currentTaskId && sourceTaskId === currentTaskId),
        generationAction: currentTaskId && sourceTaskId === currentTaskId && ['failed', 'cancelled'].includes(currentTaskStatus)
            ? 'continue' : 'regenerate'
    }));
    bindListener('restartBtn', 'click', () => startSummary({ forceRestart: true }));
    document.querySelectorAll('input[name="outputMode"]').forEach((input) => {
        input.addEventListener('change', handleOutputModeChange);
    });
    bindListener('cancelTaskBtn', 'click', cancelCurrentTask);
    bindListener('retryPreferencesBtn', 'click', retryWorkspacePreferencesSave);
    bindListener('reloadPreferencesBtn', 'click', () => {
        if (window.confirm('重新读取会放弃本页尚未保存的设置，继续吗？')) window.location.reload();
    });
    bindListener('reconnectTaskBtn', 'click', reconnectCurrentTask);
    bindListener('downloadMdBtn', 'click', downloadSummary);
    bindListener('exportSubtitleBtn', 'click', exportSubtitle);
    bindListener('copyNoteBtn', 'click', copyFullNote);
    bindListener('formatSelect', 'change', handleNoteFormatChange);
    bindListener('refreshRecentTasksBtn', 'click', () => loadRecentTasks());
    bindListener('loadMoreHistoryBtn', 'click', () => loadRecentTasks(false, true));
    bindListener('historySearch', 'input', () => {
        // Invalidate an in-flight response immediately, before the debounce fires.
        historyRequestSequence++;
        window.clearTimeout(historySearchTimer);
        historySearchTimer = window.setTimeout(changeHistoryFilters, 300);
    });
    for (const id of ['historyStatus','historyOutput','historyStartDate','historyEndDate']) bindListener(id,'change',changeHistoryFilters);
    bindListener('clearHistoryFiltersBtn','click',()=>{
        for(const id of ['historySearch','historyStatus','historyOutput','historyStartDate','historyEndDate']) byId(id).value='';
        changeHistoryFilters();
    });
    bindListener('exportSettingsBtn','click',exportSettingsBackup);
    bindListener('restoreSettingsBtn','click',chooseSettingsBackupFile);
    bindListener('settingsBackupFile','change',readSettingsBackupFile);
    bindListener('deleteSelectedTasksBtn', 'click', () => deleteRecentTaskIds([...selectedRecentTaskIds]));
    bindListener('selectAllRecentTasks', 'change', () => {
        selectedRecentTaskIds.clear();
        if (byId('selectAllRecentTasks').checked) deletableRecentTaskIds.forEach(id => selectedRecentTaskIds.add(id));
        syncRecentSelection();
    });
    const recentTaskList = byId('recentTaskList');
    if (recentTaskList) {
        recentTaskList.addEventListener('change', (event) => {
            const id = event.target.dataset.selectTaskId;
            if (!id) return;
            if (event.target.checked) selectedRecentTaskIds.add(id); else selectedRecentTaskIds.delete(id);
            syncRecentSelection();
        });
        recentTaskList.addEventListener('click', (event) => {
            const deleteButton = event.target.closest('[data-delete-task-id]');
            if (deleteButton) {
                deleteStoppedTask(deleteButton.dataset.deleteTaskId);
                return;
            }
            const button = event.target.closest('[data-task-id]');
            if (button) openRecentTask(button.dataset.taskId);
        });
    }
    bindListener('llmProfile', 'change', handleProfileChange);
    bindListener('llmProfile', 'change', updateRenameVisibility);
    bindListener('renameProfileBtn', 'click', handleProfileRenameClick);
    bindListener('llmModel', 'change', handleModelChange);
    bindListener('llmTestBtn', 'click', testLlmConnection);
    bindListener('diagBtn', 'click', runDiagnostics);
    bindListener('logRetentionDays', 'change', saveLogRetention);
    loadLogRetention();
    bindListener('autostartEnabled', 'change', saveAutostartSetting);
    bindListener('viewLogsBtn', 'click', openAppLog);
    loadAutostartSetting();
    bindListener('updateIgnoreBtn', 'click', ignoreUpdateOffer);
    bindListener('updateCancelBtn', 'click', dismissUpdateOffer);
    bindListener('saveKeyBtn', 'click', saveApiKey);
    bindListener('clearKeyBtn', 'click', clearSavedKey);
    bindListener('customProfileAddBtn', 'click', addCustomProfile);
    bindListener('customProfileDeleteBtn', 'click', deleteCustomProfile);
    bindListener('downloadModelBtn', 'click', () => controlModelDownload('start'));
    bindListener('pauseModelDownloadBtn', 'click', () => controlModelDownload('pause'));
    bindListener('resumeModelDownloadBtn', 'click', () => controlModelDownload('resume'));
    bindListener('checkEnvironmentBtn', 'click', () => checkProjectEnvironment());
    bindListener('repairEnvironmentBtn', 'click', repairProjectEnvironment);
    pollModelDownload();
    bindListener('manualModelBtn', 'click', manualImportWhisperModel);
    bindListener('saveModelDirectoryBtn', 'click', () => saveModelDirectory(false));
    bindListener('resetModelDirectoryBtn', 'click', () => saveModelDirectory(true));
    bindListener('deleteModelBtn', 'click', deleteSelectedModel);
    bindListener('whisperModel', 'change', renderSelectedModelPaths);
    bindListener('retryModelStatusBtn', 'click', refreshWhisperModelHints);
    bindListener('mcpTransport', 'change', () => {
        prefs.ui.mcp_transport = byId('mcpTransport').value;
        persistPrefs();
        const url = byId('mcpSseUrl');
        if (url) url.textContent = mcpSseUrl();
    });
    bindListener('copyMcpUrlBtn', 'click', () => copyTextToClipboard(mcpSseUrl(), 'MCP 地址已复制'));
    bindListener('copyMcpConfigBtn', 'click', () =>
        copyTextToClipboard(mcpJsonConfig(), 'MCP 配置已复制，到客户端粘贴导入即可'));
    bindListener('sourceType', 'change', () => {
        toggleSourceType();
        toggleScreenshotSettings();
        resetSubmitLabel(true);
    });
    bindPreferenceAutoSave();
    const videoUrl = byId('videoUrl');
    if (videoUrl) {
        videoUrl.addEventListener('input', () => {
            resetSubmitLabel(true);
            window.clearTimeout(biliHintDebounce);
            biliHintDebounce = window.setTimeout(() => updateBiliHint(), 300);
            scheduleBiliPagesPreview();
        });
        videoUrl.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.isComposing && !byId('updateModal')?.open) startSummary();
        });
    }
    bindListener('biliHintScanBtn', 'click', startBiliLogin);
    bindListener('biliHintManualBtn', 'click', revealBiliCredentials);
    bindListener('douyinLoginBtn', 'click', startDouyinLogin);
    bindListener('biliHintDismissBtn', 'click', dismissBiliHint);
    bindListener('biliPagesAllBtn', 'click', () => setBiliPagesSelection('all'));
    bindListener('biliPagesNoneBtn', 'click', () => setBiliPagesSelection('none'));
    bindListener('biliPagesInvertBtn', 'click', () => setBiliPagesSelection('invert'));
    initBiliLogin();
    bindListener('screenshotDensity', 'change', toggleScreenshotSettings);
    bindListener('visionScreenshots', 'change', () => { visionPreference = byId('visionScreenshots').checked; updateVisionCheckbox(); persistGlobals(); });
    bindListener('autoCropScreenshots', 'change', () => { cropPreference = byId('autoCropScreenshots').checked; persistGlobals(); });
    bindListener('retryVisionCheckBtn', 'click', () => { visionForceRefresh = true; lastVisionIdentity = ''; scheduleVisionCheck(); });
    ['llmProfile', 'llmModel', 'customModelName', 'customBaseUrl', 'apiKey'].forEach(id => {
        bindListener(id, 'input', scheduleVisionCheck);
        bindListener(id, 'change', scheduleVisionCheck);
    });
    bindListener('timedScreenshots', 'change', () => { toggleScreenshotSettings(); persistGlobals(); });
    bindListener('screenshotInterval', 'change', persistGlobals);
    bindListener('localFile', 'change', () => {
        updateFileInfo();
        resetSubmitLabel(true);
    });
    initThemeControl();
    window.addEventListener('beforeunload', () => {
        stopPolling();
        stopElapsedTimer();
        stopDouyinLoginPolling();
        stopManualImportPolling();
    });
}

function bindListener(id, event, handler) {
    const element = byId(id);
    if (element) {
        element.addEventListener(event, handler);
    } else {
        console.warn(`[AI Video Note] 页面缺少元素 #${id}，已跳过对应事件绑定`);
    }
}

function byId(id) {
    return document.getElementById(id);
}

const TASK_STATUS_LABELS = {
    uploaded: '待处理',
    pending: '准备中',
    queued: '排队中',
    processing: '处理中',
    awaiting_agent: '等待 agent 生成',
    cancelling: '取消中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消'
};

// 已结束 + 尚未开始处理的任务可以直接删；运行中的任务（queued/processing）只能先取消
const DELETABLE_TASK_STATUSES = ['completed', 'failed', 'cancelled', 'uploaded', 'pending'];

// 后端给的 elapsed_seconds 从提交起算（含排队），未结束的任务本地继续按秒推进，
// 不必为此多打一轮 /api/tasks
const LIVE_TASK_STATUSES = ['pending', 'queued', 'processing', 'cancelling'];
// 上传后一直没提交的任务：elapsed 同样从创建起算，显示出来只是个虚构的数字，不如不提
const NO_ELAPSED_TASK_STATUSES = ['uploaded'];
let recentTaskRows = new Map();
let recentTasksSyncedAt = 0;
let recentTasksElapsedTimer = null;

function recentTaskMetaText(task, nowSeconds) {
    const created = formatTaskDate(task.created_at);
    if (NO_ELAPSED_TASK_STATUSES.includes(task.status)) return created;
    const live = LIVE_TASK_STATUSES.includes(task.status);
    const base = Number(task.elapsed_seconds) || 0;
    const elapsed = formatElapsedCompact(live ? base + Math.max(0, nowSeconds - recentTasksSyncedAt) : base);
    if (!elapsed) return created;
    return [created, `${live ? '已用时' : '共耗时'} ${elapsed}`].filter(Boolean).join(' · ');
}

function armRecentTasksElapsedTicker() {
    const hasLive = Array.from(recentTaskRows.values()).some(
        (row) => LIVE_TASK_STATUSES.includes(row.task.status)
    );
    if (!hasLive) {
        if (recentTasksElapsedTimer !== null) window.clearInterval(recentTasksElapsedTimer);
        recentTasksElapsedTimer = null;
        return;
    }
    if (recentTasksElapsedTimer !== null) return;
    recentTasksElapsedTimer = window.setInterval(() => {
        const now = Date.now() / 1000;
        recentTaskRows.forEach((row) => {
            row.meta.textContent = recentTaskMetaText(row.task, now);
        });
    }, 1000);
}

let historyItems = [];
let historyNextOffset = 0;
let historyHasMore = false;
let historyRequestSequence = 0;
let historyLoading = false;
let historySearchTimer = null;

function changeHistoryFilters() {
    window.clearTimeout(historySearchTimer);
    selectedRecentTaskIds.clear();
    return loadRecentTasks(false);
}

async function loadRecentTasks(silent = false, append = false) {
    if (append && (historyLoading || !historyHasMore)) return;
    const sequence = ++historyRequestSequence;
    historyLoading = true;
    byId('refreshRecentTasksBtn').disabled = true;
    byId('loadMoreHistoryBtn').disabled = true;
    const fields = {q: byId('historySearch')?.value || '', status: byId('historyStatus')?.value || '',
        output: byId('historyOutput')?.value || '', start_date: byId('historyStartDate')?.value || '',
        end_date: byId('historyEndDate')?.value || '', utc_offset: -new Date().getTimezoneOffset(),
        offset: append ? historyNextOffset : 0, limit: 20};
    const query = Object.entries(fields).map(([key,value])=>`${key}=${encodeURIComponent(value)}`).join('&');
    try {
        const response = await fetch(`${API_BASE}/tasks?${query}`, {cache: 'no-store'});
        const data = await readResponse(response, '读取最近任务失败');
        if (sequence !== historyRequestSequence) return;
        const page = Array.isArray(data.tasks) ? data.tasks : [];
        if (append) {
            const elapsed = Math.max(0, Date.now()/1000 - recentTasksSyncedAt);
            historyItems = historyItems.map(task => LIVE_TASK_STATUSES.includes(task.status)
                ? {...task, elapsed_seconds:(Number(task.elapsed_seconds)||0)+elapsed} : task);
        }
        historyItems = append ? Array.from(new Map([...historyItems, ...page].map(task=>[task.task_id,task])).values()) : page;
        historyHasMore = data.has_more === true;
        historyNextOffset = Number(data.next_offset) || historyItems.length;
        renderRecentTasks(historyItems);
        byId('historyCount').textContent = `已显示 ${historyItems.length} / 共 ${data.total ?? historyItems.length} 条`;
        byId('loadMoreHistoryBtn').hidden = !historyHasMore;
        byId('historyEmpty').hidden = historyItems.length !== 0;
    } catch (error) {
        if (sequence === historyRequestSequence && !silent) showToast(error.message, 'error');
    } finally {
        if (sequence === historyRequestSequence) {
            historyLoading = false;
            byId('refreshRecentTasksBtn').disabled = false;
            byId('loadMoreHistoryBtn').disabled = false;
        }
    }
}


function renderRecentTasks(tasks) {
    window.AIVideoNoteMotion?.prepareTasks(tasks);
    const panel = byId('recentTasksPanel');
    const list = byId('recentTaskList');
    list.replaceChildren();
    panel.hidden = false;
    recentTasksSyncedAt = Date.now() / 1000;
    recentTaskRows = new Map();
    deletableRecentTaskIds = tasks.filter(task => DELETABLE_TASK_STATUSES.includes(task.status)).map(task => task.task_id);
    for (const id of selectedRecentTaskIds) if (!deletableRecentTaskIds.includes(id)) selectedRecentTaskIds.delete(id);
    tasks.forEach((task, index) => {
        const row = document.createElement('div');
        row.className = 'recent-task-row';
        const select = document.createElement('input');
        select.type = 'checkbox';
        select.dataset.selectTaskId = task.task_id;
        select.checked = selectedRecentTaskIds.has(task.task_id);
        select.disabled = historyDeletionBusy || !DELETABLE_TASK_STATUSES.includes(task.status);
        select.setAttribute('aria-label', `选择任务：${task.title || task.task_id}`);
        row.append(select);
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'recent-task-item';
        button.dataset.taskId = task.task_id;

        const title = document.createElement('strong');
        if (task.output === 'transcript') {
            // 标签放最前：长标题被省略号截断时，仍能看出这是一条字幕稿
            const kind = document.createElement('span');
            kind.className = 'recent-task-kind';
            kind.textContent = '转录';
            title.append(kind);
        }
        title.append(task.title || '未命名任务');
        const meta = document.createElement('span');
        meta.textContent = recentTaskMetaText(task, recentTasksSyncedAt);
        recentTaskRows.set(task.task_id, { meta, task });
        const status = document.createElement('em');
        status.className = `recent-task-status ${task.status || ''}`;
        status.textContent = TASK_STATUS_LABELS[task.status] || task.step_name || '未知';

        const text = document.createElement('span');
        text.className = 'recent-task-text';
        text.append(title, meta);
        button.append(text, status);
        row.append(button);
        // 排队中/处理中只能取消（后端拒绝删除运行中的任务）；未开始的任务可以直接删掉，
        // 否则上传后放弃的孤儿任务会永久占着最近任务列表
        if (DELETABLE_TASK_STATUSES.includes(task.status)) {
            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.disabled = historyDeletionBusy;
            deleteButton.className = 'recent-task-delete';
            deleteButton.dataset.deleteTaskId = task.task_id;
            const statusLabel = `${TASK_STATUS_LABELS[task.status] || ''}任务`;
            deleteButton.title = `删除${statusLabel}`;
            deleteButton.setAttribute('aria-label', `删除${statusLabel}：${task.title || '未命名任务'}`);
            deleteButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14M10 10v6m4-6v6"/></svg>';
            row.append(deleteButton);
        }
        window.AIVideoNoteMotion?.decorateTask(row, status, task, index);
        list.append(row);
    });
    syncRecentSelection();
    armRecentTasksElapsedTicker();
}

function syncRecentSelection() {
    const count = selectedRecentTaskIds.size;
    byId('deleteSelectedTasksBtn').textContent = `删除所选（${count}）`;
    byId('deleteSelectedTasksBtn').disabled = historyDeletionBusy || !count;
    const all = byId('selectAllRecentTasks');
    all.disabled = historyDeletionBusy || !deletableRecentTaskIds.length;
    all.checked = !!count && count === deletableRecentTaskIds.length;
    all.indeterminate = count > 0 && count < deletableRecentTaskIds.length;
    document.querySelectorAll('[data-select-task-id]').forEach(input => {
        input.checked = selectedRecentTaskIds.has(input.dataset.selectTaskId);
        input.disabled = historyDeletionBusy || !deletableRecentTaskIds.includes(input.dataset.selectTaskId);
    });
}

async function deleteStoppedTask(taskId) { await deleteRecentTaskIds([taskId]); }

async function deleteRecentTaskIds(ids) {
    if (historyDeletionBusy || !ids.length) return;
    if (!window.confirm(`确定删除 ${ids.length} 个任务及其保存文件吗？将清理任务音频、字幕、截图、关联的下载视频、归档笔记及此任务生成的 Obsidian 笔记与图片，删除后无法恢复。共享视频也会删除，其他历史任务需要时将重新下载；浏览器另存的副本需手动删除。`)) return;
    historyDeletionBusy = true;
    syncRecentSelection();
    try {
        const response = await fetch(`${API_BASE}/tasks/delete`, {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task_ids: ids})
        });
        const data = await readResponse(response, '删除任务失败');
        for (const id of data.deleted || []) selectedRecentTaskIds.delete(id);
        if ((data.deleted || []).includes(currentTaskId)) {
            resetTaskView();
            byId('progressArea').hidden = true;
            byId('resultArea').hidden = true;
        }
        const failures = data.failed || [];
        showToast(failures.length ? `已删除 ${(data.deleted || []).length} 个；${failures.length} 个未删除：${failures.map(item => item.error).join('；')}` : `已删除 ${(data.deleted || []).length} 个任务及保存文件`, failures.length ? 'warning' : 'success');
    } catch (error) { showToast(`删除失败：${error.message}`, 'error'); }
    finally {
        historyDeletionBusy = false;
        await loadRecentTasks(true);
        syncRecentSelection();
    }
}

function formatTaskDate(value) {
    const date = new Date(value || '');
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    }).format(date);
}

function formatElapsedCompact(value) {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    if (!total) return '';
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    if (hours) return `${hours}时${minutes}分`;
    if (minutes) return `${minutes}分${seconds}秒`;
    return `${seconds}秒`;
}

let recentTaskRequestSequence = 0;
async function openRecentTask(taskId) {
    const requestSequence = ++recentTaskRequestSequence;
    const viewSequence = taskViewSequence;
    let selectedView = viewSequence;
    try {
        const response = await fetch(`${API_BASE}/task/${encodeURIComponent(taskId)}`, {
            cache: 'no-store'
        });
        const task = await readResponse(response, '读取任务失败');
        if (requestSequence !== recentTaskRequestSequence || viewSequence !== taskViewSequence) return;
        resetTaskView();
        selectedView = taskViewSequence;
        currentTaskId = taskId;
        sourceTaskId = taskId;
        sourceFilename = task.source_url ? '' : (task.uploaded_filename || '');
        byId('localFile').value = '';
        if (task.generation_mode === "agent") {
            byId("generationMode").value = "agent";
            updateGenerationSettings();
            if (task.note_format) byId("formatSelect").value = task.note_format;
        }
        if (task.source_url) {
            byId('sourceType').value = 'url';
            byId('videoUrl').value = task.source_url;
        } else if (task.uploaded_filename) {
            byId('sourceType').value = /\.(mp3|m4a|wav|flac|aac|ogg|opus|wma)$/i.test(task.uploaded_filename || '') ? 'audio' : 'local';
        }
        toggleSourceType();
        updateProgress(task.progress || 0);
        updateStep(task.step || 0);
        setTranscriptTaskView(task.output === 'transcript');
        renderTaskAdvisory(task.advisory, task.screenshot_warning);
        renderAgentRunInfo(task.agent_runtime);
        if (Array.isArray(task.logs)) updateLogs(task.logs);

        if (task.status === 'completed') {
            if (byId('agentResultPanel')) byId('agentResultPanel').hidden = !task.agent_runtime;
            if (task.agent_runtime) showAgentRunSummary(task);
            await completeTask(task.result, task.elapsed_seconds, task.download_name);
            if (requestSequence !== recentTaskRequestSequence || taskId !== currentTaskId || selectedView !== taskViewSequence) return;
        } else if (window.syncTranscriptReview?.(taskId, task)) {
            schedulePoll(taskId);
        } else if (task.status === 'awaiting_agent') {
            showAgentWaiting(task);
            schedulePoll(taskId, 0);
        } else if (['queued', 'processing', 'cancelling'].includes(task.status)) {
            setTaskActive(true);
            startElapsedTimer(task.elapsed_seconds);
            setTaskState('processing', TASK_STATUS_LABELS[task.status]);
            byId('networkState').textContent = task.status === 'cancelling'
                ? '等待当前步骤停止'
                : '已连接到运行任务';
            schedulePoll(taskId, 0);
        } else {
            showStoppedTask(task);
        }
        byId('progressArea').scrollIntoView({ behavior: window.AIVideoNoteMotion?.scrollBehavior() || 'smooth', block: 'start' });
    } catch (error) {
        if (requestSequence !== recentTaskRequestSequence || selectedView !== taskViewSequence) return;
        showToast(error.message, 'error');
        loadRecentTasks(true);
    }
}

function showStoppedTask(task) {
    stopElapsedTimer(task.elapsed_seconds);
    setTaskActive(false);
    const cancelled = task.status === 'cancelled';
    setTaskState(cancelled ? 'cancelled' : 'failed', TASK_STATUS_LABELS[task.status] || '已停止');
    byId('taskError').hidden = false;
    byId('taskErrorMessage').textContent = task.error || (cancelled
        ? '任务已取消；可以复用已有中间文件重新生成。'
        : '任务未完成；可以从已有中间文件继续。');
    byId('restartBtn').disabled = false;
    byId('networkState').textContent = '历史任务';
    setSubmitButton(false, '继续生成');
}

function isLocalSource() { return ['local', 'audio'].includes(byId('sourceType').value); }

function toggleSourceType() {
    byId('localFile').accept = byId('sourceType').value === 'audio' ? 'audio/*,.mp3,.m4a,.wav,.flac,.aac,.ogg,.opus,.wma' : 'video/*,audio/*';
    const isLocal = isLocalSource();
    byId('urlField').hidden = isLocal;
    byId('fileField').hidden = !isLocal;
    updateFileInfo();
    updateBiliHint();
    if (isLocal) {
        hideBiliPagesPanel();
    } else {
        scheduleBiliPagesPreview();
    }
}

// ---- 输出类型：生成笔记 / 仅转录字幕 ----
// 仅转录仍提交到 /api/summarize + output=transcript，而不是 /api/transcribe：
// 后者是 agent 取原料的路线，任务不进网页端「最近任务」。
function selectedOutputMode() {
    return document.querySelector('input[name="outputMode"]:checked')?.value || 'note';
}

function isTranscriptMode() {
    return selectedOutputMode() === 'transcript';
}

function selectedNoteFormat() {
    return byId('formatSelect').value || 'markdown';
}

function isObsidianNote() {
    return !isTranscriptMode() && selectedNoteFormat() === 'obsidian';
}

function handleNoteFormatChange() {
    applyOutputMode();
    persistGlobals();
}

function handleOutputModeChange() {
    // Native grid interpolation shares one layout update; no cloned panels,
    // crossfades, or competing requestAnimationFrame loops.
    applyOutputMode();
    persistGlobals();
}

// 程序化切换输出类型（例如从字幕稿转去生成笔记），保持界面与偏好和单选一致
function setOutputMode(mode) {
    const input = document.querySelector(`input[name="outputMode"][value="${mode}"]`);
    if (!input || input.checked) return;
    input.checked = true;
    window.AIVideoNoteMotion?.cancelPanel(byId('sourceModeContent'));
    applyOutputMode();
    persistGlobals();
}

function normalizeAiCommentaryPosition(position, legacyEnabled = false) {
    if (['none', 'start', 'end'].includes(position)) return position;
    return legacyEnabled === true || legacyEnabled === 'true' ? 'end' : 'none';
}

function selectedAiCommentaryPosition() {
    return normalizeAiCommentaryPosition(byId('aiCommentary').value);
}

function updateSummaryStyleHint() {
    const hints = {
        faithful: '按主题整理原视频的要点、例子与细节，去掉口头重复；不承诺逐字记录。',
        concise: '适合先快速了解视频，提炼主要观点与结论，省略大量细节。',
        detailed: '先整理视频内容，再单独追加 AI 点评；点评属于模型判断，不代表视频原话。',
        learning: '适合课程与自学：以文字讲清概念、原理和例子，再附复习问题；图片辅助理解。',
        tutorial: '适合跟着操作：用文字说明每步怎么做、参数、结果及注意事项；图片辅助对照。',
        cards: '适合积累知识：把概念、结论和方法拆成可独立阅读的小卡片。',
        outline: '适合梳理结构：输出 Markdown 层级大纲，展现主题及其分支关系。',
        meeting: '适合会议和讨论：提取议题、明确结论及待办，不编造负责人和日期。'
    };
    byId('summaryStyleHint').textContent = (hints[byId('summaryStyle').value] || hints.faithful) + ' 图片数量由配图设置独立控制，风格不决定是否有图。';
}

function updateGenerationSettings() {
    const agent = isAgentMode();
    if (byId('reasoningEffortField')) byId('reasoningEffortField').hidden = agent;
    for (const id of ['apiModelSettings', 'apiConnectionSettings']) {
        const element = byId(id);
        if (element) element.hidden = agent;
    }
    const group = byId('agentExecutionSettings');
    if (group) group.hidden = !agent;
    const executionHint = byId('agentExecutionHint');
    if (executionHint) executionHint.textContent = window.AIVideoNoteI18n?.translate(byId('agentExecution')?.value === 'auto' ? '在网页选择参数、提交素材并下载笔记，软件自动调用 agent 客户端。使用前需启动所选客户端并完成登录，可以挂在后台；无需在 agent 中对话，也无需连接 MCP。' : '在 Agent 聊天中发送视频链接；接入方法见下方「Agent 聊天接入」。') || (byId('agentExecution')?.value === 'auto' ? '在网页选择参数、提交素材并下载笔记，软件自动调用 agent 客户端。使用前需启动所选客户端并完成登录，可以挂在后台；无需在 agent 中对话，也无需连接 MCP。' : '在 Agent 聊天中发送视频链接；接入方法见下方「Agent 聊天接入」。');
    const cli = byId('agentCliSettings');
    if (cli) cli.hidden = byId('agentExecution')?.value !== 'auto';
    const custom = byId('agentCommandField');
    if (custom) custom.hidden = byId('agentProvider')?.value !== 'custom';
    const modelSettings = byId('agentModelSettings');
    if (modelSettings) modelSettings.hidden = false;
    if (byId('agentEffortField')) byId('agentEffortField').hidden = byId('agentProvider')?.value !== 'codex';
    const connection = byId('agentChatConnectionSection');
    if (connection) {
        connection.hidden = !agent || byId('agentExecution')?.value === 'auto';
        connection.open = agent && !connection.hidden;
    }
}

function applyOutputMode() {
    updateGenerationSettings();
    const transcript = isTranscriptMode();
    for (const id of ['sidebarNoteSettings', 'sourceNoteSettings', 'sidebarScreenshotSettings']) {
        const region = byId(id);
        if (region) region.inert = transcript;
    }
    toggleScreenshotSettings();
    updateObsidianSettings();
    if (byId('reasoningEffortField')) byId('reasoningEffortField').hidden = isAgentMode();
    const hint = byId('outputModeHint');
    if (hint) {
        // Preserve natural text height while its wrapper opens and closes.
        const hintRegion = byId('transcriptModeHintRegion');
        if (hintRegion) hintRegion.inert = !transcript;
        hint.textContent = '仅转录字幕：识别后会调用 AI 校对，模型与接入方式可在左侧「AI接入设置」配置。有修改建议时等待确认，再导出带时间轴的字幕。';
    }
    toggleImageLayout();
    toggleScreenshotSettings();
    resetSubmitLabel();
}

function updateObsidianSettings() {
    const visible = selectedNoteFormat() === "obsidian";
    byId("noteSaveSettings").hidden = visible;
    byId('obsidianSettings').hidden = !visible;
    const mode = byId('obsidianAttachmentMode').value;
    const subfolder = mode === 'note_subfolder';
    byId('obsidianAttachmentFolderField').hidden = !subfolder && mode !== 'specified_folder';
    byId('obsidianAttachmentFolderLabel').textContent = subfolder ? '附件子文件夹' : '附件文件夹路径';
    byId('obsidianAttachmentFolderHint').textContent = subfolder
        ? '相对于笔记所在文件夹。' : '相对于仓库根目录，例如 pictures 或 assets/images。';
    const vault = byId('obsidianVault').value.trim();
    const separator = vault.includes('\\') ? '\\' : '/';
    const join = (base, relative) => relative.trim()
        ? `${base.replace(/[\\/]+$/, '')}${separator}${relative.trim().replace(/[\\/]/g, separator)}` : base;
    const notes = join(vault, byId('obsidianNoteFolder').value);
    const images = mode === 'vault_root' ? vault : mode === 'note_folder' ? notes
        : join(subfolder ? notes : vault, byId('obsidianAttachmentFolder').value);
    byId('obsidianPathPreview').textContent = vault
        ? `笔记保存位置：${notes}\n图片保存位置：${images}` : '填写仓库位置后显示笔记和图片的保存路径。';
}

let biliHintDebounce = null;
let biliPromptDismissed = false;
let douyinLoginTimer = null;
let douyinCookies = null;

function isBilibiliUrl(value) {
    return /bilibili\.com|b23\.tv|BV[0-9A-Za-z]{10}/.test(value);
}

function isDouyinUrl(value) {
    return /(^|\.)douyin\.com|iesdouyin\.com/.test(value);
}

function hasBiliCredentials() {
    return Boolean(
        biliSavedLogin || byId('sessdata').value.trim()
        || byId('biliJct').value.trim()
        || byId('buvid3').value.trim()
    );
}

// ---- Whisper 未缓存确认的降噪逻辑 ----
// 1) 明显走平台字幕、用不到 Whisper 的提交不弹；
// 2) 已确认过一次下载的模型长期不再弹（localStorage 记忆；下拉框仍保留“未缓存”提示）。
function taskWillLikelyUseSubtitles(request) {
    if (!request || ['local', 'audio'].includes(request.sourceType)) return false;
    if (isBilibiliUrl(request.videoUrl) && hasBiliCredentials()) return true;
    if (isDouyinUrl(request.videoUrl) && douyinCookies) return true;
    return false;
}

function whisperDownloadConfirmed(modelId) {
    return prefs.confirmations.whisper_download[modelId] === true;
}

function rememberWhisperDownloadConfirm(modelId) {
    prefs.confirmations.whisper_download[modelId] = true;
    persistPrefs();
}

function updateBiliHint(forceShow = false) {
    const value = byId('videoUrl').value.trim();
    const notLocal = !isLocalSource();
    const isBili = notLocal && isBilibiliUrl(value);
    const isDouyin = notLocal && isDouyinUrl(value);
    const hint = byId('biliHint');
    const actions = byId('biliHintActions');
    const biliScanButton = byId('biliHintScanBtn');
    const biliManualButton = byId('biliHintManualBtn');
    // 兼容旧版缓存的 HTML：提示条元素缺失时静默跳过，不影响其他功能
    if (!hint || !actions || !biliScanButton || !biliManualButton) return;
    const douyinButton = byId('douyinLoginBtn');
    const douyinStatus = byId('douyinLoginStatus');
    if (douyinButton) douyinButton.hidden = true;
    if (douyinStatus) douyinStatus.textContent = '';
    if (forceShow || (isBili && !hasBiliCredentials() && !biliPromptDismissed)) {
        byId('biliHintTitle').textContent = '检测到 B 站链接';
        byId('biliHintDesc').textContent = '填写访问凭据可优先使用 AI 字幕，无需等待本地转写。';
        actions.hidden = false;
        biliScanButton.hidden = false;
        biliManualButton.hidden = false;
        hint.hidden = false;
    } else if (forceShow || (isDouyin && !biliPromptDismissed)) {
        byId('biliHintTitle').textContent = '检测到抖音链接';
        byId('biliHintDesc').textContent = '先尝试直接解析；如果需要验证，可在本机浏览器完成后重试。';
        actions.hidden = false;
        biliScanButton.hidden = true;
        biliManualButton.hidden = true;
        if (douyinButton) douyinButton.hidden = false;
        hint.hidden = false;
    } else {
        hint.hidden = true;
    }
}

function revealBiliCredentials() {
    updateBiliHint(true);
    const section = byId('credentialsSection');
    section.open = true;
    section.scrollIntoView({ behavior: window.AIVideoNoteMotion?.scrollBehavior() || 'smooth', block: 'nearest' });
    byId('sessdata').focus();
}

function dismissBiliHint() {
    biliPromptDismissed = true;
    updateBiliHint();
}

// ---- B 站分 P 勾选面板 ----
// 多分 P 链接在提交前显式决定转写范围：**默认一个都不勾**，要用户主动勾。
// 42 P 的课误点「开始生成」就是几十小时的转写，代价远大于多点几下。
// 只有链接里已经表达过意图时才预勾（?p=N 勾该 P）。勾选结果总是随请求下发
// bilibili_pages（全选也下发），否则「URL 带 ?p=N + 勾了全部」会落回只转该 P。
// 预览是尽力而为：接口失败就藏面板，提交行为退回原来的 URL 规则。
let biliPagesDebounce = null;
let biliPagesToken = 0;
let biliPagesCurrent = null; // { url, payload }
const biliPagesCache = new Map();

function scheduleBiliPagesPreview() {
    window.clearTimeout(biliPagesDebounce);
    biliPagesDebounce = window.setTimeout(loadBiliPagesPanel, 500);
}

async function loadBiliPagesPanel() {
    const notLocal = !isLocalSource();
    const normalized = notLocal ? normalizeVideoInput(byId('videoUrl').value) : null;
    if (!normalized || !isBilibiliUrl(normalized)) {
        hideBiliPagesPanel();
        return;
    }
    if (biliPagesCurrent && biliPagesCurrent.url === normalized) return;
    if (!biliPagesCache.has(normalized)) {
        const token = ++biliPagesToken;
        const payload = await fetchBiliPages(normalized);
        if (token !== biliPagesToken) return;
        if (!payload) {
            hideBiliPagesPanel();
            return;
        }
        biliPagesCache.set(normalized, payload);
    }
    renderBiliPagesPanel(normalized, biliPagesCache.get(normalized));
}

async function fetchBiliPages(url) {
    const body = { video_url: url };
    const sessdata = byId('sessdata').value.trim();
    if (sessdata) {
        body.bilibili_cookie = {
            sessdata,
            bili_jct: byId('biliJct').value.trim(),
            buvid3: byId('buvid3').value.trim()
        };
    }
    try {
        const response = await fetch(`${API_BASE}/bili-pages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!response.ok) return null;
        return await response.json();
    } catch {
        return null;
    }
}

function renderBiliPagesPanel(url, payload) {
    const panel = byId('biliPagesPanel');
    const list = byId('biliPagesList');
    if (!panel || !list) return;
    if (!payload || !Array.isArray(payload.pages) || payload.pages.length <= 1) {
        hideBiliPagesPanel();
        return;
    }
    // 默认勾选优先级：上次任务显式勾选的分 P > 链接 ?p=N > 不勾。
    // 重新处理同一视频时沿用上次范围，不用重新勾一遍（后端按链接匹配返回）。
    const previousPages = Array.isArray(payload.previous_pages)
        ? payload.previous_pages.map(Number).filter(Number.isInteger)
        : [];
    const previousSet = new Set(previousPages);
    const pinned = urlPageParam(url);
    list.innerHTML = '';
    payload.pages.forEach((page) => {
        const item = document.createElement('label');
        item.className = 'bili-page-item';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = String(page.page);
        checkbox.checked = previousSet.size
            ? previousSet.has(page.page)
            : page.page === pinned;
        checkbox.addEventListener('change', updateBiliPagesSummary);
        const index = document.createElement('span');
        index.className = 'bili-page-index';
        index.textContent = `P${page.page}`;
        const name = document.createElement('span');
        name.className = 'bili-page-name';
        name.textContent = page.part;
        name.title = page.part;
        item.append(checkbox, index, name);
        const duration = formatPageDuration(page.duration);
        if (duration) {
            const time = document.createElement('span');
            time.className = 'bili-page-duration';
            time.textContent = duration;
            item.append(time);
        }
        list.append(item);
    });
    biliPagesCurrent = { url, payload };
    panel.hidden = false;
    updateBiliPagesSummary();
}

function hideBiliPagesPanel() {
    const panel = byId('biliPagesPanel');
    if (panel) panel.hidden = true;
    biliPagesCurrent = null;
}

function setBiliPagesSelection(mode) {
    const boxes = byId('biliPagesList').querySelectorAll('input[type="checkbox"]');
    boxes.forEach((box) => {
        if (mode === 'invert') {
            box.checked = !box.checked;
        } else {
            box.checked = mode === 'all';
        }
    });
    updateBiliPagesSummary();
}

function updateBiliPagesSummary() {
    const panel = byId('biliPagesPanel');
    if (!panel || panel.hidden || !biliPagesCurrent) return;
    const durations = new Map(
        (biliPagesCurrent.payload.pages || []).map((page) => [
            Number(page.page),
            Number(page.duration) || 0,
        ])
    );
    const rows = Array.from(byId('biliPagesList').querySelectorAll('.bili-page-item'));
    let selected = 0;
    let selectedSeconds = 0;
    let totalSeconds = 0;
    rows.forEach((row) => {
        const box = row.querySelector('input[type="checkbox"]');
        const seconds = durations.get(Number(box.value)) || 0;
        totalSeconds += seconds;
        row.classList.toggle('is-checked', box.checked);
        if (box.checked) {
            selected += 1;
            selectedSeconds += seconds;
        }
    });
    const scope = selected === 0
        ? '未勾选任何分 P'
        : selected === rows.length
            ? `已全选，合计 ${formatPageDuration(totalSeconds) || '未知'}`
            : `已选 ${selected}/${rows.length} 个，合计 ${formatPageDuration(selectedSeconds) || '未知'}`;
    byId('biliPagesSummary').textContent = `共 ${rows.length} 个分 P · ${scope}`;
}

function biliPagesPanelVisible() {
    const panel = byId('biliPagesPanel');
    return Boolean(panel && !panel.hidden);
}

function checkedBiliPages() {
    return Array.from(byId('biliPagesList').querySelectorAll('input[type="checkbox"]:checked'))
        .map((box) => parseInt(box.value, 10))
        .filter((value) => Number.isFinite(value));
}

function urlPageParam(url) {
    try {
        const value = parseInt(new URL(url).searchParams.get('p'), 10);
        return Number.isFinite(value) && value > 0 ? value : 0;
    } catch {
        return 0;
    }
}

function formatPageDuration(seconds) {
    const total = Math.round(Number(seconds) || 0);
    if (total <= 0) return '';
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h > 0
        ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
        : `${m}:${String(s).padStart(2, '0')}`;
}

let biliLoginTimer = null;
let biliLoginGeneration = 0;
let biliLoginPollGeneration = null;
let biliSavedLogin = false;

function initSelectPickers() {
    if (!window.CSS?.supports('appearance', 'base-select')) return;
    const prepare = (event) => {
        const select = event.target?.closest?.('select');
        if (!select || select.disabled || select.multiple || select.hasAttribute('size')) return;
        prepareSelectPicker(select);
    };
    document.addEventListener('pointerdown', prepare);
    document.addEventListener('keydown', (event) => {
        if (['ArrowDown', 'ArrowUp', ' ', 'Enter'].includes(event.key)) prepare(event);
    });
}

function prepareSelectPicker(select) {
    let bounds = select.getBoundingClientRect();
    // Keep a useful amount of space below the trigger before the browser opens it.
    if (window.innerHeight - bounds.bottom < 172 && !select.matches(':open')) {
        select.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
        bounds = select.getBoundingClientRect();
    }
    const available = Math.max(44, window.innerHeight - bounds.bottom - 12);
    select.style.setProperty('--select-picker-space', `${available}px`);
}

function initBiliLogin() {
    bindListener('biliLoginBtn', 'click', startBiliLogin);
    bindListener('biliLoginCancelBtn', 'click', cancelBiliLogin);
    bindListener('biliLogoutBtn', 'click', logoutBili);
    bindListener('biliSaveLoginBtn', 'click', () => saveBiliLogin().catch(error => showToast(error.message, 'error')));
    refreshBiliSavedLogin();
}

async function startBiliLogin() {
    const section = byId('credentialsSection');
    section.open = true;
    section.scrollIntoView({ behavior: window.AIVideoNoteMotion?.scrollBehavior() || 'smooth', block: 'nearest' });
    if (biliLoginTimer || byId('biliLoginBtn').disabled) {
        if (!byId('biliQrPanel').hidden) {
            byId('biliQrPanel').scrollIntoView({ behavior: window.AIVideoNoteMotion?.scrollBehavior() || 'smooth', block: 'nearest' });
        }
        return;
    }
    const generation = ++biliLoginGeneration;
    byId('biliLoginBtn').disabled = true;
    byId('biliLoginStatus').textContent = '正在获取二维码…';
    try {
        const response = await fetch(`${API_BASE}/bili-login/start`, { method: 'POST' });
        const data = await readResponse(response, '启动扫码登录失败');
        if (generation !== biliLoginGeneration) return;
        if (!data.ok) {
            stopBiliLoginPolling();
            showToast(data.error || data.message || '启动失败', 'error');
            return;
        }
        if (data.state === 'ready' && data.cookies) {
            // 该浏览器配置已登录过：直接回填，无需等待弹窗
            fillBiliCredentials(data.cookies);
            await saveBiliLogin();
            await cancelBiliLogin();
            await refreshBiliSavedLogin();
            return;
        }
        byId('biliQrImage').src = data.qr_image || '';
        byId('biliQrPanel').hidden = !data.qr_image;
        if (data.qr_image) {
            byId('biliQrPanel').scrollIntoView({ behavior: window.AIVideoNoteMotion?.scrollBehavior() || 'smooth', block: 'nearest' });
        }
        byId('biliLoginStatus').textContent = data.state === 'waiting' && data.qr_image ? '' : (data.message || '');
        byId('biliLoginCancelBtn').hidden = false;
        byId('biliLoginBtn').disabled = true;
        biliLoginTimer = window.setInterval(pollBiliLogin, 2000);
        pollBiliLogin();
    } catch (error) {
        if (generation !== biliLoginGeneration) return;
        stopBiliLoginPolling();
        showToast(`启动失败：${error.message}`, 'error');
    }
}

function fillBiliCredentials(cookies) {
    byId('sessdata').value = cookies.sessdata || '';
    byId('biliJct').value = cookies.bili_jct || '';
    byId('buvid3').value = cookies.buvid3 || '';
    const section = byId('credentialsSection');
    section.open = true;
    section.scrollIntoView({ behavior: window.AIVideoNoteMotion?.scrollBehavior() || 'smooth', block: 'nearest' });
    showToast('B 站凭据已导入并填入下方表单', 'success');
    updateBiliHint();
}

async function pollBiliLogin() {
    const generation = biliLoginGeneration;
    if (biliLoginTimer === null || biliLoginPollGeneration === generation) return;
    biliLoginPollGeneration = generation;
    try {
        const response = await fetch(`${API_BASE}/bili-login/status`, { cache: 'no-store' });
        const data = await readResponse(response, '读取登录状态失败');
        if (generation !== biliLoginGeneration) return;
        byId('biliLoginStatus').textContent = data.state === 'waiting' && !byId('biliQrPanel').hidden ? '' : (data.message || '');
        if (data.state === 'ready' && data.cookies) {
            stopBiliLoginPolling();
            fillBiliCredentials(data.cookies);
            await saveBiliLogin();
            await cancelBiliLogin();
            await refreshBiliSavedLogin();
        } else if (['failed', 'timeout'].includes(data.state)) {
            stopBiliLoginPolling();
            byId('biliLoginStatus').textContent = data.message || '导入失败';
            showToast(data.message || '导入失败', 'error');
        }
    } catch {
        if (generation !== biliLoginGeneration) return;
        // 瞬时失败不停止轮询，避免扫码成功后错过回填
        byId('biliLoginStatus').textContent = '状态读取失败，正在重试…';
    } finally {
        if (biliLoginPollGeneration === generation) biliLoginPollGeneration = null;
    }
}

function stopBiliLoginPolling() {
    biliLoginGeneration += 1;
    if (biliLoginTimer !== null) {
        window.clearInterval(biliLoginTimer);
        biliLoginTimer = null;
    }
    byId('biliLoginBtn').disabled = false;
    byId('biliLoginCancelBtn').hidden = true;
    byId('biliQrPanel').hidden = true;
    byId('biliQrImage').removeAttribute('src');
    byId('biliLoginStatus').textContent = '';
}

async function cancelBiliLogin() {
    stopBiliLoginPolling();
    try {
        await fetch(`${API_BASE}/bili-login/cancel`, { method: 'POST' });
    } catch {
        // 后端不可达时忽略，浏览器窗口由用户自行关闭
    }
}

async function startDouyinLogin() {
    if (douyinLoginTimer) return;
    const button = byId('douyinLoginBtn');
    const status = byId('douyinLoginStatus');
    button.disabled = true;
    status.textContent = '正在打开抖音浏览器…';
    try {
        const response = await fetch(`${API_BASE}/douyin-login/start`, { method: 'POST' });
        const data = await readResponse(response, '启动抖音浏览器失败');
        if (!data.ok) {
            status.textContent = data.error || data.message || '启动失败';
            button.disabled = false;
            return;
        }
        status.textContent = data.message || '请在弹出的浏览器中完成登录或验证';
        douyinLoginTimer = window.setInterval(pollDouyinLogin, 2000);
        pollDouyinLogin();
    } catch (error) {
        status.textContent = `启动失败：${error.message}`;
        button.disabled = false;
    }
}

async function pollDouyinLogin() {
    try {
        const response = await fetch(`${API_BASE}/douyin-login/status`, { cache: 'no-store' });
        const data = await readResponse(response, '读取抖音浏览器状态失败');
        byId('douyinLoginStatus').textContent = data.message || '';
        if (data.state === 'ready' && data.cookies) {
            douyinCookies = data.cookies;
            stopDouyinLoginPolling();
            showToast('抖音浏览器验证完成，下一次提交会携带本机登录态', 'success');
        } else if (['failed', 'timeout'].includes(data.state)) {
            stopDouyinLoginPolling();
        }
    } catch {
        byId('douyinLoginStatus').textContent = '状态读取失败，正在重试…';
    }
}

function stopDouyinLoginPolling() {
    if (douyinLoginTimer !== null) {
        window.clearInterval(douyinLoginTimer);
        douyinLoginTimer = null;
    }
    byId('douyinLoginBtn').disabled = false;
}

function renderHistoryFileName() {
    const file = byId('localFile')?.files?.[0];
    const name = isLocalSource() && !file && sourceTaskId === currentTaskId ? sourceFilename : '';
    byId('fileField')?.classList?.toggle('has-history-file', Boolean(name));
    const label = byId('localFileName');
    if (label) { label.textContent = name; label.title = name; }
    return name;
}

function updateFileInfo() {
    const historyName = renderHistoryFileName();
    if (!isLocalSource()) {
        byId('fileInfo').textContent = '支持常见视频链接；平台字幕可用时优先读取';
        return;
    }
    const file = byId('localFile').files[0];
    byId('fileInfo').textContent = file
        ? `${file.name} · ${formatBytes(file.size)}`
        : historyName || '支持常见视频格式及 MP3、WAV、M4A、FLAC 等音频；不限制文件大小'
}

function timedScreenshotEnabled() { return byId('timedScreenshots').checked; }
function screenshotsEnabled() { if (byId('sourceType').value === 'audio') return false; return timedScreenshotEnabled() || selectedScreenshotDensity() !== 'none'; }

function updateVisionCheckbox() {
    const blocked = isTranscriptMode() || byId('sourceType').value === 'audio' || timedScreenshotEnabled() || selectedScreenshotDensity() === 'none';
    byId('visionScreenshotSettings').hidden = byId('sourceType').value === 'audio';
    byId('visionCapabilityHint').dataset.state = visionCapability;
    byId('retryVisionCheckBtn').hidden = isAgentMode();
    byId('retryVisionCheckBtn').disabled = isTaskActive || isSubmitting || blocked || isAgentMode();
    byId('visionScreenshots').disabled = isTaskActive || isSubmitting || blocked || (!isAgentMode() && visionCapability !== 'supported');
    byId('visionScreenshots').checked = !blocked && (isAgentMode() || visionCapability === 'supported') && visionPreference;
    if (byId('autoCropScreenshotSettings')) byId('autoCropScreenshotSettings').hidden = byId('sourceType').value === 'audio';
    if (byId('autoCropScreenshots')) {
        byId('autoCropScreenshots').disabled = isTaskActive || isSubmitting || !byId('visionScreenshots').checked || byId('visionScreenshots').disabled;
        byId('autoCropScreenshots').checked = !blocked && byId('visionScreenshots').checked && cropPreference;
    }
}

function scheduleVisionCheck() {
    if (isAgentMode()) {
        if (visionCheckTimer !== null) window.clearTimeout(visionCheckTimer);
        ++visionCheckToken; visionCheckTimer = null; lastVisionIdentity = "";
        visionCapability = "unknown"; updateVisionCheckbox();
        byId("visionCapabilityHint").textContent = "Agent 模式需使用支持图片的客户端模型。";
        return;
    }
    updateVisionCheckbox();
    if (isTranscriptMode() || byId('sourceType').value === 'audio' || timedScreenshotEnabled() || selectedScreenshotDensity() === 'none') {
        if (visionCheckTimer !== null) window.clearTimeout(visionCheckTimer);
        visionCheckTimer = null;
        lastVisionIdentity = '';
        ++visionCheckToken;
        visionCapability = 'unknown';
        byId('visionCapabilityHint').textContent = timedScreenshotEnabled() ? '定时截图不启用视觉核对；请选择 AI 配图后检测' : '选择 AI 配图后检测所选模型的视觉能力';
        updateVisionCheckbox();
        return;
    }
    const model = getSelectedModelConfig();
    const key = typedApiKey();
    const identity = JSON.stringify([model?.provider, model?.baseUrl, model?.model, key, hasReusableKey()]);
    if (identity === lastVisionIdentity) return;
    if (visionCheckTimer !== null) window.clearTimeout(visionCheckTimer);
    lastVisionIdentity = identity;
    const token = ++visionCheckToken;
    visionCapability = 'unknown';
    updateVisionCheckbox();
    const hint = byId('visionCapabilityHint');
    if (!model?.model || !model?.baseUrl || (!key && !hasReusableKey())) {
        hint.textContent = '请先配置所选模型及 API Key，才能检测视觉能力';
        return;
    }
    hint.textContent = '正在检测所选模型的视觉能力…';
    visionCheckTimer = window.setTimeout(async () => {
        visionCheckTimer = null;
        const force = visionForceRefresh;
        visionForceRefresh = false;
        try {
            const response = await fetchWithTimeout(`${API_BASE}/llm-vision${force ? '?refresh=true' : ''}`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({model_type: model.provider, base_url: model.baseUrl, model: model.model, ...(key ? {api_key: key} : {})})
            }, 30000);
            const data = await readResponse(response, '视觉能力检测失败');
            if (token !== visionCheckToken) return;
            visionCapability = data.supported === true ? 'supported' : (data.state || 'unknown');
            hint.textContent = data.message || '暂时无法确认所选模型是否支持视觉识别';
        } catch {
            if (token !== visionCheckToken) return;
            visionCapability = 'error';
            hint.textContent = '视觉能力检测失败，请检查连接后重新检测';
        }
        updateVisionCheckbox();
    }, 800);
}

function toggleScreenshotSettings() {
    const noPictures = byId('sourceType').value === 'audio';
    byId('screenshotDensityField').hidden = noPictures;
    byId('timedScreenshotSettings').hidden = noPictures;
    scheduleVisionCheck();
    byId('screenshotIntervalField').hidden = !timedScreenshotEnabled();
    byId('screenshotDensity').disabled = isTaskActive || isSubmitting || timedScreenshotEnabled();
    byId('screenshotVideoSettings').hidden = !screenshotsEnabled() || isLocalSource();
    updateFileInfo();
}

const WHISPER_MODEL_SIZES = {
    base: '~142MB',
    small: '~466MB',
    medium: '~1.5GB',
    'large-v3': '~3.1GB',
    turbo: '~1.6GB',
    'belle-turbo-zh': '~0.8GB'
};

let whisperFallbackNotified = false;

function modelOptionLabel(baseLabel, modelId, status, compatible = true, runtimeStatus = null) {
    const size = WHISPER_MODEL_SIZES[modelId];
    const sizeLabel = size ? `，约 ${size.replace(/^~/, '')}` : '';
    let label;
    if (status === 'cached') label = `${baseLabel}（已安装${sizeLabel}）`;
    else if (status === 'incomplete') label = `${baseLabel}（下载未完成${sizeLabel}）`;
    else if (status === 'missing') label = `${baseLabel}（未安装${sizeLabel}）`;
    else if (status === 'unsupported') label = `${baseLabel}（请重启软件以更新状态${sizeLabel}）`;
    else label = size ? `${baseLabel}（约 ${size.replace(/^~/, '')}）` : baseLabel;
    if (!compatible || status === 'incompatible') return `${label} · 请关闭 GPU 加速后选择`;
    if (status === 'cached' && ['missing', 'incomplete'].includes(runtimeStatus)) return `${label} · 当前加速格式需下载`;
    return label;
}

function snapshotWhisperLabels() {
    Array.from(byId('whisperModel').options).forEach((option) => {
        option.dataset.baseLabel = option.dataset.baseLabel || option.textContent;
        option.textContent = modelOptionLabel(option.dataset.baseLabel, option.value, 'unknown');
    });
}

function selectedGpuBackend() {
    return byId('useGpu').checked ? 'auto' : 'cuda';
}

function updateGpuSettings() {
    refreshGpuDetected();
    Array.from(byId('whisperModel').options).forEach((option) => {
        option.disabled = false;
        option.textContent = modelOptionLabel(option.dataset.baseLabel || option.textContent, option.value, 'unknown', !option.disabled);
    });
}

async function refreshWhisperModelHints() {
    const backend = selectedGpuBackend();
    try {
        const response = await fetch(`${API_BASE}/whisper-models?gpu_backend=${backend}`, { cache: 'no-store' });
        const data = await readResponse(response, '读取模型状态失败');
        if (backend !== selectedGpuBackend()) return;
        renderModelStorage(data);
        const returnedIds = new Set((data.models || []).map(model => model.id));
        const outdated = Array.from(byId('whisperModel').options).some(option => !returnedIds.has(option.value));
        byId('modelStatusNotice').hidden = !outdated;
        byId('modelStatusHint').textContent = outdated ? '当前后台版本较旧，请通过 start.bat 重新启动软件。' : '';
        const byIdMap = {};
        (data.models || []).forEach((model) => { byIdMap[model.id] = model; });
        Array.from(byId('whisperModel').options).forEach((option) => {
            const model = byIdMap[option.value];
            const baseLabel = option.dataset.baseLabel || option.textContent;
            const status = model ? (model.installation_status || (model.status === 'unsupported' ? 'unknown' : model.status)) : 'unsupported';
            option.disabled = model ? (model.compatible === false || model.status === 'unsupported') : option.disabled;
            option.dataset.status = model?.status || 'unknown';
            option.textContent = modelOptionLabel(baseLabel, option.value, status, !option.disabled && model?.compatible !== false, model?.status) + (model?.acceleration_note ? ` · ${model.acceleration_note}` : '');
        });
    } catch {
        if (backend !== selectedGpuBackend()) return;
        byId('modelStatusNotice').hidden = false;
        byId('modelStatusHint').textContent = '模型下载状态读取失败，请点击重新读取。';
        // Never keep a stale "downloaded" label after status refresh fails.
        Array.from(byId('whisperModel').options).forEach((option) => {
            option.dataset.status = 'unknown';
            option.textContent = modelOptionLabel(option.dataset.baseLabel || option.textContent, option.value, 'unknown', !option.disabled);
        });
    }
}

let modelStorageState = null;
let modelStorageBusy = false;

function renderModelStorage(data) {
    modelStorageState = data;
    byId('modelDirectoryCurrent').textContent = data.directory || '未读取到路径';
    byId('modelDirectoryDefault').textContent = data.default_directory || '未读取到路径';
    const input = byId('modelDirectory');
    if (!input.dataset.loaded) {
        input.value = data.directory === data.default_directory ? '' : (data.directory || '');
        input.dataset.loaded = 'true';
    }
    renderSelectedModelPaths();
}

function renderSelectedModelPaths() {
    const model = modelStorageState?.models?.find((item) => item.id === byId('whisperModel').value);
    const paths = model?.managed_paths || [];
    byId('selectedModelPaths').textContent = paths.length
        ? `所选模型文件夹：\n${paths.join('\n')}`
        : (model?.status === 'cached' ? '所选模型使用其他位置的共享缓存，请在原位置管理。' : '当前下载文件夹内没有所选模型。');
    byId('deleteModelBtn').disabled = modelStorageBusy || isTaskActive || isSubmitting || !paths.length;
    ['modelDirectory', 'saveModelDirectoryBtn', 'resetModelDirectoryBtn'].forEach((id) => {
        byId(id).disabled = modelStorageBusy || isTaskActive || isSubmitting;
    });
}

async function saveModelDirectory(reset) {
    if (modelStorageBusy || isTaskActive || isSubmitting) return;
    modelStorageBusy = true;
    renderSelectedModelPaths();
    try {
        const response = await fetch(`${API_BASE}/whisper-models/directory`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ directory: reset ? '' : byId('modelDirectory').value.trim() })
        });
        const data = await readResponse(response, '保存模型位置失败');
        byId('modelDirectory').value = data.directory === data.default_directory ? '' : data.directory;
        renderModelStorage(data);
        await refreshWhisperModelHints();
        showToast('模型位置已保存，原位置的文件已保留', 'success');
    } catch (error) {
        showToast(error.message || '保存模型位置失败', 'error');
    } finally {
        modelStorageBusy = false;
        renderSelectedModelPaths();
    }
}

async function deleteSelectedModel() {
    if (modelStorageBusy || isTaskActive || isSubmitting) return;
    const model = byId('whisperModel').value;
    const paths = modelStorageState?.models?.find((item) => item.id === model)?.managed_paths || [];
    if (!paths.length || !window.confirm(`确定删除模型「${model}」吗？以下文件夹将被永久删除：\n\n${paths.join('\n')}\n\n再次使用时需要重新下载或导入。`)) return;
    modelStorageBusy = true;
    renderSelectedModelPaths();
    try {
        const response = await fetch(`${API_BASE}/whisper-models/${encodeURIComponent(model)}`, { method: 'DELETE' });
        const data = await readResponse(response, '删除模型失败');
        delete prefs.confirmations.whisper_download[model];
        persistPrefs();
        renderModelStorage(data);
        showToast('所选模型已删除', 'success');
    } catch (error) {
        showToast(error.message || '删除模型失败', 'error');
    } finally {
        modelStorageBusy = false;
        await refreshWhisperModelHints();
        renderSelectedModelPaths();
    }
}

function formatBytes(bytes) {
    if (!bytes) return '0 MB';
    return `${(bytes / 1024 / 1024).toFixed(bytes > 10 * 1024 * 1024 ? 0 : 1)} MB`;
}

// ---- Whisper 模型手动导入（大模型下载慢/反复失败时的替代方案）----
// 约定：把该模型的权重文件放入 workspace/_model_cache/manual/{model}/，
// 后端实时扫描该目录，放入后自动识别为「已缓存」。
// 需要哪些文件、去哪个仓库下载由 /api/whisper-models/manual-folder 给出
// （词表文件名各仓库不统一，vocabulary.txt / vocabulary.json 都算数）。
const MANUAL_IMPORT_POLL_MS = 5000;
const MANUAL_IMPORT_POLL_MAX_TICKS = 24; // 约 2 分钟后停止轮询
let manualImportPollTimer = null;
let manualImportPollRemaining = 0;

function stopManualImportPolling() {
    if (manualImportPollTimer) {
        window.clearInterval(manualImportPollTimer);
        manualImportPollTimer = null;
    }
}

function startManualImportPolling(modelId) {
    stopManualImportPolling();
    manualImportPollRemaining = MANUAL_IMPORT_POLL_MAX_TICKS;
    manualImportPollTimer = window.setInterval(async () => {
        manualImportPollRemaining -= 1;
        if (isTaskActive || manualImportPollRemaining <= 0) {
            stopManualImportPolling();
            return;
        }
        await refreshWhisperModelHints();
        const option = byId('whisperModel').selectedOptions[0];
        if (option && option.value === modelId && option.dataset.status === 'cached') {
            stopManualImportPolling();
            showToast(`已识别手动导入的模型文件（${modelId}）`, 'success');
        }
    }, MANUAL_IMPORT_POLL_MS);
}

async function requestManualImportGuide(modelId, openFolder) {
    const response = await fetch(`${API_BASE}/whisper-models/manual-folder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId, open: openFolder, gpu_backend: selectedGpuBackend() })
    });
    return readResponse(response, '读取模型导入信息失败');
}

async function manualImportWhisperModel() {
    const option = byId('whisperModel').selectedOptions[0];
    if (!option) return;
    const modelId = option.value;
    const modelLabel = (option.dataset.baseLabel || option.textContent).split('（')[0];
    let guide;
    try {
        // 文件清单与仓库地址以后端为准：词表文件名各仓库不统一，turbo 系也不在 Systran 仓
        guide = await requestManualImportGuide(modelId, false);
    } catch (error) {
        showToast(error.message || '读取模型导入信息失败', 'error');
        return;
    }
    showManualImportDialog(modelId, modelLabel, guide);
}

function showManualImportDialog(modelId, modelLabel, guide) {
    document.getElementById('manualImportDialog')?.remove();
    const dialog = document.createElement('dialog');
    dialog.id = 'manualImportDialog';
    dialog.className = 'manual-import-dialog';
    dialog.setAttribute('aria-labelledby', 'manualImportTitle');
    const add = (parent, tag, text, className) => {
        const element = document.createElement(tag);
        element.textContent = text;
        if (className) element.className = className;
        parent.append(element);
        return element;
    };
    const content = add(dialog, 'div', '', 'manual-import-content');
    add(content, 'h3', `手动导入模型 · ${modelLabel}`).id = 'manualImportTitle';
    add(content, 'p', '按下面的步骤下载文件，再放入对应文件夹。所有路径、地址和文件名均可选中复制。');
    const steps = add(content, 'ol', '', 'manual-import-steps');
    [
        '打开对应组件的下载页，在文件列表中找到下方列出的文件。点击文件名进入详情页，再点击下载按钮；不要使用“网页另存为”。',
        '下载清单中的所有必需文件，保留原始文件名和扩展名。清单含“或”时选择其中一种，例如 vocabulary.txt 或 vocabulary.json；不要下载成网页文件，也不要给文件名加上 (1)。',
        '打开导入文件夹，把下载好的文件直接放入对应路径。不要再套一层模型文件夹；如果下载的是压缩包，先解压。多个组件要分别放到各自路径。',
        '回到软件，点击“检查安装状态”。识别成功后，模型下拉框会显示已安装；选择该模型即可开始转写，无需重启软件。'
    ].forEach(text => add(steps, 'li', text));
    guide.folders.forEach((folder, index) => {
        const section = add(content, 'section', '', 'manual-import-component');
        add(section, 'h4', `${index + 1}. ${folder.label}`);
        add(section, 'p', '需要下载的文件');
        add(section, 'pre', folder.files.join(' / '));
        const row = (label, value, link) => {
            add(section, 'p', label);
            const controls = add(section, 'div', '', 'manual-import-value');
            const field = add(controls, 'input', '');
            field.value = value;
            field.readOnly = true;
            field.setAttribute('aria-label', label);
            const copy = add(controls, 'button', '复制', 'button secondary compact-button');
            copy.type = 'button';
            copy.onclick = () => copyTextToClipboard(value, '已复制');
            if (link) {
                const anchor = add(section, 'a', '打开下载页');
                anchor.href = value; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
            }
        };
        row('下载地址', folder.download_url, true);
        row('文件放入位置', folder.path, false);
    });
    add(content, 'p', '未识别时：确认全部文件已下载完成、文件名未改变、没有多套一层文件夹，并且文件不为 0 KB。下载失败可重新下载缺失文件，再检查安装状态。');
    const actions = add(content, 'div', '', 'modal-actions');
    const open = add(actions, 'button', '打开导入文件夹', 'button secondary');
    open.type = 'button';
    open.onclick = async () => {
        open.disabled = true;
        try {
            const data = await requestManualImportGuide(modelId, true);
            showToast(data.opened ? '已打开导入文件夹' : '无法自动打开，请复制上方路径到文件资源管理器地址栏', data.opened ? 'success' : 'info');
            startManualImportPolling(modelId);
        } catch (error) { showToast(error.message || '打开模型文件夹失败', 'error'); }
        finally { open.disabled = false; }
    };
    const check = add(actions, 'button', '检查安装状态', 'button secondary');
    check.type = 'button';
    check.onclick = async () => {
        check.disabled = true;
        try {
            await refreshWhisperModelHints();
            const option = [...byId('whisperModel').options].find(item => item.value === modelId);
            const installed = option?.dataset.status === 'cached';
            showToast(installed ? '模型已安装，可以开始转写' : '尚未识别完整模型，请按上方说明检查文件', installed ? 'success' : 'info');
        } catch (error) { showToast(error.message || '检查安装状态失败', 'error'); }
        finally { check.disabled = false; }
    };
    const close = add(actions, 'button', '关闭', 'button primary');
    close.type = 'button'; close.onclick = () => dialog.close();
    dialog.addEventListener('close', () => { dialog.remove(); byId('manualModelBtn')?.focus(); });
    document.body.append(dialog);
    dialog.showModal();
    startManualImportPolling(modelId);
}

function selectedScreenshotDensity() {
    const value = byId('screenshotDensity').value;
    return ['none', 'few', 'medium', 'many'].includes(value) ? value : 'none';
}

function selectedProcessingMode() {
    const value = byId('processingMode')?.value || document.querySelector('input[name="processingMode"]:checked')?.value;
    return value === 'reuse' ? 'reuse' : 'restart';
}

async function startSummary(options = {}) {
    if (typeof settingsRecoveryBusy !== 'undefined' && settingsRecoveryBusy) return;
    if (isSubmitting || isTaskActive) {
        showToast('已有任务正在处理中', 'info');
        return;
    }
    flushPendingPrefs();   // 本次提交用的设置必须同步落盘，不能留在防抖里
    if (options.outputMode) setOutputMode(options.outputMode);

    const resumeTaskId = options.resumeCurrent ? currentTaskId : null;
    const selectedMode = selectedProcessingMode();
    const forceRestart = Boolean(options.forceRestart || (!resumeTaskId && selectedMode === 'restart'));
    const restartSourceId = forceRestart && currentTaskId && sourceTaskId === currentTaskId
        && ['local', 'audio'].includes(byId('sourceType').value)
        && !byId('localFile').files[0] ? currentTaskId : null;
    const request = validateAndBuildRequestBase(resumeTaskId || restartSourceId);
    if (!request) return;
    // 所选 Whisper 模型未完整缓存时，先确认下载（新用户首次使用）。
    // 以下情况跳过确认：复用转录/续跑（不用 Whisper）、明显走平台字幕的提交
    // （B 站已填凭据、抖音已验证）、已在任意会话确认过该模型下载的用户
    // （localStorage 长期记忆——前端无法预知本次是否真的会用到 Whisper，
    // 弹窗只负责首次告知下载体积，此后交任务内下载/失败提示）。
    if (!resumeTaskId && forceRestart && !taskWillLikelyUseSubtitles(request)) {
        const whisperOption = byId('whisperModel').selectedOptions[0];
        const status = whisperOption ? whisperOption.dataset.status : 'missing';
        const modelId = whisperOption ? whisperOption.value : '';
        if (
            whisperOption
            && status
            && status !== 'cached'
            && modelId
            && !whisperDownloadConfirmed(modelId)
        ) {
            const modelLabel = (whisperOption.dataset.baseLabel || whisperOption.textContent).split('（')[0];
            const size = WHISPER_MODEL_SIZES[modelId] || '';
            const message = status === 'incomplete'
                ? `所选模型「${modelLabel}」缓存不完整，将自动补全缺失文件（约 ${size}）。是否继续？`
                : `当前缺少所选模型「${modelLabel}」。首次使用需下载约 ${size}（下载完成后自动开始转写）。是否继续？`;
            if (!window.confirm(message)) {
                showToast('已取消，任务未提交', 'info');
                return;
            }
            rememberWhisperDownloadConfirm(modelId);
        }
    }

    // B 站链接且未填写凭据时，询问是否先补充（AI 字幕需要登录态）
    if (
        !biliPromptDismissed
        && !['local', 'audio'].includes(request.sourceType)
        && isBilibiliUrl(request.videoUrl)
        && !hasBiliCredentials()
    ) {
        updateBiliHint(true);
        if (window.confirm('检测到 B 站链接。填写访问凭据可优先使用 AI 字幕（更快、更准确）。是否现在填写？')) {
            revealBiliCredentials();
            return;
        }
        biliPromptDismissed = true;
        updateBiliHint();
    }

    const submissionFilename = request.file?.name || ((resumeTaskId || restartSourceId) ? sourceFilename : '');
    const submissionTaskId = currentTaskId;
    const submissionView = taskViewSequence;
    const ownsSubmissionView = () => submissionTaskId === currentTaskId && submissionView === taskViewSequence;
    setSubmitting(true, ['local', 'audio'].includes(request.sourceType) ? '正在上传' : '正在提交');

    try {
        const videoUrl = request.videoUrl;
        let uploadTaskId = null;
        const config = buildSummarizeConfig(
            videoUrl, uploadTaskId, resumeTaskId, forceRestart, request.transcriptOnly
        );
        config.generation_action = options.generationAction === 'continue' ? 'continue' : 'regenerate';
        if (restartSourceId) config.source_task_id = restartSourceId;

        if (['local', 'audio'].includes(request.sourceType) && !resumeTaskId && !restartSourceId) {
            addLog(request.sourceType === 'audio' ? '正在上传音频' : '正在上传本地视频');
            // 仅转录不会走截图阶段，上传时就不必让后端保留视频轨
            const wantScreenshots = !request.transcriptOnly && screenshotsEnabled();
            const uploadResult = await uploadLocalFile(request.file, wantScreenshots);
            // 文件位置由 upload_task_id 的任务记录携带；video_url 只放网络链接
            uploadTaskId = uploadResult.task_id;
            config.upload_task_id = uploadTaskId;
            if (ownsSubmissionView()) addLog(`上传完成：${uploadResult.filename || request.file.name}`, 'success');
        }

        if (ownsSubmissionView()) addLog(request.transcriptOnly
            ? '正在提交转写任务（只到字幕稿为止，不调用大模型）'
            : `正在提交总结任务 · ${request.modelConfig.name}`);
        const response = await fetch(`${API_BASE}/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        const data = await readResponse(response, '提交任务失败');

        if (!data.task_id) throw new Error('后端未返回任务 ID');
        if (!ownsSubmissionView()) {
            loadRecentTasks(true);
            return;
        }
        resetTaskView();
        currentTaskId = data.task_id;
        sourceTaskId = data.task_id;
        sourceFilename = submissionFilename;
        updateFileInfo();
        setTranscriptTaskView(request.transcriptOnly);
        loadRecentTasks(true);
        if (data.reused_task_id) addLog(`已复用任务 ${data.reused_task_id} 的中间结果`, 'success');
        addLog(`任务已创建：${currentTaskId}`, 'success');
        setTaskActive(true);
        startElapsedTimer(0);
        setTaskState('processing', '处理中');
        setSubmitting(false, '处理中');
        schedulePoll(currentTaskId, 0);
    } catch (error) {
        if (!ownsSubmissionView()) return;
        setSubmitting(false);
        if (resumeTaskId && currentMarkdown) {
            resetSubmitLabel();
            showToast(error.message || '提交失败，请确认本地后端已启动', 'error');
        } else {
            failTask(error.message || '提交失败，请确认本地后端已启动');
        }
    } finally {
        if (isSubmitting) {
            setSubmitting(false, isTaskActive ? '处理中' : '开始生成');
            byId('restartBtn').disabled = isTaskActive;
            resetSubmitLabel();
        }
    }
}

function validateAndBuildRequestBase(resumeTaskId = null) {
    const sourceType = byId('sourceType').value;
    const transcriptOnly = isTranscriptMode();
    const modelConfig = isAgentMode() ? {name: "agent"} : getSelectedModelConfig();
    const apiKey = typedApiKey();
    if (!transcriptOnly && isObsidianNote()) {
        if (!byId('obsidianVault').value.trim()) return validationError('请填写 Obsidian 仓库的完整路径');
        const mode = byId('obsidianAttachmentMode').value;
        if (['note_subfolder', 'specified_folder'].includes(mode) && !byId('obsidianAttachmentFolder').value.trim()) {
            return validationError('请填写图片保存文件夹');
        }
    }

    if (!transcriptOnly && byId('sourceType').value !== 'audio' && timedScreenshotEnabled()) {
        const interval = Number(byId('screenshotInterval').value);
        if (!Number.isInteger(interval) || interval < 1 || interval > 86400) return validationError('截图间隔请输入 1～86400 之间的整数秒数');
    }

    // Both output modes use the selected AI channel for subtitle proofreading.
    if (!isAgentMode()) {
        if (!modelConfig) return validationError('请选择有效的模型档案');
        if (!modelConfig.model) return validationError('请输入模型 ID');
        if (!modelConfig.baseUrl) return validationError('请输入 API Base URL');
        if (!apiKey && !hasReusableKey()) {
            return validationError(`请输入「${modelConfig.profileLabel}」的 API Key，或点「保存到本机」后复用`);
        }
    }

    if (['local', 'audio'].includes(sourceType)) {
        const file = byId('localFile').files[0];
        if (resumeTaskId) return { sourceType, file: null, videoUrl: '', modelConfig, transcriptOnly };
        if (!file) return validationError('请选择本地视频或音频文件');
        if (sourceType === 'audio' && !/\.(mp3|m4a|wav|flac|aac|ogg|opus|wma)$/i.test(file.name)) return validationError('请选择 MP3、M4A、WAV、FLAC、AAC、OGG、OPUS 或 WMA 音频文件');
        return { sourceType, file, videoUrl: '', modelConfig, transcriptOnly };
    }

    const videoUrl = normalizeVideoInput(byId('videoUrl').value);
    if (!videoUrl) return validationError('请输入有效链接，或包含 B 站链接 / BV 号的分享文本');
    byId('videoUrl').value = videoUrl;
    if (biliPagesPanelVisible() && !checkedBiliPages().length) {
        return validationError('请至少勾选一个分 P');
    }
    return { sourceType, file: null, videoUrl, modelConfig, transcriptOnly };
}

// 宽松识别：完整 http(s) 链接原样返回；否则从分享文本提取 B 站/抖音链接（可缺省 scheme），
// 或裸 BV/av 号补全为视频页 URL；无法识别返回 null。与后端 normalize_video_input 同语义。
function normalizeVideoInput(raw) {
    const value = (raw || '').trim();
    if (!value) return null;
    try {
        const parsed = new URL(value);
        if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return value;
    } catch { /* 非完整链接，走下方提取 */ }
    const link = value.match(/(?:https?:\/\/)?(?:www\.|m\.|v\.)?(?:bilibili\.com|b23\.tv|douyin\.com|iesdouyin\.com|youtube\.com|youtu\.be)\/\S+/i);
    if (link) {
        const cleaned = link[0].replace(/[.,;:!?。，；：！？、）】」』”/]+$/, '');
        return cleaned.startsWith('http') ? cleaned : `https://${cleaned}`;
    }
    const bv = value.match(/BV[0-9A-Za-z]{10}/);
    if (bv) return `https://www.bilibili.com/video/${bv[0]}`;
    const av = value.match(/av(\d+)/i);
    if (av) return `https://www.bilibili.com/video/av${av[1]}`;
    return null;
}

function validationError(message) {
    showToast(message, 'error');
    return null;
}

function buildSummarizeConfig(
    videoUrl, uploadTaskId, resumeTaskId = null, forceRestart = false, transcriptOnly = false
) {
    const config = {
        video_url: videoUrl,
        prefer_subtitles: true,
        whisper_model: byId('whisperModel').value,
        use_gpu: byId('useGpu').checked,
        gpu_backend: selectedGpuBackend(),
        processing_mode: forceRestart ? 'restart' : 'reuse'
    };
    // 分 P 面板可见时显式下发勾选页码（全选也下发：显式指定优先于 URL ?p=N 规则）
    if (videoUrl && biliPagesPanelVisible()) {
        const pages = checkedBiliPages();
        if (pages.length) config.bilibili_pages = pages;
    }

    if (transcriptOnly) {
        config.output = 'transcript';
        config.generation_mode = isAgentMode() ? 'agent' : 'api';
        config.note_language = byId('noteLanguage').value || 'zh-CN';
        if (isAgentMode()) {
            Object.assign(config, agentExecutionConfig());
            config.llm_config = {};
        } else {
            const channel = getSelectedModelConfig();
            config.llm_config = {model_type: channel.provider, base_url: channel.baseUrl, model: channel.model};
            const key = typedApiKey();
            if (key) config.llm_config.api_key = key;
        }
    } else {
        const modelConfig = isAgentMode() ? {} : getSelectedModelConfig();
        const typedKey = isAgentMode() ? "" : typedApiKey();
        const llmConfig = {
            model_type: modelConfig.provider,
            base_url: modelConfig.baseUrl,
            model: modelConfig.model
        };
        if (typedKey) llmConfig.api_key = typedKey;
        config.vision_screenshots = visionCapability === 'supported' && byId('visionScreenshots').checked && byId('sourceType').value !== 'audio' && !timedScreenshotEnabled() && selectedScreenshotDensity() !== 'none';
        config.timed_screenshots = byId('sourceType').value !== 'audio' && timedScreenshotEnabled();
        config.screenshot_density = byId('sourceType').value === 'audio' || config.timed_screenshots ? 'none' : selectedScreenshotDensity();
        config.include_screenshots = screenshotsEnabled();
        if (config.timed_screenshots) config.screenshot_interval = Number(byId('screenshotInterval').value);
        config.video_resolution = Number(byId('videoResolution').value) || 720;
        config.video_encoding = byId('videoEncoding')?.value || 'compatibility';
        config.video_download_directory = byId('videoDownloadDirectory').value.trim();
        config.timeline_mode = byId('timelineMode').value || 'all';
        config.source_link_position = byId('sourceLinkPosition').value || 'end';
        config.text_detail = byId('textDetail').value || 'detailed';
        config.note_format = selectedNoteFormat();
        config.note_save_directory = isObsidianNote() ? '' : byId('noteSaveDirectory').value.trim();
        config.summary_style = byId('summaryStyle').value;
        config.note_language = byId('noteLanguage').value;
        config.ai_commentary_position = selectedAiCommentaryPosition();
        config.ai_commentary = config.ai_commentary_position !== 'none';
        config.custom_prompt = byId('customPrompt').value.trim();
        config.obsidian_tags = isObsidianNote() && byId('obsidianTags').checked;
        config.obsidian_callouts = isObsidianNote() && byId('obsidianCallouts').checked;
        if (isObsidianNote()) config.obsidian_properties = byId('obsidianProperties').checked;
        if (isObsidianNote()) {
            config.obsidian = {
                vault: byId('obsidianVault').value.trim(),
                note_folder: byId('obsidianNoteFolder').value.trim(),
                attachment_mode: byId('obsidianAttachmentMode').value,
                attachment_folder: byId('obsidianAttachmentFolder').value.trim()
            };
        }
        config.reasoning_effort = byId('reasoningEffort').value;
        config.generation_mode = isAgentMode() ? "agent" : "api";
        if (isAgentMode()) Object.assign(config, agentExecutionConfig());
        config.llm_config = isAgentMode() ? {} : llmConfig;
        if (isAgentMode()) config.vision_screenshots = visionPreference && byId("sourceType").value !== "audio" && !timedScreenshotEnabled() && selectedScreenshotDensity() !== "none";
        config.auto_crop_screenshots = config.vision_screenshots === true && cropPreference;
    }

    // 平台字幕（B 站 AI 字幕 / 抖音）两条路都要靠凭据，与是否调用大模型无关
    const sessdata = byId('sessdata').value.trim();
    if (sessdata) {
        config.bilibili_cookie = {
            sessdata,
            bili_jct: byId('biliJct').value.trim(),
            buvid3: byId('buvid3').value.trim()
        };
    }
    if (douyinCookies) {
        config.douyin_cookie = douyinCookies;
    }
    if (uploadTaskId) config.upload_task_id = uploadTaskId;
    if (resumeTaskId && !forceRestart) config.resume_task_id = resumeTaskId;
    return config;
}

async function uploadLocalFile(file, includeScreenshots = false) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('retain_media', 'true');
    formData.append('include_screenshots', includeScreenshots ? '1' : '0');
    const response = await fetch(`${API_BASE}/upload`, { method: 'POST', body: formData });
    return readResponse(response, '上传失败');
}

function updateProgress(rawProgress) {
    const progress = Math.min(100, Math.max(0, Number(rawProgress) || 0));
    byId('progressFill').style.width = `${progress}%`;
    byId('progressPercent').textContent = `${Math.round(progress)}%`;
    const progressBar = document.querySelector('[role="progressbar"]');
    progressBar.setAttribute('aria-valuenow', String(Math.round(progress)));
}

function updateStep(rawStep) {
    const activeStep = Number(rawStep) || 0;
    document.querySelectorAll('.progress-steps .step').forEach((step, index) => {
        const number = index + 1;
        step.classList.toggle('completed', number < activeStep || activeStep > 6);
        step.classList.toggle('active', number === activeStep);
    });
    byId('progressArea').classList.toggle('llm-active', activeStep === 6 && isTaskActive);
}

function renderTaskAdvisory(message, screenshotWarning = '') {
    const warning = byId('taskScreenshotWarning');
    warning.textContent = screenshotWarning || '';
    warning.hidden = !warning.textContent;
    const advisory = byId('taskAdvisory');
    advisory.textContent = typeof message === 'string' ? message : '';
    advisory.hidden = !advisory.textContent;
}

function clearLogs() {
    byId('logArea').replaceChildren(createLogEntry('等待任务开始'));
}

function addLog(message, type = 'info') {
    const logArea = byId('logArea');
    logArea.appendChild(createLogEntry(message, type, true));
    logArea.scrollTop = logArea.scrollHeight;
}

function updateLogs(logs) {
    const logArea = byId('logArea');
    logArea.replaceChildren(...logs.map((log) => createLogEntry(String(log))));
    logArea.scrollTop = logArea.scrollHeight;
    if (
        !whisperFallbackNotified
        && logs.some((log) => String(log).includes('已自动降级'))
    ) {
        whisperFallbackNotified = true;
        showToast('注意：所选 Whisper 模型未缓存或无法加载，已自动降级为已缓存的模型', 'info');
        // 降级说明本次实际使用了 base，顺手刷新下拉框状态
        refreshWhisperModelHints();
    }
}

function createLogEntry(message, type = 'info', includeTime = false) {
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = includeTime ? `[${new Date().toLocaleTimeString()}] ${message}` : message;
    return entry;
}

async function runDiagnostics() {
    const button = byId('diagBtn');
    const report = byId('diagReport');
    button.disabled = true;
    button.textContent = '自检中…';
    report.hidden = false;
    report.textContent = '正在自检（本机端口 / 系统代理 / DNS / 直连对比，约几秒）…';
    try {
        const [healthVerdict, payload] = await Promise.all([
            probeHealth(),
            fetchWithTimeout(`${API_BASE}/diagnostics`, {}, 20000)
                .then((response) => readResponse(response, '自检请求失败'))
                .catch((error) => ({ ok: false, error: error.message })),
        ]);
        renderDiagnostics(report, payload, healthVerdict);
    } finally {
        button.disabled = false;
        button.textContent = '本机自检';
    }
}

function formatDiagnosticsText(payload, healthVerdict) {
    const lines = [];
    if (payload && payload.ok) {
        const checks = payload.checks || {};
        const service = checks.service || {};
        lines.push(`服务：AI Video Note v${service.version}（PID ${service.pid}，端口 ${service.port}，已运行 ${service.uptime_s} 秒）`);
        const loopback = checks.loopback_http || {};
        lines.push(`本机应答：${loopback.ok
            ? `HTTP ${loopback.status}，${loopback.is_ours ? '是本程序' : '不是本程序！'}`
            : `无应答（${loopback.error || '未知'}）`}`);
        const winsock = checks.winsock || {};
        lines.push(`Winsock：${winsock.ok ? `正常（试绑端口 ${winsock.port}）` : `异常（${winsock.error}）`}`);
        const proxy = checks.system_proxy || {};
        const registry = proxy.registry || {};
        lines.push(`系统代理：环境变量 ${proxy.env || '无'}；Windows 注册表 ${registry.available
            ? (registry.enabled
                ? `已启用 ${registry.server}${registry.listening === false ? '（那个端口上没有进程在听！）' : ''}`
                : '未启用')
            : '读不到'}`);
        Object.entries(checks.dns || {}).forEach(([host, entry]) => {
            lines.push(`DNS ${host}：${entry.ok ? entry.addresses.join(' / ') : `失败（${entry.error}）`}`);
        });
        const describe = (probe) => (probe && probe.ok ? `通（HTTP ${probe.status}）` : `不通（${(probe && probe.error) || '未知'}）`);
        const outbound = checks.outbound || {};
        lines.push(`直连 api.deepseek.com：${describe(outbound.direct)}`);
        lines.push(`走系统代理：${describe(outbound.with_system_proxy)}`);
        lines.push(`扫码登录可用调试端口：${checks.cdp_ports_free} 个`);
        (payload.hints || []).forEach((hint) => lines.push(`→ ${hint}`));
    } else {
        lines.push(`自检请求失败：${(payload && payload.error) || '未知原因'}`);
        lines.push(`本机探测：${healthVerdict}`);
    }
    return lines.join('\n');
}

function renderDiagnostics(report, payload, healthVerdict) {
    report.textContent = formatDiagnosticsText(payload, healthVerdict);
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'button ghost compact-button';
    copy.textContent = '复制报告';
    copy.addEventListener('click', () => {
        copyTextToClipboard(report.textContent, '自检报告已复制');
    });
    report.appendChild(document.createElement('br'));
    report.appendChild(copy);
}

let updateOffer = null;

async function loadAutostartSetting() {
    const input = byId('autostartEnabled');
    const status = byId('autostartStatus');
    if (!input) return;
    try {
        const data = await readResponse(await fetchWithTimeout(`${API_BASE}/autostart`, {}, 10000), '读取开机自启动设置失败');
        input.checked = Boolean(data.enabled);
        input.dataset.savedEnabled = String(input.checked);
        input.disabled = !data.supported;
        if (status) status.textContent = data.supported ? '' : '当前系统暂不支持开机自启动';
    } catch (_error) {
        if (status) status.textContent = '读取开机自启动设置失败，请刷新重试';
    }
}

async function saveAutostartSetting() {
    const input = byId('autostartEnabled');
    const status = byId('autostartStatus');
    if (!input || input.disabled) return;
    const previous = input.dataset.savedEnabled === 'true';
    input.disabled = true;
    try {
        const data = await readResponse(await fetchWithTimeout(`${API_BASE}/autostart`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: input.checked})
        }, 10000), '设置开机自启动失败');
        input.checked = Boolean(data.enabled);
        input.dataset.savedEnabled = String(input.checked);
        if (status) status.textContent = '已保存';
    } catch (_error) {
        input.checked = previous;
        if (status) status.textContent = '设置开机自启动失败，请重试';
    } finally { input.disabled = false; }
}

async function openAppLog() {
    const button = byId('viewLogsBtn');
    const status = byId('viewLogsStatus');
    if (!button || button.disabled) return;
    button.disabled = true;
    try {
        await readResponse(await fetchWithTimeout(`${API_BASE}/log/open`, {method: 'POST'}, 10000), '打开日志失败');
        if (status) status.textContent = '';
    } catch (_error) {
        if (status) status.textContent = '无法打开日志，请确认日志已生成后重试';
    } finally { button.disabled = false; }
}

async function loadLogRetention() {
    const input = byId('logRetentionDays');
    if (!input) return;
    try {
        const data = await readResponse(await fetchWithTimeout(`${API_BASE}/log-settings`, {}, 10000), '读取日志设置失败');
        input.value = data.days;
        input.dataset.savedDays = String(data.days);
        input.disabled = false;
    } catch (_error) {
        if (byId('logRetentionStatus')) byId('logRetentionStatus').textContent = '读取日志设置失败，请刷新重试';
    }
}

async function saveLogRetention() {
    const input = byId('logRetentionDays');
    const status = byId('logRetentionStatus');
    if (!input || input.disabled) return;
    if (!input.checkValidity()) { input.reportValidity(); return; }
    input.disabled = true;
    try {
        const data = await readResponse(await fetchWithTimeout(`${API_BASE}/log-settings`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({days: Number(input.value)})
        }, 10000), '保存日志设置失败');
        input.value = data.days;
        input.dataset.savedDays = String(data.days);
        if (status) status.textContent = '已保存';
    } catch (_error) {
        input.value = input.dataset.savedDays || '2';
        if (status) status.textContent = '保存日志设置失败，请重试';
    } finally { input.disabled = false; }
}

function renderUpdateReleaseLinks() {
    const github = byId('updateGithubLink');
    if (github) github.hidden = false;
}

let updatePreviousFocus = null;

function renderUpdateOffer() {
    renderUpdateReleaseLinks();
    if (!updateOffer) return;
    const data = updateOffer;
    const language = window.AIVideoNoteI18n?.language || document.documentElement.lang || 'zh-CN';
    const english = language === 'en';
    const notes = data.notes ? data.notes.trim().slice(0, 300) : '';
    const sourceName = data.source_name || (data.source === 'gitee' ? 'Gitee' : 'GitHub');
    byId('updateTitle').textContent = english ? 'New version available' : '发现新版本';
    byId('updateBody').textContent = english
        ? `New version ${data.latest_version} available (current v${data.current_version}).`
            + (notes ? `\n\nRelease notes (excerpt):\n${notes}` : '')
            + `\n\nUpdate source: ${sourceName}. Choose a website below to download the new version.`
        : `发现新版本 ${data.latest_version}（当前 v${data.current_version}）。`
            + (notes ? `\n\n更新内容（节选）：\n${notes}` : '')
            + `\n\n更新来源：${sourceName}。点击下方网站按钮，下载新版本后按发布说明更新。`;
    byId('updateIgnoreBtn').textContent = english ? 'Ignore this update' : '忽略本次更新';
    byId('updateCancelBtn').textContent = english ? 'Cancel' : '取消';
}

function restoreUpdateFocus() {
    const previous = updatePreviousFocus;
    updatePreviousFocus = null;
    if (previous?.isConnected) previous.focus();
}

function showUpdateOffer() {
    const dialog = byId('updateModal');
    if (!dialog.dataset.initialized) {
        dialog.addEventListener('close', () => {
            if (dialog.open) return;
            dialog.hidden = true;
            restoreUpdateFocus();
        });
        dialog.addEventListener('keydown', event => {
            if (event.key !== 'Tab' || event.ctrlKey || event.altKey || event.metaKey) return;
            const controls = [...dialog.querySelectorAll('a[href], button:not([disabled]), [tabindex]')]
                .filter(node => node.tabIndex >= 0 && node.getClientRects().length);
            const first = controls[0], last = controls.at(-1);
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault(); last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault(); first?.focus();
            }
        });
        dialog.dataset.initialized = 'true';
    }
    renderUpdateOffer();
    if (!dialog.open) {
        updatePreviousFocus = document.activeElement;
        dialog.hidden = false;
        dialog.showModal();
        byId('updateGithubLink').focus();
    }
}

async function runStartupUpdateCheck() {
    try {
        const language = window.AIVideoNoteI18n?.language || document.documentElement.lang || 'zh-CN';
        const response = await fetchWithTimeout(`${API_BASE}/update/check?language=${encodeURIComponent(language === 'en' ? 'en' : 'zh-CN')}`, {}, 10000);
        const data = await readResponse(response, '检查更新失败');
        if (!data.ok || !data.update_available) return;
        const ignored = (prefs.update && prefs.update.ignored_version) || '';
        if (ignored && ignored === data.latest_version) return;
        updateOffer = data;
        // Render using the current language after the request, not its starting language.
        showUpdateOffer();
    } catch {
        // 启动自检失败不打扰：网络常态问题，托盘菜单里随时可以手动检查
    }
}

function ignoreUpdateOffer() {
    if (updateOffer && updateOffer.latest_version) {
        prefs.update = prefs.update || {};
        prefs.update.ignored_version = updateOffer.latest_version;
        schedulePersist(persistPrefs);
    }
    dismissUpdateOffer();
}

function dismissUpdateOffer() {
    const dialog = byId('updateModal');
    if (dialog.open) dialog.close();
    dialog.hidden = true;
    restoreUpdateFocus();
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    byId('toastRegion').appendChild(toast);
    window.setTimeout(() => window.AIVideoNoteMotion ? window.AIVideoNoteMotion.dismissToast(toast) : toast.remove(), 4200);
}


// Each page owns a connection. Closing the last page lets the launcher exit.
let browserSessionSocket = null;
let browserSessionLeaving = false;
function connectBrowserSession() {
    browserSessionLeaving = false;
    if (browserSessionSocket && browserSessionSocket.readyState < 2) return;
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${location.host}/api/browser-session`);
    browserSessionSocket = socket;
    socket.onclose = () => {
        if (browserSessionSocket !== socket) return;
        browserSessionSocket = null;
        if (!browserSessionLeaving) window.setTimeout(connectBrowserSession, 1000);
    };
}
window.addEventListener('pageshow', connectBrowserSession);
window.addEventListener('pagehide', () => {
    browserSessionLeaving = true;
    if (browserSessionSocket) browserSessionSocket.close();
});


let modelDownloadJob = {status:'idle'};
let downloadPollTimer = null;
async function controlModelDownload(action) {
    try {
        const backend = selectedGpuBackend();
        const payload = action === 'resume' ? {model:modelDownloadJob.model,gpu_backend:modelDownloadJob.backend} : {model:byId('whisperModel').value,gpu_backend:backend};
        const response = await fetch(`${API_BASE}/model-download${action === 'pause' ? '/pause' : ''}`, {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || '下载操作失败');
        await pollModelDownload();
    } catch (error) { byId('modelDownloadProgress').textContent = error.message; }
}
async function pollModelDownload() {
    clearTimeout(downloadPollTimer);
    try {
        const response = await fetch(`${API_BASE}/model-download`,{cache:'no-store'});
        if (!response.ok) return;
        const previous = modelDownloadJob.status;
        modelDownloadJob = await response.json();
        const job = modelDownloadJob;
        const active = ['downloading','pausing'].includes(job.status);
        byId('downloadModelBtn').disabled = active;
        byId('pauseModelDownloadBtn').hidden = !active;
        byId('pauseModelDownloadBtn').disabled = job.status === 'pausing';
        byId('resumeModelDownloadBtn').hidden = !['paused','failed'].includes(job.status);
        const labels = {idle:'',downloading:'下载中',pausing:'正在暂停…',paused:'已暂停，继续时会保留下载进度',completed:'模型下载完成',failed:'下载失败'};
        const progress = active && job.file ? `：${job.file}（当前文件 ${job.percent == null ? '大小未知' : job.percent + '%'}，${(job.received / 1048576).toFixed(1)} MB）` : '';
        byId('modelDownloadProgress').textContent = (labels[job.status] || '') + progress + (job.error ? '：' + job.error : '');
        if (previous !== job.status && ['completed','paused','failed'].includes(job.status)) await refreshWhisperModelHints();
        downloadPollTimer = setTimeout(pollModelDownload, active ? 1000 : 5000);
    } catch (_) { downloadPollTimer = setTimeout(pollModelDownload,5000); }
}
let environmentPollTimer = null;
async function checkProjectEnvironment() {
    clearTimeout(environmentPollTimer);
    try {
        const response = await fetch(`${API_BASE}/environment`,{cache:'no-store'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || '环境检查失败');
        const running = data.repair.status === 'running';
        byId('repairEnvironmentBtn').disabled = running || !data.can_repair;
        const lines = data.checks.map(item => `${item.ok ? '✓' : '✗'} ${item.name}：${item.detail}`);
        if (data.repair.status !== 'idle') {
            lines.push('',{running:'正在修复…',completed:'修复完成，请关闭页面后重新启动软件。',failed:'修复失败，请检查下方信息后重试。'}[data.repair.status] || '');
            lines.push(...data.repair.log.slice(-8));
        }
        byId('environmentResult').textContent = lines.join('\n');
        if (running) environmentPollTimer = setTimeout(checkProjectEnvironment,1000);
    } catch (error) { byId('environmentResult').textContent = error.message; }
}
async function repairProjectEnvironment() {
    byId('repairEnvironmentBtn').disabled = true;
    try {
        const response = await fetch(`${API_BASE}/environment/repair`,{method:'POST'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || '无法开始修复');
        await checkProjectEnvironment();
    } catch (error) {
        byId('environmentResult').textContent = error.message;
        byId('repairEnvironmentBtn').disabled = false;
    }
}


function isAgentMode() { return byId('generationMode')?.value === 'agent'; }
function showAgentWaiting(task = {}) {
    setTaskActive(false);
    byId('agentResultPanel').hidden = false;
    if (byId('agentAutomaticActions')) byId('agentAutomaticActions').hidden = Boolean(task.agent_auto_running);
    if (byId('agentManualFallback')) byId('agentManualFallback').hidden = false;
    byId('taskAdvisory').hidden = !(task.advisory || task.screenshot_warning);
    const waitingMessage = byId('agentWaitingMessage');
    if (waitingMessage) {
        const message = task.agent_auto_running ? 'agent 正在后台自动处理，完成后会显示笔记和下载按钮，无需复制任务包或手动导入。' : '材料已准备好，请在已连接 MCP 的 agent 中说“处理待生成笔记”，结果会自动回到网页。';
        waitingMessage.textContent = window.AIVideoNoteI18n?.translate(message) || message;
    }
    byId('cancelTaskBtn').hidden = false;
    byId('cancelTaskBtn').disabled = false;
    setTaskState('awaiting_agent', task.agent_auto_running ? 'agent 正在自动生成' : '等待 agent 生成');
    if (byId('agentAutoRetryBtn')) byId('agentAutoRetryBtn').disabled = Boolean(task.agent_auto_running);
    byId('networkState').textContent = '材料已准备好';
    setSubmitButton(false, '提交新任务');
}
async function copyAgentPacket() {
    const taskId = currentTaskId;
    try {
        let offset = 0, packet, segments = [];
        do {
            const response = await fetch(`${API_BASE}/agent/jobs/${encodeURIComponent(taskId)}?offset=${offset}&limit=500`, {cache:'no-store'});
            packet = await readResponse(response, '读取任务材料失败');
            segments.push(...packet.segments); offset = packet.next_offset;
        } while (offset !== null && offset !== undefined);
        const text = `任务 ID：${taskId}\n标题：${packet.title}\n\n${packet.prompt}\n\n设置：${JSON.stringify(packet.settings, null, 2)}\n\n字幕资料：\n${segments.map(s => `[${s.start}-${s.end}秒] ${s.text}`).join('\n')}\n\n截图引用：${JSON.stringify(packet.screenshots)}\n候选截图分组：${JSON.stringify(packet.candidate_groups || [])}\n\n完成后将笔记正文粘贴到网页中的同一任务并提交。`;
        await copyTextToClipboard(text, '任务包已复制');
    } catch (error) { showToast(error.message, 'error'); }
}
async function importAgentResult() {
    const taskId = currentTaskId;
    const markdown = byId('agentResultText').value.trim();
    if (!markdown) { showToast('请先填写笔记正文', 'error'); return; }
    byId('importAgentResultBtn').disabled = true;
    try {
        const response = await fetch(`${API_BASE}/agent/jobs/${encodeURIComponent(taskId)}/import`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({markdown})});
        await readResponse(response, '导入结果失败');
        if (taskId === currentTaskId) await openRecentTask(taskId);
    } catch (error) { showToast(error.message, 'error'); }
    finally { byId('importAgentResultBtn').disabled = false; }
}

// The native tray runs outside the browser, so send the UI preference to it.
window.syncNativeLanguage = async function(language) {
    try {
        await fetch(`${API_BASE}/ui/language`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({language})
        });
    } catch (error) {
        console.debug('Native language sync unavailable', error);
    }
};

function agentExecutionConfig() {
    const execution = byId('agentExecution')?.value || 'manual';
    const provider = byId('agentProvider')?.value || 'codex';
    if (execution === 'auto' && ((byId('agentModel')?.value === '__custom__' && !selectedAgentModel()) || !/^(?:[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,127})?$/.test(selectedAgentModel()))) throw new Error('请填写有效的模型 ID');
    let command = [];
    if (execution === 'auto' && provider === 'custom') {
        try { command = JSON.parse(byId('agentCommand').value); } catch { throw new Error('命令参数必须是 JSON 字符串数组'); }
        if (!Array.isArray(command) || !command.length || !command.every(a => typeof a === 'string' && a)) throw new Error('命令参数必须是 JSON 字符串数组');
        if (selectedAgentModel() && !command.some(arg => arg.includes('{model}'))) throw new Error('自定义命令指定模型时，请在参数列表中使用 {model}');
    }
    return {agent_execution: execution, agent_provider: provider, agent_command: command, agent_model: execution === 'auto' ? selectedAgentModel() : '', agent_reasoning_effort: provider === 'codex' && execution === 'auto' ? byId('agentReasoningEffort')?.value || 'default' : 'default'};
}
async function checkAgentRuntime() {
    try {
        const response = await fetch(`${API_BASE}/agent/runtime`, {cache:'no-store'});
        const data = await readResponse(response, '检测 agent 失败');
        agentModelCatalogs = data.agent_models || {codex: data.codex_models || []};
        agentModelCatalog = agentModelCatalogs[byId('agentProvider')?.value || 'codex'] || [];
        renderAgentModelOptions(selectedAgentModel(), byId('agentReasoningEffort')?.value || 'default');
        const status = byId('agentRuntimeStatus');
        status.replaceChildren();
        for (const [key, name] of [['codex', 'Codex'], ['claude', 'Claude Code（命令行版）'], ['antigravity', 'Antigravity CLI'], ['qwen', 'Qwen Code']]) {
            const item = document.createElement('span');
            item.className = 'agent-runtime-item';
            item.textContent = name + ': ' + (data[key] ? '已检测到' : '未检测到');
            status.appendChild(item);
        }
    } catch (error) { byId('agentRuntimeStatus').textContent = error.message; }
}
async function retryAgentAuto() {
    const taskId = currentTaskId;
    byId('agentAutoRetryBtn').disabled = true;
    try {
        const config = agentExecutionConfig();
        const response = await fetch(`${API_BASE}/agent/jobs/${encodeURIComponent(taskId)}/auto`, {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(config)
        });
        await readResponse(response, '启动自动处理失败');
        showToast('已启动自动处理，结果会回到网页', 'success');
    } catch (error) { showToast(error.message, 'error'); }
    finally { byId('agentAutoRetryBtn').disabled = false; }
}
document.addEventListener('DOMContentLoaded', () => {
    for (const id of ['agentExecution','agentCommand','agentCustomModel','agentReasoningEffort']) bindListener(id, 'change', () => {persistGlobals(); updateGenerationSettings();});
    bindListener('agentProvider', 'change', switchAgentModelProvider);
    bindListener('agentModel', 'change', () => { updateAgentModelFields(); persistGlobals(); });
    bindListener('checkAgentRuntimeBtn','click',checkAgentRuntime);
    checkAgentRuntime();
    bindListener('agentAutoRetryBtn','click',retryAgentAuto);
});

async function refreshBiliSavedLogin() {
    try {
        const response = await fetch(`${API_BASE}/bili-credentials`, {cache:'no-store'});
        const data = await readResponse(response, '读取登录状态失败');
        biliSavedLogin = Boolean(data.saved);
        byId('biliLogoutBtn').hidden = !biliSavedLogin;
        byId('biliLoginStatus').textContent = biliSavedLogin ? '已保存登录状态（本机）' : '';
        updateBiliHint();
    } catch (error) { byId('biliLoginStatus').textContent = error.message; }
}
async function saveBiliLogin() {
    const credentials = {sessdata:byId('sessdata').value.trim(), bili_jct:byId('biliJct').value.trim(), buvid3:byId('buvid3').value.trim()};
    if (!credentials.sessdata) throw new Error('请先扫码登录或填写 SESSDATA');
    const response = await fetch(`${API_BASE}/bili-credentials`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(credentials)});
    await readResponse(response, '保存登录状态失败');
    await refreshBiliSavedLogin();
}
async function logoutBili() {
    const button = byId('biliLogoutBtn'); button.disabled = true;
    try {
        await cancelBiliLogin();
        const response = await fetch(`${API_BASE}/bili-credentials`, {method:'DELETE'});
        await readResponse(response, '退出登录失败');
        for (const id of ['sessdata','biliJct','buvid3']) byId(id).value = '';
        biliSavedLogin = false;
        await refreshBiliSavedLogin();
        showToast('已退出登录并清除本机凭据', 'success');
    } catch (error) {showToast(error.message, 'error');}
    finally {button.disabled = false;}
}

window.addEventListener('ui-language-changed', (event) => {
    renderUpdateOffer();
    renderNoteSaveLocation();
    if (!byId('noteLanguage')) return;
    if (event.detail?.userInitiated || !prefs.global.note_language) {
        const language = document.documentElement.lang === 'en' ? 'en' : 'zh-CN';
        byId('noteLanguage').value = language;
        if (event.detail?.userInitiated) {
            prefs.global.note_language = language;
            persistPrefs();
        }
    }
});

let agentModelCatalog = [];
let agentModelCatalogs = {};
let activeAgentModelProvider = 'codex';
function switchAgentModelProvider() {
    prefs.global.agent_models ||= {};
    prefs.global.agent_models[activeAgentModelProvider] = {model:selectedAgentModel(), effort:byId('agentReasoningEffort')?.value || 'default'};
    activeAgentModelProvider = byId('agentProvider').value;
    agentModelCatalog = agentModelCatalogs[activeAgentModelProvider] || [];
    const saved = prefs.global.agent_models[activeAgentModelProvider] || {};
    renderAgentModelOptions(saved.model || '', saved.effort || 'default');
    persistGlobals();
    updateGenerationSettings();
}
const AGENT_EFFORTS = ['minimal','low','medium','high','xhigh','max','ultra'];
function selectedAgentModel() {
    const choice = byId('agentModel')?.value || '';
    return choice === '__custom__' ? (byId('agentCustomModel')?.value || '').trim() : choice;
}
function renderAgentModelOptions(model = '', effort = 'default') {
    const select = byId('agentModel');
    if (!select) return;
    agentModelCatalog = agentModelCatalogs[byId('agentProvider')?.value || 'codex'] || [];
    const catalog = agentModelCatalog.filter(m => m && typeof m.id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,127}$/.test(m.id));
    select.innerHTML = '<option value="">客户端默认模型</option>' + catalog.map(m => `<option data-no-i18n value="${escapeHtml(m.id)}">${escapeHtml(m.label || m.id)}</option>`).join('') + '<option value="__custom__">手动填写模型 ID</option>';
    select.value = !model ? '' : catalog.some(m => m.id === model) ? model : '__custom__';
    if (byId('agentCustomModel')) byId('agentCustomModel').value = model;
    updateAgentModelFields(effort);
    const hint = byId('agentModelHint');
    if (hint) hint.textContent = window.AIVideoNoteI18n?.translate(catalog.length ? '已读取本机模型名称；列表不代表账号权限，实际可用性由客户端确认。' : '未读取到模型列表，可手动填写模型 ID，或使用客户端默认模型。') || (catalog.length ? '已读取本机模型名称；列表不代表账号权限，实际可用性由客户端确认。' : '未读取到模型列表，可手动填写模型 ID，或使用客户端默认模型。');
}
function updateAgentModelFields(effort = byId('agentReasoningEffort')?.value || 'default') {
    const field = byId('agentCustomModelField');
    if (field) field.hidden = byId('agentModel')?.value !== '__custom__';
    const select = byId('agentReasoningEffort');
    if (!select) return;
    const current = agentModelCatalog.find(m => m.id === selectedAgentModel());
    const allowed = current?.reasoning_efforts?.length ? AGENT_EFFORTS.filter(e => current.reasoning_efforts.includes(e)) : AGENT_EFFORTS;
    select.innerHTML = '<option value="default">客户端默认</option>' + allowed.map(e => `<option value="${e}">${e}</option>`).join('');
    select.value = allowed.includes(effort) ? effort : 'default';
}
function showAgentRunSummary(task) {
    const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
    byId('agentResultPanel').hidden = false;
    byId('agentWaitingMessage').textContent = tr(task.status === 'completed' ? 'agent 处理已完成，笔记和下载文件已就绪。' : 'agent 本次处理已停止，可查看运行记录。');
    if (byId('agentAutomaticActions')) byId('agentAutomaticActions').hidden = true;
    if (byId('agentManualFallback')) byId('agentManualFallback').hidden = true;
}

function renderAgentRunInfo(runtime) {
    const panel = byId('agentRunInfo');
    if (!panel) return;
    panel.hidden = !runtime;
    if (!runtime) { panel.textContent = ''; return; }
    const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
    const unknown = ['starting','running'].includes(runtime.state) ? '等待客户端报告' : '未报告';
    const requestedEffort = runtime.requested_reasoning_effort === 'default' ? tr('客户端默认') : runtime.requested_reasoning_effort || tr('客户端默认');
    const reportedEffort = runtime.reported_reasoning_effort === 'none' ? tr('none（未启用额外推理）') : runtime.reported_reasoning_effort || tr(unknown);
    const effort = value => runtime.provider === 'Codex' ? `<span class="agent-effort-inline">${escapeHtml(tr('推理强度'))}：${escapeHtml(value)}</span>` : '';
    panel.innerHTML = `<strong>${escapeHtml(tr('agent 后台处理'))}</strong><dl class="agent-model-details">`
        + `<div><dt>${escapeHtml(tr('请求模型'))}</dt><dd>${escapeHtml(runtime.requested_model || tr('客户端默认'))}${effort(requestedEffort)}</dd></div>`
        + `<div><dt>${escapeHtml(tr('客户端报告模型'))}</dt><dd>${escapeHtml(runtime.reported_model || tr(unknown))}${effort(reportedEffort)}</dd></div></dl>`
        + `<small class="agent-current-stage">${escapeHtml(runtime.provider || 'Agent')} · ${escapeHtml(tr(runtime.stage || '笔记生成'))}</small>`;

}
