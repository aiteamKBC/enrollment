"""Saved WebVTT timing; pure parsing, with no service or framework imports."""
import re
from html import unescape


def cue_seconds(value):
    match = re.fullmatch(r'(-?)(?:(\d+):)?([0-5]\d):([0-5]\d)\.(\d{3})', value.strip())
    if not match:
        return None
    sign, hours, minutes, seconds, millis = match.groups()
    seconds = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000
    return -seconds if sign else seconds


def parse_vtt_cues(vtt):
    source = vtt.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    if not source.startswith('WEBVTT'):
        raise ValueError('Expected saved WebVTT content.')
    cues, seen = [], set()
    for block in re.split(r'\n\s*\n', source):
        lines = block.splitlines()
        if not lines or lines[0].startswith(('WEBVTT', 'NOTE', 'STYLE', 'REGION')):
            continue
        timing = next((index for index, line in enumerate(lines[:2]) if '-->' in line), None)
        if timing is None:
            continue
        match = re.match(r'^\s*(\S+)\s+-->\s+(\S+)', lines[timing])
        if not match:
            continue
        start, end = cue_seconds(match[1]), cue_seconds(match[2])
        if start is None or end is None or end <= start:
            continue
        raw = '\n'.join(lines[timing + 1:])
        voice = re.search(r'<v(?:\.[^\s>]+)?\s+([^>]+)>', raw)
        speaker = unescape(voice[1]).strip() if voice else ''
        text = unescape(re.sub(r'<[^>]*>', '', raw)).strip()
        key = (start, end, speaker, text)
        if text and key not in seen:
            seen.add(key)
            cues.append({'start': start, 'end': end, 'speaker': speaker, 'text': text})
    return sorted(cues, key=lambda cue: (cue['start'], cue['end']))
