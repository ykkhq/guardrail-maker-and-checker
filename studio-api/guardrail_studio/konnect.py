"""Minimal Konnect client: find/create the control plane and deploy compiled pipelines.

The token is read from KONNECT_PAT_FILE (preferred) or KONNECT_PAT. It is only
ever sent in the Authorization header, never logged or returned.

Deploy is an idempotent upsert: Konnect's PUT needs a UUID, so each entity id
is a uuid5 of its kind and name. Every entity carries the `pipeline:{slug}` tag,
so stale plugins of the pipeline are deleted and nothing else on the control
plane is touched.
"""

from __future__ import annotations

import os
import pathlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from guardrail_studio.compiler import CompiledPipeline

REGIONS = ("us", "eu", "au", "me", "in", "sg")
_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "guardrail-studio.konghq.local")


def entity_id(kind: str, name: str) -> str:
    """Stable id, so redeploying the same pipeline updates the same entities."""
    return str(uuid.uuid5(_NS, f"{kind}/{name}"))


class KonnectError(Exception):
    def __init__(self, method: str, path: str, status: int, body: Any):
        self.status, self.body = status, body
        super().__init__(f"{method} {path} -> {status}: {body}")


def load_token() -> str:
    if path := os.environ.get("KONNECT_PAT_FILE"):
        return pathlib.Path(path).expanduser().read_text().strip()
    if token := os.environ.get("KONNECT_PAT"):
        return token.strip()
    raise RuntimeError("set KONNECT_PAT_FILE or KONNECT_PAT")


@dataclass
class DeployResult:
    control_plane_id: str
    service_id: str
    route_id: str
    plugins: dict[str, str] = field(default_factory=dict)  # instance_name -> id
    deleted: list[str] = field(default_factory=list)


class Konnect:
    def __init__(self, token: str, region: str = "us", transport: httpx.BaseTransport | None = None,
                 timeout: float = 20.0):
        self.region = region
        self._http = httpx.Client(
            base_url=f"https://{region}.api.konghq.com",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Konnect:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = self._http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = r.text
            raise KonnectError(method, path, r.status_code, body)
        return r.json() if r.content else None

    # --- control planes -----------------------------------------------------------

    def find_control_plane(self, name: str) -> dict[str, Any] | None:
        data = self._req("GET", "/v2/control-planes", params={"filter[name][eq]": name})["data"]
        return data[0] if data else None

    def ensure_control_plane(self, name: str, description: str = "") -> dict[str, Any]:
        """Hybrid control plane: the data plane runs self-hosted in Docker."""
        if cp := self.find_control_plane(name):
            return cp
        return self._req("POST", "/v2/control-planes", json={
            "name": name,
            "description": description,
            "cluster_type": "CLUSTER_TYPE_CONTROL_PLANE",
            "auth_type": "pki_client_certs",
            "labels": {"managed-by": "guardrail-studio"},
        })

    def dp_certificates(self, cp_id: str) -> list[dict[str, Any]]:
        page = self._req("GET", f"/v2/control-planes/{cp_id}/dp-client-certificates") or {}
        return page.get("items") or page.get("data") or []  # {} when none are pinned

    def ensure_dp_certificate(self, cp_id: str, cert_pem: str) -> bool:
        """Pin a data-plane client certificate. Returns True if it was newly added."""
        if any(c["cert"].strip() == cert_pem.strip() for c in self.dp_certificates(cp_id)):
            return False
        self._req("POST", f"/v2/control-planes/{cp_id}/dp-client-certificates", json={"cert": cert_pem})
        return True

    # --- core entities -------------------------------------------------------------

    def _core(self, cp_id: str) -> str:
        return f"/v2/control-planes/{cp_id}/core-entities"

    def list_by_tag(self, cp_id: str, kind: str, tag: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        params: dict[str, Any] = {"tags": tag, "size": 1000}
        while True:
            page = self._req("GET", f"{self._core(cp_id)}/{kind}", params=params)
            items += page.get("data", [])
            offset = page.get("offset")
            if not offset:
                return items
            params["offset"] = offset

    def deploy(self, cp_id: str, compiled: CompiledPipeline) -> DeployResult:
        base = self._core(cp_id)
        svc_id = entity_id("service", compiled.service["name"])
        route_id = entity_id("route", compiled.route["name"])
        self._req("PUT", f"{base}/services/{svc_id}", json=compiled.service)
        self._req("PUT", f"{base}/routes/{route_id}", json=compiled.route | {"service": {"id": svc_id}})
        result = DeployResult(control_plane_id=cp_id, service_id=svc_id, route_id=route_id)

        # Remove stale plugins first, so a plugin that moved to a new instance
        # name does not collide with the one-plugin-per-route rule.
        wanted = {entity_id("plugin", p["instance_name"]): p for p in compiled.plugins}
        for p in self.list_by_tag(cp_id, "plugins", compiled.service["tags"][1]):
            if p["id"] not in wanted:
                self._req("DELETE", f"{base}/plugins/{p['id']}")
                result.deleted.append(p.get("instance_name") or p["id"])

        for pid, plugin in wanted.items():
            self._req("PUT", f"{base}/plugins/{pid}", json=plugin | {"route": {"id": route_id}})
            result.plugins[plugin["instance_name"]] = pid
        return result

    def undeploy(self, cp_id: str, slug: str) -> list[str]:
        """Delete everything tagged pipeline:{slug}: plugins, then routes, then services."""
        base, tag, removed = self._core(cp_id), f"pipeline:{slug}", []
        for kind in ("plugins", "routes", "services"):
            for e in self.list_by_tag(cp_id, kind, tag):
                self._req("DELETE", f"{base}/{kind}/{e['id']}")
                removed.append(f"{kind}/{e.get('name') or e.get('instance_name') or e['id']}")
        return removed
