"""Tests for GatewayAdmin deploy against a fake Admin API."""

from __future__ import annotations

import httpx

from guardrail_studio.compiler import compile_pipeline
from guardrail_studio.gateway_admin import GatewayAdmin


class FakeAdmin:
    def __init__(self):
        self.services: dict[str, dict] = {}
        self.routes: dict[str, dict] = {}
        self.plugins: dict[str, dict] = {}
        self.workspaces: set[str] = {"default"}
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return f"id-{self._n}"

    def _strip_ws(self, path: str) -> tuple[str | None, str]:
        """Return (workspace, path_without_workspace). Workspace routes stay global."""
        parts = path.strip("/").split("/")
        if parts and parts[0] == "workspaces":
            return None, path
        if parts and parts[0] == "status":
            return None, path
        if len(parts) >= 2 and parts[1] in ("services", "routes", "plugins"):
            return parts[0], "/" + "/".join(parts[1:])
        return None, path

    def handler(self, req: httpx.Request) -> httpx.Response:
        import json

        raw = req.url.path
        method = req.method
        body = req.read()
        payload = json.loads(body) if body else {}
        ws, path = self._strip_ws(raw)

        if path == "/status":
            return httpx.Response(200, json={"database": {"reachable": True}})

        if method == "GET" and raw.startswith("/workspaces/"):
            name = raw.rsplit("/", 1)[-1]
            if name in self.workspaces:
                return httpx.Response(200, json={"name": name})
            return httpx.Response(404, json={"message": "not found"})

        if method == "POST" and path == "/workspaces":
            self.workspaces.add(payload["name"])
            return httpx.Response(201, json={"name": payload["name"]})

        # Entity ops are keyed by workspace + name/id.
        key_prefix = f"{ws or 'default'}:"

        if method == "GET" and path.startswith("/services/") and path.count("/") == 2:
            name = path.rsplit("/", 1)[-1]
            row = self.services.get(key_prefix + name)
            if row:
                return httpx.Response(200, json=row)
            return httpx.Response(404, json={"message": "not found"})

        if method == "POST" and path == "/services":
            sid = self._id()
            row = {**payload, "id": sid}
            self.services[key_prefix + payload["name"]] = row
            return httpx.Response(201, json=row)

        if method == "PATCH" and path.startswith("/services/"):
            name = path.rsplit("/", 1)[-1]
            self.services[key_prefix + name].update(payload)
            return httpx.Response(200, json=self.services[key_prefix + name])

        if method == "DELETE" and path.startswith("/services/") and path.count("/") == 2:
            name = path.rsplit("/", 1)[-1]
            self.services.pop(key_prefix + name, None)
            self.routes = {k: v for k, v in self.routes.items() if v.get("service") != name or v.get("_ws") != ws}
            self.plugins = {k: v for k, v in self.plugins.items() if v.get("service") != name or v.get("_ws") != ws}
            return httpx.Response(204)

        if method == "GET" and "/routes" in path and path.endswith("/routes"):
            svc = path.split("/")[2]
            data = [r for r in self.routes.values() if r.get("service") == svc and r.get("_ws") == ws]
            return httpx.Response(200, json={"data": data})

        if method == "GET" and "/routes/" in path:
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "services":
                rname = parts[3]
                for r in self.routes.values():
                    if r["name"] == rname and r.get("_ws") == ws:
                        return httpx.Response(200, json=r)
                return httpx.Response(404, json={"message": "not found"})

        if method == "POST" and path.endswith("/routes"):
            svc = path.split("/")[2]
            rid = self._id()
            row = {**payload, "id": rid, "service": svc, "_ws": ws}
            self.routes[rid] = row
            return httpx.Response(201, json=row)

        if method == "PATCH" and path.startswith("/routes/"):
            rid = path.rsplit("/", 1)[-1]
            self.routes[rid].update(payload)
            return httpx.Response(200, json=self.routes[rid])

        if method == "DELETE" and path.startswith("/routes/"):
            rid = path.rsplit("/", 1)[-1]
            self.routes.pop(rid, None)
            return httpx.Response(204)

        if method == "GET" and path.endswith("/plugins"):
            svc = path.split("/")[2]
            data = [p for p in self.plugins.values() if p.get("service") == svc and p.get("_ws") == ws]
            return httpx.Response(200, json={"data": data})

        if method == "POST" and path.endswith("/plugins"):
            svc = path.split("/")[2]
            pid = self._id()
            row = {**payload, "id": pid, "service": svc, "_ws": ws}
            self.plugins[pid] = row
            return httpx.Response(201, json=row)

        if method == "PATCH" and path.startswith("/plugins/"):
            pid = path.rsplit("/", 1)[-1]
            self.plugins[pid].update(payload)
            return httpx.Response(200, json=self.plugins[pid])

        if method == "DELETE" and path.startswith("/plugins/"):
            pid = path.rsplit("/", 1)[-1]
            self.plugins.pop(pid, None)
            return httpx.Response(204)

        return httpx.Response(500, json={"path": raw, "method": method})


def test_gateway_deploy_upserts_service_route_plugins(graph_of):
    fake = FakeAdmin()
    compiled = compile_pipeline(graph_of())
    with GatewayAdmin("http://admin", token="t", workspace="guardrail",
                      transport=httpx.MockTransport(fake.handler)) as g:
        res = g.deploy(compiled)
    assert "guardrail:gs-jp-support" in fake.services
    assert "guardrail" in fake.workspaces
    assert any(r["name"] == "gs-jp-support-chat" for r in fake.routes.values())
    assert len(res.plugins) == 3  # ai-proxy-advanced + prompt_guard + datakit
    assert set(p["name"] for p in fake.plugins.values()) == {"ai-proxy-advanced", "ai-prompt-guard", "datakit"}
    assert res.workspace == "guardrail"

    with GatewayAdmin("http://admin", token="t", workspace="guardrail",
                      transport=httpx.MockTransport(fake.handler)) as g:
        g.deploy(compiled)
    assert len(fake.services) == 1
    assert len([p for p in fake.plugins.values() if p["name"] == "datakit"]) == 1


def test_gateway_undeploy_deletes_service(graph_of):
    fake = FakeAdmin()
    compiled = compile_pipeline(graph_of())
    with GatewayAdmin("http://admin", token="t", workspace="guardrail",
                      transport=httpx.MockTransport(fake.handler)) as g:
        g.deploy(compiled)
        removed = g.undeploy("jp-support")
    assert "services/gs-jp-support" in removed
    assert fake.services == {}
    assert fake.routes == {}
    assert fake.plugins == {}
