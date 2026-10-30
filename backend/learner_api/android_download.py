"""Private, resumable Android installer delivery; authorization stays in mobile.py."""
import hashlib
import os
import re
from django.http import HttpResponse, StreamingHttpResponse
from config.video_streaming import video_chunks

_DIGESTS = {}
CHUNK_SIZE = 256 * 1024

def _digest(source):
    try:
        stat = os.fstat(source.fileno())
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    except (AttributeError, OSError):
        key = None
    if key is not None and key in _DIGESTS:
        return _DIGESTS[key]
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    source.seek(0)
    value = digest.hexdigest()
    if key is not None:
        if len(_DIGESTS) >= 8:
            _DIGESTS.clear()
        _DIGESTS[key] = value
    return value

class InstallerResponse(StreamingHttpResponse):
    def __init__(self, source, *args, **kwargs):
        self.source = source
        super().__init__(*args, **kwargs)

    def close(self):
        try:
            super().close()
        finally:
            self.source.close()

def installer_response(request, source):
    try:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(0)
        digest = _digest(source)
        etag = '"' + digest + '"'
        start, end, status = 0, size - 1, 200
        value = request.headers.get("Range", "")
        if value and request.headers.get("If-Range", etag) == etag:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
            try:
                if not match or not any(match.groups()):
                    raise ValueError()
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                else:
                    suffix = int(right)
                    if suffix <= 0:
                        raise ValueError()
                    start = max(0, size - suffix)
                if start >= size or end < start:
                    raise ValueError()
            except ValueError:
                source.close()
                response = HttpResponse(status=416)
                response["Content-Range"] = f"bytes */{size}"
                response["Accept-Ranges"] = "bytes"
                response["ETag"] = etag
                return response
            status = 206
        source.seek(start)
        def chunks():
            remaining = end - start + 1
            while remaining > 0:
                chunk = source.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise OSError("Installer changed during transfer")
                remaining -= len(chunk)
                yield chunk
        response = InstallerResponse(source, video_chunks(request, chunks()), status=status,
            content_type="application/vnd.android.package-archive")
        response["Content-Length"] = str(end - start + 1)
        response["Content-Disposition"] = 'attachment; filename="KBC-Learn-Android-test.apk"'
        response["Accept-Ranges"] = "bytes"
        response["ETag"] = etag
        response["X-APK-SHA256"] = digest
        response["Cache-Control"] = "private, no-store, no-transform"
        response["X-Content-Type-Options"] = "nosniff"
        if status == 206:
            response["Content-Range"] = f"bytes {start}-{end}/{size}"
        return response
    except BaseException:
        source.close()
        raise

