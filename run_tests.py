#!/usr/bin/env python
"""Run the full test suite with the standard library (no pytest needed).

    python run_tests.py

Writes a copy of the results to ``test_results.txt`` as well, since some
terminals buffer stderr oddly.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


def main() -> int:
    with open(os.path.join(ROOT, "test_results.txt"), "w", encoding="utf-8") as fh:
        loader = unittest.TestLoader()
        suite = loader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
        runner = unittest.TextTestRunner(stream=fh, verbosity=2)
        result = runner.run(suite)
        fh.write("\n\nSUMMARY ran=%d failures=%d errors=%d skipped=%d ok=%s\n" % (
            result.testsRun, len(result.failures), len(result.errors),
            len(result.skipped), result.wasSuccessful()))
    # Echo the summary to the console too.
    with open(os.path.join(ROOT, "test_results.txt"), "r", encoding="utf-8") as fh:
        sys.stdout.write(fh.read())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
