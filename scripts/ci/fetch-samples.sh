#!/usr/bin/env bash
# Downloads test files too large to keep in the repository, checking each
# against its pinned SHA-256. The tests that need them skip without them,
# unless REQUIRE_PST is set (as in CI).
#
# enron.pst: 71 emails from the EDRM Enron Email Data Set, produced by ZL
# Technologies, Inc., licensed CC BY 3.0 US; copy from the pst-extractor
# project's test data.
set -euo pipefail
dir="$(dirname "$0")/../../services/api/tests/samples/download"
mkdir -p "$dir"
fetch() {
  local name=$1 url=$2 sha=$3
  if [ ! -f "$dir/$name" ] || ! echo "$sha  $dir/$name" | sha256sum -c --quiet - 2>/dev/null; then
    curl -fsSL --retry 4 -o "$dir/$name" "$url"
  fi
  echo "$sha  $dir/$name" | sha256sum -c -
}
fetch enron.pst https://raw.githubusercontent.com/epfromer/pst-extractor/7d08ef9bc7112c23275fa948306c76bba6e24fba/src/__tests__/testdata/enron.pst \
  6152489de3472be6f1f840935d34b6dbf5f62b025dd015929ea9cf66eb5a5b46
