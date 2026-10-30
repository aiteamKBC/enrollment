"""Strict active-segment validation; no activity, hours or points inferred."""
import hashlib
import json
import re

MAX_SEGMENTS = 120
MAX_SEGMENT_MS = 5000


class RewardError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}", value):
        raise RewardError("invalid_reference", 400)
    return value


def integer(value, lower=0, upper=2**31-1):
    if type(value) is not int or not lower <= value <= upper:
        raise RewardError("invalid_integer", 400)
    return value


def intervals(value, elapsed_ms, *, max_offset_ms=86400000):
    if not isinstance(value, list) or len(value) > MAX_SEGMENTS:
        raise RewardError("invalid_intervals", 400)
    checked, last_end = [], 0
    for item in value:
        if not isinstance(item, dict) or set(item) != {"startOffsetMs", "endOffsetMs"}:
            raise RewardError("invalid_interval", 400)
        start = integer(item["startOffsetMs"], 0, max_offset_ms)
        end = integer(item["endOffsetMs"], 0, max_offset_ms)
        if not start < end or end-start > MAX_SEGMENT_MS or start < last_end:
            raise RewardError("invalid_interval", 400)
        if end > elapsed_ms:
            raise RewardError("observation_ahead_of_server")
        checked.append([start, end])
        last_end = end
    return checked


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def merged_milliseconds(values):
    total, current = 0, None
    for start, end in sorted(values):
        if current is None:
            current = [start, end]
        elif start <= current[1]:
            current[1] = max(current[1], end)
        else:
            total += current[1]-current[0]
            current = [start, end]
    if current:
        total += current[1]-current[0]
    return total
