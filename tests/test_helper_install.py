import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from install import install_archive, https_url
from comfy_dlss_experimental.helper_artifacts import helper_names, find_helper, sha256


class HelperInstallTests(unittest.TestCase):
    def make_archive(self, directory, target="linux-x86_64", version="test-v1", extra=None, bad_hash=False):
        files = {name: b"helper" for name in helper_names(target).values()}
        files["THIRD_PARTY_NOTICES.txt"] = b"notices"
        manifest = {"schema_version": 1, "target": target, "version": version,
                    "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
        if bad_hash:
            manifest["files"][next(iter(files))] = "0" * 64
        files["manifest.json"] = json.dumps(manifest).encode()
        if extra:
            files.update(extra)
        archive = directory / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, value in files.items():
                bundle.writestr(name, value)
        return archive

    def test_install_idempotent_and_locator_verifies_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            destination = install_archive(archive, sha256(archive), target="linux-x86_64", install_root=root / "sidecar/bin")
            self.assertEqual(install_archive(archive, sha256(archive), target="linux-x86_64", install_root=root / "sidecar/bin"), destination)
            with patch("comfy_dlss_experimental.helper_artifacts.REPO", root), \
                 patch("comfy_dlss_experimental.helper_artifacts.target_platform", return_value="linux-x86_64"):
                self.assertEqual(find_helper("nvof"), destination / "dlss-nvof-helper")
                (destination / "dlss-nvof-helper").write_bytes(b"tampered")
                with self.assertRaises(ValueError):
                    find_helper("nvof")
            with self.assertRaises(FileExistsError):
                install_archive(archive, sha256(archive), target="linux-x86_64", install_root=root / "sidecar/bin")

    def test_wrong_checksum_target_traversal_and_extra_dll_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for options in ({"extra": {"../escape": b"x"}}, {"extra": {"nvngx.dll": b"x"}},
                            {"version": "../escape"}, {"target": "windows-x86_64"}, {"bad_hash": True}):
                archive = self.make_archive(root, **options)
                with self.assertRaises(ValueError):
                    install_archive(archive, sha256(archive), target="linux-x86_64", install_root=root / "bin")
                self.assertFalse((root / "bin/linux-x86_64/active.json").exists())
            archive = self.make_archive(root)
            with self.assertRaises(ValueError):
                install_archive(archive, "0" * 64, target="linux-x86_64", install_root=root / "bin")

    def test_download_requires_https_no_credentials(self):
        for value in ("http://example.com/x", "file:///tmp/x", "https://user:pass@example.com/x"):
            with self.assertRaises(ValueError):
                https_url(value)
        self.assertEqual(https_url("https://example.com/x"), "https://example.com/x")
