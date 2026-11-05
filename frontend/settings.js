// The browser is a cache; the workspace record is authoritative after startup.
const workspacePreferences = {mode: 'browser', source: null, revision: null, pending: null, saving: false};

function preferenceStorageNotice(message = '', retry = false) {
    const notice = byId('preferencesStorageNotice');
    if (notice) notice.hidden = !message;
    const label = byId('preferencesStorageMessage');
    if (label) label.textContent = message;
    const button = byId('retryPreferencesBtn');
    if (button) button.hidden = !retry;
}

async function readPreferencesResponse(response) {
    const data = await response.json();
    if (!response.ok) {
        const error = new Error(data.detail?.message || '设置暂时无法读写，已有文件保持不变');
        error.permanent = response.status === 409 || response.status === 422 || response.status === 413;
        throw error;
    }
    if (data.schema > PREFS_SCHEMA) {
        const error = new Error('设置来自更新版本，已停止写入以保护原文件');
        error.permanent = true;
        throw error;
    }
    if (data.schema !== PREFS_SCHEMA || typeof data.exists !== 'boolean' || !(data.revision === null || typeof data.revision === 'string') ||
        (data.exists && (!data.preferences || typeof data.preferences !== 'object'))) {
        throw new Error('设置响应无效，未覆盖已有配置');
    }
    return data;
}

async function loadWorkspacePreferences() {
    workspacePreferences.mode = 'loading';
    try {
        const response = await fetchWithTimeout(`${API_BASE}/preferences`, {cache: 'no-store'}, 10000);
        const data = await readPreferencesResponse(response);
        workspacePreferences.source = data.preferences;
        workspacePreferences.revision = data.revision;
        workspacePreferences.mode = 'ready';
        preferenceStorageNotice();
    } catch (error) {
        workspacePreferences.mode = 'blocked';
        preferenceStorageNotice(error.message || '设置读取失败，已停止自动保存，请重新读取设置');
    }
}

function mergePreferenceRecords(base, update) {
    const result = {...base};
    for (const [key, value] of Object.entries(update)) {
        if (['__proto__', 'prototype', 'constructor'].includes(key)) continue;
        result[key] = value && typeof value === 'object' && !Array.isArray(value)
            ? mergePreferenceRecords(base?.[key] || {}, value) : value;
    }
    return result;
}

async function saveWorkspacePreferences() {
    if (workspacePreferences.saving || workspacePreferences.mode !== 'ready') return;
    workspacePreferences.saving = true;
    try {
        while (workspacePreferences.pending && workspacePreferences.mode === 'ready') {
            const pending = workspacePreferences.pending;
            workspacePreferences.pending = null;
            try {
                const response = await fetchWithTimeout(`${API_BASE}/preferences`, {
                    method: 'PUT', headers: {'Content-Type': 'application/json'}, keepalive: true,
                    body: JSON.stringify({client_schema: PREFS_SCHEMA,
                        preferences: pending, expected_revision: workspacePreferences.revision,
                        import_source: workspacePreferences.revision === null ? workspacePreferences.source : null})
                }, 10000);
                const data = await readPreferencesResponse(response);
                workspacePreferences.revision = data.revision;
                workspacePreferences.source = data.preferences;
                preferenceStorageNotice();
            } catch (error) {
                workspacePreferences.pending ||= pending;
                workspacePreferences.mode = error.permanent ? 'blocked' : 'error';
                preferenceStorageNotice(error.message || '设置保存失败，本次修改尚未保存', !error.permanent);
            }
        }
    } finally {
        workspacePreferences.saving = false;
    }
}

function retryWorkspacePreferencesSave() {
    if (workspacePreferences.mode !== 'error') return;
    workspacePreferences.mode = 'ready';
    return saveWorkspacePreferences();
}

// Preferences, model profiles, credential controls and connection tests.
function removeLegacySecrets() {
    LEGACY_SENSITIVE_KEYS.forEach((key) => localStorage.removeItem(key));
}

const GLOBAL_PREF_KEYS = [
    'note_format', 'generation_mode', 'agent_execution', 'agent_provider', 'agent_command', 'agent_models', 'agent_model', 'agent_reasoning_effort',
    'screenshot_density', 'timed_screenshots', 'vision_screenshots', 'auto_crop_screenshots',
    'video_resolution', 'video_encoding', 'video_download_directory', 'note_save_directory',
    'obsidian_tags', 'obsidian_callouts', 'obsidian_properties', 'obsidian_vault', 'obsidian_note_folder', 'obsidian_attachment_mode', 'obsidian_attachment_folder',
    'whisper_model',
    'screenshot_interval',
    'include_screenshots',
    'use_gpu', 'gpu_backend',
    'summary_style', 'ai_commentary', 'ai_commentary_position', 'note_language', 'timeline_mode', 'source_link_position', 'text_detail',
    'reasoning_effort',
    'processing_mode',
    'output_mode'
];

function defaultProvider() {
    return Object.keys(PROVIDER_CONFIG)[0];
}

function defaultPrefs() {
    return {
        schema: PREFS_SCHEMA,
        templates: [],
        default_template_id: "",
        provider: defaultProvider(),
        custom_id: '',
        customs: [],
        by_provider: {},
        confirmations: { whisper_download: {} },
        global: {
            note_format: 'markdown',
            generation_mode: 'api',
            agent_execution: 'manual', agent_provider: 'codex', agent_command: '', agent_models: {}, agent_model: '', agent_reasoning_effort: 'default',
            obsidian_tags: false,
            obsidian_callouts: false,
            obsidian_properties: true,
            obsidian_vault: '',
            obsidian_note_folder: '',
            obsidian_attachment_mode: 'note_subfolder',
            obsidian_attachment_folder: 'attachments',
            whisper_model: 'base',
            screenshot_density: '',
            video_resolution: '720',
            video_encoding: 'compatibility',
            video_download_directory: '', note_save_directory: '',
            screenshot_interval: '10',
            timed_screenshots: false,
            vision_screenshots: false,
            auto_crop_screenshots: false,
            include_screenshots: false,
            use_gpu: false,
            gpu_backend: "auto",
            summary_style: 'faithful', ai_commentary: false, ai_commentary_position: null, note_language: '',
            timeline_mode: 'all', source_link_position: 'end', text_detail: 'detailed',
            reasoning_effort: 'auto',
            processing_mode: 'restart',
            output_mode: 'note'
        },
        ui: { font_size: 'standard', theme: 'system', mcp_hint_seen: false, mcp_transport: 'http' },
        update: { ignored_version: '' }
    };
}

