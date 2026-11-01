"""Match the Bilibili part range before reusing a complete transcript."""
from urllib.parse import parse_qs, urlsplit


def _page_set(pages):
    values = frozenset(int(page) for page in pages)
    if not values or any(page < 1 for page in values):
        raise ValueError('Invalid Bilibili part range')
    return values


def _requested_pages(url, pages):
    if pages:
        return _page_set(pages)
    try:
        page = int(parse_qs(urlsplit(url).query).get('p', ['0'])[-1])
    except ValueError:
        page = 0
    return frozenset({page}) if page > 0 else None  # None means all parts.


def bilibili_pages_match(source_url, pages, previous_url, manifest):
    """Explicit selection overrides ?p=; actual processed parts override old inputs.

    Video identity is checked by the caller. Keep video-level URL normalization
    unchanged for history and media caches, which have separate reuse policies.
    """
    host = (urlsplit(source_url).hostname or '').lower()
    if host != 'bilibili.com' and not host.endswith('.bilibili.com') and host != 'b23.tv':
        return True
    try:
        requested = _requested_pages(source_url, pages)
        recorded = manifest.get('bili_pages')
        if recorded:
            previous = _page_set(item['page'] for item in recorded)
            if requested is None:
                total = int(manifest.get('bili_total_pages') or 0)
                # An old explicit all-selection is reusable only when its actual
                # coverage proves that no part is missing.
                return total == len(previous) and previous == frozenset(range(1, total + 1))
        else:
            previous = _requested_pages(previous_url, manifest.get('bilibili_pages'))
        return requested == previous
    except (ValueError, TypeError, KeyError):
        return False  # Unknown/corrupt ranges must never silently reuse text.
