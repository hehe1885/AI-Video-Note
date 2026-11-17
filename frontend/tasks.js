// Task polling, timing, cancellation and lifecycle transitions.
function schedulePoll(taskId, delay = POLL_DELAY_MS) {
    stopPolling();
    pollTimer = window.setTimeout(() => pollTask(taskId), delay);
}

const DEFAULT_MAX_CONCURRENT_TASKS = 3;
let maxConcurrentTasks = DEFAULT_MAX_CONCURRENT_TASKS;
const ACTIVE_SLOT_STATUSES = new Set(['pending', 'queued', 'processing', 'cancelling']);
const LIVE_UI_STATUSES = new Set(['pending', 'queued', 'processing', 'cancelling', 'awaiting_agent']);
const activeTasksMap = new Map();

function formatConcurrencyText(text, ...args) {
    const template = window.AIVideoNoteI18n?.translate?.(text) || text;
    return args.length
        ? template.replace(/\{(\d+)\}/g, (match, idx) => {
            const i = Number(idx);
            return i < args.length ? String(args[i] ?? '') : match;
        })
        : template;
}

function isRunningSlotStatus(status) {
    return ACTIVE_SLOT_STATUSES.has(status);
}

function isLiveTaskStatus(status) {
    return LIVE_UI_STATUSES.has(status);
}

function activeTaskCount() {
    let count = 0;
    const countedIds = new Set();
    for (const [id, item] of activeTasksMap.entries()) {
        if (isRunningSlotStatus(item?.status)) {
            count += 1;
            countedIds.add(id);
        }
    }
    const viewingActive = typeof isTaskActive !== 'undefined' && isTaskActive;
    const viewingId = typeof currentTaskId !== 'undefined' ? currentTaskId : null;
    const viewingStatus = typeof currentTaskStatus !== 'undefined' ? currentTaskStatus : '';
    if (viewingActive && (!viewingId || (!countedIds.has(viewingId) && (!viewingStatus || isRunningSlotStatus(viewingStatus))))) {
        count += 1;
    }
    return count;
}

function hasAnyActiveTask() {
    if ((typeof isTaskActive !== 'undefined' && isTaskActive) || activeTaskCount() > 0) return true;
    for (const item of activeTasksMap.values()) {
        if (isLiveTaskStatus(item?.status)) return true;
    }
    return false;
}

function atConcurrentTaskLimit() {
    return activeTaskCount() >= Math.max(1, Number(maxConcurrentTasks) || DEFAULT_MAX_CONCURRENT_TASKS);
}

function syncConcurrencyControls() {
    const submitting = typeof isSubmitting !== 'undefined' && isSubmitting;
    const busy = Boolean(submitting || atConcurrentTaskLimit());
    if (typeof setSourceControlsDisabled === 'function') setSourceControlsDisabled(busy);
    const submitBtn = typeof byId === 'function' ? byId('submitBtn') : null;
    if (submitBtn) submitBtn.disabled = busy;
    const density = typeof byId === 'function' ? byId('screenshotDensity') : null;
    if (density && typeof timedScreenshotEnabled === 'function') {
        density.disabled = busy || timedScreenshotEnabled();
    }
    if (typeof updateVisionCheckbox === 'function' && typeof byId === 'function' && byId('visionCapabilityHint')?.dataset) {
        updateVisionCheckbox();
    }
    if (typeof updateClearVideoUrlButton === 'function') updateClearVideoUrlButton();
    if (typeof syncDocumentTitle === 'function') syncDocumentTitle();
}

function setMaxConcurrentTasks(limit) {
    const parsed = Number(limit);
    const next = Number.isFinite(parsed) && parsed >= 1 ? Math.floor(parsed) : DEFAULT_MAX_CONCURRENT_TASKS;
    if (maxConcurrentTasks === next) return;
    maxConcurrentTasks = next;
    syncConcurrencyControls();
    if (!(typeof isSubmitting !== 'undefined' && isSubmitting) && typeof resetSubmitLabel === 'function') resetSubmitLabel();
    if (typeof renderActiveTasksBadge === 'function') renderActiveTasksBadge();
}

