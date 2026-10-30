"""Canonical Dashboard work week, in Django's configured business timezone."""
from datetime import timedelta
from django.utils import timezone


def work_week(today=None, *, offset=0):
    today = today if today is not None else timezone.localdate()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    return monday, monday + timedelta(days=4)
