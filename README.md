# Marksman

A progress tracker for shooting sports — pistol, rifle, shotgun, and airguns,
indoor or outdoor. Its core feature **analyses a marked-up image of a target**
(the groupings of your shots), turns it into precise marksmanship metrics, and
**tracks your progress over time** — overall, by weapon **category**, and by
**specific weapon**.

It is pure Python standard library: **no third-party packages required**
(works on Python 3.9+). Image analysis reads PNG out of the box; if Pillow
happens to be installed, JPEG and other formats work too.

## What it measures

From a set of shots it computes the standard measures used to judge a group:

| Metric | Meaning |
|---|---|
| **Group size (extreme spread)** | Largest centre-to-centre distance — the headline "group size". |
| **Mean radius** | Average distance of shots from the group centre — a stable precision measure. |
| **RMS radius / CEP / σx, σy** | Further precision descriptors. |
| **Zero error (POA–POI)** | How far the group centre sits from your point of aim (sight zero). |
| **Score** | Points off the target's rings, with optional ISSF decimal (tenths) scoring and "edge breaks the line" caliber handling. |

Group size is also reported as an **angle** (mrad / MOA) using the shooting
distance, and score as a **percentage of maximum**, so sessions at different
distances and on different target faces can be compared on equal terms.

## Install (optional)

It runs straight from the source tree — no install needed. To get the
`marksman` command on your PATH:

```
pip install -e .
```

Otherwise call it as a module: `python -m marksman.cli ...`

## Quick start

```bash
# 1) Register a weapon
python -m marksman.cli weapon add --id ap1 --name "Walther LP500" \
    --category "Air Pistol" --caliber 4.5mm --caliber-mm 4.5 --airgun

# 2a) Analyse a photo/scan where each shot is marked with a red dot/circle.
#     The image spans a 170 mm target face; find the bull automatically.
python -m marksman.cli analyze --weapon ap1 --target "ISSF 10m Air Pistol" \
    --distance 10 --image my_target.png --color red --auto-center

# 2b) ...or just type the shot coordinates (mm from point of aim):
python -m marksman.cli analyze --weapon ap1 --target "ISSF 10m Air Pistol" \
    --distance 10 --shots "1.2,3.4  -2.0,5.1  0.5,-1.0"

# 3) Track progress
python -m marksman.cli progress                 # overall
python -m marksman.cli progress --by-category
python -m marksman.cli progress --by-weapon
python -m marksman.cli progress --weapon ap1 --sessions
python -m marksman.cli sessions                 # list every saved target
python -m marksman.cli targets                  # built-in target faces
```

## Analysing an image

The shooter marks each shot on the target (a coloured pen dot or ring around
each hole is the most reliable). Then:

* **Detection** — `--mode marker` finds the coloured marks (`--color
  red|green|blue|orange|purple|yellow`, or `--rgb r,g,b`). `--mode holes`
  detects dark bullet holes directly on a clean scan.
* **Where the centre is** — `--center X,Y` (pixels), or `--auto-center` to use
  the dark bull, or it defaults to the image centre.
* **Scale (mm per pixel)** — one of `--mm-per-px`, `--reference "x1,y1 x2,y2
  mm"` (two points a known distance apart), or `--face-width MM` (the image
  spans a target face that wide). If you pass a `--target`, its known face
  width is used as a fallback.

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
stats = analyze_group(shots, target=get_target("ISSF 10m Air Pistol"))
print(stats.extreme_spread_mm, stats.total_score)
```

## Scope & accuracy

Ring dimensions follow published ISSF/NRA nominals and are intended for
personal progress tracking, not official scoring. For matches, use the
official scored target.
