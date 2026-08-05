"""Equipment packs: the content layer, kept out of the app.

Marksman itself knows nothing about any particular kind of shooting.  It knows
about *tools* that launch *projectiles* at *target faces*, and about drills
judged on a metric.  Everything with a subject matter -- what the tool is
called, what the faces are, which drills exist -- lives in a **pack**: a plain
JSON file the app reads at startup.

That split means the app runs with no packs at all (you can still log sessions
against your own targets), the bundled packs are ordinary data rather than
special cases, and anyone can write a pack for their own discipline without
touching the source.

Packs are **data, never code** -- ``json.load`` and nothing else, so an
installed pack cannot execute anything.

Sensitivity
-----------
Every pack declares a ``sensitivity`` 1-5 saying how regulated or contentious
its subject is.  A pack above the user's ``max_sensitivity`` setting is found
and listed but **not loaded** until they raise the threshold on purpose.  The
bundled packs are 1-2; the ceiling starts at 2, so the app out of the box only
ever loads recreational content.  Raising it is a deliberate, recorded act.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import targets as targets_mod
from .goals import METRICS
from .models import TargetSpec, register_categories

SCHEMA_VERSION = 1

#: Sensitivity ladder.  The number is the pack's own declaration; the app only
#: compares it against the user's ceiling.
SENSITIVITY = {
    1: ("Toy", "Recreational, sold as a toy, no licence anywhere."),
    2: ("Sport", "Hobby equipment with site rules and eye protection."),
    3: ("Regulated", "Licence, permit or club membership typical."),
    4: ("Restricted", "Heavily regulated; professional or club context."),
    5: ("Unclassified", "User-authored; the app makes no judgement."),
}

#: Ceiling applied when the user hasn't chosen one.  Bundled packs sit at or
#: below this, so a fresh install loads only recreational content.
DEFAULT_MAX_SENSITIVITY = 2

#: Where packs live: the ones shipped with the app, then the user's own.  A
#: user pack with the same id as a bundled one replaces it.
BUNDLED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "packs")


def user_dir() -> str:
    """Where the user's own packs go (``MARKSMAN_PACKS`` overrides)."""
    env = os.environ.get("MARKSMAN_PACKS")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".marksman", "packs")


# --------------------------------------------------------------------------- #
# Validation -- a pack is untrusted input, so everything is checked
# --------------------------------------------------------------------------- #

class PackError(ValueError):
    """A pack file is malformed. The message names the file and the problem."""


def _need(d: Dict[str, Any], key: str, kind, where: str):
    if key not in d:
        raise PackError("%s: missing %r" % (where, key))
    val = d[key]
    if not isinstance(val, kind):
        raise PackError("%s: %r should be %s" % (where, key, getattr(kind, "__name__", kind)))
    return val


def _text(d: Dict[str, Any], key: str, where: str, default: str = "") -> str:
    val = d.get(key, default)
    if not isinstance(val, str):
        raise PackError("%s: %r should be text" % (where, key))
    return val


def validate(raw: Dict[str, Any], where: str = "pack") -> Dict[str, Any]:
    """Check one pack dict; return it normalised. Raises :class:`PackError`."""
    if not isinstance(raw, dict):
        raise PackError("%s: a pack must be a JSON object" % where)
    ver = raw.get("schema_version")
    if ver != SCHEMA_VERSION:
        raise PackError("%s: schema_version %r, expected %d"
                        % (where, ver, SCHEMA_VERSION))

    pid = _need(raw, "id", str, where).strip().lower()
    if not pid or not all(c.isalnum() or c in "-_" for c in pid):
        raise PackError("%s: id %r must be alphanumeric (- and _ allowed)" % (where, pid))

    sens = raw.get("sensitivity")
    if sens not in SENSITIVITY:
        raise PackError("%s: sensitivity %r must be one of %s"
                        % (where, sens, ", ".join(str(k) for k in sorted(SENSITIVITY))))

    pack = {
        "id": pid,
        "name": _need(raw, "name", str, where),
        "version": _text(raw, "version", where, "1.0.0"),
        "author": _text(raw, "author", where),
        "description": _text(raw, "description", where),
        "sensitivity": sens,
        "safety": _text(raw, "safety", where),
        "terms": {}, "categories": [], "targets": [], "drills": [],
        "projectile_mm": None, "source": where,
    }

    terms = raw.get("terms", {})
    if not isinstance(terms, dict):
        raise PackError("%s: terms should be an object" % where)
    pack["terms"] = dict((str(k), str(v)) for k, v in terms.items())

    cats = raw.get("categories", [])
    if not isinstance(cats, list) or any(not isinstance(c, str) for c in cats):
        raise PackError("%s: categories should be a list of names" % where)
    pack["categories"] = list(cats)

    pmm = raw.get("projectile_mm")
    if pmm is not None:
        if not isinstance(pmm, (int, float)) or not (0 < pmm <= 200):
            raise PackError("%s: projectile_mm out of range" % where)
        pack["projectile_mm"] = float(pmm)

    for t in raw.get("targets", []):
        pack["targets"].append(_target(t, where))
    for d in raw.get("drills", []):
        pack["drills"].append(_drill(d, pid, where))

    seen = set()
    for d in pack["drills"]:
        if d["id"] in seen:
            raise PackError("%s: duplicate drill id %r" % (where, d["id"]))
        seen.add(d["id"])
    return pack


