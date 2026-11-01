import json
import urllib.error
import pytest
from fastapi.testclient import TestClient
from backend import update_check, main, ui_language
import launcher

class Response:
    def __init__(self,payload):self.payload=payload
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self):return json.dumps(self.payload).encode()

@pytest.mark.parametrize("source,expected",[
    ("github",update_check.GITHUB_LATEST_RELEASE_API),
    ("gitee",update_check.GITEE_LATEST_RELEASE_API)])
def test_release_api_source_and_timeout(monkeypatch,source,expected):
    calls=[]
    def fetch(request,timeout):
        calls.append((request.full_url,timeout))
        return Response({"tag_name":"v9.9.9","body":"release notes"})
    monkeypatch.setattr(update_check.urllib.request,"urlopen",fetch)
    info=update_check.fetch_latest_release("1.0.0",source=source)
    assert calls==[(expected,5)]
    assert info["source"]==source and info["update_available"]
    assert info["html_url"]==update_check.release_source(source)[1]

@pytest.mark.parametrize("url",["https://github.com/hehe1885/AI-Video-Note/releases/latest","https://evil.example/download","http://gitee.com/release","https://user:password@gitee.com/release"])
def test_gitee_update_link_stays_on_gitee(monkeypatch,url):
    monkeypatch.setattr(update_check.urllib.request,"urlopen",lambda *a,**k:Response({"tag_name":"v2.0.0","html_url":url}))
    assert update_check.fetch_latest_release("1.0.0",source="gitee")["html_url"]==update_check.GITEE_RELEASE_URL

@pytest.mark.parametrize("payload",[
    {},[],{"tag_name":"invalid"},{"tag_name":"v2.0.0","draft":True},
    {"tag_name":"v2.0.0","prerelease":True}])
def test_invalid_or_unpublished_release_does_not_offer_update(monkeypatch,payload):
    monkeypatch.setattr(update_check.urllib.request,"urlopen",lambda *a,**k:Response(payload))
    assert update_check.check_summary("1.0.0",source="gitee")["ok"] is False

def test_gitee_unavailable_does_not_fall_back_to_github(monkeypatch):
    calls=[]
    def fetch(request,timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url,404,"not ready",None,None)
    monkeypatch.setattr(update_check.urllib.request,"urlopen",fetch)
    result=update_check.check_summary("1.0.0",source="gitee")
    assert not result["ok"] and result["source_name"]=="Gitee"
    assert calls==[update_check.GITEE_LATEST_RELEASE_API]

@pytest.mark.parametrize("saved,query,source",[("zh-CN","", "gitee"),("en","", "github"),("en","?language=zh-CN","gitee"),("zh-CN","?language=en","github")])
def test_web_update_source_follows_ui_language(monkeypatch,tmp_path,saved,query,source):
    monkeypatch.setattr(main,"WORKSPACE_DIR",tmp_path)
    ui_language.save_language(tmp_path,saved)
    calls=[]
    def summary(current,timeout=5,language="zh-CN"):
        source=update_check.source_for_language(language)
        calls.append(source)
        return {"ok":True,"source":source,"source_name":update_check.source_name(source)}
    monkeypatch.setattr(update_check,"check_all_sources",summary)
    result=TestClient(main.app).get("/api/update/check"+query)
    assert result.json()["source"]==source and calls==[source]
    assert TestClient(main.app).get("/api/update/check?language=invalid").status_code==422

@pytest.mark.parametrize("language,source",[("zh-CN","gitee"),("en","github")])
def test_tray_uses_same_source_and_message(monkeypatch,language,source):
    monkeypatch.setattr(launcher,"update_language",lambda:language)
    calls=[]
    def fetch(current,timeout=5,source="github"):
        calls.append(source)
        return {"tag_name":"v99.0.0","html_url":update_check.release_source(source)[1],
                "update_available": True, "notes": ""}
    monkeypatch.setattr(update_check,"fetch_latest_release",fetch)
    messages=[];opened=[]
    monkeypatch.setattr(launcher,"show_update_message",lambda message,**kw:messages.append(message) or 6)
    monkeypatch.setattr(launcher.webbrowser,"open",opened.append)
    launcher.check_for_updates()
    assert set(calls)=={"github","gitee"}
    assert update_check.source_name(source) in messages[0]
    assert opened==[update_check.DEFAULT_RELEASE_URL]

@pytest.mark.parametrize("language", ["zh-CN", "en"])
@pytest.mark.parametrize("state", ["new", "current", "unpublished", "failed"])
def test_tray_release_links_for_language_and_result(monkeypatch, language, state):
    monkeypatch.setattr(launcher, "update_language", lambda: language)
    def release():
        if state == "unpublished":
            raise urllib.error.HTTPError("https://example.test", 404, "unpublished", None, None)
        if state == "failed":
            raise RuntimeError("network failure")
        return {"tag_name": "v99.0.0" if state == "new" else launcher.VERSION,
                "html_url": update_check.DEFAULT_RELEASE_URL}
    monkeypatch.setattr(launcher, "latest_release_info", release)
    messages = []
    monkeypatch.setattr(launcher, "show_update_message", lambda message, **kw: messages.append(message) or 0)
    launcher.check_for_updates()
    assert update_check.DEFAULT_RELEASE_URL in messages[0]
    assert update_check.GITEE_RELEASE_URL not in messages[0]