function upsertActiveTaskState(taskId, task) {
    if (!taskId || !task) return;
    if (isLiveTaskStatus(task.status)) {
        const prev = activeTasksMap.get(taskId) || {};
        const merged = {...prev, ...task, task_id: taskId};
        activeTasksMap.set(taskId, merged);
        if (typeof backgroundTaskSnapshot !== 'undefined' && backgroundTaskSnapshot instanceof Map) {
            backgroundTaskSnapshot.set(taskId, {
                status: merged.status,
                reviewStatus: merged.transcript_review?.status || '',
                title: merged.title || merged.video_title || taskId
            });
        }
    } else if (!activeTasksMap.delete(taskId)) {
        return;
    }
    syncConcurrencyControls();
    if (!(typeof isSubmitting !== 'undefined' && isSubmitting) && typeof resetSubmitLabel === 'function') resetSubmitLabel();
    if (typeof renderActiveTasksBadge === 'function') renderActiveTasksBadge();
}

function removeActiveTaskState(taskId) {
    if (!taskId || !activeTasksMap.delete(taskId)) return;
    syncConcurrencyControls();
    if (!(typeof isSubmitting !== 'undefined' && isSubmitting) && typeof resetSubmitLabel === 'function') resetSubmitLabel();
    if (typeof renderActiveTasksBadge === 'function') renderActiveTasksBadge();
    if (!hasAnyActiveTask() && typeof stopBackgroundTaskSync === 'function') stopBackgroundTaskSync();
}

