#!/usr/bin/env python3
"""Create a portable source bundle beside the app directory."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def main():
    root = Path(__file__).resolve().parent
    target = root.parent/(root.name+".zip")
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        files = sorted(p for p in root.rglob("*") if p.is_file() and
                       not any(s in ("__pycache__", ".venv", ".pytest_cache") for s in p.relative_to(root).parts) and
                       p.suffix not in (".pyc", ".pyo"))
        for path in files:
            archive.write(path, path.relative_to(root.parent))
    with ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Archive integrity check failed")
    print(f"{target}\n{len(files)} files · {target.stat().st_size:,} bytes · archive integrity OK")


if __name__ == "__main__":
    main()
