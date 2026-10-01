"""Deploy a pipeline graph to a Konnect AI Gateway.

  KONNECT_PAT_FILE=~/.kong/kpat uv run python -m guardrail_studio.deploy samples/jp-support.json
  ... --find-only       only locate the AI Gateway
  ... --create          create the AI Gateway if missing (in --region)
  ... --undeploy        remove the pipeline's entities
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import httpx

from guardrail_common.graph import PipelineGraph
from guardrail_studio.compiler import CompileError, CompileOptions, compile_pipeline
from guardrail_studio.konnect import REGIONS, Konnect, KonnectError, load_token


def locate(token: str, name: str, region: str | None) -> tuple[str, dict | None]:
    """Return (region, AI Gateway) searching all regions unless one is given."""
    for r in [region] if region else REGIONS:
        try:
            with Konnect(token, r, timeout=10) as k:
                if cp := k.find_control_plane(name):
                    return r, cp
        except KonnectError as exc:
            msg = exc.body.get("message") if isinstance(exc.body, dict) else str(exc.body)[:120]
            print(f"  region {r}: HTTP {exc.status} {msg}", file=sys.stderr)
        except httpx.HTTPError as exc:
            print(f"  region {r}: {type(exc).__name__}", file=sys.stderr)
    return region or "us", None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("graph", nargs="?")
    ap.add_argument("--control-plane", default="guardrail-service")
    ap.add_argument("--region")
    ap.add_argument("--engine-url", default=CompileOptions.engine_url)
    ap.add_argument("--find-only", action="store_true")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--undeploy", action="store_true")
    args = ap.parse_args(argv)

    token = load_token()
    region, cp = locate(token, args.control_plane, args.region)
    with Konnect(token, region) as k:
        if cp is None:
            if not args.create:
                print(f"AI Gateway '{args.control_plane}' not found (use --create --region <r>)")
                return 2
            cp = k.ensure_control_plane(args.control_plane, "Guardrail Studio pipelines")
            print(f"created AI Gateway {cp['name']} ({cp['id']}) in {region}")
        print(f"AI Gateway: {cp['name']} id={cp['id']} region={region} "
              f"endpoint={cp.get('endpoints', {}).get('configuration')}")
        if args.find_only or not args.graph:
            return 0

        graph = PipelineGraph.model_validate(json.loads(pathlib.Path(args.graph).read_text()))
        if args.undeploy:
            for e in k.undeploy(cp["id"], graph.slug):
                print(f"deleted {e}")
            return 0
        try:
            compiled = compile_pipeline(graph, CompileOptions(engine_url=args.engine_url))
        except CompileError as exc:
            print(f"compile failed: {exc}")
            return 1
        for w in compiled.warnings:
            print(f"warning: {w}")
        try:
            res = k.deploy(cp["id"], compiled)
        except KonnectError as exc:
            print(f"deploy failed: {exc}")
            return 1
        print(f"deployed model={res.model_id} provider={res.provider_id} endpoint={compiled.endpoint_path}")
        for name, pid in res.policies.items():
            print(f"  policy {name} = {pid}")
        for name in res.deleted:
            print(f"  removed stale policy {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