async function pollTask(taskId) {
    if (taskId !== currentTaskId) return;
    const viewSequence = taskViewSequence;
    pollTimer = null;
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/task/${encodeURIComponent(taskId)}`,
            { cache: 'no-store' },
            POLL_TIMEOUT_MS
        );
        const task = await readResponse(response, '读取任务状态失败');
        if (taskId !== currentTaskId || viewSequence !== taskViewSequence) return;
        if (task.title && lastRecentTitleKey !== `${taskId}/${task.title}`) {
            lastRecentTitleKey = `${taskId}/${task.title}`;
            loadRecentTasks(true);
        }
        const reconnected = !byId('taskConnectionNotice').hidden;
        clearTaskConnectionNotice();
        pollErrorCount = 0;
        if (reconnected) {
            startElapsedTimer(task.elapsed_seconds);
            addLog('已重新连接到原任务', 'success');
        }
        renderAgentRunInfo(task.agent_runtime);
        syncElapsedTimer(task.elapsed_seconds);
        byId('networkState').textContent = task.step === 6
            ? task.progress_message || '模型正在生成笔记'
            : '连接正常';
        byId('networkState').classList.remove('network-warning');

        updateProgress(task.progress);
        updateStep(task.step);
        renderTaskAdvisory(task.advisory, task.screenshot_warning);
        if (Array.isArray(task.logs) && task.logs.length) updateLogs(task.logs);

        upsertActiveTaskState(taskId, task);
        if (window.syncTranscriptReview?.(taskId, task)) {
            schedulePoll(taskId);
            return;
        }

        if (task.status === 'awaiting_agent') {
            showAgentWaiting(task);
            schedulePoll(taskId);
            return;
        }
        if (task.status === 'completed') {
            if (byId('agentResultPanel')) byId('agentResultPanel').hidden = !task.agent_runtime;
            if (task.agent_runtime) showAgentRunSummary(task);
            await completeTask(task.result, task.elapsed_seconds, task.download_name);
            return;
        }
        if (byId('agentResultPanel')) byId('agentResultPanel').hidden = !task.agent_runtime;
            if (task.agent_runtime) showAgentRunSummary(task);
        if (task.status === 'failed') {
            failTask(task.error || '后端未提供失败原因', task.elapsed_seconds);
            return;
        }
        if (task.status === 'cancelled') {
            markTaskCancelled(
                '任务已取消；已生成的中间文件仍保留在工作目录',
                task.elapsed_seconds
            );
            return;
        }
        schedulePoll(taskId);
    } catch (error) {
        if (taskId !== currentTaskId || viewSequence !== taskViewSequence) return;
        pollErrorCount += 1;
        byId('networkState').textContent = `连接异常，重试 ${pollErrorCount}/${MAX_POLL_ERRORS}`;
        byId('networkState').classList.add('network-warning');
        addLog(`状态连接异常：${error.message}`, 'warning');

        if (pollErrorCount >= MAX_POLL_ERRORS) {
            stopPolling();
            stopElapsedTimer();
            setTaskActive(true);
            byId('taskConnectionNotice').hidden = false;
            byId('reconnectTaskBtn').disabled = false;
            byId('networkState').textContent = '连接中断，任务状态待确认';
            return;
        }
        schedulePoll(taskId, Math.min(POLL_DELAY_MS * pollErrorCount, 8000));
    }
}

function clearTaskConnectionNotice() {
    byId('taskConnectionNotice').hidden = true;
    byId('reconnectTaskBtn').disabled = false;
    byId('networkState').classList.remove('network-warning');
}

function reconnectCurrentTask() {
    const button = byId('reconnectTaskBtn');
    if (!currentTaskId || byId('taskConnectionNotice').hidden || button.disabled) return;
    button.disabled = true;
    // Keep the error count: a failed manual attempt returns directly to the notice.
    byId('networkState').textContent = '正在重新连接原任务';
    schedulePoll(currentTaskId, 0);
}

function stopPolling() {
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
}

function resetElapsedTimer() {
    stopElapsedTimer(0);
    elapsedBaseSeconds = 0;
    elapsedSyncedAt = Date.now();
    renderElapsedTime(0);
}

function startElapsedTimer(initialSeconds = 0) {
    stopElapsedTimer(initialSeconds);
    elapsedBaseSeconds = Math.max(0, Number(initialSeconds) || 0);
    elapsedSyncedAt = Date.now();
    renderElapsedTime(elapsedBaseSeconds);
    elapsedTimer = window.setInterval(() => {
        const seconds = elapsedBaseSeconds + (Date.now() - elapsedSyncedAt) / 1000;
        renderElapsedTime(seconds);
    }, 500);
}

function syncElapsedTimer(rawSeconds) {
    const seconds = Number(rawSeconds);
    if (!Number.isFinite(seconds) || seconds < 0) return;
    elapsedBaseSeconds = seconds;
    elapsedSyncedAt = Date.now();
    renderElapsedTime(seconds);
}

function stopElapsedTimer(finalSeconds = null) {
    if (elapsedTimer !== null) window.clearInterval(elapsedTimer);
    elapsedTimer = null;
    if (finalSeconds !== null) syncElapsedTimer(finalSeconds);
}

function renderElapsedTime(rawSeconds) {
    const total = Math.max(0, Math.floor(Number(rawSeconds) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    byId('elapsedTime').textContent = hours
        ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
        : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function resetTaskView() {
    clearTaskConnectionNotice();
    if (byId("agentResultPanel")) byId("agentResultPanel").hidden = true;
    if (byId("agentResultText")) byId("agentResultText").value = "";
    taskViewSequence += 1;
    resetNoteDownloads();
    window.resetResultLocation?.();
    window.resetNoteReview?.();
    window.syncTranscriptReviewNotice?.();
    renderAgentRunInfo(null);
    isCancelling = false;
    cancellationTaskId = null;
    stopPolling();
    resetElapsedTimer();
    setTaskActive(false);
    pollErrorCount = 0;
    whisperFallbackNotified = false;
    currentTaskId = null;
    sourceTaskId = null;
    sourceFilename = '';
    renderHistoryFileName();
    currentMarkdown = '';
    currentHtml = '';
    currentDownloadName = '';
    setTranscriptTaskView(false);
    byId('progressArea').hidden = false;
    byId('resultArea').hidden = true;
    byId('taskError').hidden = true;
    byId('downloadMdBtn').disabled = true;
    byId('exportSubtitleBtn').disabled = true;
    byId('copyNoteBtn').disabled = true;
    byId('outputNotice').hidden = true;
    byId('outputPath').textContent = '';
    byId('taskDirPath').textContent = '';
    renderTaskAdvisory(null);
    byId('networkState').textContent = '准备提交';
    byId('networkState').classList.remove('network-warning');
    clearLogs();
    updateProgress(0);
    updateStep(0);
    setTaskState('processing', '准备中');
}

// 结果区标题、复制按钮文案与进度步数都跟着"这一条是字幕稿还是笔记"变
function setTranscriptTaskView(on) {
    isTranscriptTask = Boolean(on);
    byId('progressArea').classList.toggle('transcript-mode', isTranscriptTask);
    byId('resultTitle').textContent = isTranscriptTask ? '字幕稿（带时间轴）' : '视频笔记';
    byId('copyNoteBtn').textContent = isTranscriptTask ? '复制字幕稿' : '复制笔记';
}

async function completeTask(result, elapsedSeconds = null, downloadName = '') {
    const taskId = currentTaskId;
    const viewSequence = taskViewSequence;
    removeActiveTaskState(taskId);
    window.clearTranscriptReviewTask?.(taskId);
    clearTaskConnectionNotice();
    stopPolling();
    currentDownloadName = typeof downloadName === 'string' ? downloadName.trim() : '';
    stopElapsedTimer(elapsedSeconds ?? result?.processing_seconds ?? null);
    if (!await showResult(result)) return;
    if (taskId !== currentTaskId || viewSequence !== taskViewSequence) return;
    setTaskActive(false);
    updateProgress(100);
    updateStep(7);
    setTaskState('completed', result?.export_warning ? '已完成（保存失败）' : result?.screenshot_warning ? '已完成（配图失败）' : (isTranscriptTask ? '转录完成' : '已完成'));
    byId('networkState').textContent = '任务完成';
    addLog(isTranscriptTask ? '带时间轴字幕稿已生成' : '视频笔记已生成', 'success');
    if (!atConcurrentTaskLimit()) setSubmitButton(false, isTranscriptTask ? '再次转写' : '重新生成笔记');
    const completionWarning = result?.export_warning || result?.screenshot_warning;
    showToast(completionWarning || (isTranscriptTask ? '字幕稿已生成' : '视频笔记已生成'), completionWarning ? 'warning' : 'success');
    loadRecentTasks(true);
    // 任务期间可能下载/补全了 Whisper 模型，自动纠正下拉框缓存状态，避免下次提交误弹确认
    refreshWhisperModelHints();
}

function failTask(message, elapsedSeconds = null) {
    removeActiveTaskState(currentTaskId);
    window.clearTranscriptReviewTask?.(currentTaskId);
    clearTaskConnectionNotice();
    stopPolling();
    stopElapsedTimer(elapsedSeconds);
    setTaskActive(false);
    setTaskState('failed', '失败');
    byId('taskError').hidden = false;
    byId('taskErrorMessage').textContent = message;
    byId('networkState').textContent = '任务已停止';
    addLog(`任务失败：${message}`, 'error');
    if (!atConcurrentTaskLimit()) setSubmitButton(false, '继续生成');
    showToast(message, 'error');
    loadRecentTasks(true);
    refreshWhisperModelHints();
}

const backgroundCancellingIds = new Set();

async function cancelTaskById(taskId, options = {}) {
    if (!taskId) return;
    const isViewedActiveTask = taskId === currentTaskId
        && (isTaskActive || byId("agentResultPanel")?.hidden === false);
    if (options.fromCurrentView || isViewedActiveTask) {
        if (!currentTaskId || (!isTaskActive && byId("agentResultPanel")?.hidden !== false) || isCancelling || cancellationTaskId === currentTaskId) return;
        if (!window.confirm('取消当前任务？已生成的音频和转录文件会保留。')) return;
        const targetId = currentTaskId;
        const viewSequence = taskViewSequence;
        isCancelling = true;
        setTaskActive(true);
        try {
            const response = await fetch(`${API_BASE}/task/${encodeURIComponent(targetId)}/cancel`, {method:'POST'});
            const data = await readResponse(response, '取消任务失败');
            if (targetId !== currentTaskId || viewSequence !== taskViewSequence) return;
            window.clearTranscriptReviewTask?.(targetId);
            if (data.status === 'cancelled') {
                markTaskCancelled('任务已取消；已生成的中间文件仍保留在工作目录',data.elapsed_seconds);
            } else {
                cancellationTaskId = targetId;
                if (activeTasksMap.has(targetId)) {
                    activeTasksMap.set(targetId, {...activeTasksMap.get(targetId), task_id: targetId, status: 'cancelling'});
                }
                setTaskState('processing', '取消中');
                byId('networkState').textContent = '等待当前步骤停止';
                addLog('取消请求已发送，正在等待当前步骤停止', 'warning');
                showToast('取消请求已发送', 'info');
            }
        } catch (error) {
            if (targetId !== currentTaskId || viewSequence !== taskViewSequence) return;
            showToast(`取消失败：${error.message}`, 'error');
        } finally {
            if (targetId === currentTaskId && viewSequence === taskViewSequence) {
                isCancelling = false;
                setTaskActive(isTaskActive);
            }
        }
        return;
    }

    if (backgroundCancellingIds.has(taskId) || cancellationTaskId === taskId) return;
    if (!window.confirm(formatConcurrencyText('取消该后台任务？已生成的音频和转录文件会保留。'))) return;
    backgroundCancellingIds.add(taskId);
    const entry = typeof recentTaskRows !== 'undefined' && recentTaskRows instanceof Map ? recentTaskRows.get(taskId) : null;
    if (entry?.cancelBtn) entry.cancelBtn.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/task/${encodeURIComponent(taskId)}/cancel`, {method:'POST'});
        const data = await readResponse(response, '取消任务失败');
        window.clearTranscriptReviewTask?.(taskId);
        if (data.status === 'cancelled') {
            removeActiveTaskState(taskId);
            showToast(formatConcurrencyText('后台任务已取消'), 'info');
        } else {
            const prev = activeTasksMap.get(taskId) || {};
            activeTasksMap.set(taskId, {...prev, task_id: taskId, status: 'cancelling'});
            syncConcurrencyControls();
            if (typeof renderActiveTasksBadge === 'function') renderActiveTasksBadge();
            showToast('取消请求已发送', 'info');
        }
        if (typeof loadRecentTasks === 'function') loadRecentTasks(true);
    } catch (error) {
        showToast(`取消失败：${error.message}`, 'error');
        if (entry?.cancelBtn) entry.cancelBtn.disabled = false;
    } finally {
        backgroundCancellingIds.delete(taskId);
    }
}

