#!/usr/bin/env python3
"""Smoke tests for the single entry point (stdlib unittest).

These check the launcher's wiring without starting servers or a browser: it can
locate the project root, report the build state, and the control console it drives
imports cleanly with the expected deployments.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "suite"))

import launcher  # noqa: E402


class TestLauncher(unittest.TestCase):
    def test_root_resolves_to_repo(self):
        self.assertTrue(os.path.isdir(os.path.join(launcher.ROOT, "scripts")))
        self.assertTrue(os.path.isfile(os.path.join(launcher.ROOT, "suite", "console.py")))

    def test_tools_built_is_boolean(self):
        self.assertIn(launcher.tools_built(), (True, False))

    def test_console_imports_with_deployments(self):
        sys.path.insert(0, os.path.join(ROOT, "suite"))
        import console
        import tase2ctl
        deps = tase2ctl.load_profiles()
        self.assertIn("sim-demo", deps)
        self.assertTrue(hasattr(console, "Handler"))
        self.assertTrue(hasattr(console, "SUP"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
