"""Pure canonical point arithmetic shared by Student and Coach reads."""


def point_ratio(done, total):
    return {'completed': done, 'total': total,
            'percent': round(done / total * 100, 2) if total else None,
            'status': 'ready' if total else 'empty'}
