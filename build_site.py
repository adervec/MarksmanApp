#!/usr/bin/env python3
"""Assemble the installable app into ``_site/``.

The hosted build is the same UI and the same Python as ``marksman web`` -- it
just runs the engine in the browser instead of behind an HTTP server. So this
script copies rather than generates: the page comes from the package, the
engine is the package zipped up, and only the PWA plumbing in ``site/`` is
written specially for the hosted build.

Run it with no arguments, then serve ``_site/`` over http:// to try it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "_site")
SKIP = {"__pycache__", ".pytest_cache"}


def _build_id() -> str:
    """Something that changes when the app does, so caches expire."""
    env = os.environ.get("GITHUB_SHA")
    if env:
        return env[:12]
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"],
                             cwd=ROOT, capture_output=True, text=True, check=True)
        return out.stdout.strip() or "dev"
    except (OSError, subprocess.CalledProcessError):
        return "dev"


def _zip_package(dest: str) -> int:
    """The engine, as Pyodide unpacks it onto sys.path."""
    files = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        pkg = os.path.join(ROOT, "marksman")
        for base, dirs, names in os.walk(pkg):
            dirs[:] = [d for d in dirs if d not in SKIP]
            for name in sorted(names):
                if name.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(base, name)
                z.write(path, os.path.relpath(path, ROOT))
                files += 1
    return files


def _icons() -> None:
    """Drawn by the app's own logo module -- no binary checked in for this."""
    from marksman import imageio, logo
    for name, size in (("icon.png", 512), ("icon-512.png", 512), ("icon-192.png", 192)):
        with open(os.path.join(OUT, name), "wb") as fh:
            fh.write(imageio.encode_png(logo.make_logo(size)))


def main() -> int:
    sys.path.insert(0, ROOT)
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    # The UI, straight from the package: one page, not a copy that drifts.
    shutil.copy2(os.path.join(ROOT, "marksman", "webapp", "index.html"),
                 os.path.join(OUT, "index.html"))

    # PWA plumbing, with the cache stamped so a deploy actually reaches people.
    build = _build_id()
    for name in sorted(os.listdir(os.path.join(ROOT, "site"))):
        src = os.path.join(ROOT, "site", name)
        if not os.path.isfile(src):
            continue
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace("__BUILD__", build))

    files = _zip_package(os.path.join(OUT, "marksman.zip"))
    _icons()
    shutil.copytree(os.path.join(ROOT, "guide"), os.path.join(OUT, "guide"))

    # A page that cannot reach its engine is a blank screen, so fail the build
    # here rather than in someone's browser.
    for need in ("index.html", "boot.js", "sw.js", "manifest.webmanifest",
                 "marksman.zip", "icon-192.png", "icon-512.png", "guide/index.html"):
        path = os.path.join(OUT, need)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            print("build failed: %s is missing or empty" % need)
            return 1
    with open(os.path.join(OUT, "index.html"), encoding="utf-8") as fh:
        if 'src="boot.js"' not in fh.read():
            print("build failed: the page no longer loads boot.js")
            return 1

    size = sum(os.path.getsize(os.path.join(b, n))
               for b, _, ns in os.walk(OUT) for n in ns)
    print("built _site: %d python files zipped, %.1f KB total, build %s"
          % (files, size / 1024.0, build))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
