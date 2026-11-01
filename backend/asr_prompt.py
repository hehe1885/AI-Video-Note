"""Keep video metadata from imposing a mismatched writing language on speech."""
from __future__ import annotations

import math
import unicodedata


def title_prompt_mismatches_speech(prompt, info):
    if not isinstance(prompt, str) or not prompt.strip():
        return False
    language = getattr(info, 'language', None)
    probability = getattr(info, 'language_probability', None)
    # Han characters also occur in Japanese; uncertainty must not discard a hint.
    if language in {None, '', 'unknown', 'zh', 'yue', 'ja', 'ko'}:
        return False
    if not isinstance(probability, (int, float)) or not math.isfinite(probability) or probability < .8:
        return False
    text = unicodedata.normalize('NFKC', prompt)
    han = sum('\u4e00' <= character <= '\u9fff' for character in text)
    letters = sum(character.isalpha() for character in text)
    return han >= 3 and han >= letters * .35