async function cancelCurrentTask() {
    return cancelTaskById(currentTaskId, {fromCurrentView: true});
}

function markTaskCancelled(message, elapsedSeconds = null) {
    clearTaskConnectionNotice();
    removeActiveTaskState(currentTaskId);
    window.clearTranscriptReviewTask?.(currentTaskId);
    stopPolling();
    stopElapsedTimer(elapsedSeconds);
    setTaskActive(false);
    setTaskState('cancelled', '已取消');
    byId('taskError').hidden = false;
    byId('taskErrorMessage').textContent = message;
    byId('networkState').textContent = '任务已取消';
    addLog(message, 'warning');
    if (!atConcurrentTaskLimit()) setSubmitButton(false, '继续生成');
    showToast('任务已取消', 'info');
    loadRecentTasks(true);
    refreshWhisperModelHints();
}

function setSubmitting(active, label = '开始生成') {
    isSubmitting = active;
    setSubmitButton(active, label);
    if (byId('restartBtn')) byId('restartBtn').disabled = active;
    syncConcurrencyControls();
}

function setTaskActive(active) {
    isTaskActive = active;
    syncConcurrencyControls();
    const cancelButton = byId('cancelTaskBtn');
    if (cancelButton) {
        cancelButton.hidden = !active;
        const cancellationPending = active && cancellationTaskId === currentTaskId;
        cancelButton.disabled = !active || isCancelling || cancellationPending;
        cancelButton.textContent = !active ? '取消任务' : (isCancelling ? '正在取消' : cancellationPending ? '取消请求已发送' : '取消任务');
    }
    if (!isSubmitting) resetSubmitLabel();
    if (!active && !hasAnyActiveTask() && typeof stopBackgroundTaskSync === 'function') {
        stopBackgroundTaskSync();
    }
}

