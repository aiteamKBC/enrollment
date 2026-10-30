"""Bounded decoding for authored JSON objects and the raw JSONB text loader."""
import json


def presentation_object(value, *, max_layers=1):
    """Decode containers only; never coerce an authored scalar's value/type."""
    for _ in range(max_layers):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}
