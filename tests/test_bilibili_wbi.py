"""Public WBI protocol and native-download fallback tests; no live requests."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from backend import bilibili_wbi as wbi
from backend.video_processor import VideoProcessor

IMG = '7cd084941338484aae1ad9425b84077c'
SUB = '4932caff0ff746eab6f01bf08b70ac45'
NAV = {'data': {'wbi_img': {'img_url': f'https://i0.hdslb.com/bfs/wbi/{IMG}.png',
                            'sub_url': f'https://i0.hdslb.com/bfs/wbi/{SUB}.png'}}}


class Response(BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


@pytest.fixture
def keys(monkeypatch):
    fresh = wbi.WbiKeys()
    monkeypatch.setattr(wbi, 'keys', fresh)
    return fresh


def test_dynamic_keys_are_shared_by_concurrent_requests_and_refreshed(keys, monkeypatch):
    calls = []
    def nav(request, timeout=None):
        calls.append(request.full_url)
        return Response(json.dumps(NAV).encode())
    monkeypatch.setattr(wbi.urllib.request, 'urlopen', nav)
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: keys.get({'User-Agent': 'test'}), range(6)))
    assert len(calls) == 1 and len(set(results)) == 1
    assert results[0] == 'ea1db124af3c7062474693fa704f4ff8'
    keys.expires = 0
    keys.get({})
    assert len(calls) == 2
    assert set(vars(keys)) == {'lock', 'key', 'expires', 'retry_after'}


def test_signing_filters_values_and_does_not_mutate_input():
    params = {'foo': "a!'()*b", 'w_rid': 'old', 'wts': 1}
    signed = wbi.sign_parameters(params, 'public-mixin', timestamp=1700000000)
    assert signed == {'foo': 'ab', 'wts': '1700000000',
        'w_rid': hashlib.md5(b'foo=ab&wts=1700000000public-mixin').hexdigest()}
    assert params == {'foo': "a!'()*b", 'w_rid': 'old', 'wts': 1}


@pytest.mark.parametrize('payload', [{}, {'data': {'wbi_img': {}}},
    {'data': {'wbi_img': {'img_url': 'invalid', 'sub_url': 'invalid'}}}])
def test_missing_keys_fail_cleanly_with_bounded_fetches(keys, monkeypatch, payload):
    calls = []
    def nav(*args, **kwargs):
        calls.append(1)
        return Response(json.dumps(payload).encode())
    monkeypatch.setattr(wbi.urllib.request, 'urlopen', nav)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='签名参数'):
            keys.get({})
    assert len(calls) == 1 and keys.key == ''


def test_rejected_api_request_retries_with_dynamic_wbi_signature(keys, monkeypatch):
    calls = []
    def api(request, timeout=None):
        calls.append(request)
        if request.full_url == wbi.NAV_URL:
            return Response(json.dumps(NAV).encode())
        if 'w_rid=' in request.full_url:
            return Response(b'{"code":0,"data":{"dash":{}}}')
        return Response(b'{"code":-403,"message":"signature required"}')
    monkeypatch.setattr(wbi.urllib.request, 'urlopen', api)
    headers = {'Cookie': 'SESSDATA=fake', 'User-Agent': 'test'}
    result = VideoProcessor._bilibili_get_json(VideoProcessor.BILI_PLAYURL_API,
        {'bvid': 'BV1miht6gERa', 'cid': 2}, headers)
    assert result['code'] == 0 and len(calls) == 3
    assert urlsplit(calls[-1].full_url).path == '/x/player/wbi/playurl'
    assert parse_qs(urlsplit(calls[-1].full_url).query)['cid'] == ['2']
    assert calls[1].get_header('Cookie') == 'SESSDATA=fake'


def test_failed_key_fetch_preserves_the_original_api_error(keys, monkeypatch):
    calls = []
    def api(request, timeout=None):
        calls.append(request.full_url)
        return Response(b'{"code":-352,"message":"request rejected"}')
    monkeypatch.setattr(wbi.urllib.request, 'urlopen', api)
    assert VideoProcessor._bilibili_get_json(VideoProcessor.BILI_PLAYURL_API, {}, {})['code'] == -352
    assert len(calls) == 2


@pytest.mark.parametrize('base,backup', [('base_url', 'backup_url'), ('baseUrl', 'backupUrl')])
def test_native_download_tries_distinct_backup_addresses(tmp_path, monkeypatch, base, backup):
    processor = VideoProcessor(tmp_path)
    processor._bili_view_cache['BV1miht6gERa'] = {'bvid': 'BV1miht6gERa'}
    monkeypatch.setattr(processor, '_bilibili_play_pages', lambda *args:
        ('BV1miht6gERa', [{'page': 2, 'cid': 102}]))
    monkeypatch.setattr(processor, '_bilibili_get_json', lambda *args:
        {'code': 0, 'data': {'dash': {'audio': [{base: 'https://cdn.example/primary',
            backup: ['https://cdn.example/primary', 'https://backup.example/audio'], 'bandwidth': 64}]}}})
    calls = []
    def download(url, target, *args, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.URLError('primary unavailable')
        target.write_bytes(b'valid media placeholder')
        return target
    monkeypatch.setattr(processor, '_download_direct_media', download)
    target = tmp_path / 'audio.m4a'
    assert processor._download_bilibili_media_via_api(
        'https://www.bilibili.com/video/BV1miht6gERa?p=2', target, None, 'audio') == target
    assert calls == ['https://cdn.example/primary', 'https://backup.example/audio']


@pytest.mark.parametrize('data,headers', [
    (b'<html>blocked</html>', {'Content-Type': 'text/html'}),
    (b'{"error":true}', {'Content-Type': 'application/json'}),
    (b'partial', {'Content-Type': 'video/mp4', 'Content-Length': '100'}),
])
def test_bad_media_responses_are_not_published(tmp_path, monkeypatch, data, headers):
    processor = VideoProcessor(tmp_path)
    monkeypatch.setattr(wbi.urllib.request, 'urlopen', lambda *args, **kwargs: Response(data, headers))
    target = tmp_path / 'preview.mp4'
    with pytest.raises(RuntimeError):
        processor._download_direct_media('https://cdn.example/video', target,
            'https://www.bilibili.com/', None, user_agent=processor.BILI_USER_AGENT)
    assert not target.exists() and not target.with_name(target.name + '.part').exists()


@pytest.mark.parametrize('canonical_bvid', ['BV17x411w7KC', None])
def test_av_link_uses_the_platforms_playurl_identifier(tmp_path, monkeypatch, canonical_bvid):
    processor = VideoProcessor(tmp_path)
    calls = []
    def api(url, params, headers):
        calls.append((url, params))
        if url == processor.BILI_VIEW_API:
            return {'code': 0, 'data': {'title': 'Public sample', 'bvid': canonical_bvid,
                'pages': [{'page': 1, 'cid': 279786, 'duration': 20}]}}
        return {'code': 0, 'data': {'dash': {'audio': [{'baseUrl': 'https://cdn.example/audio'}]}}}
    def download(url, target, referer, *args, **kwargs):
        expected = canonical_bvid or 'av170001'
        assert referer == f'https://www.bilibili.com/video/{expected}/'
        target.write_bytes(b'media')
        return target
    monkeypatch.setattr(processor, '_bilibili_get_json', api)
    monkeypatch.setattr(processor, '_download_direct_media', download)
    target = tmp_path / 'audio.m4a'
    processor._download_bilibili_media_via_api('https://www.bilibili.com/video/av170001', target, None, 'audio')
    params = calls[-1][1]
    assert params['cid'] == 279786
    assert 'aid' not in params
    if canonical_bvid:
        assert params['bvid'] == canonical_bvid
    else:
        assert params['avid'] == 170001
