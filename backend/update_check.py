"""Release checks shared by the web UI and tray."""
from __future__ import annotations
import json
import urllib.request
from typing import Any
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

REPOSITORY = "hehe1885/AI-Video-Note"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
DEFAULT_RELEASE_URL = f"https://github.com/{REPOSITORY}/releases/latest"
GITEE_LATEST_RELEASE_API = f"https://gitee.com/api/v5/repos/{REPOSITORY}/releases/latest"
GITEE_RELEASE_URL = f"https://gitee.com/{REPOSITORY}/releases/latest"

def source_for_language(language: str) -> str:
    return "gitee" if language == "zh-CN" else "github"

def source_name(source: str) -> str:
    return "Gitee" if source == "gitee" else "GitHub"

def release_source(source: str) -> tuple[str, str]:
    if source == "gitee":
        return GITEE_LATEST_RELEASE_API, GITEE_RELEASE_URL
    if source == "github":
        return GITHUB_LATEST_RELEASE_API, DEFAULT_RELEASE_URL
    raise ValueError("Unsupported update source")

def version_tuple(value: str) -> tuple[int, ...]:
    """Compare v1.2.3 or 1.2.3-rc1 as numeric version tuples."""
    text = str(value or "").strip().lstrip("vV")
    parts: list[int] = []
    for part in text.split("."):
        digits = ""
        for character in part:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts or [0])

def fetch_latest_release(current_version: str, timeout: float = 5.0, source: str = "github") -> dict[str, Any]:
    api_url, page_url = release_source(source)
    request = urllib.request.Request(api_url, headers={
        "Accept": "application/json" if source == "gitee" else "application/vnd.github+json",
        "User-Agent": f"AI-Video-Note/{current_version}",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{source_name(source)} returned invalid release data")
    tag = str(payload.get("tag_name") or "").strip()
    if not tag or version_tuple(tag) == (0,):
        raise RuntimeError(f"{source_name(source)} did not return a valid version")
    if payload.get("draft") or payload.get("prerelease"):
        raise RuntimeError(f"{source_name(source)} has no stable release available")
    candidate = str(payload.get("html_url") or "")
    parsed = urlparse(candidate)
    # A Gitee update must not redirect to GitHub or an arbitrary returned website.
    if parsed.scheme != "https" or parsed.hostname != ("gitee.com" if source == "gitee" else "github.com") or parsed.username or parsed.password:
        candidate = page_url
    return {
        "tag_name": tag,
        "html_url": candidate,
        "update_available": version_tuple(tag) > version_tuple(current_version),
        "notes": str(payload.get("body") or "")[:600],
        "source": source,
        "source_name": source_name(source),
    }

def check_summary(current_version: str, timeout: float = 5.0, source: str = "github") -> dict[str, Any]:
    try:
        # Preserve the legacy two-argument call for integrations using GitHub.
        info = fetch_latest_release(current_version, timeout) if source == "github" else fetch_latest_release(current_version, timeout, source)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}".encode("utf-8", "replace").decode("utf-8", "replace")
        return {"ok": False, "error": reason[:200], "current_version": current_version,
                "source": source, "source_name": source_name(source)}
    return {
        "ok": True, "current_version": current_version,
        "latest_version": info["tag_name"], "update_available": info["update_available"],
        "release_url": info["html_url"], "notes": info["notes"],
        "source": source, "source_name": source_name(source),
    }


def check_all_sources(current_version: str, timeout: float = 5.0, language: str = "zh-CN") -> dict[str, Any]:
    """Check both hosts concurrently; choose newest, using language only for ties."""
    preferred = source_for_language(language)
    sources = [preferred, "github" if preferred == "gitee" else "gitee"]
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="update-source") as pool:
        futures = [pool.submit(check_summary, current_version, timeout, source) for source in sources]
        results = [future.result() for future in futures]
    available = [result for result in results if result["ok"]]
    if not available:
        return {"ok": False, "current_version": current_version,
                "error": "No usable release from GitHub or Gitee", "checked_sources": sources}
    selected = max(available, key=lambda result: version_tuple(result["latest_version"]))
    return {**selected, "checked_sources": sources}
