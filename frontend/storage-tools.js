// Task-owned desktop actions and previewed, explicit cache maintenance.
(() => {
    const node = id => document.getElementById(id);
    const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
    const sizes = bytes => {
        let amount = Number(bytes) || 0, unit = 0;
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit++; }
        return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}`;
    };
    let location = null, locationBusy = false;
    const locationButtons = ['openOutputFileBtn', 'openOutputFolderBtn', 'copyOutputPathBtn'];
    function updateLocationButtons() {
        for (const id of locationButtons) if (node(id)) node(id).disabled = !location || locationBusy;
    }
    window.resetResultLocation = () => { location = null; locationBusy = false; updateLocationButtons(); };
    window.setResultLocation = (taskId, result) => {
        location = result?.output_directory ? {taskId, sequence: taskViewSequence} : null;
        locationBusy = false;
        updateLocationButtons();
    };
    async function outputAction(target) {
        if (!location || locationBusy) return;
        const snapshot = location;
        const current = () => location === snapshot && currentTaskId === snapshot.taskId && taskViewSequence === snapshot.sequence;
        locationBusy = true; updateLocationButtons();
        try {
            const prefix = `${API_BASE}/task/${encodeURIComponent(snapshot.taskId)}`;
            const response = target === 'copy'
                ? await fetchWithTimeout(`${prefix}/output-location`, {cache:'no-store'}, 15000)
                : await fetchWithTimeout(`${prefix}/output/open`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({target})}, 15000);
            const data = await readResponse(response, '读取输出位置失败');
            if (!current()) return;
            if (target === 'copy') await copyTextToClipboard(data.file_path, '路径已复制');
            else showToast(tr('已打开输出位置'), 'success');
        } catch (error) {
            if (current()) showToast(tr('打开或复制输出位置失败：') + tr(error.message), 'error');
        } finally {
            if (current()) { locationBusy = false; updateLocationButtons(); }
        }
    }

    let usage = null, plan = null, sequence = 0, pending = false, cleaning = false;
    let message = {key:'', detail:''};
    const labels = {audio:'音频素材', video:'视频素材', screenshots:'截图', models:'语音识别模型'};
    function setMessage(key, detail = '') { message = {key, detail}; renderMessage(); }
    function renderMessage() {
        node('storageStatus').textContent = tr(message.key) + (typeof message.detail === 'function' ? message.detail() : tr(message.detail));
    }
    function controls() {
        for (const id of ['storageRefreshBtn','storageAudio','storageVideo']) node(id).disabled = pending || cleaning;
        node('storageCleanBtn').disabled = pending || cleaning || !plan?.can_cleanup || !plan?.count;
    }
    function renderUsage() {
        const list = node('storageSizes'); list.replaceChildren();
        if (!usage) return;
        for (const [kind, label] of Object.entries(labels)) {
            const title = document.createElement('dt'), value = document.createElement('dd');
            title.textContent = tr(label); value.textContent = sizes(usage.categories[kind]?.bytes);
            list.append(title, value);
        }
        const title = document.createElement('dt'), value = document.createElement('dd');
        title.textContent = tr('合计'); value.textContent = sizes(usage.total_bytes); list.append(title, value);
    }
    function renderPreview() {
        node('storagePreview').hidden = !plan;
        const list = node('storagePreviewFiles'); list.replaceChildren();
        if (!plan) return;
        node('storagePreviewSummary').textContent = `${tr('待清理文件：')}${plan.count} · ${tr('文件大小：')}${sizes(plan.bytes)} · ${tr('预计释放空间：')}${sizes(plan.reclaimable_bytes)}`;
        for (const file of plan.files) {
            const item = document.createElement('li');
            item.textContent = `${tr(labels[file.kind])} · ${sizes(file.bytes)} · ${file.path}`;
            list.append(item);
        }
        if (plan.truncated) {
            const item = document.createElement('li'); item.textContent = tr('仅展示前 50 个文件，清理数量以预览合计为准。'); list.append(item);
        }
    }
    function clearPlan() { plan = null; renderPreview(); controls(); }
    async function loadStorage() {
        if (pending || cleaning) return;
        const kinds = ['audio','video'].filter(kind => node(kind === 'audio' ? 'storageAudio' : 'storageVideo').checked);
        const preview = kinds.length > 0;
        const revision = ++sequence;
        pending = true; clearPlan(); setMessage(preview ? '正在预览清理…' : '正在统计素材大小…'); controls();
        try {
            const response = await fetchWithTimeout(`${API_BASE}/storage${preview ? '/preview' : ''}`,
                preview ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({kinds})} : {cache:'no-store'}, 60000);
            const data = await readResponse(response, '读取存储信息失败');
            if (revision !== sequence) return;
            usage = data; plan = preview ? data : null;
            renderUsage(); renderPreview();
            setMessage(!data.can_cleanup ? '任务运行中，可查看统计，暂不能清理。'
                : data.unreadable_count ? '部分文件无法读取，统计可能不完整。'
                : preview ? (data.count ? '请核对预览，再点击清理。' : '没有可清理的音视频缓存。') : '素材大小已更新。');
        } catch (error) {
            if (revision === sequence) setMessage('读取存储信息失败：', () => tr(error.message));
        } finally {
            if (revision === sequence) { pending = false; controls(); }
        }
    }
    async function cleanup() {
        if (!plan?.count || !plan.can_cleanup || pending || cleaning) return;
        const token = plan.token;
        cleaning = true; clearPlan(); setMessage('正在清理缓存…'); controls();
        let received = false;
        try {
            const response = await fetchWithTimeout(`${API_BASE}/storage/cleanup`, {
                method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token})
            }, 60000);
            received = true;
            const data = await readResponse(response, '缓存清理失败');
            setMessage('缓存清理完成。', () => ` ${tr('已移除：')}${data.removed_count} · ${tr('未清理：')}${data.failed_count} · ${tr('预计释放空间：')}${sizes(data.reclaimed_bytes)}`
                + (data.index_error ? ` ${tr('部分缓存记录未能更新，请刷新检查。')}` : ''));
            // Keep the completed result visible; refresh only the size inventory.
            if (node('storageDialog').open) {
                try {
                    const refreshed = await readResponse(await fetchWithTimeout(`${API_BASE}/storage`, {cache:'no-store'}, 60000), '读取存储信息失败');
                    usage = refreshed; renderUsage();
                } catch (_) {
                    const completed = message.detail;
                    setMessage('缓存清理完成。', () => completed() + ' ' + tr('统计刷新失败，请点击刷新统计。'));
                }
            }
        } catch (error) { setMessage(received ? '缓存清理失败：' : '无法确认清理结果，请刷新统计。', () => ' ' + tr(error.message)); }
        finally { cleaning = false; controls(); }
    }
    function openManager() {
        const dialog = node('storageDialog');
        if (!dialog.open) dialog.showModal();
        if (cleaning) { setMessage('正在清理缓存…'); controls(); }
        else return loadStorage();
    }
    function init() {
        node('openOutputFileBtn')?.addEventListener('click', () => outputAction('file'));
        node('openOutputFolderBtn')?.addEventListener('click', () => outputAction('folder'));
        node('copyOutputPathBtn')?.addEventListener('click', () => outputAction('copy'));
        node('storageManageBtn')?.addEventListener('click', openManager);
        node('storageRefreshBtn')?.addEventListener('click', () => loadStorage());
        node('storageCleanBtn')?.addEventListener('click', cleanup);
        node('storageAudio')?.addEventListener('change', loadStorage);
        node('storageVideo')?.addEventListener('change', loadStorage);
        node('storageCloseBtn')?.addEventListener('click', () => node('storageDialog').close());
        node('storageDialog')?.addEventListener('close', () => {
            sequence++; pending = false; clearPlan(); node('storageManageBtn')?.focus();
        });
    }
    document.addEventListener('DOMContentLoaded', init);
    window.addEventListener('ui-language-changed', () => {
        if (node('storageSizes')) { renderUsage(); renderPreview(); renderMessage(); }
    });
})();
