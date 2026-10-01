#!/usr/bin/env bash
# Tear down what the setup created (the inverse of deploy --create, bootstrap
# and scripts/up.sh):
#   default         containers, network, volumes, locally built images,
#                   kong/certs/ and kong/konnect.env
#   --keep-volumes  keep the models cache, ollama and studio-data volumes
#   --konnect       also delete the AI Gateway in Konnect (asks first; -y skips)
#                   PAT: ~/.kong/kpat (override: KONNECT_PAT_FILE)
set -euo pipefail
cd "$(dirname "$0")/.."

keep_volumes=0 konnect=0 yes=0
for arg in "$@"; do
  case "$arg" in
    --keep-volumes) keep_volumes=1 ;;
    --konnect) konnect=1 ;;
    -y|--yes) yes=1 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 1 ;;
  esac
done

if command -v docker >/dev/null; then
  down=(down --remove-orphans --rmi local)
  (( keep_volumes )) || down+=(-v)
  docker compose --profile ollama --profile konnect-dp "${down[@]}"
  echo "docker: stack removed$( (( keep_volumes )) && echo ', volumes kept')"
else
  echo "docker: not installed, skipped"
fi

if (( konnect )); then
  gw="${KONNECT_CONTROL_PLANE:-guardrail-service}"
  if (( ! yes )); then
    read -r -p "Delete AI Gateway '$gw' in Konnect? [y/N] " answer
    [[ "$answer" == [yY]* ]] || { echo "konnect: skipped"; konnect=0; }
  fi
  if (( konnect )); then
    pat="${KONNECT_PAT_FILE:-$HOME/.kong/kpat}"
    [[ -r "$pat" ]] || { echo "cannot read $pat (Konnect PAT)" >&2; exit 1; }
    args=(--control-plane "$gw")
    [[ -n "${KONNECT_REGION:-}" ]] && args+=(--region "$KONNECT_REGION")
    (cd studio-api && KONNECT_PAT_FILE="$pat" uv run python -m guardrail_studio.teardown "${args[@]}")
  fi
fi

rm -rf kong/certs
rm -f kong/konnect.env
echo "files: removed kong/certs/ and kong/konnect.env"
