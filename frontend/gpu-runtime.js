/* GPU runtime preparation is explicit and does not change the GPU preference. */
let gpuDetectionSequence = 0;
let gpuRuntimeLastPrompt = '';
let gpuRuntimeViewPinned = false;
let gpuRuntimeInstallSequence = 0;
let gpuRuntimeInstallTimer = null;
let gpuRuntimeSelectedEngine = 'auto';
let gpuRuntimeJob = null;

function gpuInstallActive(job) {
    return Boolean(job?.id && !['completed', 'failed', 'idle'].includes(job.status));
}

function stopWatchingGpuInstall() {
    gpuRuntimeInstallSequence++;
    clearTimeout(gpuRuntimeInstallTimer);
    gpuRuntimeInstallTimer = null;
    gpuRuntimeViewPinned = false;
}

function closeGpuRuntimeDialog() {
    const dialog = byId('gpuRuntimeDialog');
    if (dialog?.open) dialog.close();
    stopWatchingGpuInstall();
}

function gpuRuntimePanels(engine) {
    byId('gpuRuntimeCudaSteps').hidden = engine !== 'cuda';
    byId('gpuRuntimeVulkanSteps').hidden = engine !== 'vulkan';
}

function renderGpuRuntime(runtime) {
    if (gpuRuntimeViewPinned || gpuInstallActive(gpuRuntimeJob)) return;
    const dialog = byId('gpuRuntimeDialog');
    const model = byId('whisperModel').value;
    const relevant = model !== 'paraformer-zh';
    const missing = runtime && relevant && ['missing', 'download_on_first_use'].includes(runtime.status);
    if (!byId('useGpu').checked || !missing) {
        closeGpuRuntimeDialog();
        gpuRuntimeLastPrompt = '';
        return;
    }
    if (!['cuda', 'vulkan'].includes(runtime.engine)) return;
    const key = JSON.stringify([runtime.engine, runtime.status, [...(runtime.missing || [])].sort()]);
    if (gpuRuntimeLastPrompt === key && !dialog.open) return;
    gpuRuntimeSelectedEngine = runtime.engine;
    gpuRuntimePanels(runtime.engine);
    byId('gpuRuntimeDialogStatus').textContent = '';
    byId('gpuRuntimeInstallBtn').disabled = false;
    byId('gpuRuntimeInstallProgress').hidden = true;
    gpuRuntimeLastPrompt = key;
    if (!dialog.open) dialog.showModal();
}

async function recheckGpuRuntime() {
    const button = byId('gpuRuntimeRecheckBtn');
    button.disabled = true;
    byId('gpuRuntimeDialogStatus').textContent = '正在检查组件…';
    try {
        const runtime = await refreshGpuDetected();
        if (byId('gpuRuntimeDialog').open && runtime) {
            byId('gpuRuntimeDialogStatus').textContent = runtime.status === 'present'
                ? '已找到所需文件，请从托盘重启软件。' : '仍缺少所需组件，请按说明完成准备。';
        }
    } finally { button.disabled = false; }
}

async function refreshGpuDetected() {
    const sequence = ++gpuDetectionSequence;
    const hint = byId('gpuDetected');
    if (!byId('useGpu').checked) {
        renderGpuRuntime(null);
        hint.hidden = true;
        hint.textContent = '';
        return;
    }
    hint.hidden = false;
    hint.dataset.state = 'pending';
    hint.textContent = '正在检测显卡…';
    try {
        const response = await fetch(`${API_BASE}/gpu-device?backend=${selectedGpuBackend()}`, {cache:'no-store'});
        const data = await readResponse(response, '检测显卡失败');
        if (sequence !== gpuDetectionSequence) return;
        hint.dataset.state = data.detected ? 'detected' : 'pending';
        const wasOpen = byId('gpuRuntimeDialog').open;
        renderGpuRuntime(data.runtime);
        if (wasOpen && !gpuRuntimeViewPinned && data.runtime?.status === 'present') showToast('已找到所需文件，请从托盘重启软件。', 'success');
        const engine = selectedGpuBackend() === 'auto' && data.backend ? `（${data.backend === 'cuda' ? '优先 CUDA' : data.backend === 'cpu' ? 'CPU' : 'Vulkan'}）` : '';
        hint.textContent = data.detected ? `已检测到显卡：${data.name}${engine}` : '未检测到可用显卡，将使用 CPU';
        return data.runtime;
    } catch {
        if (sequence !== gpuDetectionSequence) return;
        hint.dataset.state = 'pending';
        hint.textContent = '暂时无法检测显卡，请确认软件已重启';
        if (byId('gpuRuntimeDialog').open) byId('gpuRuntimeDialogStatus').textContent = '组件检查失败，请稍后重新检测。';
    }
}

function renderGpuInstallJob(job) {
    gpuRuntimeJob = job;
    gpuRuntimeSelectedEngine = job.engine || gpuRuntimeSelectedEngine;
    gpuRuntimePanels(gpuRuntimeSelectedEngine);
    const active = gpuInstallActive(job);
    byId('gpuRuntimeInstallBtn').disabled = active || job.status === 'completed';
    byId('gpuRuntimeRecheckBtn').disabled = active;
    const labels = {
        checking: '正在检查组件…', downloading: '正在下载安装包…',
        awaiting_admin: '请在 Windows 管理员窗口中确认安装…', installing: '正在安装组件…',
        verifying: '正在验证显卡加速…', completed: '安装完成，已通过加速检查。请从托盘重启软件。',
        failed: '安装未完成：'
    };
    let message = labels[job.status] || '';
    if (job.status === 'failed') message += job.error || '请稍后重试，或查看手动安装说明。';
    if (job.status === 'downloading' && job.total) {
        message += ` ${(Number(job.received || 0) / 1e6).toFixed(1)} / ${(Number(job.total) / 1e6).toFixed(1)} MB`;
    }
    byId('gpuRuntimeDialogStatus').textContent = message;
    const progress = byId('gpuRuntimeInstallProgress');
    progress.hidden = !active;
    if (job.status === 'downloading' && job.total) {
        progress.max = job.total; progress.value = job.received || 0;
    } else if (job.status === 'installing' && job.total_files) {
        progress.max = job.total_files; progress.value = job.installed_files || 0;
    } else { progress.removeAttribute?.('value'); }
}

