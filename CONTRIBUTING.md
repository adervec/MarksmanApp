# Contributing to Marksman

Thanks for your interest! Marksman is a small, **non‑commercial hobby project**.
Contributions — bug reports, fixes, docs, new target faces or skins — are
welcome. Please keep the following in mind.

## Ground rules

- **Standard library only (runtime).** The whole app runs on a stock Python
  3.9+ interpreter with **zero runtime dependencies**, and that's a core design
  goal. Please don't add a runtime dependency. (Pillow stays *optional* and is
  imported only if present; dev‑only tools like `pytest` are fine.)
- **Stay compatible with Python 3.9–3.13.** CI tests this range across Linux,
  Windows, and macOS.
- **Keep plain output stable.** Colour/skins must remain off for non‑terminals,
  `NO_COLOR`, and `--no-color`, so piped/redirected output stays byte‑for‑byte
  plain. Tests rely on this.
- **Mind the domain.** This project is informational only and about **airsoft**.
  Don't add content that presents itself as professional coaching, medical,
  safety, or legal advice, and don't weaken the existing disclaimers. See
  [DISCLAIMER.md](DISCLAIMER.md).

## Getting set up

No install is required to run or test:

```bash
git clone https://github.com/adervec/MarksmanApp.git
cd MarksmanApp
python run_tests.py        # runs the full suite (stdlib unittest), exits non‑zero on failure
```

Optional, for an editable install and the `marksman` command / pytest:

```bash
python -m pip install -e ".[dev]"
marksman --help
pytest                      # equivalent to run_tests.py
```

## Making a change

1. Branch off `dev` (e.g. `git switch -c fix/short-description dev`).
2. Make your change **with tests** — add or update tests under `tests/` for any
   behaviour change. Match the existing code style (clear names, docstrings,
   standard library).
3. Run `python run_tests.py` and make sure all tests pass.
4. Open a pull request. CI (the test matrix + a packaging build) must be green
   before merge.

Small, focused PRs are easiest to review. If you're planning something large,
open an issue first so we can talk it through.

## Reporting bugs & requesting features

Open a GitHub issue with steps to reproduce (a minimal `--shots "..."` example
is perfect), what you expected, and what happened. For **security** issues,
please follow [SECURITY.md](SECURITY.md) instead of filing a public issue.

## Licensing of contributions

By submitting a contribution, you agree that it is your own work (or you have
the right to submit it) and that it will be licensed under the project's
[MIT License](LICENSE). You retain copyright to your contributions.

## Code of Conduct

Participation in this project is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md). Please be respectful.
