"""Test package.

The airsoft pack is no longer bundled -- it ships as an optional pack in the
repo's ``packs/`` directory. Point the user-pack lookup there so the suite
still has it to exercise, which also means the tests run it down the same path
a user's own installed pack takes.
"""
import os

os.environ.setdefault(
    "MARKSMAN_PACKS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packs"),
)
