"""Bounded retries for low-confidence decoding loops, without model-specific tuning."""
from __future__ import annotations

import math
from pathlib import Path
import re
import tempfile
import unicodedata

DEFAULT_DECODE_OPTIONS = {
    'repetition_penalty': 1.0,
    'no_repeat_ngram_size': 0,
    'condition_on_previous_text': True,
}
RETRY_DECODE_OPTIONS = {
    'repetition_penalty': 1.0,
    'no_repeat_ngram_size': 10,
    'condition_on_previous_text': False,
}
MAX_RETRY_REGIONS = 3
MAX_RETRY_AUDIO_SECONDS = 90.0
MAX_REGION_SECONDS = 28.0  # With one second of padding on each side, at most 30 seconds.


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def has_long_loop(text):
    text = normalize(text)
    if len(text) < 40:
        return False
    # Work on bounded strings; a malformed result cannot create unbounded regex work.
    for offset in range(0, len(text), 1000):
        chunk = text[max(0, offset - 480):offset + 1000]
        for match in re.finditer(r'(.{4,80}?)\1{5,}', chunk):
            if len(match.group()) >= 40 and len(match.group()) >= len(chunk) * .6:
                return True
    return False


def record(segment):
    def finite(name):
        value = getattr(segment, name, None)
        return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None
    return {'start': float(segment.start), 'end': float(segment.end), 'text': segment.text.strip(),
            'avg_logprob': finite('avg_logprob'), 'compression_ratio': finite('compression_ratio')}


def mean_probability(records):
    if not records or any(r['avg_logprob'] is None for r in records):
        return None
    weights = [max(.01, r['end'] - r['start']) for r in records]
    return sum(r['avg_logprob'] * w for r, w in zip(records, weights)) / sum(weights)


def suspicious(records):
    probability = mean_probability(records)
    return (probability is not None and (probability <= -.6 or implausibly_dense_loop(records))
            and any((r['compression_ratio'] or 0) >= 2.4 for r in records)
            and has_long_loop(''.join(r['text'] for r in records)))


def implausibly_dense_loop(records):
    if not records:
        return False
    duration = records[-1]['end'] - records[0]['start']
    text = ''.join(r['text'] for r in records)
    return (duration > 0 and len(normalize(text)) / duration >= 80
            and any((r['compression_ratio'] or 0) >= 2.4 for r in records)
            and has_long_loop(text))


def find_regions(records):
    """Single-segment loops and runs of six identical adjacent subtitle lines."""
    regions = []
    index = 0
    while index < len(records):
        end = index + 1
        key = normalize(records[index]['text'])
        if len(key) >= 4:
            while (end < len(records) and normalize(records[end]['text']) == key
                   and 0 <= records[end]['start'] - records[end - 1]['end'] <= 1
                   and records[end]['end'] - records[index]['start'] <= MAX_REGION_SECONDS):
                end += 1
        if end - index < 6:
            end = index + 1
        group = records[index:end]
        duration = group[-1]['end'] - group[0]['start']
        if 0 < duration <= MAX_REGION_SECONDS and suspicious(group):
            regions.append((index, end))
        index = end
    return regions


def candidate_decision(original, candidate):
    if not candidate or not normalize(''.join(r['text'] for r in candidate)):
        return 'empty_candidate'
    if has_long_loop(''.join(r['text'] for r in candidate)):
        return 'repetition_remains'
    old_probability, new_probability = mean_probability(original), mean_probability(candidate)
    if (old_probability is None or new_probability is None or new_probability < -.6
            or (not implausibly_dense_loop(original) and new_probability < old_probability + .15)):
        return 'insufficient_confidence_gain'
    # Never infer corrected numerical values from language-model likelihood.
    numbers = lambda rows: re.findall(r'\d+(?:[.,]\d+)*|[零〇一二三四五六七八九十百千万亿两]+',
                                     unicodedata.normalize('NFKC', ''.join(r['text'] for r in rows)))
    if numbers(original) != numbers(candidate):
        return 'numbers_changed'
    if (candidate[0]['start'] > original[0]['start'] + 1.5
            or candidate[-1]['end'] < original[-1]['end'] - 1.5):
        return 'coverage_reduced'
    return 'accepted'


