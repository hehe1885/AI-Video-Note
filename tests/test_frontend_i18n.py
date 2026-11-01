"""Every static Chinese UI message needs an English catalog entry."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_static_ui_has_english_translations():
    source = (ROOT / 'frontend/locales/en.js').read_text(encoding='utf-8')
    catalog = json.loads(source[source.index('{'):source.rindex('}') + 1])
    class Messages(HTMLParser):
        def __init__(self):
            super().__init__()
            self.messages = []
        def handle_data(self, text):
            self.messages.append(text.strip())
        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name in {'placeholder', 'title', 'aria-label', 'label'}:
                    self.messages.append(value or '')
    parser = Messages()
    page = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
    parser.feed(page)
    missing = sorted({text for text in parser.messages if re.search(r'[\u4e00-\u9fff]', text) and text not in catalog})
    assert not missing, 'Add English translations in frontend/locales/en.js: ' + repr(missing)
    assert page.index('id="languageToggle"') < page.index('id="serviceState"')
