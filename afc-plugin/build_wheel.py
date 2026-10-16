"""Build dist/orca_afc.whl, the file to install from OrcaSlicer's Plugins dialog.

Standard library only:  python3 afc-plugin/build_wheel.py

The file keeps the same name from release to release because OrcaSlicer names
a plugin's folder and key after the file: installing a newer orca_afc.whl
replaces the old one instead of adding a second AFC plugin.
"""

import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE / "orca_afc"
NAME = "AFC"
SUMMARY = "Armored Turtle AFC lanes, spools and dryers in OrcaSlicer"
AUTHOR = "J0eB0l"
# Browser preview only; the plugin hands the page its answers itself.
LEFT_OUT = {"afc-mock.js"}
# Fixed time stamps, so the same sources give the same file.
DATE = (2026, 1, 1, 0, 0, 0)


def version() -> str:
    text = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)


def package_files():
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc" and path.name not in LEFT_OUT:
            yield path.relative_to(HERE).as_posix(), path.read_bytes()


def record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return "%s,sha256=%s,%d" % (name, digest, len(data))


def build(out_dir: Path) -> Path:
    ver = version()
    dist_info = "orca_afc-%s.dist-info" % ver
    meta = {
        dist_info + "/METADATA": ("Metadata-Version: 2.1\nName: %s\nVersion: %s\nSummary: %s\nAuthor: %s\n"
                                  "Requires-Python: >=3.10\nImport-Name: orca_afc\n" % (NAME, ver, SUMMARY, AUTHOR)),
        dist_info + "/WHEEL": "Wheel-Version: 1.0\nGenerator: afc-plugin build_wheel.py\nRoot-Is-Purelib: true\n"
                              "Tag: py3-none-any\n",
        dist_info + "/top_level.txt": "orca_afc\n",
    }
    entries = list(package_files()) + [(name, text.encode("utf-8")) for name, text in meta.items()]
    record = "".join(record_line(name, data) + "\n" for name, data in entries) + dist_info + "/RECORD,,\n"
    entries.append((dist_info + "/RECORD", record.encode("utf-8")))

    out_dir.mkdir(parents=True, exist_ok=True)
    wheel = out_dir / "orca_afc.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo(name, DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return wheel


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dist")
    print("%s (version %s, %d bytes)" % (out, version(), out.stat().st_size))
