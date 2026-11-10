#!/bin/bash
# Record the terminal evidence of the film under `script -q`: one .log and one .exit per command,
# in public/real/. Nothing is typed by hand into a log; the film reads the logs.
# Needs: repo/.venv (uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
#        -e skills/address-flag-check -e skills/indicator-condition-check), the extracted record in
#        video/public/record, Node, network for the skill and re-settle steps. Never reads .env.
set -u
VIDEO="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$VIDEO")"
REAL="$VIDEO/public/real"
REC="video/public/record"
WORK="${TMPDIR:-/tmp}/hoshitori_term_work"
mkdir -p "$REAL" "$WORK"
cd "$REPO" || exit 1
export ADDRESS_FLAG_CHECK_HOME="$WORK/afc"   # keep the OFAC list copy out of the home folder
export PYTHONDONTWRITEBYTECODE=1

# rec NAME 'shell command': run under script, write NAME.log and NAME.exit (the command's real exit code)
rec() {
  local name="$1" cmd="$2"
  rm -f "$REAL/$name.log" "$REAL/$name.exit"
  script -q "$REAL/$name.log" /bin/bash -c "( $cmd ); echo \$? > '$REAL/$name.exit'" >/dev/null 2>&1 < /dev/null
  # script(1) writes a literal "^D" plus two backspaces at the start when stdin is not a terminal: that terminal artefact is dropped, nothing else is touched
  python3 - "$REAL/$name.log" <<'PY'
import sys
p = sys.argv[1]; b = open(p, "rb").read()
if b.startswith(b"^D\x08\x08"): open(p, "wb").write(b[4:])
PY
  echo "$name: exit $(cat "$REAL/$name.exit" 2>/dev/null || echo '?') ($(wc -c < "$REAL/$name.log") bytes)"
}

CYCLE=$(ls -d "$REC"/bundles/* | head -1)
FORGED=$(python3 -c "import json;print('$REC/'+json.load(open('$REC/forgery.json'))['path'])")
ROOT="$REC/trusted_root.json"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

rec verify_python "echo '\$ python -m hoshitori.cli $CYCLE --trusted-root $ROOT --now $NOW'; .venv/bin/python -m hoshitori.cli '$CYCLE' --trusted-root '$ROOT' --now $NOW | tee '$WORK/python.json'; exit \${PIPESTATUS[0]}"
rec verify_node "echo '\$ node verifier-js/bin/verify_bundle.mjs $CYCLE --trusted-root $ROOT --now $NOW'; node verifier-js/bin/verify_bundle.mjs '$CYCLE' --trusted-root '$ROOT' --now $NOW | tee '$WORK/node.json'; exit \${PIPESTATUS[0]}"
rec diff "echo '\$ diff <(jq -S \"del(.verifier)\" python.json) <(jq -S \"del(.verifier)\" node.json)'; diff <(jq -S 'del(.verifier)' '$WORK/python.json') <(jq -S 'del(.verifier)' '$WORK/node.json') && echo 'reports identical apart from the verifier field'"
rec verify_forgery "echo '\$ python -m hoshitori.cli $FORGED --trusted-root $ROOT --now $NOW'; .venv/bin/python -m hoshitori.cli '$FORGED' --trusted-root '$ROOT' --now $NOW; rc=\$?; echo \"exit \$rc\"; exit \$rc"

A1=0x000000000000000000000000000000000000dEaD
A2=1BitcoinEaterAddressDontSendf59kuE
rec skill_address "echo '\$ address-flag-check update-list'; .venv/bin/address-flag-check update-list | jq -Mc '{action,error,address_strings}'; for a in '$A1 ethereum' '$A2 bitcoin'; do set -- \$a; echo \"\$ address-flag-check check --address \$1 --chain \$2\"; .venv/bin/address-flag-check check --address \$1 --chain \$2 | jq -Mr '.status, .data_mode, .summary.headline, (.summary.key_points[]|\"- \"+.)'; done"
rec skill_envelope "echo '\$ address-flag-check check --address $A1 --chain ethereum'; .venv/bin/address-flag-check check --address $A1 --chain ethereum; echo '\$ address-flag-check check --address $A2 --chain bitcoin'; .venv/bin/address-flag-check check --address $A2 --chain bitcoin"
export COND='{"left":{"indicator":"rsi"},"op":"<","right":{"value":30}}'
rec skill_indicator "echo \"\$ indicator-condition-check check --symbol SOL --condition '\$COND'\"; .venv/bin/indicator-condition-check check --symbol SOL --condition \"\$COND\""
rec resettle "echo '\$ python3 scripts/resettle_published.py $REC'; python3 scripts/resettle_published.py '$REC'"