function readStoredPrefs() {
    try {
        const raw = localStorage.getItem(PREFS_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
        return parsed;
    } catch (_error) {
        throw new Error('浏览器旧设置无法读取，未覆盖原记录');
    }
}

// 递归丢弃敏感字段名的值，并把不可序列化的东西剔掉。
function sanitizeForStorage(value) {
    if (Array.isArray(value)) return value.map(sanitizeForStorage);
    if (value && typeof value === 'object') {
        const clean = {};
        Object.entries(value).forEach(([key, item]) => {
            if (SECRET_FIELD_PATTERN.test(key)) {
                console.warn(`[AI Video Note] 拒绝把敏感字段 ${key} 写入浏览器存储`);
                return;
            }
            const sanitized = sanitizeForStorage(item);
            if (sanitized !== undefined) clean[key] = sanitized;
        });
        return clean;
    }
    if (typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number') {
        return value;
    }
    return undefined;
}

// 全文件唯一的存储写入入口。
function persistPrefs() {
    if (settingsRecoveryBusy || ['loading', 'blocked'].includes(workspacePreferences.mode)) return;
    const snapshot = sanitizeForStorage(mergePreferenceRecords(workspacePreferences.source || {}, prefs));
    try {
        // Keep the original browser record until the first disk save succeeds.
        if (workspacePreferences.mode === 'browser' || workspacePreferences.revision !== null) {
            localStorage.setItem(PREFS_KEY, JSON.stringify(snapshot));
        }
    } catch (error) {
        console.warn('[AI Video Note] 偏好保存失败（浏览器可能禁用了本地存储）：', error);
    }
    if (workspacePreferences.mode !== 'browser') {
        workspacePreferences.pending = snapshot;
        void saveWorkspacePreferences();
    }
}

function schedulePersist(job) {
    window.clearTimeout(persistTimer);
    persistJob = job;
    persistTimer = window.setTimeout(() => {
        persistTimer = null;
        const pending = persistJob;
        persistJob = null;
        if (pending) pending();
    }, PREFS_DEBOUNCE_MS);
}

function flushPendingPrefs() {
    if (!persistTimer) return;
    window.clearTimeout(persistTimer);
    persistTimer = null;
    const pending = persistJob;
    persistJob = null;
    if (pending) pending();
}

function isPresetModel(provider, modelId) {
    const config = PROVIDER_CONFIG[provider];
    return Boolean(config && config.models.some(([id]) => id === modelId));
}

function normalizeWhisperModel(model) {
    return ['tiny', 'tiny.en', 'base.en', 'small.en', 'medium.en', 'paraformer-zh'].includes(model) ? 'base' : model;
}

function legacyWhisperConfirms() {
    const confirmed = {};
    try {
        Object.keys(localStorage)
            .filter((key) => key.startsWith(LEGACY_WHISPER_CONFIRM_PREFIX))
            .forEach((key) => {
                if (localStorage.getItem(key) === '1') {
                    confirmed[key.slice(LEGACY_WHISPER_CONFIRM_PREFIX.length)] = true;
                }
            });
    } catch (error) {
        console.warn('[AI Video Note] 迁移下载确认记录失败：', error);
    }
    return confirmed;
}

function migrateLegacyPrefs() {
    if (readStoredPrefs()) return null;
    const next = defaultPrefs();
    const legacyProvider = localStorage.getItem('llm_provider')
        || LEGACY_PROVIDER_MAP[localStorage.getItem('llm_model')]
        || '';
    next.provider = PROVIDER_CONFIG[legacyProvider] ? legacyProvider : defaultProvider();
    const legacyModelId = localStorage.getItem('llm_model_id') || '';
    const legacyManual = (localStorage.getItem('custom_model_name') || '').trim();
    const legacyBaseUrl = (localStorage.getItem('custom_base_url') || '').trim();
    // custom 的两个键本来就只属于自定义接口，直接搬进那条档案不会串到别的档案；
    // 真正会串味的是被所有 Provider 共用的 llm_model_id，下面按档案逐个甄别。
    const customEntry = {
        id: 'c-1',
        label: '自定义接口',
        base_url: legacyBaseUrl,
        model_id: CUSTOM_MODEL_ID,
        manual_model: legacyManual
    };
    next.customs.push(customEntry);
    if (next.provider === 'custom') {
        next.custom_id = customEntry.id;
    } else if (isPresetModel(next.provider, legacyModelId)) {
        next.by_provider[next.provider] = { model_id: legacyModelId, manual_model: '' };
    } else if (legacyModelId === CUSTOM_MODEL_ID && legacyManual) {
        // 内置 Provider + 手填模型 ID：这两个键本来就成对使用，不属于串味的数据。
        next.by_provider[next.provider] = { model_id: CUSTOM_MODEL_ID, manual_model: legacyManual };
    }
    // 其余情况（llm_model_id 属于别的 Provider，即旧版共用单键造成的串写）直接丢弃：
    // 宁可让用户重选一次，也不会把 A 档案的模型 ID 变成 B 档案输入框里的内容。
    GLOBAL_PREF_KEYS.forEach((key) => {
        const value = localStorage.getItem(key);
        if (value !== null) next.global[key] = value;
    });
    next.global.timed_screenshots = next.global.timed_screenshots === 'true';
    next.global.include_screenshots = next.global.include_screenshots === 'true';
    next.global.use_gpu = next.global.use_gpu === true || next.global.use_gpu === 'true';
    // 「最大」这一档已从界面上去掉（实测对笔记任务是净伤害，见后端 generate_summary 那条）；
    // 存过它的旧偏好按「质量」处理，不要静默变成"自动"
    if (next.global.reasoning_effort === 'max') {
        next.global.reasoning_effort = 'high';
    }
    if (!['auto', 'off', 'high'].includes(next.global.reasoning_effort)) {
        next.global.reasoning_effort = 'auto';
    }
    const theme = localStorage.getItem('theme');
    if (theme) next.ui.theme = theme;
    next.global.whisper_model = normalizeWhisperModel(next.global.whisper_model);
    next.confirmations.whisper_download = legacyWhisperConfirms();
    return next;
}

function mergePrefs(stored) {
    if (!stored) return null;
    if (stored.schema > PREFS_SCHEMA) throw new Error('设置来自更新版本，已停止写入以保护原文件');
    const next = defaultPrefs();
    if (Array.isArray(stored.templates)) next.templates = stored.templates.filter(t => t && typeof t.id === 'string' && typeof t.name === 'string' && t.settings && typeof t.settings === 'object').slice(0, 50).map(sanitizeForStorage);
    if (typeof stored.default_template_id === 'string') next.default_template_id = stored.default_template_id;
    if (typeof stored.provider === 'string') next.provider = stored.provider;
    if (typeof stored.custom_id === 'string') next.custom_id = stored.custom_id;
    if (Array.isArray(stored.customs)) {
        next.customs = stored.customs
            .filter((entry) => entry && typeof entry === 'object' && typeof entry.id === 'string')
            .map((entry) => ({
                id: entry.id,
                label: String(entry.label || ''),
                base_url: String(entry.base_url || ''),
                model_id: String(entry.model_id || ''),
                manual_model: String(entry.manual_model || '')
            }));
    }
    if (stored.by_provider && typeof stored.by_provider === 'object') {
        Object.entries(stored.by_provider).forEach(([provider, record]) => {
            if (!PROVIDER_CONFIG[provider] || !record || typeof record !== 'object') return;
            next.by_provider[provider] = {
                model_id: String(record.model_id || ''),
                manual_model: String(record.manual_model || '')
            };
        });
    }
    if (stored.global && typeof stored.global === 'object') {
        GLOBAL_PREF_KEYS.forEach((key) => {
            const value = stored.global[key];
            if (value !== undefined && value !== null) next.global[key] = value;
        });
        next.global.gpu_backend = 'auto';
        if (!['brief', 'standard', 'detailed'].includes(next.global.text_detail)) next.global.text_detail = 'detailed';
    }
    if (stored.confirmations && typeof stored.confirmations === 'object') {
        const confirmed = stored.confirmations.whisper_download;
        if (confirmed && typeof confirmed === 'object') {
            Object.entries(confirmed).forEach(([model, value]) => {
                if (value === true) next.confirmations.whisper_download[model] = true;
            });
        }
    }
    if (stored.ui && ['small', 'compact', 'standard', 'comfortable', 'large', 'maximum'].includes(stored.ui.font_size)) next.ui.font_size = stored.ui.font_size === 'small' ? 'compact' : stored.ui.font_size;
    if (stored.ui && typeof stored.ui.theme === 'string') next.ui.theme = stored.ui.theme;
    if (stored.ui && stored.ui.mcp_hint_seen === true) next.ui.mcp_hint_seen = true;
    if (stored.ui && ['http', 'sse'].includes(stored.ui.mcp_transport)) next.ui.mcp_transport = stored.ui.mcp_transport;
    if (stored.update && typeof stored.update.ignored_version === 'string') next.update.ignored_version = stored.update.ignored_version;
    next.global.whisper_model = normalizeWhisperModel(next.global.whisper_model);
    return next;
}

function initPreferences() {
    let stored = null;
    try {
        const source = workspacePreferences.mode === 'ready' && workspacePreferences.source
            ? workspacePreferences.source : readStoredPrefs() || migrateLegacyPrefs();
        stored = mergePrefs(source);
        workspacePreferences.source = source;
        if (workspacePreferences.mode === 'blocked') stored = null;
    } catch (error) {
        // 隐私模式等禁用本地存储时，只丢"记住上次的选择"，页面必须照常可用。
        console.warn('[AI Video Note] 读取历史偏好失败，已有记录保持不变：', error);
        workspacePreferences.mode = 'blocked';
        preferenceStorageNotice(error.message);
    }
    prefs = stored || defaultPrefs();
    if (!PROVIDER_CONFIG[prefs.provider]) prefs.provider = defaultProvider();
    applyPreferencesToForm();
    persistPrefs();
}

function providerRecord(provider) {
    if (!prefs.by_provider[provider]) {
        prefs.by_provider[provider] = { model_id: '', manual_model: '' };
    }
    return prefs.by_provider[provider];
}

function newCustomEntry(label) {
    return {
        id: `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
        label: label || '自定义接口',
        base_url: '',
        model_id: '',
        manual_model: ''
    };
}

function ensureCustoms() {
    if (!prefs.customs.length) prefs.customs.push(newCustomEntry());
    return prefs.customs;
}

function activeCustom() {
    if (prefs.provider !== 'custom') return null;
    const entries = ensureCustoms();
    const entry = entries.find((item) => item.id === prefs.custom_id) || entries[0];
    prefs.custom_id = entry.id;
    return entry;
}

// 当前档案自己的记录：模型选择和手填地址都只属于它，不与其他档案共用字段。
function activeRecord() {
    return prefs.provider === 'custom' ? activeCustom() : providerRecord(prefs.provider);
}

function profileSelectValue() {
    return prefs.provider === 'custom'
        ? `${CUSTOM_PROFILE_PREFIX}${prefs.custom_id}`
        : prefs.provider;
}

function selectProfileByValue(value) {
    if (value.startsWith(CUSTOM_PROFILE_PREFIX)) {
        prefs.provider = 'custom';
        const id = value.slice(CUSTOM_PROFILE_PREFIX.length);
        if (prefs.customs.some((item) => item.id === id)) prefs.custom_id = id;
    } else if (PROVIDER_CONFIG[value]) {
        prefs.provider = value;
    }
}

function renderProfileOptions() {
    const select = byId('llmProfile');
    if (!select) return null;
    ensureCustoms();
    select.replaceChildren();
    Object.entries(PROVIDER_CONFIG).forEach(([provider, config]) => {
        if (provider === 'custom') return;
        select.add(new Option(config.name, provider));
    });
    prefs.customs.forEach((entry) => {
        const option = new Option(entry.label || '自定义接口', `${CUSTOM_PROFILE_PREFIX}${entry.id}`);
        option.title = entry.base_url || '尚未填写接口地址';
        select.add(option);
    });
    const wanted = profileSelectValue();
    const available = Array.from(select.options).map((option) => option.value);
    // 档案被删除或旧版 HTML 缺少选项时退回可用项，但用户选过的档案仍留在偏好里。
    select.value = available.includes(wanted) ? wanted : available[0];
    if (select.value !== wanted) selectProfileByValue(select.value);
    updateRenameVisibility();
    return select;
}

function applyPreferencesToForm() {
    if (byId("generationMode")) byId("generationMode").value = prefs.global.generation_mode || "api";
    if (byId('mcpTransport')) byId('mcpTransport').value = prefs.ui.mcp_transport || 'http';
    if (byId('agentExecution')) byId('agentExecution').value = prefs.global.agent_execution || 'manual';
    if (byId('agentProvider')) byId('agentProvider').value = (prefs.global.agent_provider === 'gemini' ? 'antigravity' : prefs.global.agent_provider) || 'codex';
    if (byId('agentCommand')) byId('agentCommand').value = prefs.global.agent_command || '';
    activeAgentModelProvider = byId('agentProvider')?.value || 'codex';
    const savedAgentModel = prefs.global.agent_models?.[activeAgentModelProvider];
    renderAgentModelOptions(savedAgentModel?.model ?? prefs.global.agent_model ?? '', savedAgentModel?.effort ?? prefs.global.agent_reasoning_effort ?? 'default');
    renderProfileOptions();
    renderProfileForm();
    // Templates may still contain a retired model even after preferences migrate.
    prefs.global.whisper_model = normalizeWhisperModel(prefs.global.whisper_model);
    byId('whisperModel').value = prefs.global.whisper_model;
    snapshotWhisperLabels();
    byId('screenshotDensity').value = ['none', 'few', 'medium', 'many'].includes(prefs.global.screenshot_density)
        ? prefs.global.screenshot_density : (prefs.global.include_screenshots === true ? 'medium' : 'none');
    byId('timedScreenshots').checked = prefs.global.timed_screenshots === true;
    visionPreference = prefs.global.vision_screenshots === true;
    cropPreference = prefs.global.auto_crop_screenshots === true;
    const interval = Number(prefs.global.screenshot_interval);
    byId('screenshotInterval').value = Number.isInteger(interval) && interval >= 1 && interval <= 86400 ? String(interval) : '10';
    byId('videoResolution').value = ['360', '480', '720', '1080'].includes(String(prefs.global.video_resolution)) ? String(prefs.global.video_resolution) : '720';
    if (byId('videoEncoding')) byId('videoEncoding').value = prefs.global.video_encoding === 'size' && window.videoCodecAvailable ? 'size' : 'compatibility';
    byId('videoDownloadDirectory').value = prefs.global.video_download_directory || '';
    byId('noteSaveDirectory').value = prefs.global.note_save_directory || '';
    renderNoteSaveLocation();
    byId('useGpu').checked = prefs.global.use_gpu === true;
    prefs.global.gpu_backend = 'auto';
    updateGpuSettings();
    refreshWhisperModelHints();
    if (prefs.global.summary_style === 'obsidian') {
        prefs.global.summary_style = 'faithful';
        prefs.global.note_format = 'obsidian';
    }
    if (prefs.global.output_mode === 'obsidian') {
        prefs.global.output_mode = 'note';
        prefs.global.note_format = 'obsidian';
    }
    byId('formatSelect').value = ['markdown', 'obsidian', 'docx', 'html', 'json', 'txt', 'png', 'pdf', 'enex'].includes(prefs.global.note_format)
        ? prefs.global.note_format : 'markdown';
    if (prefs.global.summary_style === 'detailed') {
        prefs.global.summary_style = 'faithful'; prefs.global.ai_commentary = true;
    }
    if (prefs.global.summary_style === 'science') prefs.global.summary_style = 'faithful';
    if (prefs.global.summary_style === 'cards') prefs.global.summary_style = 'faithful';
    if (prefs.global.summary_style === 'outline') prefs.global.summary_style = 'concise';
    byId('summaryStyle').value = prefs.global.summary_style;
    byId('noteLanguage').value = prefs.global.note_language || (document.documentElement.lang === 'en' ? 'en' : 'zh-CN');
    byId('aiCommentary').value = normalizeAiCommentaryPosition(prefs.global.ai_commentary_position, prefs.global.ai_commentary);
    byId('timelineMode').value = prefs.global.timeline_mode || 'all';
    byId('sourceLinkPosition').value = prefs.global.source_link_position || 'end';
    byId('textDetail').value = prefs.global.text_detail || 'detailed';
    updateSummaryStyleHint();
    byId('obsidianTags').checked = Boolean(prefs.global.obsidian_tags);
    byId('obsidianCallouts').checked = Boolean(prefs.global.obsidian_callouts);
    byId('obsidianProperties').checked = prefs.global.obsidian_properties !== false;
    byId('obsidianVault').value = prefs.global.obsidian_vault || '';
    byId('obsidianNoteFolder').value = prefs.global.obsidian_note_folder || '';
    byId('obsidianAttachmentMode').value = prefs.global.obsidian_attachment_mode || 'note_subfolder';
    byId('obsidianAttachmentFolder').value = prefs.global.obsidian_attachment_folder || 'attachments';
    updateObsidianSettings();
    // 存过「最大」的旧偏好落到「质量」而不是被判成非法值退回 auto：
    // 这个 select 的值就是提交给后端的 reasoning_effort，静默变 auto 会改变实际档位
    byId('reasoningEffort').value = prefs.global.reasoning_effort === 'max'
        ? 'high'
        : (['auto', 'off', 'high'].includes(prefs.global.reasoning_effort)
            ? prefs.global.reasoning_effort : 'auto');
    if (byId('processingMode')) byId('processingMode').value = prefs.global.processing_mode === 'reuse' ? 'reuse' : 'restart';
    const processingModeInput = document.querySelector(
        `input[name="processingMode"][value="${prefs.global.processing_mode}"]`
    );
    if (processingModeInput) processingModeInput.checked = true;
    // 旧版 HTML 缓存里没有这组单选时，selectedOutputMode() 自然回落到笔记路线
    const outputModeInput = document.querySelector(
        `input[name="outputMode"][value="${prefs.global.output_mode}"]`
    );
    if (outputModeInput) outputModeInput.checked = true;
    applyOutputMode();
    toggleScreenshotSettings();
    applyTheme(prefs.ui.theme);
    applyFontSize(prefs.ui.font_size);
    refreshKeyStatus();
}

function applyFontSize(size) {
    const allowed = ['compact', 'standard', 'comfortable', 'large', 'maximum'];
    const migrated = size === 'small' ? 'compact' : size;
    const selected = allowed.includes(migrated) ? migrated : 'standard';
    document.documentElement.dataset.fontSize = selected;
    if (byId('fontSize')) byId('fontSize').value = selected;
}

function initThemeControl() {
    if (byId('fontSize')) byId('fontSize').addEventListener('change', (event) => {
        prefs.ui.font_size = event.target.value;
        persistPrefs();
        applyFontSize(prefs.ui.font_size);
    });
    byId('themeControl').addEventListener('click', (event) => {
        const button = event.target.closest('[data-theme-option]');
        if (!button) return;
        prefs.ui.theme = button.dataset.themeOption;
        persistPrefs();
        applyTheme(prefs.ui.theme);
    });
}

let themeTransition = null;
let themeRevision = 0;
function applyTheme(theme) {
    const revision = ++themeRevision;
    const update = () => {
        if (revision === themeRevision) applyThemeImmediately(theme);
    };
    themeTransition?.skipTransition();
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
    if (typeof document.startViewTransition === 'function' && !reduced && document.documentElement.dataset.theme !== theme) {
        try {
            themeTransition = document.startViewTransition(update);
            themeTransition.ready.catch(() => {});
            themeTransition.finished.catch(() => update());
        } catch (_error) {
            update();
        }
    } else update();
}

function applyThemeImmediately(theme) {
    const root = document.documentElement;
    // Hover transitions otherwise delay some surfaces while the page changes immediately.
    // Flush the theme while transitions are suppressed, then restore ordinary feedback.
    const changing = root.dataset.theme !== theme && !!root.classList;
    if (changing) {
        root.classList.add('theme-changing');
        void root.offsetHeight;
    }
    try {
        root.dataset.theme = theme;
        document.querySelectorAll('#themeControl [data-theme-option]').forEach((button) => {
            const isActive = button.dataset.themeOption === theme;
            button.classList.toggle('active', isActive);
            button.setAttribute('aria-pressed', String(isActive));
        });
        if (changing) void root.offsetHeight;
    } finally {
        if (changing) root.classList.remove('theme-changing');
    }
}

let noteDirectoryPickerBusy = false;

function renderNoteSaveLocation() {
    if (!byId('noteSaveDirectory')) return;
    const directory = byId('noteSaveDirectory').value.trim();
    const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
    byId('noteSaveDirectoryLabel').textContent = directory || tr('使用默认保存位置');
    byId('noteSaveDirectoryLabel').title = directory || tr('使用默认保存位置');
    byId('resetNoteDirectoryBtn').hidden = !directory;
    byId('chooseNoteDirectoryBtn').disabled = isTaskActive || noteDirectoryPickerBusy || byId('noteSaveDirectory').disabled;
    byId('resetNoteDirectoryBtn').disabled = isTaskActive || noteDirectoryPickerBusy || byId('noteSaveDirectory').disabled;
    byId('chooseNoteDirectoryBtn').textContent = tr(noteDirectoryPickerBusy ? '请在系统窗口中选择文件夹…' : '选择保存文件夹');
}

function resetNoteSaveDirectory() {
    if (isTaskActive || noteDirectoryPickerBusy || byId('noteSaveDirectory').disabled) return;
    byId('noteSaveDirectory').value = '';
    renderNoteSaveLocation();
    persistGlobals();
}

async function exportAgentSkill() {
    const button = byId('exportSkillBtn');
    if (!button || button.disabled) return;
    const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
    const status = byId('skillExportStatus');
    button.disabled = true;
    status.textContent = tr('请在系统窗口中选择 Skill 保存文件夹…');
    try {
        const response = await fetch(`${API_BASE}/skill/export`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({language: window.AIVideoNoteI18n?.language === 'en' ? 'en' : 'zh-CN'})
        });
        const result = await readResponse(response, 'Skill 导出失败');
        status.textContent = result.cancelled ? tr('已取消导出') : `${tr('Skill 已保存：')}${result.path}`;
    } catch (error) {
        status.textContent = error.message || tr('Skill 导出失败');
    } finally {
        button.disabled = false;
    }
}

async function chooseNoteSaveDirectory() {
    if (isTaskActive || noteDirectoryPickerBusy || byId('noteSaveDirectory').disabled) return;
    noteDirectoryPickerBusy = true;
    renderNoteSaveLocation();
    try {
        // A person may spend several minutes in the system dialog: no fetch timeout.
        const response = await fetch(`${API_BASE}/note-directory/select`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({current_directory: byId('noteSaveDirectory').value,
                language: window.AIVideoNoteI18n?.language === 'en' ? 'en' : 'zh-CN'})
        });
        const result = await readResponse(response, '选择保存文件夹失败');
        if (!result.cancelled && typeof result.directory === 'string' && result.directory) {
            byId('noteSaveDirectory').value = result.directory;
            persistGlobals();
        }
    } catch (error) {
        showToast(error.message || '选择保存文件夹失败', 'error');
    } finally {
        noteDirectoryPickerBusy = false;
        renderNoteSaveLocation();
    }
}

function persistGlobals() {
    if (byId("generationMode")) prefs.global.generation_mode = byId("generationMode").value;
    if (byId('agentExecution')) prefs.global.agent_execution = byId('agentExecution').value;
    if (byId('agentProvider')) prefs.global.agent_provider = byId('agentProvider').value;
    if (byId('agentCommand')) prefs.global.agent_command = byId('agentCommand').value;
    prefs.global.agent_model = selectedAgentModel();
    prefs.global.agent_reasoning_effort = byId('agentReasoningEffort')?.value || 'default';
    prefs.global.agent_models ||= {};
    prefs.global.agent_models[activeAgentModelProvider] = {model: prefs.global.agent_model, effort: prefs.global.agent_reasoning_effort};
    const values = prefs.global;
    values.note_format = selectedNoteFormat();
    values.obsidian_tags = byId('obsidianTags').checked;
    values.obsidian_callouts = byId('obsidianCallouts').checked;
    values.obsidian_properties = byId('obsidianProperties').checked;
    values.obsidian_vault = byId('obsidianVault').value.trim();
    values.obsidian_note_folder = byId('obsidianNoteFolder').value.trim();
    values.obsidian_attachment_mode = byId('obsidianAttachmentMode').value;
    values.obsidian_attachment_folder = byId('obsidianAttachmentFolder').value.trim();
    values.whisper_model = byId('whisperModel').value || values.whisper_model;
    values.video_resolution = byId('videoResolution').value;
    values.video_encoding = byId('videoEncoding')?.value || 'compatibility';
    values.video_download_directory = byId('videoDownloadDirectory').value.trim();
    values.note_save_directory = byId('noteSaveDirectory').value.trim();
    values.screenshot_density = selectedScreenshotDensity();
    values.timed_screenshots = timedScreenshotEnabled();
    values.vision_screenshots = visionPreference;
    values.auto_crop_screenshots = cropPreference;
    values.screenshot_interval = byId('screenshotInterval').value;
    values.include_screenshots = screenshotsEnabled();
    values.use_gpu = byId('useGpu').checked;
    values.gpu_backend = 'auto';
    values.timeline_mode = byId('timelineMode').value;
    values.source_link_position = byId('sourceLinkPosition').value;
    values.text_detail = byId('textDetail').value;
    values.summary_style = byId('summaryStyle').value;
    values.note_language = prefs.global.note_language || '';
    values.ai_commentary_position = selectedAiCommentaryPosition();
    values.ai_commentary = values.ai_commentary_position !== 'none';
    values.reasoning_effort = byId('reasoningEffort').value;
    values.processing_mode = selectedProcessingMode();
    values.output_mode = document.querySelector('input[name="outputMode"]:checked')?.value
        || values.output_mode;
    persistPrefs();
}

function persistProfileForm() {
    const record = activeRecord();
    if (!record) return;
    record.model_id = byId('llmModel').value || '';
    record.manual_model = byId('customModelName').value.trim();
    if (prefs.provider === 'custom') record.base_url = byId('customBaseUrl').value.trim();
    persistPrefs();
}

function handleModelChange() {
    toggleCustomConfig();
    persistProfileForm();
}

// 改动即记住：曾经只有点「保存偏好」才落盘，正常提交不保存，
// 于是重开页面必然弹回默认档案。
function bindPreferenceAutoSave() {
    bindListener('noteLanguage', 'change', () => { prefs.global.note_language = byId('noteLanguage').value; persistGlobals(); });
    bindListener('aiCommentary', 'change', persistGlobals);
    bindListener('summaryStyle', 'change', () => { updateObsidianSettings(); updateSummaryStyleHint(); });
    ['obsidianVault', 'obsidianNoteFolder', 'obsidianAttachmentMode', 'obsidianAttachmentFolder'].forEach((id) => {
        bindListener(id, 'change', () => { updateObsidianSettings(); persistGlobals(); });
        if (id !== 'obsidianAttachmentMode') {
            bindListener(id, 'input', () => { updateObsidianSettings(); schedulePersist(persistGlobals); });
        }
    });
    ['obsidianTags', 'obsidianCallouts', 'obsidianProperties', 'timelineMode', 'sourceLinkPosition', 'textDetail', 'whisperModel', 'summaryStyle', 'reasoningEffort', 'screenshotDensity', 'videoResolution', 'useGpu']
        .forEach((id) => bindListener(id, 'change', persistGlobals));
    bindListener('useGpu', 'change', () => { updateGpuSettings(); persistGlobals(); refreshWhisperModelHints(); });
    bindListener('whisperModel', 'change', refreshGpuDetected);
    bindListener('processingMode', 'change', persistGlobals);
    bindListener('videoDownloadDirectory', 'change', persistGlobals);
    bindListener('chooseNoteDirectoryBtn', 'click', chooseNoteSaveDirectory);
    bindListener('exportSkillBtn', 'click', exportAgentSkill);
    bindListener('resetNoteDirectoryBtn', 'click', resetNoteSaveDirectory);
    bindListener('videoDownloadDirectory', 'input', () => schedulePersist(persistGlobals));
    document.querySelectorAll('input[name="processingMode"]').forEach((input) => {
        input.addEventListener('change', persistGlobals);
    });
    ['customBaseUrl', 'customModelName'].forEach((id) => {
        const element = byId(id);
        if (!element) return;
        element.addEventListener('input', () => {
            toggleCustomConfig();
            schedulePersist(persistProfileFormAndKeyStatus);
        });
    });
    bindListener('customProfileLabel', 'input', handleCustomProfileLabelInput);
    bindListener('apiKey', 'input', updateKeyControls);
    window.addEventListener('pagehide', flushPendingPrefs);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') flushPendingPrefs();
    });
}

function persistProfileFormAndKeyStatus() {
    persistProfileForm();
    refreshKeyStatus();
}

function resetPreferences() {
    const message = '将把模型档案选择、识别参数和主题恢复为默认值。\n\n'
        + '将重置 workspace 中的界面偏好，保留模板和本机已保存的 API Key。确定继续吗？';
    if (!window.confirm(message)) return;
    const savedTemplates = prefs.templates || [];
    prefs = defaultPrefs();
    prefs.templates = savedTemplates;
    applyPreferencesToForm();
    window.refreshNoteTemplates?.();
    persistPrefs();
    showToast('已恢复默认偏好', 'info');
}

function handleProfileChange() {
    const select = byId('llmProfile');
    if (!select) return;
    selectProfileByValue(select.value);
    // 换档案时清掉输入框里未保存的 Key：贴错档案比多贴一次危险得多。
    clearTypedApiKey();
    keyMatch = null;
    renderProfileForm();
    persistPrefs();
    refreshKeyStatus();
}

function renderProfileForm() {
    const record = activeRecord();
    byId('customBaseUrl').value = (record && record.base_url) || '';
    byId('customProfileLabel').value = (record && record.label) || '';
    populateModelOptions(record);
    updateKeyControls();
}

// 只读取当前档案自己的记录：preferred 不再可能来自另一个档案，
// 识别不了的模型 ID 只会落回本档案的「手动输入」选项，绝不写入任何输入框。
function populateModelOptions(record) {
    const provider = prefs.provider;
    const providerConfig = PROVIDER_CONFIG[provider] || PROVIDER_CONFIG[defaultProvider()];
    const modelSelect = byId('llmModel');
    modelSelect.replaceChildren();

    providerConfig.models.forEach(([modelId, label]) => {
        const option = new Option(label, modelId);
        option.title = modelId;
        modelSelect.add(option);
    });
    modelSelect.add(new Option('手动输入模型 ID…', CUSTOM_MODEL_ID));

    const preferred = (record && record.model_id) || providerConfig.defaultModel;
    const hasPreset = Boolean(preferred)
        && Array.from(modelSelect.options).some((option) => option.value === preferred);
    modelSelect.value = hasPreset ? preferred : CUSTOM_MODEL_ID;
    byId('customModelName').value = (record && record.manual_model) || '';
    toggleCustomConfig();
}

function toggleCustomConfig() {
    const customProvider = prefs.provider === 'custom';
    const customModel = byId('llmModel').value === CUSTOM_MODEL_ID;
    byId('customBaseUrlField').hidden = !customProvider;
    byId('customModelNameField').hidden = !customModel;
    byId('customApiConfig').hidden = !customProvider;
}

// ---- 自定义接口档案（可命名多份） ----

// 档案下拉旁的「重命名」：只对已保存的自定义档案生效（内置 Provider 不可改名）。
function updateRenameVisibility() {
    const button = byId('renameProfileBtn');
    if (!button) return;
    const select = byId('llmProfile');
    button.hidden = !String((select && select.value) || '').startsWith(CUSTOM_PROFILE_PREFIX);
}

function handleProfileRenameClick() {
    const record = activeCustom();
    if (!record) return;
    const next = window.prompt('新的档案名称', record.label || '自定义接口');
    if (next === null) return;
    const trimmed = next.trim();
    if (!trimmed || trimmed === record.label) return;
    record.label = trimmed;
    byId('customProfileLabel').value = trimmed;
    renderProfileOptions();
    persistPrefs();
    showToast(`已改名为「${trimmed}」`, 'success');
}

function handleCustomProfileLabelInput() {
    const record = activeCustom();
    if (!record) return;
    record.label = byId('customProfileLabel').value.trim();
    schedulePersist(() => {
        persistPrefs();
        renderProfileOptions();
        updateKeyControls();
    });
}

function addCustomProfile() {
    persistProfileForm();
    const current = activeCustom();
    const entry = newCustomEntry('新的自定义接口');
    if (current) entry.base_url = current.base_url;
    prefs.provider = 'custom';
    prefs.customs.push(entry);
    prefs.custom_id = entry.id;
    renderProfileOptions();
    renderProfileForm();
    persistPrefs();
    refreshKeyStatus();
    // 新档案默认叫「新的自定义接口」，聚焦名称框引导第一件事就是改名
    byId('customProfileLabel').focus();
    showToast('已新建自定义接口档案，填好名称和地址即可使用', 'info');
}

function deleteCustomProfile() {
    const entry = activeCustom();
    if (!entry) return;
    if (prefs.customs.length <= 1) {
        showToast('至少保留一个自定义接口档案', 'info');
        return;
    }
    const message = `删除档案「${entry.label}」？\n\n`
        + '只删除本浏览器里的名称、地址和模型选择；本机已保存的 API Key 按接口地址保管，'
        + '不会被清除（别的档案指向同一地址时仍可复用）。';
    if (!window.confirm(message)) return;
    prefs.customs = prefs.customs.filter((item) => item.id !== entry.id);
    prefs.custom_id = prefs.customs[0].id;
    renderProfileOptions();
    renderProfileForm();
    persistPrefs();
    refreshKeyStatus();
    showToast(`已删除档案「${entry.label}」`, 'success');
}

// ---- 本机已保存的 API Key（按接口地址保管，浏览器从不持有） ----

let keyStatus = { storage: null, error: null };
let keyMatch = null;
let keyStatusToken = 0;

function hasReusableKey() {
    return Boolean(keyMatch && keyMatch.key_state === 'saved');
}

// Key 输入框的唯一读取入口：页面元素缺失时退回空串，而不是在启动路径上抛错。
function typedApiKey() {
    const element = byId('apiKey');
    return element ? element.value.trim() : '';
}

function clearTypedApiKey() {
    const element = byId('apiKey');
    if (element) element.value = '';
}

// 让后端按它自己的地址规范化规则回答"这个端点存了 Key 没有"，
// 前端不复制一套规范化逻辑，就不会出现两边判断不一致。
async function refreshKeyStatus() {
    const token = ++keyStatusToken;
    const config = getSelectedModelConfig();
    if (!config || !config.baseUrl) {
        keyMatch = null;
        keyStatus = { storage: null, error: null };
        updateKeyControls();
        return;
    }
    const params = new URLSearchParams({ base_url: config.baseUrl, provider: config.provider });
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/llm-keys?${params.toString()}`, { cache: 'no-store' }, 8000
        );
        const data = await readResponse(response, '读取本机 Key 状态失败');
        if (token !== keyStatusToken) return;
        keyStatus = { storage: data.storage || null, error: data.error || null };
        keyMatch = data.match || null;
    } catch (error) {
        if (token !== keyStatusToken) return;
        keyStatus = { storage: null, error: null };
        keyMatch = null;
    }
    updateKeyControls();
}

