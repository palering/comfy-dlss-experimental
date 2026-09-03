from __future__ import annotations

import threading
import copy
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any


@dataclass(slots=True)
class PreviewSessionRecord:
    session_id: str
    created_at: float
    public: dict[str, Any]
    sequence: dict[str, Any]
    runtime: dict[str, Any]
    contract: dict[str, Any]
    profile_a: dict[str, Any] | None
    profile_b: dict[str, Any]
    client_id: str | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)


class PreviewSessionRegistry:
    """Per-node preview state with cancellation and monotonic event revisions."""

    def __init__(self, max_sessions: int = 8) -> None:
        self.max_sessions = max_sessions
        self._records: OrderedDict[str, PreviewSessionRecord] = OrderedDict()
        self._lock = threading.RLock()

    def create(
        self,
        *,
        sequence: dict[str, Any],
        runtime: dict[str, Any],
        contract: dict[str, Any],
        profile_a: dict[str, Any] | None,
        profile_b: dict[str, Any],
        start_time: float,
        duration: float,
        preview_scale: int,
        node_id: str = "",
        client_id: str | None = None,
        workflow_id: str = "",
    ) -> PreviewSessionRecord:
        session_id = uuid.uuid4().hex
        created_at = time()
        public = {
            "schema_version": 2,
            "node_id": str(node_id),
            "workflow_id": workflow_id,
            "created_at": created_at,
            "revision": 0,
            "session_id": session_id,
            "state": "blocked" if not runtime.get("ready") else "preparing",
            "start_time": start_time,
            "duration": duration,
            "preview_scale": preview_scale,
            "source": sequence.get("public", {}),
            "runtime": {
                "backend": runtime.get("backend"),
                "runtime_key": runtime.get("runtime_key"),
                "ready": runtime.get("ready", False),
                "errors": runtime.get("errors", []),
            },
            "contract": contract,
            "profile_a": profile_a,
            "profile_b": profile_b,
            "transport": {
                "original_url": None,
                "original_view": None,
                "processed_url": None,
                "processed_view": None,
                "time_origin": 0,
            },
        }
        record = PreviewSessionRecord(
            session_id=session_id,
            created_at=created_at,
            public=public,
            sequence=sequence,
            runtime=runtime,
            contract=contract,
            profile_a=profile_a,
            profile_b=profile_b,
            client_id=client_id,
        )
        with self._lock:
            if node_id:
                for previous in self._records.values():
                    if (previous.public.get("node_id") == str(node_id) and previous.client_id == client_id
                            and previous.public.get("workflow_id") == workflow_id):
                        previous.cancelled.set()
            self._records[session_id] = record
            self._records.move_to_end(session_id)
            while len(self._records) > self.max_sessions:
                _, removed = self._records.popitem(last=False)
                removed.cancelled.set()
        return record

    def list_public(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(record.public) for record in reversed(self._records.values())]

    def update(self, record: PreviewSessionRecord, **fields) -> dict:
        with self._lock:
            record.public.update(fields)
            record.public["revision"] += 1
            return copy.deepcopy(record.public)

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            record = self._records.get(session_id)
            if record is None:
                return False
            record.cancelled.set()
            return True

    def get(self, session_id: str) -> PreviewSessionRecord | None:
        with self._lock:
            return self._records.get(session_id)

    def remove(self, session_id: str) -> bool:
        with self._lock:
            record = self._records.pop(session_id, None)
            if record:
                record.cancelled.set()
            return record is not None


preview_sessions = PreviewSessionRegistry()


def _source_video_view(video: Any) -> dict[str, str] | None:
    """Return a safe Comfy /view descriptor for file-backed VIDEO inputs."""

    if isinstance(video, (str, Path)):
        source = video
    else:
        if video is None or not hasattr(video, "get_stream_source"):
            return None
        try:
            source = video.get_stream_source()
        except (OSError, RuntimeError, ValueError):
            return None
    if not isinstance(source, (str, Path)):
        return None
    try:
        import folder_paths  # type: ignore
    except ImportError:
        return None

    source_path = Path(source).resolve()
    roots = [
        ("input", Path(folder_paths.get_input_directory()).resolve()),
        ("temp", Path(folder_paths.get_temp_directory()).resolve()),
        ("output", Path(folder_paths.get_output_directory()).resolve()),
    ]
    for folder_type, root in roots:
        try:
            relative = source_path.relative_to(root)
        except ValueError:
            continue
        parent = relative.parent.as_posix()
        return {
            "filename": relative.name,
            "subfolder": "" if parent == "." else parent,
            "type": folder_type,
        }
    return None


def send_preview_event(payload: dict[str, Any], client_id: str | None = None) -> None:
    try:
        from server import PromptServer  # type: ignore

        PromptServer.instance.send_sync("dlss.experimental.preview_session", payload, sid=client_id)
    except (ImportError, AttributeError, RuntimeError):
        # Tests and command-line probes do not have a running PromptServer.
        return
