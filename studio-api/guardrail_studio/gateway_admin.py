"""Kong Gateway Admin API client for deploying compiled pipelines (classic entities).

Upserts Service + Route + plugins (ai-proxy-advanced, datakit, native AI plugins)
by name / instance_name. Does not run deck gateway sync (avoids wiping other demos).

Scoped to a Kong Workspace (KONG_WORKSPACE, default ``guardrail``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from guardrail_studio.compiler.compile import CompiledPipeline
from guardrail_studio.compiler.gateway import DEFAULT_SERVICE_URL, TAG, to_gateway_entities

_WS_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")


class GatewayError(Exception):
    def __init__(self, method: str, path: str, status: int, body: Any):
        self.status, self.body = status, body
        super().__init__(f"{method} {path} -> {status}: {body}")


@dataclass
class GatewayDeployResult:
    service_id: str
    route_id: str
    plugins: dict[str, str] = field(default_factory=dict)  # instance_name -> id
    deleted: list[str] = field(default_factory=list)
    workspace: str = "default"


def load_admin_token() -> str:
    if path := os.environ.get("KONG_ADMIN_TOKEN_FILE"):
        from pathlib import Path
        return Path(path).expanduser().read_text().strip()
    if token := (os.environ.get("KONG_ADMIN_TOKEN") or "").strip():
        return token
    # Local kong-enterprise/docker default.
    return "kongadmin"


def normalize_workspace(name: str | None) -> str:
    ws = (name or "").strip() or "default"
    if not _WS_RE.match(ws):
        raise ValueError(f"invalid Kong workspace name: {ws!r}")
    return ws


class GatewayAdmin:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        workspace: str | None = None,
        service_url: str = DEFAULT_SERVICE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ):
        self.base_url = (base_url or os.environ.get("KONG_ADMIN_URL", "http://localhost:8001")).rstrip("/")
        self.service_url = service_url
        self.workspace = normalize_workspace(
            workspace if workspace is not None else os.environ.get("KONG_WORKSPACE", "guardrail")
        )
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        tok = token if token is not None else load_admin_token()
        if tok:
            headers["Kong-Admin-Token"] = tok
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GatewayAdmin:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _ws(self, path: str) -> str:
        """Prefix Admin path with the active workspace."""
        if not path.startswith("/"):
            path = "/" + path
        return f"/{self.workspace}{path}"

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = self._http.request(method, path, **kw)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = r.text
            raise GatewayError(method, path, r.status_code, body)
        return r.json() if r.content else None

    def _get(self, path: str) -> Any:
        return self._req("GET", path)

    def _pages(self, path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = None
        while True:
            q = {"offset": offset} if offset else {}
            page = self._req("GET", path, params=q) or {}
            out.extend(page.get("data") or [])
            offset = page.get("offset")
            if not offset:
                break
        return out

    def ensure_workspace(self) -> None:
        """Create the workspace if missing. ``default`` always exists."""
        if self.workspace == "default":
            return
        if self._get(f"/workspaces/{self.workspace}"):
            return
        self._req("POST", "/workspaces", json={"name": self.workspace})

    def deploy(self, compiled: CompiledPipeline) -> GatewayDeployResult:
        self.ensure_workspace()
        entities = to_gateway_entities(compiled, service_url=self.service_url)
        svc_body = entities["services"][0]
        plugins_spec = svc_body.pop("plugins")
        routes_spec = svc_body.pop("routes")
        route_body = routes_spec[0]

        name = svc_body["name"]
        existing = self._get(self._ws(f"/services/{name}"))
        if existing:
            self._req("PATCH", self._ws(f"/services/{name}"), json={k: v for k, v in svc_body.items() if k != "name"})
            service_id = existing["id"]
        else:
            service_id = self._req("POST", self._ws("/services"), json=svc_body)["id"]

        route_name = route_body["name"]
        existing_route = self._get(self._ws(f"/services/{name}/routes/{route_name}"))
        if existing_route is None:
            for r in self._pages(self._ws(f"/services/{name}/routes")):
                if r.get("name") == route_name:
                    existing_route = r
                    break
        if existing_route:
            self._req(
                "PATCH",
                self._ws(f"/routes/{existing_route['id']}"),
                json={k: v for k, v in route_body.items() if k != "name"},
            )
            route_id = existing_route["id"]
        else:
            route_id = self._req("POST", self._ws(f"/services/{name}/routes"), json=route_body)["id"]

        want = {p["instance_name"]: p for p in plugins_spec}
        by_instance = {
            p["instance_name"]: p
            for p in self._pages(self._ws(f"/services/{name}/plugins"))
            if p.get("instance_name")
        }

        result = GatewayDeployResult(
            service_id=service_id, route_id=route_id, workspace=self.workspace
        )
        for iname, plugin in want.items():
            body = {
                "name": plugin["name"],
                "instance_name": plugin["instance_name"],
                "tags": plugin["tags"],
                "config": plugin["config"],
            }
            if found := by_instance.get(iname):
                self._req("PATCH", self._ws(f"/plugins/{found['id']}"), json=body)
                result.plugins[iname] = found["id"]
            else:
                created = self._req("POST", self._ws(f"/services/{name}/plugins"), json=body)
                result.plugins[iname] = created["id"]

        for iname, plug in by_instance.items():
            if iname not in want and TAG in (plug.get("tags") or []):
                self._req("DELETE", self._ws(f"/plugins/{plug['id']}"))
                result.deleted.append(iname)
        return result

    def undeploy(self, slug: str) -> list[str]:
        """Remove Service (+ routes/plugins) for this pipeline slug. Idempotent."""
        name = f"gs-{slug}"
        removed: list[str] = []
        svc = self._get(self._ws(f"/services/{name}"))
        if not svc:
            return removed
        for r in self._pages(self._ws(f"/services/{name}/routes")):
            self._req("DELETE", self._ws(f"/routes/{r['id']}"))
            removed.append(f"routes/{r.get('name') or r['id']}")
        for p in self._pages(self._ws(f"/services/{name}/plugins")):
            self._req("DELETE", self._ws(f"/plugins/{p['id']}"))
            removed.append(f"plugins/{p.get('instance_name') or p['id']}")
        self._req("DELETE", self._ws(f"/services/{name}"))
        removed.append(f"services/{name}")
        return removed

    def ready(self) -> bool:
        try:
            r = self._http.get("/status", timeout=3)
            return r.status_code < 400
        except httpx.HTTPError:
            return False
