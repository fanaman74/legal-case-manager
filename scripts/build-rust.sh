#!/usr/bin/env bash
# Builds the Rust version as a folder you can unzip and run:
#   dist/rust/CaseFileManager/casefiles(.exe) and web/ (the screens)
#   dist/rust/CaseFileManager-rust-windows.zip on Windows
set -euo pipefail
cd "$(dirname "$0")/.."
(cd web && npm ci && npm run build)
(cd rust && cargo build --release --locked)
out=dist/rust/CaseFileManager
rm -rf dist/rust && mkdir -p "$out"
exe=casefiles; [[ "${OS:-}" == "Windows_NT" ]] && exe=casefiles.exe
cp "rust/target/release/$exe" "$out/"
cp -r services/api/app/web "$out/web"
cp rust/README-run.txt "$out/README.txt"
if [[ "${OS:-}" == "Windows_NT" ]]; then
  (cd dist/rust && pwsh -NoProfile -Command "Compress-Archive -Path CaseFileManager -DestinationPath CaseFileManager-rust-windows.zip")
fi
echo "Built $out"
