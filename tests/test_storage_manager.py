import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.storage_manager import (GiB, inventory, make_cache_room,
    remove_entries, tree_size, validate, DEFAULTS, _current, check_job, file_allowance)


class StorageTests(unittest.TestCase):
    def test_inventory_removal_only_generated_ids_and_busy_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "data/prepared-clips" / ("a" * 64)
            job = root / "temp/dlss-experimental" / ("render-" + "b" * 32)
            for p in (cache, job):
                p.mkdir(parents=True)
                (p / "output.mp4").write_bytes(b"keep-or-delete")
            outside = root / "data/prepared-clips/user-file"
            outside.write_bytes(b"never delete")
            with patch("comfy_dlss_experimental.storage_manager._busy", return_value=False):
                listing = inventory(root / "data", root / "temp")
                self.assertEqual(len(listing["entries"]), 2)
                self.assertEqual(len(listing["entries"][0]["files"]), 1)
                ids = [e["id"] for e in listing["entries"]]
                with self.assertRaises(ValueError):
                    remove_entries([str(outside)], root / "data", root / "temp")
                with patch("comfy_dlss_experimental.storage_manager._busy", return_value=True):
                    with self.assertRaisesRegex(ValueError, "active"):
                        remove_entries(ids, root / "data", root / "temp")
                result = remove_entries(ids, root / "data", root / "temp")
                self.assertEqual(result["reclaimed_bytes"], 28)
                self.assertTrue(outside.exists())
                self.assertFalse(job.exists())
                self.assertFalse(cache.exists())
                with self.assertRaisesRegex(ValueError, "changed"):
                    remove_entries(ids, root / "data", root / "temp")

    def test_lru_evicts_inactive_but_protects_consumers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, active = [root / "prepared-clips" / (letter * 64) for letter in "ab"]
            for p in (old, active):
                p.mkdir(parents=True)
                # Sparse file: large logical length, tiny actual disk allocation.
                with (p / "color.rgba").open("wb") as f:
                    f.truncate(GiB // 2)
            os.utime(old, (1, 1))
            with patch("comfy_dlss_experimental.storage_manager.settings", return_value=DEFAULTS | {"cache_gib": 1}):
                make_cache_room(root, GiB // 4, [active])
                self.assertFalse(old.exists())
                self.assertTrue(active.exists())
                with self.assertRaisesRegex(ValueError, "active"):
                    make_cache_room(root, GiB, [active])

    def test_limits_and_encoder_allowance(self):
        for change in ({"job_gib": 9}, {"free_gib": 0}, {"cache_gib": True}, {"entry_mib": 1000}):
            with self.assertRaises(ValueError):
                validate(DEFAULTS | change)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {"path": root, "budget": 32 * 1024**2, "settings": DEFAULTS, "peak_bytes": 0}
            token = _current.set(state)
            try:
                with patch("comfy_dlss_experimental.storage_manager.shutil.disk_usage") as disk:
                    disk.return_value.free = 10 * GiB
                    self.assertEqual(file_allowance(), 16 * 1024**2)
                    with self.assertRaisesRegex(ValueError, "limit"):
                        check_job(state["budget"] + 1)
                    disk.return_value.free = GiB
                    with self.assertRaisesRegex(ValueError, "reserve"):
                        check_job()
            finally:
                _current.reset(token)

    def test_symlinks_not_listed_or_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "data/prepared-clips"
            parent.mkdir(parents=True)
            other = root / "outside"
            other.mkdir()
            (other / "secret").write_bytes(b"untouched")
            try:
                (parent / ("a" * 64)).symlink_to(other, target_is_directory=True)
            except OSError:
                self.skipTest("symlink privileges unavailable")
            with patch("comfy_dlss_experimental.storage_manager._busy", return_value=False):
                self.assertEqual(inventory(root / "data", root / "temp")["entries"], [])
            self.assertEqual(tree_size(parent), 0)
