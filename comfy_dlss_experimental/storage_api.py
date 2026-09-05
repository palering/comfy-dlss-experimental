from __future__ import annotations

import asyncio
from urllib.parse import urlsplit
from aiohttp import web
from .storage_manager import inventory, remove_entries, save_settings


def register_storage_routes(routes):
    async def intent(request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Expected application/json")
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            raise web.HTTPForbidden(text="Cross-origin storage mutations are not allowed")
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid JSON")
        if not isinstance(body, dict) or body.get("confirm") is not True:
            raise web.HTTPBadRequest(text="Explicit confirmation is required")
        return body

    @routes.get("/dlss-experimental/storage")
    async def list_storage(_request):
        return web.json_response(await asyncio.to_thread(inventory))

    @routes.post("/dlss-experimental/storage/remove")
    async def remove_storage(request):
        body = await intent(request)
        try:
            return web.json_response(await asyncio.to_thread(remove_entries, body.get("ids")))
        except (ValueError, OSError) as error:
            return web.json_response({"error": str(error)}, status=409)

    @routes.post("/dlss-experimental/storage/settings")
    async def update_settings(request):
        body = await intent(request)
        try:
            return web.json_response(await asyncio.to_thread(save_settings, body.get("settings")))
        except (ValueError, OSError) as error:
            return web.json_response({"error": str(error)}, status=409)
