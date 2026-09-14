"""Static safety/packaging contracts; these do not execute Windows CI or MSVC."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build-sdk-worker.yml"


class SDKWorkerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.source = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_only_and_read_only(self):
        trigger = re.search(r"^on:\n(.*?)(?=^\S)", self.source, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(trigger)
        self.assertEqual(trigger.group(1).strip(), "workflow_dispatch:")
        self.assertIn("permissions:\n  contents: read\n", self.source)
        self.assertIn("persist-credentials: false", self.source)
        self.assertIn("runs-on: windows-2022", self.source)
        self.assertNotIn("secrets.", self.source)
        self.assertNotIn("contents: write", self.source)
        self.assertNotIn("gh release", self.source)

    def test_sdk_pinned_and_verified_without_shell_expression_inputs(self):
        self.assertRegex(self.source, r"NGX_SDK_COMMIT: [0-9a-f]{40}\n")
        self.assertRegex(self.source, r"NGX_SDK_ARCHIVE_SHA256: [0-9a-f]{64}\n")
        self.assertIn("https://github.com/NVIDIA/DLSS.git", self.source)
        self.assertIn("git -C $sdk checkout --detach $env:NGX_SDK_COMMIT", self.source)
        self.assertIn("$actualCommit -cne $env:NGX_SDK_COMMIT", self.source)
        self.assertIn("$archiveHash -cne $env:NGX_SDK_ARCHIVE_SHA256", self.source)
        for block in re.findall(r"        run: \|\n((?:          .*\n|\n)+)", self.source):
            self.assertNotIn("${{", block)

    def test_release_runtime_and_cpu_tests_are_required(self):
        self.assertIn("-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL", self.source)
        self.assertIn("--config Release", self.source)
        self.assertIn("ctest --test-dir build-sdk-worker", self.source)
        self.assertIn("--timeout 30 --no-tests=error", self.source)
        self.assertIn("if ($LASTEXITCODE -ne 0) { throw 'CPU contract tests failed' }", self.source)
        self.assertIn("gpu_acceptance_performed = $false", self.source)
        self.assertIn("runtime_loading_or_model_acceptance_performed = $false", self.source)
        self.assertIn("helper_installer_compatible = $false", self.source)

    def test_explicit_owned_file_allowlist_and_upload_paths(self):
        allowed = re.search(r"\$allowed = @\(([^\n]+)\)", self.source)
        self.assertIsNotNone(allowed)
        self.assertEqual(re.findall(r"'([^']+)'", allowed.group(1)), [
            "LICENSE", "build-provenance.json", "caller/nvngx.dll", "comfy-dlss-worker.exe",
        ])
        self.assertIn("caller/Release/nvngx.dll", self.source)
        self.assertIn("Release/comfy-dlss-worker.exe", self.source)
        self.assertIn("Compare-Object -ReferenceObject $allowed -DifferenceObject $actual", self.source)
        upload = self.source.split("      - name: Upload development bundle only\n", 1)[1]
        paths = re.search(r"          path: \|\n((?:            [^\n]+\n)+)", upload)
        self.assertIsNotNone(paths)
        self.assertEqual([line.strip() for line in paths.group(1).splitlines()], [
            "sdk-worker-artifact/comfy-dlss-sdk-worker-windows-x86_64.zip",
            "sdk-worker-artifact/comfy-dlss-sdk-worker-windows-x86_64.zip.sha256",
            "sdk-worker-artifact/build-provenance.json",
        ])
        self.assertIn("if-no-files-found: error", upload)
        self.assertIn("retention-days: 7", upload)

    def test_provenance_has_identity_hashes_and_acceptance_boundary(self):
        for field in ("source_commit", "sdk_commit", "sdk_archive_sha256", "compiler",
                      "runtime_library", "run_id", "run_attempt", "files"):
            self.assertRegex(self.source, rf"\b{field} = ")
        self.assertIn("Get-FileHash -LiteralPath $destination -Algorithm SHA256", self.source)
        self.assertIn("Get-FileHash -LiteralPath $zipPath -Algorithm SHA256", self.source)
        self.assertIn("No GPU/model acceptance or release publication was performed.", self.source)


if __name__ == "__main__":
    unittest.main()
