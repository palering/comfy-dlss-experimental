from __future__ import annotations

from aiohttp import web
import asyncio

from .preview import preview_sessions

_routes_registered = False


def register_preview_routes() -> None:
    global _routes_registered
    if _routes_registered:
        return
    try:
        from server import PromptServer  # type: ignore
    except ImportError:
        return

    instance = getattr(PromptServer, "instance", None)
    if instance is None:
        return
    routes = instance.routes

    @routes.get("/dlss-experimental/host")
    async def host_platform(_request):
        import platform
        return web.json_response({"platform": platform.system().lower()})

    @routes.get("/dlss-experimental/executions")
    async def list_executions(_request):
        from .execution_log import execution_status
        return web.json_response(await asyncio.to_thread(execution_status, include_gpu=True))

    @routes.get("/dlss-experimental/preview/sessions")
    async def list_preview_sessions(_request):
        return web.json_response({"sessions": preview_sessions.list_public()})

    @routes.post("/dlss-experimental/executions/{execution_id}/release-worker")
    async def release_worker(request):
        from .execution_log import request_worker_release
        # Explicit JSON intent avoids treating a cross-site HTML form as consent.
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Expected application/json")
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid JSON")
        if not isinstance(body, dict) or body.get("confirm_terminate") is not True:
            raise web.HTTPBadRequest(text="Explicit task termination confirmation is required")
        result = request_worker_release(request.match_info["execution_id"])
        return web.json_response(result, status=202 if result["accepted"] else 409)

    @routes.get("/dlss-experimental/preview/sessions/{session_id}")
    async def get_preview_session(request):
        record = preview_sessions.get(request.match_info["session_id"])
        if record is None:
            raise web.HTTPNotFound(text="Unknown preview session")
        return web.json_response(record.public)

    @routes.post("/dlss-experimental/workers/{worker_id}/release")
    async def release_resident_worker(request):
        from .resident_worker import resident_worker
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Expected application/json")
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid JSON")
        if not isinstance(body, dict) or body.get("mode") not in {"idle", "cancel", "after_task"}:
            raise web.HTTPBadRequest(text="Expected idle, cancel or after_task release mode")
        if body["mode"] == "cancel" and body.get("confirm_terminate") is not True:
            raise web.HTTPBadRequest(text="Task cancellation requires confirmation")
        result = resident_worker.request_release(request.match_info["worker_id"],
            mode=body["mode"], execution_id=body.get("execution_id"))
        return web.json_response(result, status=202 if result["accepted"] else 409)

    @routes.delete("/dlss-experimental/preview/sessions/{session_id}")
    async def delete_preview_session(request):
        removed = preview_sessions.remove(request.match_info["session_id"])
        return web.json_response({"removed": removed})

    @routes.post("/dlss-experimental/preview/sessions/{session_id}/cancel")
    async def cancel_preview_session(request):
        if not preview_sessions.cancel(request.match_info["session_id"]):
            raise web.HTTPNotFound(text="Unknown preview session")
        return web.json_response({"cancel_requested": True})

    _routes_registered = True
