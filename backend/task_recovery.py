"""One restart-status policy for task hydration and compact history queries."""
from functools import lru_cache
import json
import math

INTERRUPTED_STATUSES = {'pending', 'queued', 'processing', 'cancelling'}


def safe_number(value, default=0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (ValueError, TypeError, OverflowError):
        return default


def valid_transcript(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('segments'), list) or not payload['segments']:
        return False
    for item in payload['segments']:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str) or not item['text'].strip():
            return False
        start, end = item.get('start'), item.get('end')
        if (type(start) not in {int, float} or type(end) not in {int, float}
                or safe_number(start, None) is None or safe_number(end, None) is None or start < 0 or end < start):
            return False
    return True


@lru_cache(maxsize=4096)
def _readable_artifact(path, transcript, size, modified, changed):
    """Validate once per file revision; history never retains full note bodies."""
    try:
        with path.open('r', encoding='utf-8') as stream:
            if transcript:
                return valid_transcript(json.load(stream))
            nonempty = False
            while chunk := stream.read(65536):
                nonempty = nonempty or bool(chunk.strip())
            return nonempty
    except (OSError, ValueError, TypeError, RecursionError):
        return False


def valid_artifact(path, transcript=False):
    try:
        stat = path.stat()
        return (path.is_file() and stat.st_size > 0
                and _readable_artifact(path, transcript, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    except OSError:
        return False


def recovered_status(directory, manifest):
    saved = manifest.get('runtime') if isinstance(manifest.get('runtime'), dict) else {}
    status = str(saved.get('status') or '')
    transcript = manifest.get('output') == 'transcript'
    # A body is saved before export. It cannot override a recorded failure,
    # cancellation, unfinished pipeline, or a pending Agent submission.
    if status in {'failed', 'cancelled', 'uploaded'}:
        return status
    if status in INTERRUPTED_STATUSES:
        return 'failed'
    if status == 'awaiting_agent':
        # Note jobs can be reclaimed; transcript jobs lost their waiting coroutine.
        return status if not transcript and (directory / 'agent_job.json').is_file() else 'failed'
    if status and status != 'completed':
        return 'failed'
    if saved.get('error'):
        # Also repair contradictory completed+error records written by old recovery.
        return 'failed'
    artifact = directory / ('transcript.json' if transcript else 'notes.md')
    if valid_artifact(artifact, transcript):
        if status == 'completed' or not (directory / 'transcript_review.json').exists():
            return 'completed'  # Compatibility for legacy manifests with no status.
    if status == 'completed':
        return 'failed'
    return 'uploaded' if any(path.is_file() for path in directory.glob('input.*')) else 'failed'
