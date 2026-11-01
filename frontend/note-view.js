// Note rendering, math, image references and browser exports.
async function showResult(result) {
    const taskId = currentTaskId;
    const viewSequence = taskViewSequence;
    const isCurrentView = () => taskId === currentTaskId && viewSequence === taskViewSequence;
    if (!result || typeof result !== 'object') {
        failTask('任务完成，但后端未返回可用的结果');
        return false;
    }
    const transcript = result.output === 'transcript';
    setTranscriptTaskView(transcript);
    if (!transcript && result.input_type === 'audio') byId('resultTitle').textContent = '音频笔记';

    let markdown = result.markdown;
    if (transcript && typeof markdown !== 'string') {
        // 转录正文刻意不放进 result：它会被每 2 秒一次的轮询反复搬运并落盘，
        // 所以完成或从历史打开时现读磁盘上的 transcript.md
        try {
            markdown = await fetchTranscriptText(taskId);
        } catch (error) {
            if (!isCurrentView()) return false;
            failTask(error.message);
            return false;
        }
    }
    if (!isCurrentView()) return false;
    if (typeof markdown !== 'string') {
        failTask('任务完成，但后端未返回可用的 Markdown 内容');
        return false;
    }

    currentMarkdown = markdown;
    const content = byId('markdownContent');
    if (transcript) {
        currentHtml = '';
        renderTranscript(content, markdown);
    } else {
        currentHtml = replaceImagePaths(markdown);
        content.innerHTML = renderMarkdown(currentHtml);
    }
    window.initNoteReview?.(result, currentTaskId);
    byId('resultArea').hidden = false;
    byId('downloadMdBtn').disabled = false;
    byId('exportSubtitleBtn').disabled = false;
    byId('copyNoteBtn').disabled = false;
    const outputDirectory = typeof result.output_directory === 'string'
        ? result.output_directory.trim()
        : '';
    const archivedPath = typeof result.obsidian_path === 'string' && result.obsidian_path.trim()
        ? result.obsidian_path.trim()
        : typeof result.archived_path === 'string'
        ? result.archived_path.trim()
        : '';
    // 主行放"该拿的那一份"。归档文件名带视频标题，任务目录是一串 UUID 加固定名的中间
    // 产物（notes.md / transcript.md）——把后者摆在主行，用户会以为产物就叫 transcript.md
    // （群测原话），而下载和归档早就按标题命名了。
    const primaryPath = archivedPath || outputDirectory;
    byId('outputPathLabel').textContent = archivedPath ? '已保存到' : '输出文件已保存在';
    byId('outputPath').textContent = primaryPath;
    byId('outputNotice').hidden = !primaryPath;
    byId('taskDirPath').textContent = outputDirectory;
    byId('taskDirNotice').hidden = !outputDirectory;
    const videoPath = typeof result.video_path === 'string' && /\.(?:mp4|mkv|webm|m4s|mov|avi|m4v)$/i.test(result.video_path.trim())
        ? result.video_path.trim() : '';
    byId('videoSavePath').textContent = videoPath;
    byId('videoPathNotice').hidden = !videoPath;
    window.setResultLocation?.(taskId, result);
    return true;
}

async function exportSubtitle() {
    if (!currentTaskId) return;
    const taskId = currentTaskId;
    const viewSequence = taskViewSequence;
    const filename = noteFileName('.srt');
    const button = byId('exportSubtitleBtn');
    button.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/task/${encodeURIComponent(taskId)}/transcript?output_format=json`);
        const data = await readResponse(response, '导出字幕失败');
        const stamp = (seconds) => {
            const ms = Math.max(0, Math.round(Number(seconds) * 1000));
            return `${String(Math.floor(ms / 3600000)).padStart(2, '0')}:${String(Math.floor(ms / 60000) % 60).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')},${String(ms % 1000).padStart(3, '0')}`;
        };
        if (!Array.isArray(data.segments) || !data.segments.length) throw new Error('尚无可导出的字幕');
        const text = data.segments.map((item, i) => `${i + 1}\n${stamp(item.start)} --> ${stamp(item.end)}\n${item.text}\n`).join('\n');
        triggerBlobDownload(new Blob([text], {type: 'text/plain;charset=utf-8'}), '.srt', filename);
        showToast('字幕已导出', 'success');
    } catch (error) { showToast(error.message, 'error'); }
    finally { if (taskId === currentTaskId && viewSequence === taskViewSequence) button.disabled = false; }
}