function updateKeyControls() {
    const chip = byId('keyState');
    if (!chip) return;
    const typed = typedApiKey();
    const config = getSelectedModelConfig();
    const insecure = Boolean(keyStatus.storage) && keyStatus.storage.secure === false;
    const suffix = insecure ? '（本机不支持加密，明文存储）' : '';
    let text;
    let state = 'muted';
    if (keyStatus.error === 'corrupt_keys_file') {
        text = '本机密钥档案已损坏，可删除 workspace/llm_keys.json 后重新保存';
        state = 'error';
    } else if (!config || !config.baseUrl) {
        text = '填好接口地址后才能确认本机是否已保存 Key';
    } else if (typed) {
        text = hasReusableKey()
            ? `已填入新 Key（本机现存的仍是 ${keyMatch.api_key_masked || ''}）`
            : '已填入 Key，未保存则只在本次页面会话内有效';
        state = 'warn';
    } else if (keyMatch && keyMatch.key_state === 'undecryptable') {
        text = '本机已存的 Key 无法解密（可能来自其他机器或其他 Windows 账户），请重填后保存';
        state = 'error';
    } else if (hasReusableKey()) {
        text = `本机已保存 ${keyMatch.api_key_masked || ''} · ${keyMatch.label || keyMatch.endpoint || ''}${suffix}`;
        state = 'ok';
    } else if (keyStatus.storage) {
        text = `该接口未保存 Key${suffix}`;
        state = 'muted';
    } else {
        text = '未连接本机服务，暂不确定是否已保存 Key';
        state = 'muted';
    }
    chip.textContent = text;
    chip.className = `key-chip ${state}`;
    scheduleVisionCheck();
    const saveButton = byId('saveKeyBtn');
    if (saveButton) saveButton.disabled = !typed;
    const clearButton = byId('clearKeyBtn');
    if (clearButton) clearButton.hidden = !(keyMatch && keyMatch.saved);
}