async function pollGpuInstall(sequence) {
    try {
        const response = await fetch(`${API_BASE}/gpu-runtime-install`, {cache:'no-store'});
        const job = await readResponse(response, '读取安装进度失败');
        if (sequence !== gpuRuntimeInstallSequence) return;
        renderGpuInstallJob(job);
        if (gpuInstallActive(job)) {
            gpuRuntimeInstallTimer = setTimeout(() => pollGpuInstall(sequence), 1000);
        }
    } catch (error) {
        if (sequence !== gpuRuntimeInstallSequence) return;
        byId('gpuRuntimeDialogStatus').textContent = '暂时无法读取安装进度，将自动重试。';
        gpuRuntimeInstallTimer = setTimeout(() => pollGpuInstall(sequence), 2500);
    }
}

async function openGpuInstallDialog() {
    stopWatchingGpuInstall();
    const sequence = gpuRuntimeInstallSequence;
    gpuRuntimeViewPinned = true;
    const dialog = byId('gpuRuntimeDialog');
    byId('gpuRuntimeInstallBtn').disabled = true;
    byId('gpuRuntimeRecheckBtn').disabled = false;
    byId('gpuRuntimeDialogStatus').textContent = '正在检查组件…';
    if (!dialog.open) dialog.showModal();
    try {
        const responses = await Promise.all([
            fetch(`${API_BASE}/gpu-device?backend=auto`, {cache:'no-store'}),
            fetch(`${API_BASE}/gpu-runtime-install`, {cache:'no-store'})
        ]);
        const device = await readResponse(responses[0], '检测显卡失败');
        const job = await readResponse(responses[1], '读取安装进度失败');
        if (sequence !== gpuRuntimeInstallSequence) return;
        gpuRuntimeSelectedEngine = device.backend;
        gpuRuntimePanels(device.backend);
        if (gpuInstallActive(job) || (job.engine === device.backend && job.status === 'completed' && device.runtime?.status === 'present')) {
            renderGpuInstallJob(job);
            if (gpuInstallActive(job)) pollGpuInstall(sequence);
        } else {
            gpuRuntimeJob = job;
            byId('gpuRuntimeInstallBtn').disabled = !['cuda', 'vulkan'].includes(device.backend);
            byId('gpuRuntimeDialogStatus').textContent = device.detected
                ? '点击一键安装，自动下载、安装并检查所需组件。'
                : '未检测到可用显卡，请先查看手动安装说明准备显卡驱动。';
        }
    } catch (error) {
        if (sequence !== gpuRuntimeInstallSequence) return;
        byId('gpuRuntimeDialogStatus').textContent = error.message;
    }
}

async function startGpuRuntimeInstall() {
    if (byId('gpuRuntimeInstallBtn').disabled) return;
    gpuRuntimeViewPinned = true;
    const sequence = ++gpuRuntimeInstallSequence;
    clearTimeout(gpuRuntimeInstallTimer);
    byId('gpuRuntimeInstallBtn').disabled = true;
    byId('gpuRuntimeRecheckBtn').disabled = true;
    byId('gpuRuntimeDialogStatus').textContent = '正在检查组件…';
    try {
        const response = await fetch(`${API_BASE}/gpu-runtime-install`, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({backend:gpuRuntimeSelectedEngine})
        });
        const job = await readResponse(response, '启动组件安装失败');
        if (sequence !== gpuRuntimeInstallSequence) return;
        renderGpuInstallJob(job);
        if (gpuInstallActive(job)) pollGpuInstall(sequence);
    } catch (error) {
        if (sequence !== gpuRuntimeInstallSequence) return;
        renderGpuInstallJob({engine:gpuRuntimeSelectedEngine, status:'failed', error:error.message});
    }
}

function openGpuManualDialog() {
    const dialog = byId('gpuRuntimeManualDialog');
    byId('gpuManualCudaDetails').open = gpuRuntimeSelectedEngine !== 'vulkan';
    byId('gpuManualVulkanDetails').open = gpuRuntimeSelectedEngine === 'vulkan';
    if (!dialog.open) dialog.showModal();
}

document.addEventListener('DOMContentLoaded', () => {
    bindListener('gpuRuntimeCloseBtn', 'click', closeGpuRuntimeDialog);
    bindListener('gpuRuntimeRecheckBtn', 'click', recheckGpuRuntime);
    bindListener('gpuRuntimeInstallBtn', 'click', startGpuRuntimeInstall);
    bindListener('gpuRuntimeQuickBtn', 'click', openGpuInstallDialog);
    bindListener('gpuRuntimeManualBtn', 'click', openGpuManualDialog);
    bindListener('gpuRuntimeManualOpenBtn', 'click', openGpuManualDialog);
    bindListener('gpuRuntimeManualCloseBtn', 'click', () => byId('gpuRuntimeManualDialog').close());
    bindListener('gpuRuntimeDialog', 'close', stopWatchingGpuInstall);
});