async function fetchTranscriptText(taskId = currentTaskId) {
    if (!taskId) throw new Error('没有可读取的转录任务');
    const response = await fetch(
        `${API_BASE}/task/${encodeURIComponent(taskId)}/transcript?output_format=markdown`,
        { cache: 'no-store' }
    );
    const data = await readResponse(response, '读取字幕稿失败');
    if (typeof data.text !== 'string') throw new Error('后端未返回转录正文');
    return data.text;
}

// 一行一段字幕渲染成一行 DOM：时间轴单独成列，正文用 textContent 注入（不过 marked，
// 也没有 HTML 拼接），所以不需要再过一遍 DOMPurify。
function renderTranscript(content, text) {
    const rows = [];
    text.split(/\r?\n/).forEach((rawLine) => {
        const line = rawLine.trim();
        if (!line) return;
        const heading = line.match(/^#{1,6}\s+(.*)$/);
        if (heading) {
            const node = document.createElement('p');
            node.className = 'transcript-heading';
            node.textContent = heading[1];
            rows.push(node);
            return;
        }
        const timed = line.match(/^\[([^\]]+)\]\s*(.*)$/);
        if (!timed) {
            const node = document.createElement('p');
            node.className = 'transcript-plain';
            node.textContent = line;
            rows.push(node);
            return;
        }
        const row = document.createElement('div');
        row.className = 'transcript-line';
        const time = document.createElement('time');
        time.textContent = `[${timed[1]}]`;
        const body = document.createElement('span');
        body.textContent = timed[2];
        row.append(time, body);
        rows.push(row);
    });
    content.replaceChildren(...rows);
}

// 历史笔记里写的是 ./images/，磁盘上一直是 frames/；两种都要认，
// 否则历史任务重新打开时截图照样显示不出来。
const NOTE_IMAGE_RE = /!\[([^\]]*)\]\((?:\.\/)?(?:frames|images)\/([^)]+)\)/g;
const SERVED_IMAGE_RE = new RegExp(
    `!\\[([^\\]]*)\\]\\((${API_BASE}/image/[^)]+)\\)`,
    'g'
);

function replaceImagePaths(markdown) {
    if (!currentTaskId) return markdown;
    return markdown.replace(
        NOTE_IMAGE_RE,
        `![$1](${API_BASE}/image/${encodeURIComponent(currentTaskId)}/$2)`
    );
}

// 数学片段（$...$ / $$...$$）先摘成纯字母数字占位符，marked + DOMPurify 之后再
// 回填 KaTeX 本地渲染结果：KaTeX 产物依赖 inline style 与 <math> 标签，过不了
// 下面的 sanitize 白名单，也不该过——它是本机从数学源码生成的可信输出。
// 行内公式的内容不允许以空白开头/结尾且不跨行，避免把两个独立的 $ 符号误认成一对。
const MATH_SEGMENT_RE = /\$\$([\s\S]+?)\$\$|\$([^\s$](?:[^$\n]*[^\s$])?)\$/g;
const MATH_PLACEHOLDER_RE = /MATHSEGZ(\d+)ZEND/g;

