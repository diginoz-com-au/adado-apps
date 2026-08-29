#!/usr/bin/env bash
# AdaDo CLI release builder
# Produces standalone binaries for Linux and Windows via PyInstaller
# Usage: ./scripts/build-release.sh [version]
set -euo pipefail

VERSION=${1:-$(cat "$(dirname "$0")/../VERSION" | tr -d '[:space:]')}
CLI_SRC="$(dirname "$0")/../cli/ado.py"
OUT="$(dirname "$0")/../dist"

echo "==> Building AdaDo CLI v${VERSION}"
mkdir -p "$OUT"

# Check PyInstaller available
if ! command -v pyinstaller &>/dev/null; then
  echo "Installing PyInstaller…"
  pip install --quiet pyinstaller
fi

# Linux binary
echo "  [linux] Building…"
pyinstaller \
  --onefile \
  --name "ado" \
  --distpath "$OUT/linux" \
  --workpath /tmp/pyinstaller-work \
  --specpath /tmp/pyinstaller-spec \
  "$CLI_SRC"
echo "  [linux] → dist/linux/ado"

# Windows binary (cross-compile via wine or on Windows runner)
if command -v wine &>/dev/null; then
  echo "  [windows] Building via wine…"
  wine pyinstaller \
    --onefile \
    --name "ado.exe" \
    --distpath "$OUT/windows" \
    --workpath /tmp/pyinstaller-work-win \
    --specpath /tmp/pyinstaller-spec-win \
    "$CLI_SRC" || echo "  [windows] Skipped (wine build failed)"
else
  echo "  [windows] Skipping binary build (no wine). Upload cli/ado.py as the Windows installer."
fi

# Also copy raw Python script (platform-agnostic)
cp "$CLI_SRC" "$OUT/ado.py"
echo "  [any]    → dist/ado.py (requires Python 3.8+)"

# Create SHA256 checksums
cd "$OUT"
sha256sum linux/ado 2>/dev/null > SHA256SUMS || true
sha256sum ado.py >> SHA256SUMS

echo ""
echo "==> Release v${VERSION} ready in dist/"
echo "    Upload these to GitHub Releases as assets:"
ls -lh linux/ado 2>/dev/null || true
ls -lh ado.py
echo ""
echo "Tag and release:"
echo "  git tag v${VERSION}"
echo "  git push origin v${VERSION}"
echo "  gh release create v${VERSION} dist/linux/ado dist/ado.py --title \"AdaDo v${VERSION}\" --notes-file CHANGELOG.md"