def retry_regions(records, duration, media_path: Path, decode, check_cancel, progress=None, *, expected_language=None):
    """decode(path) retries once with the same model/device. Exceptions keep the original.

    Original and candidate records survive in receipts regardless of the choice.
    Probability is a heuristic here, not a measure of transcription correctness.
    """
    from .transcript import TranscriptSegment
    regions = find_regions(records)
    receipts, changes = [], {}
    used_seconds = 0.0
    for left, right in regions:
        check_cancel()
        original = records[left:right]
        start, end = original[0]['start'], original[-1]['end']
        clip_start, clip_end = max(0., start - 1.), min(duration, end + 1.)
        receipt = {'start': start, 'end': end, 'original': original,
                   'parameters': dict(RETRY_DECODE_OPTIONS), 'candidate': [], 'decision': 'budget_exhausted'}
        receipts.append(receipt)
        if len(receipts) > MAX_RETRY_REGIONS or used_seconds + clip_end - clip_start > MAX_RETRY_AUDIO_SECONDS:
            continue
        if clip_end <= clip_start:
            receipt['decision'] = 'invalid_interval'
            continue
        used_seconds += clip_end - clip_start
        try:
            # Seek/decode only this interval, rather than loading the full file again.
            from .transcript_audio import write_clip
            with tempfile.TemporaryDirectory(prefix='ai-video-note-asr-retry-') as folder:
                clip = Path(folder) / 'excerpt.wav'
                def should_abort():
                    check_cancel()
                    return False
                write_clip([(media_path, clip_start, clip_end)], clip, should_abort)
                check_cancel()
                raw, _info = decode(clip)
                candidate = []
                for item in raw:
                    check_cancel()
                    if progress:
                        progress()
                    row = record(item)
                    row['start'] += clip_start
                    row['end'] += clip_start
                    if not math.isfinite(row['start']) or not math.isfinite(row['end']):
                        raise ValueError('Invalid candidate timestamp')
                    # Trim context at word boundaries, not just at subtitle boundaries.
                    # Clamping a whole segment's times would retain neighboring words.
                    words = getattr(item, 'words', None)
                    if words:
                        selected = [w for w in words if math.isfinite(w.start) and math.isfinite(w.end)
                                    and w.end > w.start and start <= (w.start + w.end) / 2 + clip_start < end]
                        if not selected:
                            continue
                        row['text'] = ''.join(w.word for w in selected).strip()
                        row['start'] = max(start, selected[0].start + clip_start)
                        row['end'] = min(end, selected[-1].end + clip_start)
                    elif row['start'] < start or row['end'] > end:
                        continue  # Without alignment there is no safe way to remove padding.
                    if row['text'] and row['end'] > row['start']:
                        candidate.append(row)
                if expected_language and getattr(_info, 'language', None) != expected_language:
                    receipt['decision'] = 'language_changed'
                elif any(r['start'] < candidate[i - 1]['start'] for i, r in enumerate(candidate) if i):
                    receipt['decision'] = 'invalid_timestamps'
                else:
                    receipt['decision'] = candidate_decision(original, candidate)
                receipt['candidate'] = candidate
                if receipt['decision'] == 'accepted':
                    changes[left] = (right, candidate)
        except Exception as error:
            # Cancellation is recognized by the caller's callback and must propagate.
            check_cancel()
            receipt['decision'] = 'retry_failed'
            receipt['error_type'] = type(error).__name__
    result, index = [], 0
    while index < len(records):
        if index in changes:
            index, selected = changes[index]
        else:
            selected = [records[index]]
            index += 1
        result.extend(TranscriptSegment(r['start'], r['end'], r['text']) for r in selected)
    check_cancel()
    return result, receipts
