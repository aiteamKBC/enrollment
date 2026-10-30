"""Fetch the official Windows PostgreSQL tools into a private local tool folder."""
import hashlib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "private"
ROOT.mkdir(exist_ok=True)
archive = ROOT / "postgresql-tools.zip"
url = "https://get.enterprisedb.com/postgresql/postgresql-18.6-5-windows-x64-binaries.zip"
expected = "e2246ba91d22345bc3d017586c09ede52d9df180b1eeb480f050445f1cad84e2"
expected_size = 384620317
for attempt in range(5):
    offset = archive.stat().st_size if archive.exists() else 0
    if offset == expected_size:
        break
    request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            mode = 'ab' if response.status == 206 else 'wb'
            with archive.open(mode) as out:
                while block := response.read(1024 * 1024):
                    out.write(block)
    except TimeoutError:
        print('Resuming interrupted PostgreSQL tools download.', flush=True)
digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
if digest != expected:
    raise SystemExit("Official archive checksum did not match; no binaries extracted.")
with zipfile.ZipFile(archive) as z:
    for info in z.infolist():
        if info.filename.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/")):
            target = (ROOT / info.filename).resolve()
            if not target.is_relative_to(ROOT.resolve()):
                raise SystemExit("Invalid archive path")
            z.extract(info, ROOT)
print("Verified PostgreSQL tools downloaded to enrollment/private/pgsql.")
