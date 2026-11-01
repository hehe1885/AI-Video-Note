"""Bilibili's public WBI request protocol, independent of downloader clients.

Protocol reference: https://github.com/ScottSloan/Bili23-Downloader
See src/util/parse/parser/base.py for the same platform signing requirements.
"""
import hashlib
import json
from pathlib import PurePosixPath
import re
import threading
import time
import urllib.request
from urllib.parse import urlencode, urlsplit

NAV_URL = 'https://api.bilibili.com/x/web-interface/nav'
# The first 32 positions of Bilibili's published mixin permutation.
MIXIN_POSITIONS = (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
                   27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13)


class WbiKeys:
    """Cache only the public mixin key; never retain caller cookies or headers."""
    def __init__(self):
        self.lock = threading.Lock()
        self.key = ''
        self.expires = 0.0
        self.retry_after = 0.0

    def get(self, headers):
        with self.lock:
            now = time.monotonic()
            if self.key and now < self.expires:
                return self.key
            if now < self.retry_after:
                raise RuntimeError('B 站签名参数暂不可用，请稍后重试')
            self.retry_after = now + 15
            try:
                request = urllib.request.Request(NAV_URL, headers=dict(headers or {}))
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read().decode('utf-8'))
                images = payload['data']['wbi_img']
                keys = [PurePosixPath(urlsplit(images[name]).path).stem for name in ('img_url', 'sub_url')]
                if not all(re.fullmatch(r'[0-9a-fA-F]{32}', key) for key in keys):
                    raise ValueError('Invalid WBI keys')
                original = ''.join(keys)
                self.key = ''.join(original[position] for position in MIXIN_POSITIONS)
                self.expires = time.monotonic() + 300
                self.retry_after = 0.0
                return self.key
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                raise RuntimeError('无法取得 B 站签名参数，请检查网络后重试') from error


def sign_parameters(params, mixin_key, timestamp=None):
    values = {key: value for key, value in params.items() if key not in {'wts', 'w_rid'}}
    values['wts'] = round(time.time()) if timestamp is None else timestamp
    cleaned = {key: ''.join(char for char in str(values[key]) if char not in "!'()*")
               for key in sorted(values)}
    cleaned['w_rid'] = hashlib.md5((urlencode(cleaned) + mixin_key).encode('utf-8')).hexdigest()
    return cleaned


keys = WbiKeys()
