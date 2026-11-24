#!/usr/bin/env bash
# Downloads PDFium (the PDF engine from Chrome, prebuilt by
# github.com/bblanchon/pdfium-binaries), checks it against a pinned hash, and
# puts the library and its licence in the folder given:
#   get-pdfium.sh <folder>  ->  <folder>/pdfium.dll (Windows) or libpdfium.so
set -euo pipefail
dest=${1:?usage: get-pdfium.sh <folder>}
version="chromium%2F8179"
if [[ "${OS:-}" == "Windows_NT" ]]; then
  pkg=pdfium-win-x64.tgz; sha=836a77c9053f624839c66010cddea7b675628a36b8831d2330178bb169d241b6; lib=bin/pdfium.dll
else
  pkg=pdfium-linux-x64.tgz; sha=b4766a6bf2856efbcc6c3cc7d6ae9022bdc6bb36ddc1acdd92d1f1873f773f4b; lib=lib/libpdfium.so
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -sSLf --retry 3 -o "$tmp/$pkg" "https://github.com/bblanchon/pdfium-binaries/releases/download/$version/$pkg"
echo "$sha  $tmp/$pkg" | sha256sum -c --quiet -
mkdir -p "$tmp/x" "$dest"
tar xzf "$tmp/$pkg" -C "$tmp/x"
cp "$tmp/x/$lib" "$dest/"
cp "$tmp/x/LICENSE" "$dest/PDFIUM-LICENSE.txt"
echo "PDFium in $dest"