async function saveApiKey() {
    const config = getSelectedModelConfig();
    const typed = typedApiKey();
    const button = byId('saveKeyBtn');
    if (!config || !typed) return;
    if (!config.baseUrl) {
        showToast('请先填写 API Base URL', 'error');
        return;
    }
    if (button) button.disabled = true;
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/llm-keys`,
            {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider: config.provider,
                    base_url: config.baseUrl,
                    label: config.profileLabel,
                    model: config.model || null,
                    api_key: typed
                })
            },
            15000
        );
        await readResponse(response, '保存失败');
        clearTypedApiKey();
        showToast(`Key 已加密保存到本机：${config.profileLabel}`, 'success');
    } catch (error) {
        showToast(`保存失败：${error.message}`, 'error');
    }
    await refreshKeyStatus();
}

async function clearSavedKey() {
    const config = getSelectedModelConfig();
    if (!config || !config.baseUrl) return;
    const message = `将从本机删除接口地址 ${config.baseUrl} 的 API Key，删除后需重新填写。确定吗？`;
    if (!window.confirm(message)) return;
    const params = new URLSearchParams({ base_url: config.baseUrl, provider: config.provider });
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/llm-keys?${params.toString()}`, { method: 'DELETE' }, 10000
        );
        await readResponse(response, '清除失败');
        showToast('已从本机清除该接口的 Key', 'success');
    } catch (error) {
        showToast(`清除失败：${error.message}`, 'error');
    }
    await refreshKeyStatus();
}

