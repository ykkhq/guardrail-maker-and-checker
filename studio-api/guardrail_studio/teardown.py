"""Delete the Konnect AI Gateway created by guardrail_studio.deploy --create.

  KONNECT_PAT_FILE=~/.kong/kpat uv run python -m guardrail_studio.teardown

Deleting the gateway also removes its models, policies, model providers and DP
certificate. If Konnect refuses while entities remain, they are deleted first.
Run by scripts/cleanup.sh --konnect.
"""

from __future__ import annotations

import argparse
import sys

from guardrail_studio.deploy import locate
from guardrail_studio.konnect import Konnect, KonnectError, load_token


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control-plane", default="guardrail-service")
    ap.add_argument("--region")
    args = ap.parse_args(argv)

    token = load_token()
    region, cp = locate(token, args.control_plane, args.region)
    if cp is None:
        print(f"AI Gateway '{args.control_plane}' not found (already gone)")
        return 0
    with Konnect(token, region) as k:
        try:
            k.delete_control_plane(cp["id"])
        except KonnectError as exc:
            if exc.status not in (400, 409):
                raise
            for e in k.purge(cp["id"]):
                print(f"deleted {e}")
            k.delete_control_plane(cp["id"])
    print(f"deleted AI Gateway {cp['name']} ({cp['id']}) in {region}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
