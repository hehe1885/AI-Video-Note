// Task polling, timing, cancellation and lifecycle transitions.
function schedulePoll(taskId, delay = POLL_DELAY_MS) {
    stopPolling();
    pollTimer = window.setTimeout(() => pollTask(taskId), delay);
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
    window.resetTranscriptReview?.();
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
    clearTaskConnectionNotice();
    window.resetTranscriptReview?.();
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
    setSubmitButton(false, isTranscriptTask ? '再次转写' : '重新生成笔记');
    const completionWarning = result?.export_warning || result?.screenshot_warning;
    showToast(completionWarning || (isTranscriptTask ? '字幕稿已生成' : '视频笔记已生成'), completionWarning ? 'warning' : 'success');
    loadRecentTasks(true);
    // 任务期间可能下载/补全了 Whisper 模型，自动纠正下拉框缓存状态，避免下次提交误弹确认
    refreshWhisperModelHints();
}

function failTask(message, elapsedSeconds = null) {
    clearTaskConnectionNotice();
    window.resetTranscriptReview?.();
    stopPolling();
    stopElapsedTimer(elapsedSeconds);
    setTaskActive(false);
    setTaskState('failed', '失败');
    byId('taskError').hidden = false;
    byId('taskErrorMessage').textContent = message;
    byId('networkState').textContent = '任务已停止';
    addLog(`任务失败：${message}`, 'error');
    setSubmitButton(false, '继续生成');
    showToast(message, 'error');
    loadRecentTasks(true);
    refreshWhisperModelHints();
}

async function cancelCurrentTask() {
    if (!currentTaskId || (!isTaskActive && byId("agentResultPanel")?.hidden !== false) || isCancelling || cancellationTaskId === currentTaskId) return;
    if (!window.confirm('取消当前任务？已生成的音频和转录文件会保留。')) return;
    const taskId = currentTaskId;
    const viewSequence = taskViewSequence;
    isCancelling = true;
    setTaskActive(true);
    try {
        const response = await fetch(`${API_BASE}/task/${encodeURIComponent(taskId)}/cancel`, {method:'POST'});
        const data = await readResponse(response, '取消任务失败');
        if (taskId !== currentTaskId || viewSequence !== taskViewSequence) return;
        if (data.status === 'cancelled') {
            markTaskCancelled('任务已取消；已生成的中间文件仍保留在工作目录',data.elapsed_seconds);
        } else {
            cancellationTaskId = taskId;
            setTaskState('processing', '取消中');
            byId('networkState').textContent = '等待当前步骤停止';
            addLog('取消请求已发送，正在等待当前步骤停止', 'warning');
            showToast('取消请求已发送', 'info');
        }
    } catch (error) {
        if (taskId !== currentTaskId || viewSequence !== taskViewSequence) return;
        showToast(`取消失败：${error.message}`, 'error');
    } finally {
        if (taskId === currentTaskId && viewSequence === taskViewSequence) {
            isCancelling = false;
            setTaskActive(isTaskActive);
        }
    }
}

function markTaskCancelled(message, elapsedSeconds = null) {
    clearTaskConnectionNotice();
    window.resetTranscriptReview?.();
    stopPolling();
    stopElapsedTimer(elapsedSeconds);
    setTaskActive(false);
    setTaskState('cancelled', '已取消');
    byId('taskError').hidden = false;
    byId('taskErrorMessage').textContent = message;
    byId('networkState').textContent = '任务已取消';
    addLog(message, 'warning');
    setSubmitButton(false, '继续生成');
    showToast('任务已取消', 'info');
    loadRecentTasks(true);
    refreshWhisperModelHints();
}

function setSubmitting(active, label = '开始生成') {
    isSubmitting = active;
    setSubmitButton(active, label);
    byId('restartBtn').disabled = active;
    setSourceControlsDisabled(active || isTaskActive);
}

function setTaskActive(active) {
    isTaskActive = active;
    setSourceControlsDisabled(active || isSubmitting);
    byId('submitBtn').disabled = active || isSubmitting;
    const cancelButton = byId('cancelTaskBtn');
    cancelButton.hidden = !active;
    const cancellationPending = active && cancellationTaskId === currentTaskId;
    cancelButton.disabled = !active || isCancelling || cancellationPending;
    cancelButton.textContent = !active ? '取消任务' : (isCancelling ? '正在取消' : cancellationPending ? '取消请求已发送' : '取消任务');
}

function setSourceControlsDisabled(disabled) {
    byId('sourceType').disabled = disabled;
    byId('videoUrl').disabled = disabled;
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
    document.querySelectorAll('input[name="processingMode"], input[name="outputMode"]').forEach((input) => {
        input.disabled = disabled;
    });
}

function setSubmitButton(loading, label) {
    const button = byId('submitBtn');
    button.disabled = loading || isTaskActive;
    button.classList.toggle('is-loading', loading);
    button.querySelector('.btn-text').textContent = label;
}

// 换来源就是一次全新任务：留着「再次生成 / 重新提交」会让人以为还在接着上一个跑，
// 而它其实会拿新链接从头开始（只是文案不对，行为没错）。
function resetSubmitLabel(sourceChanged = false) {
    if (isSubmitting || isTaskActive) return;
    if (sourceChanged) {
        sourceTaskId = null;
        sourceFilename = '';
        renderHistoryFileName();
    }
    const reuseSource = Boolean(currentTaskId && sourceTaskId === currentTaskId);
    const continueGeneration = reuseSource && ['failed', 'cancelled'].includes(currentTaskStatus);
    if (continueGeneration) {
        setSubmitButton(false, '继续生成');
        return;
    }
    setSubmitButton(false, isTranscriptMode()
        ? (reuseSource ? '再次转写' : '开始转写')
        : (reuseSource ? '重新生成笔记' : '开始生成'));
}

function setTaskState(type, text) {
    currentTaskStatus = type;
    const state = byId('taskState');
    state.className = `status-badge ${type}`;
    state.textContent = text;
}

