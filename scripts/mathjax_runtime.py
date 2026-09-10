"""Resolve a reproducible local MathJax browser distribution."""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path


MATHJAX_VERSION = "4.1.3"
MATHJAX_URL = f"https://registry.npmjs.org/mathjax/-/mathjax-{MATHJAX_VERSION}.tgz"
MATHJAX_SHA512 = "BN/8Pkgn7G1pIDYJqd9md+JHsE/jydSYbyOZnSdSA0WziuVO8mRxdYiWFumkVVly/8U+hm9DpIIoWuvySverzw=="


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.getmembers():
        if not member.isfile() and not member.isdir():
            raise RuntimeError(f"MathJax archive contains a link or special entry: {member.name}")
        target = (destination / member.name).resolve()
        if destination not in target.parents and target != destination:
            raise RuntimeError("MathJax archive contains an unsafe path")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError(f"MathJax archive entry cannot be read: {member.name}")
        with source, target.open("wb") as stream:
            shutil.copyfileobj(source, stream)


def ensure_mathjax(project_root: Path, work_dir: str = "work", version: str = MATHJAX_VERSION) -> Path:
    """Download MathJax once into a project cache and return tex-svg.js."""
    if version != MATHJAX_VERSION:
        raise RuntimeError(f"unsupported MathJax version: {version}; expected {MATHJAX_VERSION}")
    cache = (project_root / work_dir / "_runtime" / f"mathjax-{version}").resolve()
    script = cache / "tex-svg.js"
    metadata = cache / "runtime.json"
    if script.is_file() and metadata.is_file():
        try:
            info = json.loads(metadata.read_text(encoding="utf-8"))
            if info.get("version") == version and info.get("sha512") == MATHJAX_SHA512:
                return script
        except (OSError, ValueError):
            pass

    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qbank-mathjax-") as temp_dir:
        temp = Path(temp_dir)
        archive_path = temp / "mathjax.tgz"
        try:
            with urllib.request.urlopen(MATHJAX_URL, timeout=90) as response, archive_path.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        except Exception as exc:
            raise RuntimeError(
                f"unable to download MathJax {version} from {MATHJAX_URL}; "
                "provide mathjax_local_script for offline execution"
            ) from exc
        digest = base64.b64encode(hashlib.sha512(archive_path.read_bytes()).digest()).decode("ascii")
        if digest != MATHJAX_SHA512:
            raise RuntimeError("MathJax download integrity check failed")
        staging = temp / "package"
        staging.mkdir()
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_extract(archive, staging)
        extracted = staging / "package"
        if not (extracted / "tex-svg.js").is_file():
            raise RuntimeError("MathJax archive is missing tex-svg.js")
        if cache.exists():
            shutil.rmtree(cache)
        shutil.move(str(extracted), str(cache))
    metadata.write_text(json.dumps({
        "version": version,
        "url": MATHJAX_URL,
        "sha512": MATHJAX_SHA512,
        "entry": "tex-svg.js",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return script
