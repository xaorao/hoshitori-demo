# Demo hosting

This repository mirrors the project source so that a static host can build the site from it. `vercel.json` runs `scripts/vercel_build.sh`, which builds the interface in `web/`, unpacks `docs/record/record.tar.gz` under `data/` and keeps the verification bundle of the newest cycle (a deployment limits its files; the full record is in the tarball).

The submitted project is the repository named in the submission form; this one only serves the demo.
