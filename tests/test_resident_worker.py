import threading
import unittest
from dataclasses import replace

from comfy_dlss_experimental.direct_nr import DirectNRSettings
from comfy_dlss_experimental.resident_worker import ResidentWorker, compatibility_key, persistent_supported, VERIFIED_PAIR


class Session:
    def __init__(self):
        self.healthy = True
        self.run_marker = "owned-test-marker"
        self.cleanup = {}
        self.headers = []
        self.close_count = 0
        self.close_error = False

    def write(self, value):
        self.headers.append(value)

    def close(self):
        self.close_count += 1
        if self.close_error:
            raise RuntimeError("cleanup fixture")
        self.healthy = False
        self.cleanup = {"remaining_owned_processes": 0}

    def cancel(self):
        self.healthy = False


class Trace:
    def __init__(self, name):
        self.id = name
        self.marker = None
        self.record = {}
        self.release_requested = threading.Event()
        self.worker_state = "not_started"

    def set_worker_state(self, state):
        self.worker_state = state


class ResidentTests(unittest.TestCase):
    def setUp(self):
        self.now = 1.0
        self.pool = ResidentWorker(clock=lambda: self.now, background=False)
        self.sessions = []
        self.settings = DirectNRSettings(64, 64, 3)

    def tearDown(self):
        for session in self.sessions:
            session.close_error = False
        entry = self.pool._entry
        if entry and entry["state"] == "running":
            entry["state"] = "idle"
        self.pool.shutdown()

    def factory(self):
        if self.sessions:
            self.assertFalse(self.sessions[-1].healthy, "old worker must close before replacement starts")
        session = Session()
        self.sessions.append(session)
        return session

    def acquire(self, key="same", frames=3, trace=None):
        return self.pool.acquire(key=key, runtime={"preset_path": "/test"},
            settings=replace(self.settings, frame_count=frames), factory=self.factory, idle_seconds=30, trace=trace)

    def complete(self, lease):
        lease.client.index += lease.requested_frames  # lifecycle fixture: protocol validation has its own tests
        return self.pool.finish(lease, complete=True)

    def test_lazy_start_reuses_one_header_and_frame_count_is_per_lease(self):
        self.assertEqual(self.pool.snapshot()["workers"], [])
        self.assertEqual(self.sessions, [])
        first = self.acquire()
        self.assertFalse(first.reused)
        self.assertTrue(self.complete(first)["retained"])
        second = self.acquire(frames=5)
        self.assertTrue(second.reused)
        self.assertIs(first.session, second.session)
        self.assertTrue(self.complete(second)["retained"])
        self.assertEqual(len(second.session.headers), 1)
        self.assertEqual(self.pool.snapshot()["workers"][0]["leases"], 2)

    def test_settings_change_and_switch_to_isolated_release_first(self):
        first = self.acquire()
        self.complete(first)
        second = self.acquire("new-look")
        self.assertFalse(second.reused)
        self.assertEqual(first.session.close_count, 1)
        self.complete(second)
        self.pool.retire_idle()
        self.assertEqual(self.pool.snapshot()["workers"], [])
        self.assertEqual(second.session.close_count, 1)

    def test_idle_timeout_and_age_are_actual_process_release(self):
        lease = self.acquire()
        self.complete(lease)
        self.now += 29
        self.pool.maintain(sample=False)
        self.assertTrue(lease.session.healthy)
        self.now += 1
        self.pool.maintain(sample=False)
        self.assertFalse(lease.session.healthy)
        self.assertEqual(self.pool.snapshot()["last_release"]["state"], "released")

    def test_idle_release_cannot_abort_next_lease(self):
        old = self.acquire(trace=Trace("old"))
        self.complete(old)
        current_trace = Trace("new")
        new = self.acquire(trace=current_trace)
        self.assertEqual(old.worker_id, new.worker_id)
        self.assertFalse(self.pool.request_release(old.worker_id, mode="idle")["accepted"])
        self.assertFalse(self.pool.request_release(old.worker_id, mode="cancel", execution_id="old")["accepted"])
        self.assertFalse(self.pool.request_release(old.worker_id, mode="cancel")["accepted"])
        self.assertFalse(current_trace.release_requested.is_set())
        self.assertTrue(self.pool.request_release(new.worker_id, mode="cancel", execution_id="new")["accepted"])
        self.assertTrue(current_trace.release_requested.is_set())
        self.assertFalse(self.pool.finish(new, complete=False)["retained"])

    def test_after_task_releases_without_cancelling_active_job(self):
        trace = Trace("job")
        lease = self.acquire(trace=trace)
        self.assertTrue(self.pool.request_release(lease.worker_id, mode="after_task", execution_id="job")["accepted"])
        self.assertFalse(trace.release_requested.is_set())
        self.assertFalse(self.complete(lease)["retained"])
        self.assertEqual(trace.worker_state, "released")

    def test_manual_idle_release_stale_id_and_double_return(self):
        lease = self.acquire()
        self.complete(lease)
        with self.assertRaises(RuntimeError):
            self.complete(lease)
        self.assertTrue(self.pool.request_release(lease.worker_id)["accepted"])
        self.pool.maintain(sample=False)
        self.assertEqual(self.pool.snapshot()["workers"], [])
        newer = self.acquire()
        self.assertFalse(self.pool.request_release(lease.worker_id)["accepted"])
        self.complete(newer)

    def test_partial_or_dead_stream_is_not_reused(self):
        lease = self.acquire()
        lease.client.index += 1
        self.assertFalse(self.pool.finish(lease, complete=True)["retained"])
        lease = self.acquire()
        self.complete(lease)
        lease.session.healthy = False
        new = self.acquire()
        self.assertFalse(new.reused)
        self.complete(new)

    def test_cleanup_failure_blocks_replacement_and_is_visible(self):
        lease = self.acquire()
        self.complete(lease)
        lease.session.close_error = True
        with self.assertRaisesRegex(RuntimeError, "cleanup fixture"):
            self.acquire("new")
        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(self.pool.snapshot()["workers"][0]["state"], "cleanup_failed")
        lease.session.close_error = False
        new = self.acquire("new")
        self.complete(new)

    def test_capacity_and_validation_are_bounded(self):
        for idle in (True, 0, 901):
            with self.assertRaises(ValueError):
                self.pool.acquire(key="k", runtime={}, settings=self.settings, factory=self.factory, idle_seconds=idle)
        with self.assertRaises(ValueError):
            self.acquire(frames=65536)
        lease = self.acquire()
        lease.client.index = lease.client.settings.frame_count - 2
        self.pool.finish(lease, complete=False)

    def test_key_excludes_count_but_includes_look_runtime_and_display(self):
        runtime, env = {"runtime_key": "abc"}, {"DISPLAY": ":0"}
        key = compatibility_key(runtime, self.settings, env)
        self.assertEqual(key, compatibility_key(runtime, replace(self.settings, frame_count=9), env))
        self.assertNotEqual(key, compatibility_key(runtime, replace(self.settings, intensity=.4), env))
        self.assertNotEqual(key, compatibility_key({"runtime_key": "new"}, self.settings, env))
        self.assertNotEqual(key, compatibility_key(runtime, self.settings, {"DISPLAY": ":1"}))
        self.assertTrue(persistent_supported(dict(zip(("worker", "nvngx_dlssnr"), VERIFIED_PAIR))))
        self.assertFalse(persistent_supported({"worker": "unknown"}))
