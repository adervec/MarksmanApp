# Going Public — Plan & Checklist

A practical plan for releasing Marksman as a public, open‑source, **non‑commercial**
project. It records what's already been done and what's left to decide or do.

> I'm a software developer, not a lawyer — this is a sensible risk‑reduction
> setup, **not legal advice.** For real peace of mind on the firearms and
> trademark angles, a one‑time consult with a lawyer in your jurisdiction is the
> gold standard.

## Decisions made

| Topic | Decision |
|---|---|
| License | **MIT** — permissive, attribution‑only, no warranty. Fits "share freely, not monetizing." |
| Game‑franchise skin references | **Removed** the explicit "nods to \<Game\>" mapping from the README; kept original skin names + generic palette descriptions. |
| Instructional guide | **Kept public** with prominent disclaimers; reframed as personal notes, not professional instruction. |

## Done in this pass

- [x] **`LICENSE`** added (MIT, © 2026 Adam Erik Eryavec) — closes the gap where
      `pyproject.toml` declared MIT with no license file.
- [x] **`DISCLAIMER.md`** — no‑warranty, not‑coaching, not‑medical, firearms
      safety, not‑legal‑advice, not‑official‑scoring, no‑affiliation, trademarks,
      limitation of liability.
- [x] **`THIRD_PARTY_NOTICES.md`** — license inventory (runtime: none; optional
      Pillow HPND; dev/build MIT/Apache; CI actions; guide fonts OFL).
- [x] **README** — removed trademarked franchise names from the Skins table,
      added a top‑of‑file disclaimer note and License / Disclaimer / Trademarks
      sections.
- [x] **Guide** — added a prominent "Read first · Disclaimer" banner and
      reframed the cover stamp from "TRAINING DOCUMENT" to "PERSONAL NOTES."
- [x] **`pyproject.toml`** — added OSI license + Python classifiers and the
      repository URL for a correct public/PyPI face.

## Before you flip the repo to public

- [ ] **Scrub for secrets/PII in history.** The live database `marksman_data.json`
      is git‑ignored (good). Skim history for anything sensitive:
      `git log --stat` and `git log -p -- pyproject.toml`. There are no tokens in
      the tree today; confirm none were ever committed.
- [ ] **Confirm the copyright name** ("Adam Erik Eryavec") is how you want to be
      attributed publicly. (Your home location appears in the guide — that's your
      call to keep or generalize.)
- [ ] **Change repository visibility** to public:
      GitHub → repo **Settings → General → Danger Zone → Change visibility**.
- [ ] **Repo polish:** add a short description and topics/tags
      (`python`, `shooting-sports`, `marksmanship`, `cli`, `image-analysis`),
      and confirm the CI badge renders once public.

## Optional / nice‑to‑have

- [ ] **Community health files** (templates GitHub will surface):
      `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, and issue/PR
      templates under `.github/`. Low effort, signals a maintained project.
- [ ] **Cut a release:** `git tag v0.1.0 && git push origin v0.1.0` →
      the `release.yml` workflow builds and attaches the sdist/wheel to the
      GitHub Release.
- [ ] **PyPI (only if you want `pip install marksman`):** the name *marksman*
      may be taken — check <https://pypi.org/project/marksman/>. If free and you
      want it, set up Trusted Publishing (see comments in `release.yml`).
      Otherwise the publish job stays dormant or can be deleted.
- [ ] **License reconsideration:** you chose MIT. If you ever want an explicit
      patent grant, **Apache‑2.0** is the usual permissive upgrade; if you want
      forks to stay open, **GPL‑3.0**. Easy to change before you have outside
      contributors.

## Residual risk notes (plain English)

- **Trademarks:** the franchise mapping is gone. Remaining references (ISSF, NRA,
  IPSC target/discipline names, named ranges) are *nominative* — using a name to
  refer to the actual thing — which is generally fine and is now disclaimed. Low
  risk for a non‑commercial project.
- **Firearms‑domain liability:** the disclaimers cover coaching/medical/legal/
  safety and "use at your own risk." The guide keeps its existing "Not Legal
  Advice" callout. This is reasonable; it is not a guarantee. A lawyer consult is
  the only way to be sure.
- **Not monetizing:** keeping it non‑commercial materially lowers trademark and
  liability exposure. If that ever changes, revisit this document first.
