from __future__ import annotations

import unittest
from pathlib import Path

from comfy_dlss_experimental.models import ProtonInstallation
from comfy_dlss_experimental.runtime import build_launch_plan, format_wine_dll_overrides


class LaunchPlanTests(unittest.TestCase):
    def test_windows_launches_sidecar_directly(self) -> None:
        plan = build_launch_plan(
            platform_name="windows",
            sidecar=Path("C:/job/sidecar.exe"),
            working_directory=Path("C:/job"),
        )
        self.assertEqual(plan.command, (str(Path("C:/job/sidecar.exe")),))

    def test_linux_uses_selected_proton_and_isolated_prefix(self) -> None:
        proton = ProtonInstallation("steam-custom", "GE", "11", Path("/tools/GE"), Path("/tools/GE/proton"))
        plan = build_launch_plan(
            platform_name="linux",
            sidecar=Path("/jobs/a/sidecar.exe"),
            working_directory=Path("/jobs/a"),
            proton=proton,
            prefix=Path("/prefixes/key"),
            dll_overrides={"d3dcompiler_47": "n", "dxgi": "n,b"},
        )
        self.assertEqual(plan.command, (str(proton.executable), "run", str(Path("/jobs/a/sidecar.exe"))))
        self.assertEqual(plan.environment["STEAM_COMPAT_DATA_PATH"], str(Path("/prefixes/key")))
        self.assertEqual(plan.environment["WINEDLLOVERRIDES"], "d3dcompiler_47=n;dxgi=n,b")

    def test_dll_overrides_reject_shell_separators_and_unknown_modes(self) -> None:
        with self.assertRaises(ValueError):
            format_wine_dll_overrides({"dxgi;bad": "n,b"})
        with self.assertRaises(ValueError):
            format_wine_dll_overrides({"dxgi": "native"})
