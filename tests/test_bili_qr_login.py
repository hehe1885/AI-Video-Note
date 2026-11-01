import asyncio
from backend import bili_qr_login


def test_qr_login_states_and_cookie_import(tmp_path, monkeypatch):
    manager = bili_qr_login.BiliQrLoginManager(tmp_path)
    responses = [
        ({'code':0,'data':{'url':'https://passport.bilibili.com/h5-app/passport/login/scan?test=fixture','qrcode_key':'test-key'}},{}),
        ({'code':0,'data':{'code':86101}},{}),
        ({'code':0,'data':{'code':86090}},{}),
        ({'code':0,'data':{'code':0,'url':'https://www.bilibili.com/'}},{'SESSDATA':'test-session','bili_jct':'test-csrf'}),
    ]
    async def request(*args): return responses.pop(0)
    monkeypatch.setattr(manager,'_request',request)
    async def run():
        start = await manager.start()
        assert start['qr_image'].startswith('data:image/png;base64,') and 'key' not in start
        assert (await manager.status())['state']=='waiting'
        assert (await manager.status())['state']=='scanned'
        ready = await manager.status()
        assert ready['state']=='ready' and ready['cookies']['sessdata']=='test-session' and not ready['qr_image']
        await manager.cancel()
        assert (await manager.status())['state']=='idle'
    asyncio.run(run())
    assert list(tmp_path.iterdir())==[]


def test_expired_code_can_be_refreshed(tmp_path, monkeypatch):
    manager=bili_qr_login.BiliQrLoginManager(tmp_path)
    async def request(*args):
        return {'code':0,'data':{'url':'https://passport.bilibili.com/scan','qrcode_key':'test'}},{}
    monkeypatch.setattr(manager,'_request',request)
    async def run():
        await manager.start()
        manager.session['started_at']-=181
        assert (await manager.status())['state']=='timeout'
        assert (await manager.start())['state']=='waiting'
    asyncio.run(run())


def test_invalid_qr_url_is_rejected(tmp_path, monkeypatch):
    manager=bili_qr_login.BiliQrLoginManager(tmp_path)
    async def request(*args): return {'code':0,'data':{'url':'https://untrusted.example/','qrcode_key':'test'}},{}
    monkeypatch.setattr(manager,'_request',request)
    assert not asyncio.run(manager.start())['ok']
