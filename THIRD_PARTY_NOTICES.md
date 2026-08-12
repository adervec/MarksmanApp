# Third‑Party Notices

Marksman ships **no third‑party code or assets** of its own — the entire
application (grouping maths, scoring, tracking, storage, PNG image analysis, and
the CLI) uses only the Python standard library. This file inventories the
optional, development, and build‑time components a user or contributor might
pull in, and their licenses, for transparency.

All licenses below are permissive (no copyleft obligations on Marksman or on
your use of it).

## Trademarks and brand independence

Marksman is an **independent hobby project**. It is not affiliated with,
sponsored by, endorsed by, or connected to **any** blaster, toy, sporting‑goods
or equipment manufacturer, distributor or retailer.

Everything in this repository is original work:

- The name "Marksman", the logo, the app icon, every colour palette ("skin"),
  and all in‑app artwork are drawn from scratch by this project. The foam dart
  in the logo is a **generic** shape — a soft cylinder with a rounded head —
  drawn by [`marksman/logo.py`](marksman/logo.py) with the built‑in PNG writer.
- The bundled equipment packs describe **generic categories** of equipment
  (springer, flywheel, pump‑action, and so on). They name no brand, no product
  line, and no model.
- No third‑party logo, wordmark, product name, packaging, colour scheme or
  other trade dress is reproduced, imitated or referenced anywhere in this
  project's code, assets or documentation.

Where a user writes their own equipment pack, whatever they put in it is
**their** content, not this project's — see
[DISCLAIMER.md](DISCLAIMER.md). Any trademark that may be mentioned in
user‑authored content remains the property of its owner.

## Runtime dependencies

**None.** Marksman runs on a stock Python 3.9+ interpreter with nothing
installed. There is nothing to redistribute and no runtime license to carry.

## Optional runtime dependency (not bundled, used only if you install it)

| Component | Purpose | License |
|---|---|---|
| [Pillow](https://python-pillow.org/) (`pillow>=9.0`) | Lets the vision layer read JPEG and other formats; PNG works without it | HPND (MIT/BSD‑style, permissive) |

Pillow is imported only if it is importable; Marksman never installs or bundles
it. If you `pip install marksman[vision-extra]`, Pillow is fetched from PyPI
under its own license.

## Development / build tooling (not distributed with the package)

| Component | Purpose | License |
|---|---|---|
| [pytest](https://pytest.org/) | Optional test runner (the stdlib `run_tests.py` needs no deps) | MIT |
| [setuptools](https://setuptools.pypa.io/) | Build backend | MIT |
| [build](https://build.pypa.io/) | Builds the sdist/wheel | MIT |
| [twine](https://twine.readthedocs.io/) | Validates/uploads distributions | Apache‑2.0 |

## GitHub Actions used in CI/CD (run on GitHub's runners, not distributed)

| Action | License |
|---|---|
| `actions/checkout` | MIT |
| `actions/setup-python` | MIT |
| `actions/upload-artifact` / `actions/download-artifact` | MIT |
| `pypa/gh-action-pypi-publish` | BSD‑3‑Clause |

## Fonts in the bundled guide (loaded from a CDN, not bundled)

`Associated Guide/marksmanship_guide.html` references two open‑licensed fonts
from Google Fonts at view time; neither font file is included in this
repository:

| Font | License |
|---|---|
| [Inter](https://github.com/rsms/inter) | SIL Open Font License 1.1 |
| [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) | SIL Open Font License 1.1 |

> Note: loading fonts from Google Fonts contacts Google's servers when the guide
> is opened in a browser. To avoid any external request, self‑host the fonts or
> remove the `<link>` tags in the guide's `<head>`.

---

*If you believe something here is inaccurate or that a notice is missing, please
open an issue. This is provided for transparency and is not legal advice.*
