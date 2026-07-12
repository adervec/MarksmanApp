# Marksman

<img src="assets/marksman.png" alt="Marksman logo" width="128" align="right">

[![CI](https://github.com/adervec/MarksmanApp/actions/workflows/ci.yml/badge.svg)](https://github.com/adervec/MarksmanApp/actions/workflows/ci.yml)

A progress tracker for **airsoft** — AEGs, gas blowback (GBB) pistols and
rifles, spring, HPA, and bolt-action replicas. Its core feature **analyses a
marked-up image of a target** (the groupings of your hits), turns it into
precise marksmanship metrics, and **tracks your progress over time** — overall,
by tool **category**, and by **specific tool**.

It is pure Python standard library: **no third-party packages required**
(works on Python 3.9+). Image analysis reads PNG out of the box; if Pillow
happens to be installed, JPEG and other formats work too.

> ⚠️ **Disclaimer — please read.** Marksman is a hobby project by a software
> developer — **not** a coach, instructor, doctor, or lawyer. It computes
> metrics for **personal progress tracking only**; it is **not** coaching,
> safety, medical, or legal advice, and **not** an official scoring system.
> Airsoft still fires projectiles: **always wear ANSI-rated eye protection,
> follow your field's rules, and obey your local laws.** Provided "as is", with
> no warranty. Full text: **[DISCLAIMER.md](DISCLAIMER.md)**.

## What it measures

From a set of hits it computes the standard measures used to judge a group:

| Metric | Meaning |
|---|---|
| **Group size (extreme spread)** | Largest centre-to-centre distance — the headline "group size". |
| **Mean radius** | Average distance of hits from the group centre — a stable precision measure. |
| **RMS radius / CEP / σx, σy** | Further precision descriptors. |
| **Zero error (POA–POI)** | How far the group centre sits from your point of aim (sight/hop zero). |
| **Score** | Points off the target's rings, with optional decimal (tenths) scoring and "edge breaks the line" BB-size handling. |

Group size is also reported as an **angle** (mrad / MOA) using the distance, and
score as a **percentage of maximum**, so sessions at different distances and on
different target faces can be compared on equal terms.

## Install

It runs straight from the source tree — no install needed. To get a `marksman`
command on your PATH, pick one:

```bash
pipx install .        # isolated, recommended
pip install --user .  # or into your user site-packages
pip install -e .      # editable (for hacking on it)
```

Once installed, run `marksman ...`; without installing, `python -m marksman ...`
works from the source tree. (`python -m marksman.cli ...` also still works.)

**Windows one-click:** `install.ps1` installs the command, generates the icon,
and adds a Start Menu launch shortcut (add `-Desktop` for a desktop one too):

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Your data lives in a single `marksman_data.json` (the shortcut opens in
`%USERPROFILE%\Marksman`).

## Quick start

```bash
# 1) Register a tool (your airsoft replica)
python -m marksman.cli tool add --id aeg1 --name "Training AEG" \
    --category "AEG" --bb 6mm --bb-mm 6.0

# 2a) Analyse a photo/scan where each hit is marked with a red dot/circle.
#     The image spans a 400 mm target face; find the bull automatically.
python -m marksman.cli analyze --tool aeg1 --target "Airsoft Practice 10m" \
    --distance 10 --image my_target.png --color red --auto-center

# 2b) ...or just type the shot coordinates (mm from point of aim):
python -m marksman.cli analyze --tool aeg1 --target "Airsoft Practice 10m" \
    --distance 10 --shots "1.2,3.4  -2.0,5.1  0.5,-1.0"

# 3) Track progress
python -m marksman.cli progress                 # overall
python -m marksman.cli progress --by-category
python -m marksman.cli progress --by-tool
python -m marksman.cli progress --tool aeg1 --sessions
python -m marksman.cli sessions                 # list every saved target
python -m marksman.cli targets                  # built-in target faces

# 4) Pick a skin (see "Skins" below)
python -m marksman.cli theme                    # list skins
python -m marksman.cli theme preview inferno    # try one on
python -m marksman.cli theme set recon          # make it the default

# 5) Save space: recreate results as diagrams, then delete bulky source media
python -m marksman.cli render --tool aeg1       # redraw from stored shots
python -m marksman.cli cleanup --tool aeg1      # dry run (add --apply to delete)
```

## Analysing an image

Mark each hit on the target (a coloured pen dot or ring around each impact is
the most reliable). Then:

* **Detection** — `--mode marker` finds the coloured marks (`--color
  red|green|blue|orange|purple|yellow`, or `--rgb r,g,b`). `--mode holes`
  detects dark impact marks directly on a clean scan.
* **Where the centre is** — `--center X,Y` (pixels), or `--auto-center` to use
  the dark bull, or it defaults to the image centre.
* **Scale (mm per pixel)** — one of `--mm-per-px`, `--reference "x1,y1 x2,y2
  mm"` (two points a known distance apart), or `--face-width MM` (the image
  spans a target face that wide). If you pass a `--target`, its known face
  width is used as a fallback.

## AI coach (cowork folder)

Marksman can hand your progress to an AI coach through a **folder** — no API key,
no network calls from the app. It writes a request folder; a
[Claude Code](https://claude.com/claude-code) agent (or any Claude chat) reads it
and writes back a `reply.json`; you pull that reply back in.

```bash
marksman coach export        # writes coach/ : request.json, request.md, CLAUDE.md
# → point a Claude Code agent at coach/ (it reads CLAUDE.md), or paste
#   coach/request.md into any Claude chat. It saves coach/reply.json.
marksman coach apply         # ingest the reply: analysis, a focus, drills, tips
marksman coach show          # read the latest coaching any time
```

The request bundles the full progress dataset — group sizes (mm and mrad), score
%, zero error, per-tool and per-category trends, recent sessions and your current
practice **streak** — so the coaching is grounded in your actual numbers. It is
practice feedback only, never coaching-certification, medical, or safety advice.

## Goals

Set a target for one metric — overall or for a specific tool — and track it. A
goal is "met" once your **best** session for that scope crosses the target.

```bash
marksman goal set --metric group_size --target 30 --tool aeg1   # <= 30 mm group
marksman goal set --metric score --target 80                    # >= 80% overall
marksman goal list                                              # progress + status
marksman goal rm <id>
```

Metrics: `group_size`, `group_mrad`, `mean_radius`, `zero_error`, `score`. Any
goals you set ride along in the coach export, so the AI coach steers its focus
toward what you're chasing.

## Export your data

```bash
marksman export --format csv --out sessions.csv   # one row per session
marksman export --format json                     # to stdout (pipe it)
```

## Logo / icon

Regenerate the app icon any time (pure Python — no image libraries):

```bash
marksman logo --out assets --size 512   # → assets/marksman.png + marksman.ico
```

## Skins

The reports can wear a **skin** — a colour palette plus a little texture
(sparkline ramp, rule character, marker glyph). Every skin is an original name
with its own palette; pick whichever mood you like:

| Skin | Palette / vibe |
|---|---|
| `mono` | A clean printed score card — no colour (the default). |
| `recon` | Night-vision phosphor green & amber. |
| `orbital` | Blue HUD with green and holographic amber. |
| `inferno` | Molten blood-red and hellfire orange. |
| `frontline` | Steel-blue smoke cut with dog-tag orange. |
| `lightfall` | Deep purple lit by golden light. |
| `pandora` | Bold comic yellow with inky outlines. |
| `dust` | Desert sand versus a cool tactical blue. |
| `overdrive` | Vibrant orange energy over bright cyan. |
| `dropzone` | Crimson on gunmetal slate. |
| `tropic` | Lush tropical teal under a sunset orange. |

```bash
marksman theme                    # list skins (current one marked)
marksman theme preview orbital    # see a skin without committing
marksman theme set inferno        # save it as your default
marksman --theme dust progress    # use a skin for just this run
marksman --no-color progress      # force plain output
```

Colour is emitted **only** to a real terminal. When output is piped or
redirected, or when `NO_COLOR` is set, reports fall back to plain text
automatically — so scripts and tests see stable, uncoloured output. On Windows,
ANSI is enabled automatically (Windows Terminal, PowerShell, or modern
`conhost`).

## Recreations & cleaning up media

Source photos and videos of your targets are bulky and pile up fast. Because
every session stores the **shot coordinates** (mm from point of aim) and the
**target** they were scored on, Marksman can always *redraw* a clean diagram of
the result — the rings with your hits plotted on them — without the original
media. So the source files can be deleted to save space while the visual
result is preserved.

```bash
# Redraw a result from stored data (no source media needed)
marksman render --session 2026-04-15        # -> recreations/2026-04-15.png
marksman render --tool aeg1                 # one PNG per session
marksman render --session 2026-04-15 --out group.png

# Reclaim space. Dry run first (shows what would be freed, deletes nothing):
marksman cleanup --tool aeg1
marksman cleanup --before 2026-01-01
marksman cleanup --apply                    # saves a recreation, then deletes
```

`cleanup` is **dry-run by default**; it only deletes with `--apply`, and even
then it renders a recreation diagram *first* (unless `--no-recreate`), so the
picture of the result is never lost. The shot data is always kept, so a
recreation can be regenerated at any time. Recreations live in a
`recreations/` folder beside your database. A recreation is a small flat-colour
PNG (a few KB) — far smaller than a photo.

You can attach a source video to a session with `marksman analyze ... --video
clip.mp4`; like the image, it is just referenced and is cleared by `cleanup`.

## Data

Everything is stored in a single JSON file (`marksman_data.json` by default;
override with `--db PATH`). It is human-readable and easy to back up.

## Run the tests

```
python run_tests.py
```

## Try the demo

Generates a synthetic marked-up target, analyses it, and prints a progress
report — end to end, no setup:

```
python demo.py
```

## Library use

```python
from marksman.models import Shot
from marksman.grouping import analyze_group
from marksman.targets import get_target

shots = [Shot(1.2, 3.4), Shot(-2.0, 5.1), Shot(0.5, -1.0)]
stats = analyze_group(shots, target=get_target("Airsoft Practice 10m"))
print(stats.extreme_spread_mm, stats.total_score)
```

## Scope & accuracy

The built-in faces are **generic practice bullseyes** with reasonable ring
sizes for 6 mm BBs at common distances — they are for personal progress
tracking, not an official standard. Print whatever face you like and register a
custom one with `targets.uniform_target` + `targets.register`.

## License

Released under the [MIT License](LICENSE) — free to use, modify, and
redistribute with attribution, no warranty. © 2026 Adam Erik Eryavec. This is a
non-commercial hobby project; see **[GOING_PUBLIC.md](GOING_PUBLIC.md)** for the
public-release plan and checklist.

## Disclaimer

Marksman is **not** professional coaching, medical, safety, or legal advice, and
**not** an official scoring system. Eye protection, field rules, and legal
compliance are your responsibility. Full text: **[DISCLAIMER.md](DISCLAIMER.md)**.

## Trademarks & third-party references

Any product, brand, or organization names referenced are the property of their
respective owners and are used only descriptively — no affiliation or
endorsement is implied. Marksman bundles no third-party code or assets; see
**[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)**.
