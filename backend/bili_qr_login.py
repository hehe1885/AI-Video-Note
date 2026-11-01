"""Bilibili QR login inside the local web UI; no browser/profile is opened."""
import asyncio
import base64
import io
import time
from urllib.parse import parse_qs, urlparse

import httpx
import qrcode

GENERATE_URL = 'https://passport.bilibili.com/x/passport-login/web/qrcode/generate'
POLL_URL = 'https://passport.bilibili.com/x/passport-login/web/qrcode/poll'
TTL_SECONDS = 180


def qr_image(url):
    image = qrcode.make(url, box_size=6, border=4)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


class BiliQrLoginManager:
    def __init__(self, profile_root):
        self.session = None
        self.lock = asyncio.Lock()

    async def _request(self, url, params=None):
        async with httpx.AsyncClient(timeout=12, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'
        }) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json(), dict(response.cookies)

    def _describe(self):
        if not self.session:
            return {'ok': True, 'state': 'idle', 'message': '无进行中的登录会话', 'cookies': None}
        return {key: value for key, value in self.session.items() if key not in ('key', 'started_at')}

    async def start(self):
        async with self.lock:
            if self.session and self.session['state'] in ('waiting', 'scanned') and time.time() - self.session['started_at'] < TTL_SECONDS:
                return self._describe()
            self.session = None
            try:
                payload, _ = await self._request(GENERATE_URL)
                data = payload.get('data') or {}
                url = data.get('url', '')
                parsed = urlparse(url)
                if payload.get('code') != 0 or not data.get('qrcode_key') or parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.bilibili.com'):
                    raise ValueError('二维码接口返回异常')
                self.session = {'ok': True, 'state': 'waiting', 'message': '请使用哔哩哔哩手机客户端扫描二维码',
                    'qr_image': qr_image(url), 'key': data['qrcode_key'], 'started_at': time.time(), 'cookies': None}
            except (httpx.HTTPError, ValueError, KeyError, ImportError):
                return {'ok': False, 'state': 'failed', 'error': '获取 B 站二维码失败，请检查网络后重试'}
            return self._describe()

    async def status(self):
        async with self.lock:
            session = self.session
            if not session or session['state'] not in ('waiting', 'scanned'):
                return self._describe()
            if time.time() - session['started_at'] >= TTL_SECONDS:
                session.update(state='timeout', message='二维码已过期，请重新获取', qr_image='')
                return self._describe()
            try:
                payload, cookies = await self._request(POLL_URL, {'qrcode_key': session['key']})
                data = payload.get('data') or {}
                if payload.get('code') != 0:
                    raise ValueError('Unexpected response')
                code = data.get('code')
                if code == 86101:
                    session.update(state='waiting', message='请使用哔哩哔哩手机客户端扫描二维码')
                elif code == 86090:
                    session.update(state='scanned', message='已扫码，请在手机上确认登录')
                elif code == 86038:
                    session.update(state='timeout', message='二维码已过期，请重新获取', qr_image='')
                elif code == 0:
                    # The login API may return credentials in Set-Cookie and/or its callback URL.
                    query = parse_qs(urlparse(data.get('url', '')).query)
                    def value(name):
                        return cookies.get(name) or query.get(name, [''])[0]
                    if not value('SESSDATA'):
                        session.update(state='failed', message='登录成功但未取得凭据，请重新扫码', qr_image='')
                    else:
                        session.update(state='ready', message='登录成功', qr_image='', cookies={
                            'sessdata': value('SESSDATA'), 'bili_jct': value('bili_jct'),
                            'buvid3': value('buvid3'), 'buvid4': value('buvid4'),
                            'b_nut': value('b_nut'), 'b_lsid': value('b_lsid')})
                else:
                    session.update(state='failed', message='登录状态异常，请重新获取二维码', qr_image='')
            except (httpx.HTTPError, ValueError, TypeError):
                session['message'] = '状态读取失败，正在重试…'
            return self._describe()

    async def cancel(self):
        async with self.lock:
            self.session = None
            return {'ok': True, 'state': 'idle', 'message': '已取消扫码登录'}
