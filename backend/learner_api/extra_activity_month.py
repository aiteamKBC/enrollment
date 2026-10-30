"""Validate the reporting month without coupling it to the submission date."""
from datetime import date


def valid_activity_month(value):
    if not isinstance(value, str) or len(value) != 7:
        return False
    try:
        date.fromisoformat(value + '-01')
    except ValueError:
        return False
    return True
