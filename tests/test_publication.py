from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.check_publication import inspect_blob, inspect_index


class PublicationTests(unittest.TestCase):
    def test_common_secrets_and_private_paths(self):
        samples = {
            "github-token": "gh" + "p_" + "a" * 36,
            "private-key": "-----BEGIN " + "RSA PRIVATE KEY-----",
            "aws-access-key": "AK" + "IA" + "A" * 16,
            "private-host-address": "192" + ".168.1.2",
            "personal-home-path": "/Users" + "/example-private/project/file",
            "url-credentials": "https://account:" + "not-a-real-secret@private.example",
        }
        for expected, content in samples.items():
            with self.subTest(rule=expected):
                self.assertIn(expected, [item.rule for item in inspect_blob("notes.md", content.encode())])

    def test_safe_source_and_synthetic_fixture(self):
        content = "https://user:pass@example.com/x\n/path/to/runtime\n127.0.0.1\n" + "0" * 64
        self.assertEqual(inspect_blob("tests/example.py", content.encode()), [])
        scanner = Path(__file__).resolve().parents[1] / "scripts/check_publication.py"
        self.assertEqual(inspect_blob("scripts/check_publication.py", scanner.read_bytes()), [])

    def test_generated_binary_private_and_symlink_rejected(self):
        for path in (".agent-docs/process.md", "sidecar/build/a.cpp", "sidecar/bin/linux/helper", ".env.local", "video.mp4"):
            with self.subTest(path=path):
                self.assertTrue(inspect_blob(path, b"example"))
        self.assertTrue(inspect_blob("hidden.dat", b"\x00binary"))
        self.assertTrue(inspect_blob("link", b"../private", "120000"))
        self.assertTrue(inspect_blob("large.txt", b"a" * (1024 * 1024 + 1)))

    def test_index_not_worktree_is_scanned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def run(*arguments):
                subprocess.run(["git", "-C", str(root), *arguments], check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            run("init", "--quiet")
            self.assertEqual(inspect_index(root)[2][0].rule, "empty-index-stage-source-first")
            source = root / "example.txt"
            source.write_text("192" + ".168.1.2", encoding="utf-8")
            run("add", "example.txt")
            source.write_text("sanitized working tree", encoding="utf-8")
            count, _, findings = inspect_index(root)
            self.assertEqual(count, 1)
            self.assertEqual(findings[0].rule, "private-host-address")
            run("add", "example.txt")
            self.assertEqual(inspect_index(root)[2], [])