function setSourceControlsDisabled(disabled) {
    byId('sourceType').disabled = disabled;
    byId('videoUrl').disabled = disabled;
    if (byId('clearVideoUrlBtn')) byId('clearVideoUrlBtn').disabled = disabled;
    byId('localFile').disabled = disabled;
    byId('summaryStyle').disabled = disabled;
    byId('timelineMode').disabled = disabled;
    byId('aiCommentary').disabled = disabled;
    byId('textDetail').disabled = disabled;
    byId('visionScreenshots').disabled = disabled || visionCapability !== 'supported' || timedScreenshotEnabled() || selectedScreenshotDensity() === 'none';
    byId('retryVisionCheckBtn').disabled = disabled;
    byId('formatSelect').disabled = disabled;
    byId('imageLayoutSelect').disabled = disabled;
    byId('customPrompt').disabled = disabled;
    byId('screenshotDensity').disabled = disabled || timedScreenshotEnabled();
    byId('timedScreenshots').disabled = disabled;
    byId('screenshotInterval').disabled = disabled;
    byId('videoResolution').disabled = disabled;
    byId('videoDownloadDirectory').disabled = disabled;
    ['obsidianVault', 'obsidianNoteFolder', 'obsidianAttachmentMode', 'obsidianAttachmentFolder'].forEach((id) => {
        byId(id).disabled = disabled;
    });
    byId('obsidianTags').disabled = disabled;
    byId('obsidianCallouts').disabled = disabled;
    renderSelectedModelPaths();
    byId('reasoningEffort').disabled = disabled;
    byId('noteSaveDirectory').disabled = disabled;
    renderNoteSaveLocation();
    if (byId('processingMode')) byId('processingMode').disabled = disabled;
    document.querySelectorAll?.('input[name="processingMode"], input[name="outputMode"]').forEach((input) => {
        input.disabled = disabled;
    });
}

