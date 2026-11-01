// Shared HTTP responses, timeouts and local-service diagnostics.
async function readResponse(response, fallbackMessage) {
    if (!response.ok) {
        const message = await extractErrorMessage(response, fallbackMessage);
        // 裸 5xx（连 detail 都取不到）是 旧版那批悬案的同款症状：应答的可能根本
        // 不是本程序。自动补一次本机探测，把"端口被占/服务没答话"当场说清。
        if (response.status >= 500 && message.endsWith(`（HTTP ${response.status}）`)) {
            throw new Error(await probeLocalService(message));
        }
        throw new Error(message);
    }
    try {
        return await response.json();
    } catch {
        throw new Error('后端返回了无法解析的数据');
    }
}

async function fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, { ...options, signal: controller.signal });
    } catch (error) {
        if (error.name === 'AbortError') throw new Error('请求超时');
        throw error;
    } finally {
        window.clearTimeout(timeout);
    }
}

async function extractErrorMessage(response, fallbackMessage) {
    try {
        const data = await response.json();
        return data.detail || data.error || data.message || `${fallbackMessage}（HTTP ${response.status}）`;
    } catch {
        return `${fallbackMessage}（HTTP ${response.status}）`;
    }
}

let lastLocalProbeAt = 0;

async function probeLocalService(baseMessage) {
    const now = Date.now();
    if (now - lastLocalProbeAt < 8000) return baseMessage; // 轮询类调用别反复体检
    lastLocalProbeAt = now;
    const verdict = await probeHealth();
    return `${baseMessage}。自检：${verdict}`;
}

async function probeHealth() {
    try {
        const response = await fetchWithTimeout(`${API_BASE}/health`, {}, 2500);
        const data = await response.json().catch(() => null);
        if (data && data.service === 'AI Video Note') {
            return '本机服务在答话，但刚才那个 5xx 的响应体不是它发的 JSON，多半是后端内部崩了'
                + '（详情在 workspace\\_app.log；可点「本机自检」看完整报告）';
        }
        if (data) {
            return `这个端口的应答不是 AI Video Note（${JSON.stringify(data).slice(0, 80)}）`
                + '——端口被别的程序占着，托盘退出重开试试';
        }
        return '应答不是 JSON——端口八成被别的程序占了，托盘退出重开试试';
    } catch {
        return '本机服务没在答话——托盘可能已退出：右键托盘退出后重开，无效再重启电脑';
    }
}

