"""Minimal Konnect client: find/create the AI Gateway and deploy compiled pipelines.

The token is read from KONNECT_PAT_FILE (preferred) or KONNECT_PAT. It is only
ever sent in the Authorization header, never logged or returned.

Deploy is an idempotent upsert by name: the AI Gateway API cannot PUT a new
entity under a client-chosen id, so existing entities are looked up by name and
updated, others are created. Every entity carries the `pipeline: {slug}` label,
so stale policies of the pipeline are deleted and nothing else on the AI
Gateway is touched.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

import httpx

from guardrail_studio.compiler import CompiledPipeline

REGIONS = ("us", "eu", "au", "me", "in", "sg")
GATEWAYS = "/v1/ai-gateways"
_PAGE_SIZE = 100


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
    provider_id: str
    model_id: str
    policies: dict[str, str] = field(default_factory=dict)  # policy name -> id
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

    def _pages(self, path: str) -> list[dict[str, Any]]:
        """Every item of a page-numbered AI Gateway list (name filters are ignored server side)."""
        items: list[dict[str, Any]] = []
        number = 1
        while True:
            page = self._req("GET", path, params={"page[number]": number, "page[size]": _PAGE_SIZE}) or {}
            data = page.get("data") or []
            items += data
            if len(data) < _PAGE_SIZE:
                return items
            number += 1

    # --- AI Gateways (the control plane the AI Gateway data plane joins) --------------

    def find_control_plane(self, name: str) -> dict[str, Any] | None:
        return next((g for g in self._pages(GATEWAYS) if g.get("name") == name), None)

    def ensure_control_plane(self, name: str, description: str = "") -> dict[str, Any]:
        """Hybrid AI Gateway: the data plane runs self-hosted in Docker."""
        if gw := self.find_control_plane(name):
            return gw
        return self._req("POST", GATEWAYS, json={
            "name": name,
            "display_name": name,
            "description": description,
            "min_runtime_version": "2.1",
            "labels": {"managed-by": "guardrail-studio"},
        })

    def dp_certificates(self, gw_id: str) -> list[dict[str, Any]]:
        return self._pages(f"{GATEWAYS}/{gw_id}/data-plane-certificates")

    def ensure_dp_certificate(self, gw_id: str, cert_pem: str) -> bool:
        """Pin a data-plane client certificate. Returns True if it was newly added.

        An AI Gateway holds one certificate, so any other one is removed first.
        """
        base = f"{GATEWAYS}/{gw_id}/data-plane-certificates"
        certs = self.dp_certificates(gw_id)
        if any(c.get("cert", "").strip() == cert_pem.strip() for c in certs):
            return False
        for c in certs:
            self._req("DELETE", f"{base}/{c['id']}")
        self._req("POST", base, json={"cert": cert_pem, "title": "guardrail-studio-dp"})
        return True

    # --- models, model providers, policies ----------------------------------------

    def _list(self, gw_id: str, kind: str, slug: str) -> list[dict[str, Any]]:
        """The pipeline's entities of one kind (by label)."""
        items = self._pages(f"{GATEWAYS}/{gw_id}/{kind}")
        return [e for e in items if (e.get("labels") or {}).get("pipeline") == slug]

    def _upsert(self, gw_id: str, kind: str, entity: dict[str, Any], existing: list[dict[str, Any]]) -> str:
        base = f"{GATEWAYS}/{gw_id}/{kind}"
        if found := next((e for e in existing if e["name"] == entity["name"]), None):
            self._req("PUT", f"{base}/{found['id']}", json=entity)
            return found["id"]
        return self._req("POST", base, json=entity)["id"]

    def deploy(self, gw_id: str, compiled: CompiledPipeline) -> DeployResult:
        slug = compiled.slug
        provider_id = self._upsert(gw_id, "model-providers", compiled.provider,
                                   self._list(gw_id, "model-providers", slug))
        existing = self._list(gw_id, "policies", slug)
        policies = {p["name"]: self._upsert(gw_id, "policies", p, existing) for p in compiled.policies}
        model_id = self._upsert(gw_id, "models", compiled.model, self._list(gw_id, "models", slug))
        result = DeployResult(control_plane_id=gw_id, provider_id=provider_id, model_id=model_id, policies=policies)

        # The model no longer lists stale policies, so they can be deleted now.
        for p in existing:
            if p["name"] not in policies:
                self._req("DELETE", f"{GATEWAYS}/{gw_id}/policies/{p['id']}")
                result.deleted.append(p["name"])
        return result

    def undeploy(self, gw_id: str, slug: str) -> list[str]:
        """Delete everything labelled pipeline={slug}: the model, then policies and providers."""
        removed = []
        for kind in ("models", "policies", "model-providers"):
            for e in self._list(gw_id, kind, slug):
                self._req("DELETE", f"{GATEWAYS}/{gw_id}/{kind}/{e['id']}")
                removed.append(f"{kind}/{e['name']}")
        return removed
