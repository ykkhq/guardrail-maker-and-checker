"""Test doubles shared by studio-api tests."""

import json

import httpx

GW = "gw-1"
BASE = f"/v1/ai-gateways/{GW}"
KINDS = ("model-providers", "models", "policies")


class FakeKonnect:
    """In-memory AI Gateway entities API: POST creates, PUT updates by id, page lists, delete.

    Like Konnect, it refuses to delete a policy that a model still lists.
    """

    def __init__(self, policies=None, models=None, providers=None):
        self.store = {
            "policies": {p["id"]: p for p in (policies or [])},
            "models": {m["id"]: m for m in (models or [])},
            "model-providers": {p["id"]: p for p in (providers or [])},
        }
        self.calls: list[tuple[str, str]] = []
        self._n = 0

    @property
    def policies(self) -> dict:
        return self.store["policies"]

    @property
    def models(self) -> dict:
        return self.store["models"]

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        self.calls.append((method, path))
        assert req.headers["Authorization"] == "Bearer tok"
        body = json.loads(req.content) if req.content else None
        rest = path.removeprefix(BASE + "/").split("/")
        kind = rest[0]
        if not path.startswith(BASE + "/") or kind not in KINDS:
            return httpx.Response(404, json={"message": "not found"})
        items = self.store[kind]
        if len(rest) == 1 and method == "GET":
            assert req.url.params["page[number]"] == "1"
            return httpx.Response(200, json={"data": list(items.values()), "meta": {"page": {}}})
        if len(rest) == 1 and method == "POST":
            if any(e["name"] == body["name"] for e in items.values()):
                return httpx.Response(409, json={"message": "name exists"})
            self._n += 1
            e = body | {"id": f"new-{self._n}"}
            items[e["id"]] = e
            return httpx.Response(201, json=e)
        eid = rest[1]
        if eid not in items:
            return httpx.Response(404, json={"message": "not found"})
        if method == "PUT":
            items[eid] = body | {"id": eid}
            return httpx.Response(200, json=items[eid])
        if method == "DELETE":
            if kind == "policies" and any(items[eid]["name"] in m.get("policies", []) for m in self.models.values()):
                return httpx.Response(400, json={"detail": "another entity references this value"})
            items.pop(eid)
            return httpx.Response(204)
        return httpx.Response(405)