async function testLlmConnection() {
    const button = byId('llmTestBtn');
    const status = byId('llmTestStatus');
    const modelConfig = getSelectedModelConfig();
    if (!modelConfig) return;
    if (!modelConfig.baseUrl) {
        status.textContent = '请先填写 API Base URL';
        status.className = 'llm-test-status error';
        return;
    }
    const apiKey = typedApiKey();
    const body = {
        model_type: modelConfig.provider,
        base_url: modelConfig.baseUrl,
        model: modelConfig.model
    };
    if (apiKey) body.api_key = apiKey;
    button.disabled = true;
    status.textContent = '测试中…';
    status.className = 'llm-test-status testing';
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/llm-test`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            },
            30_000
        );
        const data = await readResponse(response, '测试请求失败');
        const detail = data.ok ? data.message : (data.message || data.error || '连接失败');
        const source = data.key_source ? ` · Key：${data.key_source}` : '';
        status.textContent = `${detail}${source}`;
        status.className = data.ok ? 'llm-test-status ok' : 'llm-test-status error';
    } catch (error) {
        status.textContent = `测试失败：${error.message}`;
        status.className = 'llm-test-status error';
    } finally {
        button.disabled = false;
        refreshKeyStatus();
    }
}

function getSelectedModelConfig() {
    const provider = prefs.provider;
    const providerConfig = PROVIDER_CONFIG[provider];
    if (!providerConfig) return null;

    const selectedModel = byId('llmModel').value;
    const model = selectedModel === CUSTOM_MODEL_ID
        ? byId('customModelName').value.trim()
        : selectedModel;
    const baseUrl = provider === 'custom'
        ? byId('customBaseUrl').value.trim()
        : providerConfig.baseUrl;
    const profileLabel = provider === 'custom'
        ? ((activeCustom() || {}).label || '自定义接口')
        : providerConfig.name;
    const modelLabel = selectedModel === CUSTOM_MODEL_ID
        ? model
        : byId('llmModel').selectedOptions[0]?.textContent;
    return {
        provider,
        baseUrl,
        model,
        profileLabel,
        name: modelLabel ? `${providerConfig.name} · ${modelLabel}` : providerConfig.name
    };
}


// Manual imports are validated against both source and current-file revisions.
let settingsRecoveryBusy = false;

async function settingsBackupResponse(response) {
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail?.message || data.detail || '备份暂时无法读写，未修改设置');
    return data;
}

async function waitForSettingsSave() {
    flushPendingPrefs();
    const deadline = Date.now() + 12000;
    while (workspacePreferences.saving && Date.now() < deadline) {
        await new Promise(resolve=>window.setTimeout(resolve,30));
    }
    if (workspacePreferences.saving) throw new Error('设置仍在保存，请稍后重试');
}

async function exportSettingsBackup() {
    if (settingsRecoveryBusy) return;
    const button = byId('exportSettingsBtn'); button.disabled = true;
    try {
        await waitForSettingsSave();
        if (workspacePreferences.mode !== 'ready') throw new Error('请先保存当前设置后再导出');
        const data = await settingsBackupResponse(await fetchWithTimeout(`${API_BASE}/preferences/export`, {cache:'no-store'}, 10000));
        if (data.format !== 'ai-video-note-settings') throw new Error('备份文件无效，未修改设置');
        triggerBlobDownload(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}), '.json',
            `AI-Video-Note-settings-${new Date().toISOString().slice(0,10)}.json`);
        byId('settingsBackupStatus').textContent = '设置已导出';
    } catch (error) { byId('settingsBackupStatus').textContent = error.message; }
    finally { button.disabled = false; }
}

function settingsImportTaskActive() {
    return isTaskActive || isSubmitting || currentTaskStatus === 'awaiting_agent';
}

function chooseSettingsBackupFile() {
    if (settingsRecoveryBusy) return;
    if (settingsImportTaskActive()) {
        byId('settingsBackupStatus').textContent = '请等待当前任务结束后再导入设置';
        return;
    }
    const input = byId('settingsBackupFile');
    input.value = ''; // Allow selecting the same file again, including after a failed import.
    input.click(); // Keep the native file chooser in the user click's synchronous call stack.
}

async function readSettingsBackupFile() {
    const input = byId('settingsBackupFile');
    const file = input.files?.[0];
    if (!file || settingsRecoveryBusy) return;
    if (settingsImportTaskActive()) {
        byId('settingsBackupStatus').textContent = '请等待当前任务结束后再导入设置';
        input.value = '';
        return;
    }
    // Start any debounced save before pausing further preference writes.
    flushPendingPrefs();
    settingsRecoveryBusy = true;
    const controls = ['restoreSettingsBtn', 'exportSettingsBtn', 'settingsBackupFile'];
    for (const id of controls) byId(id).disabled = true;
    byId('settingsBackupStatus').textContent = '正在导入设置…';
    try {
        let document;
        try {
            if (file.size > 2*1024*1024) throw new Error();
            document = JSON.parse(await file.text());
            if (!document || typeof document !== 'object' || Array.isArray(document)) throw new Error();
        } catch (_) {
            throw new Error('备份文件无效或过大，未修改设置');
        }
        await waitForSettingsSave();
        if (settingsImportTaskActive()) throw new Error('请等待当前任务结束后再导入设置');
        const preview = await settingsBackupResponse(await fetchWithTimeout(`${API_BASE}/preferences/restore/preview`, {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({document})},10000));
        if (settingsImportTaskActive()) throw new Error('请等待当前任务结束后再导入设置');
        const data = await settingsBackupResponse(await fetchWithTimeout(`${API_BASE}/preferences/restore`, {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({document,
                backup_revision:preview.backup_revision, current_revision:preview.current_revision})},10000));
        workspacePreferences.source = data.preferences;
        workspacePreferences.revision = data.revision;
        workspacePreferences.pending = null;
        workspacePreferences.mode = 'blocked'; // No stale page autosave before reload.
        byId('settingsBackupStatus').textContent = '设置已导入，正在刷新…';
        window.location.reload();
    } catch (error) {
        byId('settingsBackupStatus').textContent = error.message;
    } finally {
        settingsRecoveryBusy = false;
        input.value = '';
        for (const id of controls) byId(id).disabled = false;
    }
}