function setSubmitButton(loading, label) {
    const button = typeof byId === 'function' ? byId('submitBtn') : null;
    if (!button) return;
    button.disabled = Boolean(loading || atConcurrentTaskLimit());
    button.classList?.toggle?.('is-loading', loading);
    const textEl = button.querySelector?.('.btn-text');
    if (textEl) textEl.textContent = formatConcurrencyText(label);
}

// 换来源就是一次全新任务：留着「再次生成 / 重新提交」会让人以为还在接着上一个跑，
// 而它其实会拿新链接从头开始（只是文案不对，行为没错）。
function resetSubmitLabel(sourceChanged = false) {
    if (sourceChanged) {
        sourceTaskId = null;
        sourceFilename = '';
        renderHistoryFileName();
    }
    if (isSubmitting) return;
    if (atConcurrentTaskLimit()) {
        setSubmitButton(false, formatConcurrencyText('最多同时处理 {0} 个任务', maxConcurrentTasks));
        return;
    }
    const canReuseViewedSource = Boolean(
        currentTaskId
        && sourceTaskId === currentTaskId
        && !isTaskActive
        && !isRunningSlotStatus(currentTaskStatus)
    );
    const continueGeneration = canReuseViewedSource && ['failed', 'cancelled'].includes(currentTaskStatus);
    if (continueGeneration) {
        if (!atConcurrentTaskLimit()) setSubmitButton(false, '继续生成');
        return;
    }
    setSubmitButton(false, isTranscriptMode()
        ? (canReuseViewedSource ? '再次转写' : '开始转写')
        : (canReuseViewedSource ? '重新生成笔记' : '开始生成'));
}

function setTaskState(type, text) {
    currentTaskStatus = type;
    const state = byId('taskState');
    state.className = `status-badge ${type}`;
    state.textContent = text;
}