function extractMathSegments(markdown) {
    const segments = [];
    const protectedParts = markdown.split(/(^```[^\n]*\n[\s\S]*?^```[^\n]*$|^~~~[^\n]*\n[\s\S]*?^~~~[^\n]*$|`+[^`\n]*`+)/gm);
    const parts = protectedParts.map((part, index) => {
        if (index % 2 === 1) return part;
        return part.replace(/(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$|(?<![\\$])\$([^\s$](?:[^$\n]*?[^\s$])?)(?<!\\)\$(?!\$)|\\\[([\s\S]+?)\\\]|\\\(([^\n]+?)\\\)/g, (match, display, inline, bracketDisplay, bracketInline) => {
            segments.push({
                source: (display ?? inline ?? bracketDisplay ?? bracketInline ?? '').trim(),
                displayMode: display !== undefined || bracketDisplay !== undefined,
            });
            return `MATHSEGZ${segments.length - 1}ZEND`;
        });
    });
    return { markdown: parts.join(''), segments };
}

function restoreMathSegments(html, segments) {
    if (!segments.length) return html;
    return html.replace(MATH_PLACEHOLDER_RE, (match, indexText) => {
        const segment = segments[Number(indexText)];
        if (!segment) return match;
        const fallback = escapeHtml(
            segment.displayMode ? `$$${segment.source}$$` : `$${segment.source}$`
        );
        if (typeof katex === 'undefined') return fallback;
        try {
            return katex.renderToString(segment.source, {
                displayMode: segment.displayMode,
                throwOnError: false,
            });
        } catch {
            return fallback;
        }
    });
}

function renderMarkdownBase(markdown) {
    if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
        return `<pre>${escapeHtml(markdown)}</pre>`;
    }
    return DOMPurify.sanitize(marked.parse(markdown), {
        USE_PROFILES: { html: true },
        FORBID_TAGS: [
            'button', 'embed', 'form', 'iframe', 'input', 'math', 'object',
            'option', 'select', 'style', 'svg', 'textarea'
        ],
        FORBID_ATTR: ['style']
    });
}

function renderMarkdown(markdown) {
    const { markdown: protectedMarkdown, segments } = extractMathSegments(markdown);
    return restoreMathSegments(renderMarkdownBase(protectedMarkdown), segments);
}

async function downloadSummary() {
    if (isDownloading || !currentMarkdown) return;
    const format = byId('formatSelect').value;
    if (format === 'png') {
        await exportSummaryImage();
        return;
    }
    if (['docx', 'pdf', 'enex', 'html'].includes(format)) {
        await downloadMarkdownFile(format);
        return;
    }
    if (format === 'markdown' || format === 'obsidian') {
        await downloadMarkdownFile();
        return;
    }

    let htmlSource = currentHtml || currentMarkdown;
    if (format === 'html' && htmlSource.includes(`${API_BASE}/image/`)) {
        setDownloading(true);
        showToast('正在把截图嵌入 HTML，请稍候', 'info');
        try {
            htmlSource = await inlineServedImages(htmlSource);
        } finally {
            setDownloading(false);
        }
    }

    const formats = {
        html: { content: convertToHtml(htmlSource), extension: '.html', mime: 'text/html' },
        json: { content: convertToJson(currentMarkdown), extension: '.json', mime: 'application/json' },
        txt: { content: stripMarkdown(currentMarkdown), extension: '.txt', mime: 'text/plain' }
    };
    const selected = formats[format] || { content: currentMarkdown, extension: '.md', mime: 'text/markdown' };
    triggerBlobDownload(
        new Blob([selected.content], { type: selected.mime }),
        selected.extension,
        noteFileName(selected.extension)
    );
    showToast('下载已开始', 'success');
}

function toggleImageLayout() {
    byId('imageLayoutSelect').hidden = byId('formatSelect').value !== 'png';
}

async function copyFullNote() {
    if (!currentMarkdown) return validationError('没有可复制的笔记');
    await copyTextToClipboard(currentMarkdown, '完整笔记已复制');
}

function copyTextFallback(value) {
    const textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.setAttribute('readonly', '');
    textarea.className = 'clipboard-fallback';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    if (!copied) throw new Error('浏览器未授予剪贴板权限');
}

function applyImageExportParagraphIndent(root) {
    for (const paragraph of root.querySelectorAll('p')) {
        const text = paragraph.textContent.trim();
        const links = Array.from(paragraph.querySelectorAll('a'));
        const source = /^(?:视频链接|来源链接|原视频链接|视频来源|来源|Video(?:\s+(?:link|source|URL))?|Source(?:\s+(?:link|URL))?)\s*[:：]/i.test(text)
            && (links.length > 0 || /https?:\/\//i.test(text));
        const caption = /^(?:图(?:片)?(?:注|说明|备注)|图\s*\d*|Fig(?:ure)?\.?\s*\d+|Caption|Image\s+(?:caption|note))\s*[:：.、]/i.test(text);
        const metadata = /^(?:UP主|作者|时长|Author|Duration)\s*[:：]/i.test(text);
        let linkOnly = /^https?:\/\/\S+$/i.test(text);
        if (links.length) {
            const outsideLinks = paragraph.cloneNode(true);
            for (const link of outsideLinks.querySelectorAll('a')) link.remove();
            linkOnly ||= !outsideLinks.textContent.trim();
        }
        const prose = Boolean(text) && !source && !caption && !metadata && !linkOnly
            && !paragraph.closest('li, td, th, blockquote, figcaption')
            && !paragraph.querySelector('img, .katex-display');
        paragraph.classList.toggle('note-body-paragraph', prose);
        paragraph.classList.toggle('note-image-caption', caption);
    }
}

function centerImageExportFigures(root) {
    for (const image of root.querySelectorAll('img')) {
        if (image.closest('.image-export-figure')) continue;
        const figure = document.createElement('span');
        figure.className = 'image-export-figure';
        figure.setAttribute('role', 'figure');
        image.replaceWith(figure);
        figure.append(image);
        if (image.alt.trim()) {
            const caption = document.createElement('span');
            caption.className = 'image-export-caption';
            caption.textContent = image.alt;
            figure.append(caption);
        }
    }
}

async function exportSummaryImage() {
    if (typeof html2canvas === 'undefined') {
        showToast('图片导出组件未加载，请刷新页面后重试', 'error');
        return;
    }
    const operation = beginNoteDownload();
    const downloadName = currentDownloadName;
    const requestedLayout = byId('imageLayoutSelect').value;
    const stage = document.createElement('div');
    stage.className = 'image-export-stage';
    const sheet = document.createElement('article');
    sheet.className = 'image-export-sheet';
    sheet.innerHTML = renderMarkdown(currentHtml || currentMarkdown);
    centerImageExportFigures(sheet);
    applyImageExportParagraphIndent(sheet);
    stage.append(sheet);
    document.body.append(stage);
    try {
        if (document.fonts?.ready) await document.fonts.ready;
        await waitForExportImages(sheet);
        const width = sheet.scrollWidth;
        const height = sheet.scrollHeight;
        const areaScale = Math.sqrt(40_000_000 / Math.max(1, width * height));
        const heightScale = 30_000 / Math.max(1, height);
        const scale = Math.min(2, areaScale, heightScale);
        if (scale < 0.75) {
            throw new Error('笔记内容过长，暂时无法稳定导出图片，请改用 HTML 或 Markdown');
        }
        const canvas = await html2canvas(sheet, {
            backgroundColor: '#ffffff',
            scale,
            useCORS: true,
            logging: false,
            width,
            height,
            windowWidth: width
        });
        const paginate = requestedLayout === 'portrait' || canvas.height > 16_000;
        if (paginate) {
            if (requestedLayout === 'long') {
                showToast('笔记较长，已自动改为 3:4 分页图片', 'info');
            }
            await downloadCanvasPages(canvas, downloadName);
        } else {
            triggerBlobDownload(await canvasToBlob(canvas), '.png', noteFileName('.png', downloadName));
        }
        showToast('图片导出已完成', 'success');
    } catch (error) {
        showToast(`图片导出失败：${error.message}`, 'error');
    } finally {
        stage.remove();
        finishNoteDownload(operation);
    }
}

async function waitForExportImages(root) {
    const images = Array.from(root.querySelectorAll('img'));
    await Promise.all(images.map((img) => {
        if (img.complete) return Promise.resolve();
        return new Promise((resolve) => {
            img.addEventListener('load', resolve, { once: true });
            img.addEventListener('error', resolve, { once: true });
            window.setTimeout(resolve, 5000);
        });
    }));
}

async function downloadCanvasPages(source, downloadName = currentDownloadName) {
    const pageHeight = Math.round(source.width * 4 / 3);
    const pagePadding = Math.max(32, Math.round(source.width * 0.055));
    const contentHeight = pageHeight - pagePadding * 2;
    const overlap = Math.max(24, Math.round(source.width * 0.025));
    const pageAdvance = contentHeight - overlap;
    const pages = source.height <= contentHeight
        ? 1
        : 1 + Math.ceil((source.height - contentHeight) / pageAdvance);
    for (let index = 0; index < pages; index += 1) {
        const page = document.createElement('canvas');
        page.width = source.width;
        page.height = pageHeight;
        const context = page.getContext('2d');
        context.fillStyle = '#ffffff';
        context.fillRect(0, 0, page.width, page.height);
        const sourceY = index * pageAdvance;
        const sliceHeight = Math.min(contentHeight, source.height - sourceY);
        context.drawImage(
            source,
            0,
            sourceY,
            source.width,
            sliceHeight,
            0,
            pagePadding,
            source.width,
            sliceHeight
        );
        const suffix = `-page-${String(index + 1).padStart(3, '0')}.png`;
        triggerBlobDownload(await canvasToBlob(page), suffix, noteFileName(suffix, downloadName));
    }
}

function canvasToBlob(canvas) {
    return new Promise((resolve, reject) => {
        canvas.toBlob(
            (blob) => blob ? resolve(blob) : reject(new Error('无法生成 PNG 文件')),
            'image/png'
        );
    });
}

async function downloadMarkdownFile(format = 'markdown') {
    if (!currentTaskId) return validationError('没有可下载的任务');
    const taskId = currentTaskId;
    const downloadName = currentDownloadName;
    const operation = beginNoteDownload();
    try {
        const response = await fetch(`${API_BASE}/download/${encodeURIComponent(taskId)}${format !== 'markdown' ? '?format=' + encodeURIComponent(format) : ''}`);
        if (!response.ok) throw new Error(await extractErrorMessage(response, '下载失败'));
        const filename = serverFilename(response);
        const extension = ['docx', 'pdf', 'enex', 'html'].includes(format) ? '.' + format : '.md';
        triggerBlobDownload(await response.blob(), extension, filename || noteFileName(extension, downloadName));
        showToast(
            /\.zip$/i.test(filename) ? '笔记与截图已打包下载，解压后 .md 里才显示截图' : '下载已开始',
            'success'
        );
    } catch (error) {
        showToast(`下载失败：${error.message}`, 'error');
    } finally {
        finishNoteDownload(operation);
    }
}

// 单文件 HTML 里的 /api/image 引用只在服务运行时解析得到，离开本机就是坏图，
// 所以导出前把截图逐张取回成 data URI。取不到的（任务已删）保留原引用，不整单失败。
async function inlineServedImages(markdown) {
    const urls = [...new Set(Array.from(markdown.matchAll(SERVED_IMAGE_RE), (match) => match[2]))];
    if (!urls.length) return markdown;
    const inlined = await Promise.all(urls.map(imageDataUri));
    const byUrl = new Map(urls.map((url, index) => [url, inlined[index]]));
    return markdown.replace(
        SERVED_IMAGE_RE,
        (match, alt, url) => (byUrl.get(url) ? `![${alt}](${byUrl.get(url)})` : match)
    );
}

async function imageDataUri(url) {
    try {
        const response = await fetch(url, { cache: 'no-store' });
        if (!response.ok) return '';
        const blob = await response.blob();
        return await new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result));
            reader.onerror = () => resolve('');
            reader.readAsDataURL(blob);
        });
    } catch {
        return '';
    }
}

function serverFilename(response) {
    const header = response.headers.get('content-disposition') || '';
    const raw = header.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
        ?? header.match(/filename="?([^";]+)"?/i)?.[1];
    if (!raw) return '';
    try {
        return decodeURIComponent(raw);
    } catch {
        return raw;
    }
}

let noteDownloadSequence = 0;
function beginNoteDownload() {
    const operation = { sequence: ++noteDownloadSequence, taskId: currentTaskId, view: taskViewSequence };
    setDownloading(true);
    return operation;
}

function finishNoteDownload(operation) {
    if (operation.sequence !== noteDownloadSequence) return;
    isDownloading = false;
    if (operation.taskId === currentTaskId && operation.view === taskViewSequence) setDownloading(false);
}

function resetNoteDownloads() {
    noteDownloadSequence++;
    setDownloading(false);
}

function setDownloading(active) {
    isDownloading = active;
    const button = byId('downloadMdBtn');
    button.disabled = active;
    button.classList.toggle('is-loading', active);
    button.querySelector('.btn-text').textContent = active ? '准备下载' : '下载';
}

// 网页端自己拼的文件名（txt / html / json / png）。后端清洗过分 P 与非法字符，
// 这里只负责拼上扩展名；没有下载名时返回空串，让 triggerBlobDownload 兜时间戳。
function noteFileName(extension, downloadName = currentDownloadName) {
    return downloadName ? `${downloadName}${extension}` : '';
}

function triggerBlobDownload(blob, extension, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename || `video_summary_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}${extension}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function convertToHtml(markdown) {
    // 导出的单文件 html 不带 KaTeX 样式，公式保持 $ 原文（Typora/Obsidian 等可渲染）
    const body = renderMarkdownBase(markdown);
    return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>视频笔记</title></head><body><main>${body}</main></body></html>`;
}

function convertToJson(markdown) {
    return JSON.stringify({ summary: markdown, generated_at: new Date().toISOString(), format: 'markdown' }, null, 2);
}

function stripMarkdown(markdown) {
    const extracted = extractMathSegments(markdown);
    const plain = extracted.markdown.replace(/!\[[^\]]*\]\([^)]+\)/g, '').replace(/[#*`>|]/g, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/\n{3,}/g, '\n\n');
    return plain.replace(MATH_PLACEHOLDER_RE, (match, index) => {
        const segment = extracted.segments[Number(index)];
        return segment.displayMode ? `$$${segment.source}$$` : `$${segment.source}$`;
    });
}

function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = value;
    return element.innerHTML;
}

