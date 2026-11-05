// Human confirmation owns transcript edits; polling never overwrites an open editor.
(() => {
    let view = null;
    let previousFocus = null;
    const node = id => document.getElementById(id);
    const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
    const modal = () => node('transcriptReviewModal');
    const current = state => view === state && currentTaskId === state.taskId;
    function stopAudio(state = view, release = false) {
        if (!state) return;
        state.audio?.forEach(record => {
            record.requestVersion = (record.requestVersion || 0) + 1;
            record.player.pause();
            updateAudio(record);
            if (release && record.url) {
                record.player.removeAttribute('src'); record.player.load();
                record.url = null;
            }
        });
    }
    function hide() {
        stopAudio();
        if (modal()) modal().hidden = true;
        previousFocus?.focus?.({preventScroll: true});
    }
    function reset() {
        hide(); stopAudio(view, true); view = null;
        if (node('transcriptReviewLaterBtn')) node('transcriptReviewLaterBtn').disabled = false;
        if (node('transcriptReviewNotice')) node('transcriptReviewNotice').hidden = true;
    }
    function stamp(seconds) {
        const value = Math.max(0, Math.floor(Number(seconds) || 0));
        return [Math.floor(value / 3600), Math.floor(value % 3600 / 60), value % 60]
            .slice(value >= 3600 ? 0 : 1).map(part => String(part).padStart(2, '0')).join(':');
    }
    function textElement(tag, text, className = '', source = false) {
        const element = document.createElement(tag);
        element.textContent = text;
        if (className) element.className = className;
        if (source) element.setAttribute('data-no-i18n', '');
        return element;
    }
    const icons = {
        play: '<path d="m9 6 9 6-9 6z" fill="currentColor" stroke="none"/>',
        pause: '<path d="M9 6v12M15 6v12" stroke-width="3"/>',
        volume: '<path d="m11 5-6 4H2v6h3l6 4zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/>',
        muted: '<path d="m11 5-6 4H2v6h3l6 4zM16 9l6 6M22 9l-6 6"/>'
    };
    function setIcon(button, name, label) {
        button.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
        button.setAttribute('aria-label', tr(label));
        button.setAttribute('title', tr(label));
    }
    function updateAudio(record) {
        const player = record.player;
        const duration = Number.isFinite(player.duration) && player.duration > 0 ? player.duration : record.duration;
        const time = record.pendingSeek ?? (Number(player.currentTime) || 0);
        const playing = !player.paused && !player.ended;
        record.controls.dataset.state = playing && player.readyState < 3 ? 'loading' : playing ? 'playing' : 'paused';
        setIcon(record.play, playing ? 'pause' : 'play', playing ? '暂停音频' : '播放音频');
        record.play.setAttribute('aria-pressed', String(playing));
        record.position.textContent = `${stamp(time)} / ${stamp(duration)}`;
        record.seek.max = String(duration);
        record.seek.value = String(Math.min(duration, time));
        record.seek.setAttribute('aria-valuetext', `${stamp(time)} / ${stamp(duration)}`);
        record.seek.style.setProperty('--audio-progress', `${duration ? Math.min(100, time / duration * 100) : 0}%`);
        setIcon(record.mute, player.muted ? 'muted' : 'volume', player.muted ? '取消静音' : '静音');
        record.mute.setAttribute('aria-pressed', String(Boolean(player.muted)));
    }
    async function playAudio(state, record, reload = false) {
        if (!current(state) || state.saving || modal().hidden) return;
        if (!reload && !record.player.paused) { record.player.pause(); return; }
        stopAudio(state);
        const request = record.requestVersion;
        const active = () => current(state) && !modal().hidden;
        record.error.textContent = '';
        record.retry.hidden = true;
        if (reload || record.player.error) {
            record.player.src = record.url;
            record.player.load();
        }
        if (record.player.ended) record.player.currentTime = 0;
        try {
            await record.player.play();
            if (!active()) record.player.pause();
        } catch (error) {
            if (active() && record.requestVersion === request && error.name !== 'AbortError') {
                record.error.textContent = tr(error.name === 'NotAllowedError'
                    ? '音频已准备，请点击播放器播放' : '音频播放失败，请重试');
                record.retry.hidden = error.name === 'NotAllowedError';
            }
        }
        updateAudio(record);
    }
    function updateAccepted(state) {
        const accepted = state.accepted || new Map();
        state.rows?.forEach((row, id) => { row.hidden = accepted.has(id) && !state.showAccepted; });
        const button = node('transcriptReviewResolvedBtn');
        if (button) {
            button.hidden = !accepted.size;
            button.textContent = `${tr(state.showAccepted ? '收起已采纳项' : '查看已采纳项')} (${accepted.size})`;
        }
    }
    function acceptHigh() {
        const state = view;
        if (!state?.loaded || state.saving || state.aiBusy) return;
        let count = 0;
        state.items.forEach(item => {
            const editor = state.editors.get(item.id);
            if (!Number.isFinite(item.confidence) || item.confidence < .9 || !item.suggestion ||
                item.suggestion === item.original || !editor || state.accepted.has(item.id)) return;
            // An explicit manual correction takes precedence over a bulk suggestion.
            if (editor.value !== item.original && editor.value !== item.suggestion) return;
            editor.value = item.suggestion;
            state.accepted.set(item.id, {original: item.original, text: item.suggestion});
            state.audio.get(item.id)?.player.pause();
            count++;
        });
        state.showAccepted = false;
        updateAccepted(state);
        node('transcriptReviewStatus').textContent = count
            ? `${tr('本次已采纳')} ${count} ${tr('条高可信建议；剩余待核对')} ${state.items.length - state.accepted.size} ${tr('条。点击“应用修改并继续”后保存。')}`
            : tr('没有可批量采纳的新建议；手动修改的内容已保留。');
    }
    async function requestAudio(itemId = null) {
        const state = view;
        if (!state?.loaded || state.saving || state.aiBusy || !current(state)) return;
        const items = state.items.filter(item => itemId ? item.id === itemId :
            !state.accepted.has(item.id) && !item.audio_review &&
            (!item.suggestion || !Number.isFinite(item.confidence) || item.confidence < .9));
        if (!items.length) {
            node('transcriptReviewStatus').textContent = tr('没有尚未核对的存疑音频。');
            return;
        }
        state.aiBusy = true;
        node('transcriptReviewAudioBtn').disabled = true;
        node('transcriptReviewAiBtn').disabled = true;
        stopAudio(state);
        let completed = 0;
        try {
            for (const item of items) {
                if (!current(state) || modal().hidden) break;
                node('transcriptReviewStatus').textContent = `${tr('正在识别音频并复核')} (${completed + 1}/${items.length})…`;
                const data = await readResponse(await fetchWithTimeout(`${API_BASE}/task/${encodeURIComponent(state.taskId)}/transcript-review/${encodeURIComponent(item.id)}/audio-review`, {
                    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({revision: state.revision})
                }, 180000), '音频核对失败');
                if (!current(state)) return;
                if (data.revision !== state.revision || data.item?.id !== item.id || data.item.original !== item.original)
                    throw Error(tr('待确认内容已变化，请刷新任务后重试'));
                const drafts = new Map([...state.editors].map(([id, editor]) => [id, editor.value]));
                state.items = state.items.map(row => row.id === item.id ? data.item : row);
                stopAudio(state, true);
                render(state);
                drafts.forEach((value, id) => { if (state.editors.has(id)) state.editors.get(id).value = value; });
                completed++;
            }
            if (current(state)) node('transcriptReviewStatus').textContent = `${tr('音频复核已完成')} (${completed}/${items.length})${tr('；请确认建议，字幕尚未修改。')}`;
        } catch (error) {
            if (current(state)) node('transcriptReviewStatus').textContent = error.message || tr('音频核对失败');
        } finally {
            state.aiBusy = false;
            if (current(state)) {
                node('transcriptReviewAudioBtn').disabled = false;
                node('transcriptReviewAiBtn').disabled = false;
            }
        }
    }
    function render(state) {
        const list = node('transcriptReviewItems');
        list.replaceChildren();
        state.editors = new Map();
        state.audio = new Map();
        state.rows = new Map();
        state.accepted ||= new Map();
        state.items.forEach(item => {
            const row = document.createElement('section');
            row.className = 'transcript-review-item';
            const head = document.createElement('div');
            head.className = 'transcript-review-head';
            head.append(textElement('strong', `${stamp(item.start)}–${stamp(item.end)}`, 'transcript-review-time', true));
            const player = document.createElement('audio');
            player.controls = false; player.preload = 'none'; player.hidden = true;
            player.className = 'transcript-review-audio';
            player.setAttribute('aria-label', '对应片段音频');
            const url = `${API_BASE}/task/${encodeURIComponent(state.taskId)}/transcript-review/${encodeURIComponent(item.id)}/audio?revision=${encodeURIComponent(state.revision)}`;
            player.src = url;
            const controls = document.createElement('div');
            controls.className = 'transcript-audio-controls';
            controls.setAttribute('role', 'group'); controls.setAttribute('aria-label', '对应片段音频');
            const play = textElement('button', '', 'transcript-audio-toggle'); play.type = 'button';
            const position = textElement('span', '', 'transcript-audio-position', true);
            const seek = document.createElement('input');
            seek.type = 'range'; seek.min = '0'; seek.step = '0.01'; seek.className = 'transcript-audio-seek';
            seek.setAttribute('aria-label', '音频进度');
            const mute = textElement('button', '', 'transcript-audio-mute'); mute.type = 'button';
            controls.append(play, position, seek, mute);
            head.append(controls); row.append(head, player);
            const comparison = document.createElement('div');
            comparison.className = 'transcript-review-comparison';
            const original = document.createElement('div');
            original.className = 'transcript-review-column';
            original.append(textElement('span', '原始转录', 'transcript-review-label'));
            original.append(textElement('p', item.original, 'transcript-review-original', true));
            const editable = document.createElement('div');
            editable.className = 'transcript-review-column';
            const label = textElement('label', '确认内容（可修改）', 'transcript-review-label');
            const editor = document.createElement('textarea');
            editor.id = `review-edit-${item.segment_index}`;
            const hasSuggestion = typeof item.suggestion === 'string' && item.suggestion !== item.original;
            const confidence = typeof item.confidence === 'number' ? item.confidence : null;
            editor.value = hasSuggestion && confidence >= .9 ? item.suggestion : item.original;
            const accepted = state.accepted.get(item.id);
            if (accepted?.original === item.original) editor.value = accepted.text;
            else state.accepted.delete(item.id);
            editor.rows = 2;
            editor.required = true;
            editor.setAttribute('data-no-i18n', '');
            label.htmlFor = editor.id;
            editable.append(label, editor);
            const grade = confidence === null ? '未评估' : confidence >= .9 ? '高' : confidence >= .6 ? '中' : '低';
            const badge = textElement('span', `${tr('AI可信度：')}${tr(grade)}${confidence === null ? '' : ` · ${Math.round(confidence * 100)}%`}`, 'transcript-review-confidence');
            badge.dataset.level = confidence === null ? 'unknown' : confidence >= .9 ? 'high' : confidence >= .6 ? 'medium' : 'low';
            editable.append(badge);
            const actions = document.createElement('div');
            actions.className = 'transcript-review-actions';
            if (item.reason) original.append(textElement('p', item.reason, 'transcript-review-reason', true));
            if (item.audio_review) {
                original.append(textElement('span', '本机音频识别（供模型复核）', 'transcript-review-label'));
                original.append(textElement('p', item.audio_review.text, 'transcript-review-original', true));
            }
            if (hasSuggestion) {
                original.append(textElement('span', 'AI建议', 'transcript-review-label'));
                original.append(textElement('p', item.suggestion, 'transcript-review-suggestion', true));
                const use = textElement('button', '采用AI建议', 'button ghost compact-button');
                use.type = 'button'; use.addEventListener('click', () => {
                    if (state.aiBusy || state.saving) return;
                    editor.value = item.suggestion;
                    if (state.accepted.has(item.id)) state.accepted.set(item.id, {original: item.original, text: editor.value});
                });
                actions.append(use);
            }
            const restore = textElement('button', '保留原文', 'button ghost compact-button');
            restore.type = 'button'; restore.addEventListener('click', () => {
                if (state.aiBusy || state.saving) return;
                editor.value = item.original; state.accepted.delete(item.id); updateAccepted(state);
            });
            actions.append(restore);
            const listen = textElement('button', '识别音频并复核', 'button ghost compact-button');
            listen.type = 'button'; listen.addEventListener('click', () => requestAudio(item.id));
            actions.append(listen);
            editable.append(actions);
            comparison.append(original, editable);
            row.append(comparison);
            const error = textElement('p', '', 'transcript-review-audio-error');
            error.setAttribute('role', 'status');
            const retry = textElement('button', '重试音频', 'button ghost transcript-review-retry');
            retry.type = 'button'; retry.hidden = true;
            row.append(error, retry);
            const record = {player, error, retry, url, controls, play, position, seek, mute,
                duration: Math.max(0, Number(item.end) - Number(item.start)), pendingSeek: null};
            state.audio.set(item.id, record);
            play.addEventListener('click', () => playAudio(state, record));
            retry.addEventListener('click', () => playAudio(state, record, true));
            mute.addEventListener('click', () => { player.muted = !player.muted; updateAudio(record); });
            seek.addEventListener('input', () => {
                const time = Math.max(0, Math.min(Number(seek.max), Number(seek.value) || 0));
                if (player.readyState >= 1) { record.pendingSeek = null; player.currentTime = time; }
                else record.pendingSeek = time;
                updateAudio(record);
            });
            player.addEventListener('loadedmetadata', () => {
                if (record.pendingSeek !== null) {
                    player.currentTime = Math.min(record.pendingSeek, Math.max(0, player.duration - .01));
                    record.pendingSeek = null;
                }
                updateAudio(record);
            });
            player.addEventListener('play', () => {
                if (!current(state) || modal().hidden || state.saving) { player.pause(); return; }
                state.audio.forEach(other => { if (other !== record) other.player.pause(); });
                error.textContent = ''; retry.hidden = true;
                updateAudio(record);
            });
            ['pause', 'playing', 'waiting', 'ended', 'timeupdate', 'durationchange', 'volumechange'].forEach(event =>
                player.addEventListener(event, () => updateAudio(record)));
            player.addEventListener('error', () => {
                if (!current(state) || modal().hidden) return;
                error.textContent = tr('音频播放失败，请重试'); retry.hidden = false;
                updateAudio(record);
            });
            updateAudio(record);
            state.editors.set(item.id, editor);
            state.rows.set(item.id, row);
            list.append(row);
        });
        node('transcriptReviewDescription').textContent = state.manual
            ? '自动检查未完成。请核对以下字幕，确认原文或修改后继续。'
            : 'AI已完成字幕校对。请核对修改建议，确认后继续处理。';
        node('transcriptReviewStatus').textContent = '';
        node('transcriptReviewKeepBtn').disabled = false;
        node('transcriptReviewApplyBtn').disabled = false;
        node('transcriptReviewLaterBtn').disabled = false;
        state.loaded = true;
        updateAccepted(state);
    }
    async function load(state) {
        if (state.loading) return;
        state.loading = true;
        state.loaded = false;
        node('transcriptReviewStatus').textContent = '正在读取待确认字幕…';
        node('transcriptReviewKeepBtn').disabled = true;
        node('transcriptReviewApplyBtn').disabled = true;
        try {
            const items = [];
            let offset = 0, manual = false;
            do {
                const data = await readResponse(await fetchWithTimeout(`${API_BASE}/task/${encodeURIComponent(state.taskId)}/transcript-review?offset=${offset}&limit=100`, {cache: 'no-store'}, 10000), '读取待确认字幕失败');
                if (!current(state)) return;
                if (data.revision !== state.revision || data.status !== 'pending') throw Error('待确认内容已变化，请刷新任务后重试');
                if (!Array.isArray(data.items)) throw Error('读取待确认字幕失败');
                items.push(...data.items); manual = Boolean(data.manual_check);
                const next = data.next_offset;
                if (next !== null && next !== undefined && (!Number.isInteger(next) || next <= offset)) throw Error('读取待确认字幕失败');
                offset = next;
            } while (offset !== null && offset !== undefined);
            state.items = items; state.manual = manual;
            render(state);
            state.pendingDrafts?.forEach((value, id) => { if (state.editors.has(id)) state.editors.get(id).value = value; });
            state.pendingDrafts = null;
            if (!modal().hidden) node('transcriptReviewItems').querySelector('textarea')?.focus({preventScroll: true});
        } catch (error) {
            if (current(state)) node('transcriptReviewStatus').textContent = error.message || '读取待确认字幕失败';
        } finally { state.loading = false; }
    }
    function open() {
        if (!view || !modal()) return;
        previousFocus = document.activeElement;
        modal().hidden = false;
        node('transcriptReviewLaterBtn')?.focus({preventScroll: true});
        if (!view.loaded) load(view);
        else node('transcriptReviewItems').querySelector('textarea')?.focus({preventScroll: true});
    }
    function sync(taskId, task) {
        const pending = task.transcript_review?.status === 'pending' && ['processing', 'awaiting_agent'].includes(task.status);
        if (!pending) {
            if (view?.taskId === taskId) reset();
            return false;
        }
        if (!modal()) return false;
        if (view?.taskId === taskId && view.aiBusy) {
            if (task.progress_message && task.progress_message !== '等待确认字幕')
                node('transcriptReviewStatus').textContent = task.progress_message;
            return true;
        }
        node('transcriptReviewNotice').hidden = false;
        node('networkState').textContent = '等待确认字幕';
        setTaskActive(true);
        setTaskState('processing', '等待确认字幕');
        if (view?.taskId !== taskId || view.revision !== task.transcript_review.revision) {
            const pendingDrafts = view?.taskId === taskId ? view.pendingDrafts : null;
            const accepted = view?.taskId === taskId ? view.accepted : null;
            stopAudio(view, true);
            view = {taskId, revision: task.transcript_review.revision, loaded: false, loading: false, pendingDrafts, accepted};
            node('transcriptReviewItems').replaceChildren();
            open();
        }
        return true;
    }
    async function confirm(keepOriginal) {
        const state = view;
        if (state?.aiBusy) return;
        if (!state?.loaded || state.saving || !current(state)) return;
        if (!keepOriginal) {
            for (const editor of state.editors.values()) {
                if (!editor.value.trim()) {
                    node('transcriptReviewStatus').textContent = '字幕内容不能为空';
                    editor.focus(); editor.reportValidity(); return;
                }
            }
        }
        state.saving = true;
        stopAudio(state);
        node('transcriptReviewLaterBtn').disabled = true;
        node('transcriptReviewKeepBtn').disabled = true;
        node('transcriptReviewApplyBtn').disabled = true;
        const body = {revision: state.revision, keep_original: keepOriginal, decisions: keepOriginal ? [] :
            [...state.editors].map(([id, editor]) => ({id, text: editor.value}))};
        try {
            await readResponse(await fetchWithTimeout(`${API_BASE}/task/${encodeURIComponent(state.taskId)}/transcript-review`, {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
            }, 20000), '确认字幕失败');
            if (!current(state)) return;
            reset();
            schedulePoll(state.taskId, 0);
        } catch (error) {
            if (current(state)) node('transcriptReviewStatus').textContent = error.message || '确认字幕失败，请重试';
        } finally {
            state.saving = false;
            if (current(state)) {
                node('transcriptReviewLaterBtn').disabled = false;
                node('transcriptReviewKeepBtn').disabled = false;
                node('transcriptReviewApplyBtn').disabled = false;
            }
        }
    }
    async function requestAI() {
        const state = view;
        if (!state || !state.loaded || state.saving || state.aiBusy) return;
        state.aiBusy = true;
        node('transcriptReviewAiBtn').disabled = true;
        node('transcriptReviewStatus').textContent = tr('正在请求AI校对…');
        const drafts = new Map();
        state.items.forEach(item => {
            const value = state.editors.get(item.id)?.value;
            if (state.accepted.has(item.id) || (value !== item.original && value !== item.suggestion)) drafts.set(item.id, value);
        });
        state.editors.forEach(editor => { editor.readOnly = true; });
        state.pendingDrafts = drafts;
        stopAudio(state, true);
        try {
            const data = await readResponse(await fetchWithTimeout(`${API_BASE}/task/${encodeURIComponent(state.taskId)}/transcript-review/ai`, {
                method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({revision:state.revision})
            }, 180000), 'AI校对失败');
            if (!current(state)) return;
            if (data.queued) {
                state.pendingDrafts = drafts;
                node('transcriptReviewStatus').textContent = tr('已记录AI校对请求，请让Agent读取当前任务并提交建议。');
            } else {
                state.revision = data.revision;
                await load(state);
                drafts.forEach((value, id) => { if (state.editors.has(id)) state.editors.get(id).value = value; });
            }
        } catch (error) {
            if (current(state)) node('transcriptReviewStatus').textContent = error.message || tr('AI校对失败');
        } finally {
            state.aiBusy = false;
            state.editors?.forEach(editor => { editor.readOnly = false; });
            node('transcriptReviewAiBtn').disabled = false;
        }
    }
    document.addEventListener('DOMContentLoaded', () => {
        node('transcriptReviewAiBtn')?.addEventListener('click', requestAI);
        node('transcriptReviewHighBtn')?.addEventListener('click', acceptHigh);
        node('transcriptReviewAudioBtn')?.addEventListener('click', () => requestAudio());
        node('transcriptReviewResolvedBtn')?.addEventListener('click', () => {
            if (!view?.loaded || view.saving || view.aiBusy) return;
            view.showAccepted = !view.showAccepted; updateAccepted(view);
        });
        node('openTranscriptReviewBtn')?.addEventListener('click', open);
        node('transcriptReviewKeepBtn')?.addEventListener('click', () => confirm(true));
        node('transcriptReviewApplyBtn')?.addEventListener('click', () => confirm(false));
        node('transcriptReviewLaterBtn')?.addEventListener('click', hide);
        document.addEventListener('keydown', event => {
            if (!modal() || modal().hidden) return;
            if (view?.saving) return;
            if (event.key === 'Escape') { event.preventDefault(); hide(); }
            if (event.key === 'Tab') {
                const focusable = [...modal().querySelectorAll('button:not([disabled]):not([hidden]), textarea, input[type="range"]')];
                const first = focusable[0], last = focusable.at(-1);
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
            }
        });
    });
    window.syncTranscriptReview = sync;
    window.resetTranscriptReview = reset;
    window.openTranscriptReview = open;
})();
