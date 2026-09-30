"""Test doubles shared by studio-api tests."""

import json

import httpx

from guardrail_studio.konnect import entity_id

CP = "cp-1"
BASE = f"/v2/control-planes/{CP}/core-entities"


class FakeKonnect:
    """In-memory core-entities API: PUT-by-name upsert, tag filtering, delete."""

    def __init__(self, plugins=None):
        self.plugins = {p["id"]: p for p in (plugins or [])}
        self.calls: list[tuple[str, str]] = []
        self._n = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        self.calls.append((method, path))
        assert req.headers["Authorization"] == "Bearer tok"
        body = json.loads(req.content) if req.content else None
        if method == "PUT" and path.startswith(f"{BASE}/services/"):
            return httpx.Response(200, json=body | {"id": path.rsplit("/", 1)[1]})
        if method == "PUT" and path.startswith(f"{BASE}/routes/"):
            assert body["service"] == {"id": entity_id("service", "gs-jp-support")}
            return httpx.Response(200, json=body | {"id": path.rsplit("/", 1)[1]})
        if method == "GET" and path == f"{BASE}/plugins":
            tag = req.url.params["tags"]
            return httpx.Response(200, json={"data": [p for p in self.plugins.values() if tag in p["tags"]]})
        if method == "POST" and path == f"{BASE}/plugins":
            self._n += 1
            p = body | {"id": f"new-{self._n}"}
            self.plugins[p["id"]] = p
            return httpx.Response(201, json=p)
        if method == "PUT" and path.startswith(f"{BASE}/plugins/"):
            pid = path.rsplit("/", 1)[1]
            self.plugins[pid] = body | {"id": pid}
            return httpx.Response(200, json=self.plugins[pid])
        if method == "DELETE" and path.startswith(f"{BASE}/plugins/"):
            self.plugins.pop(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "not found"})
