"""Offline KaTeX/mhchem export images, shared by every rich document exporter."""
import base64
from contextlib import contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

from PIL import Image

# Code is matched first, so literal examples and escaped dollars stay untouched.
SEGMENTS = re.compile(r'(?P<code>^```[^\n]*\n[\s\S]*?^```[^\n]*$|^~~~[^\n]*\n[\s\S]*?^~~~[^\n]*$|`+[^`\n]*`+)|(?P<display>(?<!\\)\$\$[\s\S]+?(?<!\\)\$\$|\\\[[\s\S]+?\\\])|(?P<inline>(?<![\\$])\$[^\s$](?:[^$\n]*?[^\s$])?(?<!\\)\$(?!\$)|\\\([^\n]+?\\\))', re.M)
_lock = threading.RLock()
_cache = {}


def math_segments(content):
    for match in SEGMENTS.finditer(content):
        if match['code'] is None:
            value = match.group()
            display = match['display'] is not None
            yield match, value[2:-2] if display or value.startswith('\\(') else value[1:-1], display


def browser_path():
    candidates = [Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
                  Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe']
    for name in ('msedge', 'chromium', 'chromium-browser', 'google-chrome'):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    browser = next((str(p) for p in candidates if p.is_file()), None)
    if browser is None:
        raise RuntimeError('公式导出需要本机安装 Microsoft Edge 或 Chrome；也可选择 Markdown 保留公式源码。')
    return browser


@contextmanager
def browser_session():
    from websockets.sync.client import connect
    temporary = tempfile.TemporaryDirectory(prefix='note-math-', ignore_cleanup_errors=True)
    with temporary as folder:
        profile = Path(folder)
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        process = subprocess.Popen([browser_path(), '--headless=new', '--disable-gpu', '--no-first-run',
            '--no-default-browser-check', '--disable-extensions', '--disable-background-networking',
            '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
            '--user-data-dir=' + str(profile), 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
        socket = None
        sequence = 0
        def call(method, params=None, session=None):
            nonlocal sequence
            sequence += 1
            payload = dict(id=sequence, method=method, params=params or {})
            if session:
                payload['sessionId'] = session
            socket.send(json.dumps(payload))
            deadline = time.monotonic() + 30
            while True:
                reply = json.loads(socket.recv(timeout=max(.1, deadline-time.monotonic())))
                if reply.get('id') == sequence:
                    if 'error' in reply:
                        raise RuntimeError('公式渲染失败：' + str(reply['error']))
                    return reply.get('result', {})
        try:
            port_file = profile / 'DevToolsActivePort'
            deadline = time.monotonic() + 20
            while not port_file.is_file():
                # Edge may forward startup to a child and exit successfully before the port file exists.
                if process.poll() not in (None, 0) or time.monotonic() > deadline:
                    raise RuntimeError('无法启动本地公式渲染组件，请检查 Edge/Chrome 是否可用。')
                time.sleep(.05)
            port, endpoint = port_file.read_text().splitlines()[:2]
            socket = connect(f'ws://127.0.0.1:{port}{endpoint}', open_timeout=10, max_size=32*1024*1024)
            yield call, profile
        finally:
            if socket:
                try:
                    call('Browser.close')
                except Exception:
                    pass
                socket.close()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
            # A forwarding parent can already be gone while its browser child
            # is flushing profile files after Browser.close. Cleanup must not
            # replace a rendering error, or turn a valid export into a failure.
            for _ in range(20):
                temporary.cleanup()
                if not profile.exists():
                    break
                time.sleep(.1)


def evaluation_error(reply):
    details = reply.get('exceptionDetails') or {}
    return str((details.get('exception') or {}).get('description') or details.get('text') or 'Unknown browser error')[:500]


def wait_for_formula_page(call, session, timeout=10):
    # Checking only window.katex races with parsing the later formula element.
    # Poll across navigation rather than holding a promise in a replaced context.
    ready = "document.readyState === 'complete' && typeof window.katex?.render === 'function' && !!document.getElementById('formula')"
    deadline = time.monotonic() + timeout
    detail = ''
    while True:
        try:
            result = call('Runtime.evaluate', dict(expression=ready, returnByValue=True), session)
        except RuntimeError as error:
            if not any(text in str(error).lower() for text in ('context', 'navigat')):
                raise
            detail = str(error)
        else:
            if result.get('result', {}).get('value') is True:
                return
            if result.get('exceptionDetails'):
                detail = evaluation_error(result)
        if time.monotonic() >= deadline:
            raise RuntimeError('公式组件加载失败，请修复本地 KaTeX 资源。' + (' ' + detail if detail else ''))
        time.sleep(.05)


def render_formulas(formulas):
    """Return high resolution PNGs; serialize browser work and reuse repeated equations."""
    with _lock:
        missing = list(dict.fromkeys(item for item in formulas if item not in _cache))
        if missing:
            vendor = Path(__file__).resolve().parent.parent / 'frontend/vendor/katex'
            with browser_session() as (call, folder):
                page = folder / 'math.html'
                page.write_text('<!doctype html><meta charset="utf-8">'
                    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src file: \'unsafe-inline\'; style-src file: \'unsafe-inline\'; font-src file: data:">'
                    f'<link rel="stylesheet" href="{(vendor / "katex.min.css").as_uri()}">'
                    f'<script src="{(vendor / "katex.min.js").as_uri()}"></script>'
                    f'<script src="{(vendor / "mhchem.min.js").as_uri()}"></script>'
                    '<style>body{margin:8px;background:white}#formula{display:inline-block;padding:5px;font-size:20px}.katex-display{margin:0}</style>'
                    '<div id="formula"></div>', encoding='utf-8')
                target = call('Target.createTarget', {'url': 'about:blank'})['targetId']
                session = call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
                call('Emulation.setDeviceMetricsOverride', dict(width=1600, height=1200, deviceScaleFactor=2, mobile=False), session)
                call('Page.enable', session=session)
                navigation = call('Page.navigate', {'url': page.as_uri()}, session)
                if navigation.get('errorText'):
                    raise RuntimeError('公式页面加载失败：' + navigation['errorText'])
                wait_for_formula_page(call, session)
                for source, display in missing:
                    expression = '(async()=>{const el=document.getElementById("formula");katex.render(' + json.dumps(source) + ',el,{displayMode:' + str(display).lower() + ',throwOnError:false,trust:false,maxExpand:1000});await document.fonts.ready;const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})()'
                    reply = call('Runtime.evaluate', dict(expression=expression, awaitPromise=True, returnByValue=True), session)
                    if reply.get('exceptionDetails'):
                        raise RuntimeError('公式渲染失败：' + source[:80] + '；' + evaluation_error(reply))
                    bounds = reply['result']['value']
                    if bounds['width'] > 15000 or bounds['height'] > 15000:
                        raise RuntimeError('单个公式过大，请拆分为多个公式后导出。')
                    encoded = call('Page.captureScreenshot', dict(format='png', captureBeyondViewport=True, clip={**bounds, 'scale': 1}), session)['data']
                    _cache[(source, display)] = base64.b64decode(encoded)
        result = {item: _cache[item] for item in formulas}
        # Bound retained memory, without dropping images required by this export.
        if len(_cache) > 256:
            _cache.clear()
        return result


def prepare_math(content):
    segments = list(math_segments(content))
    if not segments:
        return content
    images = render_formulas([(source, display) for _, source, display in segments])
    result, end = [], 0
    for match, source, display in segments:
        encoded = base64.b64encode(images[(source, display)]).decode('ascii')
        alt = source.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]').replace('\n', ' ')
        title = 'note-formula-display' if display else 'note-formula-inline'
        result.extend([content[end:match.start()], f'![{alt}](data:image/png;base64,{encoded} "{title}")'])
        end = match.end()
    return ''.join(result) + content[end:]


def formula_image(src):
    if not src.startswith('data:image/png;base64,'):
        return None
    if len(src) > 32*1024*1024:
        raise ValueError('公式图片过大')
    data = base64.b64decode(src.split(',', 1)[1], validate=True)
    with Image.open(BytesIO(data)) as image:
        if image.width*image.height > 40_000_000:
            raise ValueError('公式图片过大')
        image.verify()
    return data