def _target(t: Dict[str, Any], where: str) -> Dict[str, Any]:
    if not isinstance(t, dict):
        raise PackError("%s: each target should be an object" % where)
    name = _need(t, "name", str, where)
    out = {"name": name, "notes": _text(t, "notes", where)}
    for key, lo, hi in (("ten_ring_mm", 0.1, 5000.0), ("ring_step_mm", 0.1, 5000.0),
                        ("face_mm", 1.0, 20000.0)):
        val = t.get(key)
        if not isinstance(val, (int, float)) or not (lo <= float(val) <= hi):
            raise PackError("%s: target %r has a bad %s" % (where, name, key))
        out[key] = float(val)
    rings = t.get("rings", 10)
    if not isinstance(rings, int) or not (2 <= rings <= 20):
        raise PackError("%s: target %r has a bad rings count" % (where, name))
    out["rings"] = rings
    return out


def _drill(d: Dict[str, Any], pid: str, where: str) -> Dict[str, Any]:
    if not isinstance(d, dict):
        raise PackError("%s: each drill should be an object" % where)
    did = _need(d, "id", str, where).strip().lower()
    metric = _need(d, "metric", str, where)
    if metric not in METRICS:
        raise PackError("%s: drill %r uses unknown metric %r (choose from %s)"
                        % (where, did, metric, ", ".join(METRICS)))
    cutoffs = _need(d, "cutoffs", list, where)
    if len(cutoffs) != 4 or any(not isinstance(c, (int, float)) for c in cutoffs):
        raise PackError("%s: drill %r needs exactly 4 numeric cutoffs" % (where, did))
    lower = METRICS[metric][2]
    ordered = all((cutoffs[i] > cutoffs[i + 1]) if lower else (cutoffs[i] < cutoffs[i + 1])
                  for i in range(3))
    if not ordered:
        raise PackError("%s: drill %r cutoffs must get harder, easiest first" % (where, did))
    dist = d.get("distance_m")
    if not isinstance(dist, (int, float)) or not (0 < float(dist) <= 10000):
        raise PackError("%s: drill %r has a bad distance_m" % (where, did))
    shots = d.get("shots", 5)
    if not isinstance(shots, int) or not (1 <= shots <= 500):
        raise PackError("%s: drill %r has a bad shot count" % (where, did))

    def steps(key):
        val = d.get(key, [])
        if not isinstance(val, list) or any(not isinstance(s, str) for s in val):
            raise PackError("%s: drill %r has a bad %s" % (where, did, key))
        return list(val)

    return {
        "id": did, "pack": pid,
        "name": _need(d, "name", str, where),
        "family": _text(d, "family", where, "General"),
        "distance_m": float(dist), "shots": shots,
        "target": _text(d, "target", where),
        "metric": metric, "cutoffs": [float(c) for c in cutoffs],
        "why": _text(d, "why", where), "how": steps("how"), "cues": steps("cues"),
        "warning": _text(d, "warning", where),
    }


# --------------------------------------------------------------------------- #
# Discovery and loading
# --------------------------------------------------------------------------- #

def discover() -> List[Dict[str, Any]]:
    """Every readable pack on disk, bundled first, user packs overriding by id.

    A malformed pack never stops the others loading -- it comes back with an
    ``error`` key so the UI can say which file is broken and why.
    """
    found = {}  # type: Dict[str, Dict[str, Any]]
    for directory in (BUNDLED_DIR, user_dir()):
        bundled = directory == BUNDLED_DIR
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    pack = validate(json.load(fh), where=name)
            except (OSError, ValueError) as e:
                found[path] = {"id": name[:-5], "name": name, "error": str(e),
                               "bundled": bundled, "source": path, "sensitivity": 5,
                               "drills": [], "targets": [], "categories": [], "terms": {}}
                continue
            pack["bundled"] = bundled
            pack["source"] = path
            found[pack["id"]] = pack
    return list(found.values())


_LOADED = None       # type: Optional[List[Dict[str, Any]]]


def settings(db) -> Dict[str, Any]:
    """The pack settings block from a database (never None)."""
    cfg = db.settings.get("packs") if db is not None else None
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "max_sensitivity": cfg.get("max_sensitivity", DEFAULT_MAX_SENSITIVITY),
        "disabled": list(cfg.get("disabled", [])),
    }


def status(db=None) -> List[Dict[str, Any]]:
    """Every discovered pack with why it is or isn't active."""
    cfg = settings(db)
    out = []
    for pack in sorted(discover(), key=lambda p: (p.get("sensitivity", 5), p["id"])):
        row = dict(pack)
        if pack.get("error"):
            row["active"] = False
            row["reason"] = "broken: %s" % pack["error"]
        elif pack["id"] in cfg["disabled"]:
            row["active"] = False
            row["reason"] = "switched off"
        elif pack["sensitivity"] > cfg["max_sensitivity"]:
            row["active"] = False
            row["reason"] = ("sensitivity %d is above your ceiling of %d"
                             % (pack["sensitivity"], cfg["max_sensitivity"]))
        else:
            row["active"] = True
            row["reason"] = "active"
        out.append(row)
    return out


