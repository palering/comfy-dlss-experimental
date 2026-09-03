from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from comfy_dlss_experimental.discovery import discover_proton_installations, resolve_proton_choice


class ProtonDiscoveryTests(unittest.TestCase):
    def test_discovers_custom_proton_and_reads_version(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            install = home / "tools/GE-Proton-test"
            install.mkdir(parents=True)
            executable = install / "proton"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            (install / "version").write_text("123 GE-Proton-test-1\n", encoding="utf-8")

            found = discover_proton_installations(
                home=home,
                environ={"COMFY_DLSS_PROTON_PATHS": str(install.parent)},
                platform_name="linux",
            )
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].version, "GE-Proton-test-1")
            self.assertEqual(resolve_proton_choice(found[0].selection_id, found), found[0])

    def test_deduplicates_symlinked_steam_roots(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink behavior differs on Windows")
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            real_base = home / ".local/share/Steam"
            install = real_base / "compatibilitytools.d/Test Proton"
            install.mkdir(parents=True)
            executable = install / "proton"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            steam_link = home / ".steam/root"
            steam_link.parent.mkdir(parents=True)
            steam_link.symlink_to(real_base, target_is_directory=True)

            found = discover_proton_installations(home=home, environ={}, platform_name="linux")
            self.assertEqual(len(found), 1)

    def test_non_linux_does_not_scan(self) -> None:
        self.assertEqual(discover_proton_installations(platform_name="darwin"), [])
