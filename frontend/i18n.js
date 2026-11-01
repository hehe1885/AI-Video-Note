/* UI localization only: never translate generated notes, transcripts, filenames or input values. */
(() => {
    'use strict';
    const STORAGE_KEY = 'vtn_ui_language';
    const catalog = window.AIVideoNoteEnglish || {};
    const replacements = Object.entries(catalog).sort((a, b) => b[0].length - a[0].length);
    const browserLanguage = typeof navigator !== 'undefined' ? navigator.language || 'en' : 'en';
    let language = /^zh(?:[-_]|$)/i.test(browserLanguage) ? 'zh-CN' : 'en';
    let savedLanguage = null;
    let languageRevision = 0;
    try {
        savedLanguage = localStorage.getItem(STORAGE_KEY);
        if (savedLanguage === 'en' || savedLanguage === 'zh-CN') language = savedLanguage;
    } catch (_) {}
    function translate(text) {
        if (language !== 'en' || typeof text !== 'string') return text;
        const trimmed = text.trim();
        if (catalog[trimmed]) return text.replace(trimmed, catalog[trimmed]);
        // Translate each source phrase once; never run replacements over translated English.
        const paths = [];
        const protectedText = text.replace(/(?:[A-Za-z]:[\\/]|\\\\)[^\r\n]+|https?:\/\/[^\s]+/g, value => {
            paths.push(value);
            return `VTNPATH${paths.length - 1}END`;
        });
        let remaining = protectedText, output = '';
        while (remaining) {
            let match = null, at = remaining.length;
            for (const [source, target] of replacements) {
                const index = remaining.indexOf(source);
                if (index >= 0 && (index < at || (index === at && (!match || source.length > match[0].length)))) {
                    match = [source, target]; at = index;
                }
            }
            if (!match) { output += remaining; break; }
            output += remaining.slice(0, at) + match[1];
            remaining = remaining.slice(at + match[0].length);
        }
        return output.replace(/VTNPATH(\d+)END/g, (_, index) => paths[Number(index)]);
    }
    const originals = new WeakMap();
    const attributes = new WeakMap();
    const protectedSelector = '#markdownContent, #logArea, #outputPath, #taskDirPath, #modelDirectoryCurrent, #modelDirectoryDefault, #selectedModelPaths, .recent-task-text strong, input, textarea, code, pre, script, style, [data-no-i18n]';
    function applyText(node) {
        const parent = node.parentElement;
        if (!parent || parent.closest(protectedSelector)) return;
        if (parent.closest('#fileInfo') && document.getElementById('localFile')?.files?.length) return;
        if (parent.closest('#llmProfile') && parent.tagName === 'OPTION' && parent.value?.startsWith('custom:') && !['自定义接口', '新的自定义接口', 'Custom endpoint', 'New custom endpoint'].includes(parent.textContent)) return;
        const previous = originals.get(node);
        const source = previous && node.nodeValue === previous.translated ? previous.source : node.nodeValue;
        const translated = translate(source);
        originals.set(node, {source, translated});
        if (node.nodeValue !== translated) node.nodeValue = translated;
    }
    function applyAttributes(element) {
        if (element.closest('#markdownContent, .recent-task-text strong, [data-no-i18n]')) return;
        let record = attributes.get(element);
        if (!record) { record = {}; attributes.set(element, record); }
        for (const name of ['title', 'placeholder', 'aria-label', 'label']) {
            if (!element.hasAttribute(name)) continue;
            const value = element.getAttribute(name), previous = record[name];
            const source = previous && value === previous.translated ? previous.source : value;
            const translated = translate(source);
            record[name] = {source, translated};
            if (translated !== value) element.setAttribute(name, translated);
        }
    }
    function apply(root = document.body) {
        if (!root) return;
        if (root.nodeType === 3) { applyText(root); return; }
        if (root.nodeType !== 1) return;
        applyAttributes(root);
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
        let node;
        while ((node = walker.nextNode())) {
            if (node.nodeType === 3) applyText(node); else applyAttributes(node);
        }
    }
    function updateButton() {
        const button = document.getElementById('languageToggle');
        if (!button) return;
        button.textContent = language === 'en' ? '中文' : 'English';
        button.setAttribute('aria-label', language === 'en' ? '切换为中文界面' : 'Switch to English');
        button.title = language === 'en' ? '切换为中文界面' : 'Switch to English';
    }
    function setLanguage(next, syncNative = true, userInitiated = false) {
        languageRevision++;
        language = next === 'en' ? 'en' : 'zh-CN';
        document.documentElement.lang = language;
        document.title = translate('AI Video Note - 本地视频笔记');
        try { localStorage.setItem(STORAGE_KEY, language); } catch (_) {}
        apply(); updateButton();
        window.dispatchEvent(new CustomEvent("ui-language-changed", {detail: {userInitiated}}));
        if (syncNative) window.syncNativeLanguage?.(language);
    }
    let languageTransition = null;
    let pendingLanguage = null;
    function toggleLanguage() {
        const current = pendingLanguage?.revision === languageRevision ? pendingLanguage.next : language;
        const next = current === 'en' ? 'zh-CN' : 'en';
        const revision = ++languageRevision;
        pendingLanguage = {next, revision};
        let applied = false;
        const update = () => {
            if (applied || revision !== languageRevision) return;
            applied = true;
            pendingLanguage = null;
            setLanguage(next, true, true);
        };
        languageTransition?.skipTransition();
        document.activeViewTransition?.skipTransition();
        const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
        if (typeof document.startViewTransition === 'function' && !reduced) {
            try {
                languageTransition = document.startViewTransition(update);
                languageTransition.ready.catch(() => {});
                languageTransition.finished.catch(update);
            } catch (_error) { update(); }
        } else update();
    }
    for (const name of ['confirm', 'alert', 'prompt']) {
        const original = window[name];
        if (typeof original === 'function') window[name] = (message, ...args) => original.call(window, translate(message), ...args);
    }
    window.AIVideoNoteI18n = {translate, setLanguage, apply, catalog, get language() { return language; }};
    document.documentElement.lang = language;
    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('languageToggle')?.addEventListener('click', toggleLanguage);
        // Let initial settings capture their original model labels before translating.
        setTimeout(() => {
            setLanguage(language, false);
            const initialRevision = languageRevision;
            // Workspace language wins over a cache from another port/browser.
            // A user toggle during the request still wins over a late response.
            if (typeof fetch === 'function') {
                fetch('/api/ui/language', {cache: 'no-store'})
                    .then(response => response.ok ? response.json() : Promise.reject())
                    .then(data => {
                        if (languageRevision !== initialRevision) return;
                        if (data.saved === false && ['en', 'zh-CN'].includes(savedLanguage)) {
                            window.syncNativeLanguage?.(language);
                        } else setLanguage(data.language, false);
                    })
                    .catch(() => {}); // Do not overwrite disk settings when reading failed.
            }
            new MutationObserver(records => {
                for (const record of records) {
                    if (record.type === 'characterData') applyText(record.target);
                    else if (record.type === 'attributes') applyAttributes(record.target);
                    else for (const node of record.addedNodes) apply(node);
                }
            }).observe(document.body, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:['title','placeholder','aria-label','label']});
        }, 0);
    });
})();