def load(db=None) -> List[Dict[str, Any]]:
    """Load the active packs and register their target faces. Idempotent."""
    global _LOADED
    active = [p for p in status(db) if p["active"]]
    for pack in active:
        for t in pack["targets"]:
            targets_mod.register(build_target(t))
        register_categories(pack["categories"])
    _LOADED = active
    return active


def active(db=None) -> List[Dict[str, Any]]:
    """The loaded packs, loading them on first use."""
    if _LOADED is None:
        return load(db)
    return _LOADED


def reset() -> None:
    """Forget what was loaded (tests, and after changing settings)."""
    global _LOADED
    _LOADED = None


def build_target(t: Dict[str, Any]) -> TargetSpec:
    """Turn a pack's target entry into a real :class:`TargetSpec`."""
    spec = targets_mod.uniform_target(
        t["name"], ten_ring_diameter_mm=t["ten_ring_mm"],
        ring_step_mm=t["ring_step_mm"], highest_value=t["rings"],
        lowest_value=max(1, t["rings"] - 9), face_size_mm=t["face_mm"])
    spec.notes = t.get("notes", "")
    return spec


# --------------------------------------------------------------------------- #
# What the rest of the app asks for
# --------------------------------------------------------------------------- #

def drills(db=None) -> List[Dict[str, Any]]:
    """Every drill from every active pack, in pack then catalogue order."""
    out = []
    for pack in active(db):
        out.extend(pack["drills"])
    return out


def categories(db=None) -> List[str]:
    """Tool categories offered by the active packs, plus a neutral fallback."""
    out = []
    for pack in active(db):
        for c in pack["categories"]:
            if c not in out:
                out.append(c)
    if "Other" not in out:
        out.append("Other")
    return out


def term(word: str, db=None) -> str:
    """A pack's word for a core concept ('tool', 'projectile'), else the core one.

    Only a *single* active pack gets to rename things.  With several installed
    there is no non-arbitrary winner -- calling a dart a BB would be worse than
    saying "projectile" -- so a mixed setup speaks the app's neutral language.
    """
    loaded = active(db)
    if len(loaded) == 1 and word in loaded[0]["terms"]:
        return loaded[0]["terms"][word]
    return word


def install(path: str, db=None) -> Dict[str, Any]:
    """Copy a pack file into the user pack directory after validating it."""
    with open(path, "r", encoding="utf-8") as fh:
        pack = validate(json.load(fh), where=os.path.basename(path))
    dest_dir = user_dir()
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, pack["id"] + ".json")
    with open(path, "r", encoding="utf-8") as src:
        data = src.read()
    with open(dest, "w", encoding="utf-8") as out:
        out.write(data)
    pack["source"] = dest
    reset()
    return pack


if __name__ == "__main__":  # pragma: no cover - self-check
    good = {
        "schema_version": 1, "id": "demo", "name": "Demo", "sensitivity": 1,
        "categories": ["Blaster"],
        "targets": [{"name": "Demo 5m", "ten_ring_mm": 60, "ring_step_mm": 30,
                     "face_mm": 600}],
        "drills": [{"id": "demo-5", "name": "Demo Five", "distance_m": 5,
                    "shots": 5, "target": "Demo 5m", "metric": "group_size",
                    "cutoffs": [200, 150, 100, 60], "how": ["shoot"], "cues": []}],
    }
    p = validate(good, "demo.json")
    assert p["id"] == "demo" and len(p["drills"]) == 1
    assert build_target(p["targets"][0]).outer_radius_mm > 0

    def rejects(mutate, why):
        bad = json.loads(json.dumps(good))
        mutate(bad)
        try:
            validate(bad, "bad.json")
        except PackError:
            return
        raise AssertionError("should have rejected: " + why)

    rejects(lambda b: b.update(schema_version=99), "wrong schema version")
    rejects(lambda b: b.update(sensitivity=9), "sensitivity out of range")
    rejects(lambda b: b.update(id="../evil"), "path-ish id")
    rejects(lambda b: b["drills"][0].update(metric="nonsense"), "unknown metric")
    rejects(lambda b: b["drills"][0].update(cutoffs=[1, 2, 3]), "wrong cutoff count")
    rejects(lambda b: b["drills"][0].update(cutoffs=[60, 100, 150, 200]), "backwards cutoffs")
    rejects(lambda b: b["drills"].append(dict(b["drills"][0])), "duplicate drill id")
    rejects(lambda b: b["targets"][0].update(ten_ring_mm=-5), "negative ring")

    packs = discover()
    assert packs, "no bundled packs found"
    for pk in packs:
        assert not pk.get("error"), (pk["name"], pk.get("error"))
    print("packs self-check OK: %d bundled, %d drills"
          % (len(packs), sum(len(pk["drills"]) for pk in packs)))
