"""The docs promise commands. This checks the CLI still offers them.

Public documentation drifts silently: a flag gets renamed, a pack stops
shipping a face, and the copy-paste line in the README or the guide keeps
looking plausible while failing for everyone who tries it. This pulls every
``marksman ...`` command out of the docs' code blocks and runs it past the real
parser and the real pack catalogue.

Only code blocks count -- fenced blocks in Markdown, ``<code>`` in HTML. A bare
mention in a sentence ("see ``marksman pack install``") is a reference, not a
promise, and is deliberately not checked.
"""

import html
import os
import re
import shlex
import unittest

from marksman import drills as drills_mod
from marksman import packs as packs_mod
from marksman.cli import build_parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ("README.md", os.path.join("guide", "index.html"))

_FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
_TAG = re.compile(r"<code>(.*?)</code>", re.S)
_CMD = re.compile(r"^\s*(marksman\s+.+)$", re.M)


def _commands(text):
    """Every runnable ``marksman`` line inside a code block."""
    for block in _FENCE.findall(text) + [html.unescape(t) for t in _TAG.findall(text)]:
        for line in _CMD.findall(re.sub(r"\s*\n\s+", " ", block)):
            cmd = line.split("#", 1)[0].strip().rstrip("\\")
            if "..." in cmd:
                continue                      # prose ellipsis, not a real invocation
            try:
                args = shlex.split(cmd)[1:]
            except ValueError:
                continue                      # unbalanced quotes: prose, not a command
            if args:
                yield cmd, args


def _flag(args, name):
    """The value the command actually passes for ``--name``, if it passes one."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


class TestDocumentedCommands(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()
        packs = packs_mod.active()
        self.faces = {t["name"] for p in packs for t in (p.get("targets") or [])}
        self.categories = {c for p in packs for c in (p.get("categories") or [])}
        drills = drills_mod.all_drills()
        self.drills = {d["id"] for d in drills}
        self.families = {d["family"] for d in drills if d.get("family")}

    def test_every_documented_command_parses(self):
        checked = 0
        for doc in DOCS:
            with open(os.path.join(ROOT, doc), encoding="utf-8") as fh:
                text = fh.read()
            for cmd, args in _commands(text):
                with self.subTest(doc=doc, cmd=cmd):
                    try:
                        self.parser.parse_args(args)
                    except SystemExit:
                        self.fail("%s documents a command the CLI rejects: %s" % (doc, cmd))
                checked += 1
        self.assertGreater(checked, 20, "the command scraper found almost nothing")

    def test_documented_faces_and_categories_still_ship(self):
        for doc in DOCS:
            with open(os.path.join(ROOT, doc), encoding="utf-8") as fh:
                text = fh.read()
            for cmd, args in _commands(text):
                try:
                    command = self.parser.parse_args(args).command
                except SystemExit:
                    continue                  # the other test reports this
                # '--target' is a face for 'analyze' and a number for 'goal',
                # so the subcommand decides what the value has to be.
                checks = [("--category", self.categories, "category"),
                          ("--drill", self.drills, "drill"),
                          ("--family", self.families, "drill family")]
                if command in ("analyze", "targets"):
                    checks.append(("--target", self.faces, "face"))
                # 'drill show <id>' names a drill positionally.
                if command == "drill" and len(args) > 2 and args[1] == "show":
                    with self.subTest(doc=doc, cmd=cmd, value=args[2]):
                        self.assertIn(args[2], self.drills,
                                      "%s names a drill no installed pack offers" % doc)
                for flag, known, what in checks:
                    value = _flag(args, flag)
                    if value is None or value.startswith("<"):
                        continue              # not passed, or a fill-in-the-blank
                    with self.subTest(doc=doc, cmd=cmd, value=value):
                        self.assertIn(value, known,
                                      "%s names a %s no installed pack offers" % (doc, what))


if __name__ == "__main__":
    unittest.main()
