"""Shared stable KSB-code identity and de-duplication helpers."""
import re


KSB_PARENT_CODE_RE = re.compile(r'^([KSB])(\d+)(?:\.\d+)?$')


def normalize_ksb_parent_code(value) -> str:
    code = '' if value is None else str(value).strip().upper()
    if not code:
        return ''
    match = KSB_PARENT_CODE_RE.match(code)
    if match:
        return f'{match.group(1)}{match.group(2)}'
    return code


def extract_ksb_codes(values) -> set[str]:
    codes = set()
    for value in values if isinstance(values, list) else []:
        if isinstance(value, str):
            code = normalize_ksb_parent_code(value)
        elif isinstance(value, dict):
            code = normalize_ksb_parent_code(
                value.get('code') or value.get('Code') or value.get('id')
            )
        else:
            code = ''
        if code:
            codes.add(code)
    return codes

