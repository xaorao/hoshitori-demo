#!/bin/bash
# Build the static site for Vercel from this repository: the interface, the packed record under data/, the newest cycle's bundle only
# (a deployment limits its files; the full record is docs/record/record.tar.gz).
set -euo pipefail
cd "$(dirname "$0")/.."
(cd web && npm ci && rm -rf dist && npm run build)
rm -rf .record_work site_out
mkdir .record_work
tar -xzf docs/record/record.tar.gz -C .record_work
python3 scripts/make_vercel_bundle.py web/dist .record_work site_out --keep-bundles 1
rm -f site_out/vercel.json   # the headers are set by the project file at the root of this repository
rm -rf .record_work
echo "site_out: $(find site_out -type f | wc -l) files"
