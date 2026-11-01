/* Templates and read-only transcript comparison. Loaded after the main UI. */
(() => {
    'use strict';
    const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
    const node = id => document.getElementById(id);
    const clone = value => JSON.parse(JSON.stringify(value));
    const TEMPLATE_GLOBALS = GLOBAL_PREF_KEYS.filter(key => key !== 'agent_command');
    function selectedTemplate() { return (prefs.templates || []).find(t => t.id === node('noteTemplate').value); }
    function captureTemplate() {
        flushPendingPrefs();
        persistGlobals();
        persistProfileForm();
        const settings = Object.fromEntries(TEMPLATE_GLOBALS.map(key => [key, prefs.global[key]]));
        settings.note_language = node('noteLanguage').value;
        settings.custom_prompt = node('customPrompt').value.trim();
        const record = activeRecord();
        return sanitizeForStorage({settings, provider: prefs.provider, custom_id: prefs.custom_id,
            model: {model_id: record.model_id, manual_model: record.manual_model,
                ...(prefs.provider === 'custom' ? {label: record.label, base_url: record.base_url} : {})}});
    }
    function renderTemplates(id = '') {
        const select = node('noteTemplate');
        select.replaceChildren(new Option(tr('请选择模板'), ''));
        (prefs.templates || []).forEach(t => {
            const option = new Option(t.name, t.id);
            option.setAttribute('data-no-i18n', '');
            select.add(option);
        });
        select.value = id;
        syncTemplateControls();
    }
    window.refreshNoteTemplates = renderTemplates;
    function syncTemplateControls() {
        const selected = selectedTemplate();
        node('templateName').value = selected?.name || '';
        ['applyTemplateBtn','updateTemplateBtn','renameTemplateBtn','deleteTemplateBtn','defaultTemplate'].forEach(id => node(id).disabled = !selected);
        node('defaultTemplate').checked = !!selected && prefs.default_template_id === selected.id;
    }
    function saveTemplate(update) {
        const name = node('templateName').value.trim();
        if (!name) return showToast('请填写模板名称', 'info');
        if (!update && (prefs.templates || []).length >= 50) return showToast('最多保存 50 个模板', 'info');
        const snapshot = captureTemplate();
        let template = update ? selectedTemplate() : null;
        if (update && !template) return;
        if (template) Object.assign(template, snapshot, {name});
        else {
            template = {...snapshot, name, id: `template-${Date.now()}-${Math.random().toString(36).slice(2,10)}`};
            (prefs.templates ||= []).push(template);
        }
        persistPrefs();
        renderTemplates(template.id);
        showToast('模板已保存', 'success');
    }
    function applyTemplate(template, silent = false) {
        if (!template) return;
        if (isTaskActive || isSubmitting) return showToast('请等待当前任务结束后再应用模板', 'info');
        flushPendingPrefs();
        const previousProvider = prefs.provider;
        const previousBase = activeRecord()?.base_url || '';
        for (const key of TEMPLATE_GLOBALS) if (template.settings[key] !== undefined) prefs.global[key] = clone(template.settings[key]);
        if (PROVIDER_CONFIG[template.provider]) {
            prefs.provider = template.provider;
            if (template.provider === 'custom') {
                let record = prefs.customs.find(r => r.id === template.custom_id && r.base_url === template.model?.base_url);
                if (!record) {
                    record = newCustomEntry(template.model?.label || '自定义接口');
                    prefs.customs.push(record);
                }
                Object.assign(record, template.model);
                prefs.custom_id = record.id;
            } else if (template.model) Object.assign(providerRecord(template.provider), template.model);
        }
        if (previousProvider !== prefs.provider || previousBase !== (activeRecord()?.base_url || '')) {
            clearTypedApiKey();
            keyMatch = null;
        }
        applyPreferencesToForm();
        node('customPrompt').value = template.settings.custom_prompt || '';
        updateGenerationSettings();
        persistPrefs();
        if (!silent) showToast('模板已应用，可继续调整参数', 'success');
    }

    let reviewTaskId = null;
    let revision = 0;
    let data = null;
    let page = 0;
    let filtered = [];
    let activeIndex = -1;
    const PAGE_SIZE = 80;
    const stamp = seconds => {
        const total = Math.max(0, Math.floor(Number(seconds) || 0));
        const h = Math.floor(total / 3600), m = Math.floor(total / 60) % 60, s = total % 60;
        return (h ? `${String(h).padStart(2,'0')}:` : '') + `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    };
    function resetReview() {
        revision++;
        reviewTaskId = null;
        data = null;
        page = 0;
        activeIndex = -1;
        const player = node('reviewPlayer');
        if (!player) return;
        player.pause();
        player.removeAttribute('src');
        player.load();
        player.hidden = true;
        node('noteReviewPanel').hidden = true;
        node('noteReviewLayout').classList.remove('review-open');
        node('reviewToggleBtn').setAttribute('aria-expanded', 'false');
        node('reviewSegments').replaceChildren();
        node('reviewSearch').value = '';
        node('reviewSourceLink').hidden = true;
    }
    window.resetNoteReview = resetReview;
    window.initNoteReview = (_result, taskId) => {
        resetReview();
        reviewTaskId = taskId;
        node('reviewToggleBtn').disabled = !taskId;
    };
    function highlight(index, scroll = false) {
        activeIndex = index;
        node('reviewSegments').querySelectorAll('.review-segment').forEach(row => {
            const current = Number(row.dataset.index) === index;
            row.classList.toggle('is-current', current);
            if (current && scroll) row.scrollIntoView({block:'nearest', behavior:'smooth'});
        });
    }
    function nearestSegment(seconds) {
        if (!data?.segments.length) return -1;
        const exact = data.segments.findIndex(s => seconds >= s.start && seconds < s.end);
        if (exact >= 0) return exact;
        let best = 0;
        data.segments.forEach((s,i) => { if (Math.abs(s.start-seconds) < Math.abs(data.segments[best].start-seconds)) best = i; });
        return best;
    }
    function selectSegment(index) {
        const segment = data?.segments[index];
        if (!segment) return;
        const nextPage = Math.floor(index / PAGE_SIZE);
        if (node('reviewSearch').value || page !== nextPage) {
            node('reviewSearch').value = '';
            filterSegments();
            page = nextPage;
            renderSegments();
        }
        highlight(index, true);
    }
    async function seek(seconds, index = nearestSegment(seconds)) {
        const taskId = reviewTaskId;
        if (!data || !taskId) return;
        node('noteReviewPanel').hidden = false;
        node('noteReviewLayout').classList.add('review-open');
        node('reviewToggleBtn').setAttribute('aria-expanded','true');
        selectSegment(index);
        const segment = data.segments[index];
        if (data.media_url) {
            const player = node('reviewPlayer');
            const offset = Math.max(0, Number(seconds) || 0);
            if (player.readyState < 1) {
                player.preload = 'metadata';
                player.load();
                await new Promise((resolve, reject) => {
                    let timer;
                    const cleanup = () => { clearTimeout(timer); player.removeEventListener('loadedmetadata', ready); player.removeEventListener('error', failed); };
                    const ready = () => { cleanup(); resolve(); };
                    const failed = () => { cleanup(); reject(new Error(tr('本地媒体无法播放，请打开原视频或使用本机播放器'))); };
                    player.addEventListener('loadedmetadata', ready, {once:true});
                    player.addEventListener('error', failed, {once:true});
                    timer = setTimeout(failed, 12000);
                }).catch(error => { if (reviewTaskId === taskId) showToast(error.message, 'error'); });
            }
            if (reviewTaskId !== taskId || player.readyState < 1) return;
            player.currentTime = Number.isFinite(player.duration) ? Math.min(offset, Math.max(0, player.duration - .01)) : offset;
            player.play().catch(() => { if (reviewTaskId === taskId) showToast('已定位时间点，请点击播放器播放', 'info'); });
        } else if (segment?.replay_url) {
            const link = node('reviewSourceLink');
            link.href = segment.replay_url;
            link.textContent = tr(segment.precise_jump ? '打开原视频并跳到此处' : '打开原视频（请手动定位时间点）');
            link.hidden = false;
            link.focus();
            // Keep external navigation user-triggered, avoiding popup blockers after async fetches.
            showToast('已选中字幕，请点击打开原视频', 'info');
        } else showToast('当前任务没有可回放的媒体', 'info');
    }
    function filterSegments() {
        const query = node('reviewSearch').value.trim().toLocaleLowerCase();
        filtered = (data?.segments || []).map((s,i) => ({s,i})).filter(({s}) => !query || s.text.toLocaleLowerCase().includes(query));
        page = 0;
        renderSegments();
    }
    function renderSegments() {
        const list = node('reviewSegments');
        list.replaceChildren();
        const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
        page = Math.min(Math.max(0,page), pages-1);
        filtered.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE).forEach(({s,i}) => {
            const row = document.createElement('div'); row.className = 'review-segment'; row.dataset.index = i;
            const head = document.createElement('div'); head.className = 'review-segment-head';
            const external = !data.media_url && s.replay_url;
            const time = document.createElement(external ? 'a' : 'button');
            if (external) { time.href = s.replay_url; time.target = '_blank'; time.rel = 'noopener noreferrer'; }
            else time.type = 'button';
            time.className = 'review-time';
            time.textContent = `${stamp(s.start)}–${stamp(s.end)}`;
            time.setAttribute('aria-label', `${tr('定位字幕时间点')} ${stamp(s.start)}`);
            time.addEventListener('click', () => external ? highlight(i) : seek(s.start,i));
            const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'button ghost compact-button review-copy'; copy.textContent = tr('复制原文');
            copy.addEventListener('click', async () => {
                try { await navigator.clipboard.writeText(s.text); showToast('原文已复制', 'success'); }
                catch { copyTextFallback(s.text); showToast('原文已复制', 'success'); }
            });
            const text = document.createElement('p'); text.setAttribute('data-no-i18n',''); text.textContent = s.text;
            head.append(time,copy); row.append(head,text); list.append(row);
        });
        node('reviewPrevBtn').disabled = !page;
        node('reviewNextBtn').disabled = page >= pages-1;
        node('reviewPageInfo').textContent = `${page+1} / ${pages} · ${filtered.length} ${tr('段字幕')}`;
        highlight(activeIndex);
    }
    function attachNoteTimes() {
        const content = node('markdownContent');
        const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
        const texts = [];
        while (walker.nextNode()) if (!walker.currentNode.parentElement.closest('a,button,code,pre,.katex')) texts.push(walker.currentNode);
        texts.forEach(text => {
            const expression = /(?<![\d:])(?:\d{1,3}:)?[0-5]\d:[0-5]\d(?![\d:])/g;
            if (!expression.test(text.nodeValue)) return;
            expression.lastIndex = 0;
            const fragment = document.createDocumentFragment();
            let cursor = 0;
            for (const match of text.nodeValue.matchAll(expression)) {
                fragment.append(document.createTextNode(text.nodeValue.slice(cursor, match.index)));
                const seconds = match[0].split(':').reduce((value,n) => value*60+Number(n),0);
                const button = document.createElement('button'); button.type = 'button'; button.className = 'review-time'; button.textContent = match[0];
                button.addEventListener('click', () => seek(seconds));
                fragment.append(button); cursor = match.index+match[0].length;
            }
            fragment.append(document.createTextNode(text.nodeValue.slice(cursor)));
            text.replaceWith(fragment);
        });
    }
    async function toggleReview() {
        const panel = node('noteReviewPanel');
        const open = panel.hidden;
        panel.hidden = !open;
        node('noteReviewLayout').classList.toggle('review-open',open);
        node('reviewToggleBtn').setAttribute('aria-expanded',String(open));
        if (!open) { node('reviewPlayer').pause(); return; }
        if (data) return;
        const taskId = reviewTaskId;
        const token = ++revision;
        node('noteReviewStatus').textContent = tr('正在读取原字幕…');
        try {
            const response = await fetchWithTimeout(`${API_BASE}/task/${encodeURIComponent(taskId)}/review`, {cache:'no-store'}, 12000);
            const result = await readResponse(response, '读取字幕对照失败');
            if (token !== revision || taskId !== reviewTaskId) return;
            data = result;
            data.segments = (Array.isArray(result.segments) ? result.segments : []).filter(s => Number.isFinite(s.start) && Number.isFinite(s.end) && typeof s.text === 'string');
            const player = node('reviewPlayer');
            player.hidden = !data.media_url;
            if (data.media_url) player.src = data.media_url;
            const source = node('reviewSourceLink');
            source.hidden = !data.source_url;
            if (data.source_url) { source.href = data.source_url; source.textContent = tr('打开原视频'); }
            node('noteReviewStatus').textContent = tr(data.media_url
                ? data.media_kind === 'audio' ? '点击时间点回放本地音频。原字幕是转写结果，可能存在识别错误。' : '点击时间点回放本地视频。原字幕是转写结果，可能存在识别错误。'
                : data.source_url ? '点击字幕时间点打开原视频；部分平台需手动定位。' : '可对照和搜索原字幕；当前任务没有可回放的媒体。');
            filterSegments();
            attachNoteTimes();
        } catch (error) { if (token === revision) node('noteReviewStatus').textContent = error.message; }
    }
    document.addEventListener('DOMContentLoaded', () => {
        renderTemplates();
        node('noteTemplate').addEventListener('change',syncTemplateControls);
        node('saveTemplateBtn').addEventListener('click',()=>saveTemplate(false));
        node('updateTemplateBtn').addEventListener('click',()=>saveTemplate(true));
        node('applyTemplateBtn').addEventListener('click',()=>applyTemplate(selectedTemplate()));
        node('renameTemplateBtn').addEventListener('click',()=> {
            const selected = selectedTemplate(), name = node('templateName').value.trim();
            if (!selected || !name) return showToast('请填写模板名称','info');
            selected.name = name; persistPrefs(); renderTemplates(selected.id);
        });
        node('deleteTemplateBtn').addEventListener('click',()=> {
            const selected = selectedTemplate(); if (!selected) return;
            prefs.templates = prefs.templates.filter(t => t.id !== selected.id);
            if (prefs.default_template_id === selected.id) prefs.default_template_id = '';
            persistPrefs(); renderTemplates();
        });
        node('defaultTemplate').addEventListener('change',()=> { prefs.default_template_id = node('defaultTemplate').checked ? selectedTemplate()?.id || '' : ''; persistPrefs(); });
        node('reviewToggleBtn').addEventListener('click',toggleReview);
        node('reviewSearch').addEventListener('input',filterSegments);
        node('reviewPrevBtn').addEventListener('click',()=> { page--; renderSegments(); });
        node('reviewNextBtn').addEventListener('click',()=> { page++; renderSegments(); });
        node('reviewPlayer').addEventListener('timeupdate',()=> { if (data) highlight(nearestSegment(node('reviewPlayer').currentTime)); });
        node('reviewPlayer').addEventListener('error',()=> { if (data) node('noteReviewStatus').textContent = tr('本地媒体无法播放，请打开原视频或使用本机播放器'); });
        function initializeTemplates() {
            renderTemplates();
            if (!new URLSearchParams(location.search).has('task')) {
                const initial = prefs.templates?.find(t => t.id === prefs.default_template_id);
                if (initial) { renderTemplates(initial.id); applyTemplate(initial,true); }
            }
        }
        if (window.__aiVideoNoteReady === false) {
            window.addEventListener('preferences-ready', initializeTemplates, {once: true});
        } else initializeTemplates();
    });
    // Small pure operations exposed for regression checks; no UI state mutation API.
    window.NoteTools = {captureTemplate, stamp};
})();
