#!/bin/bash
# AdaDo CLI installer — Linux / macOS
# Usage: curl -sL https://adado.diginoz.com.au/install-cli.sh | bash

set -e

ADO_URL="https://adado.diginoz.com.au"
INSTALL_DIR="/usr/local/bin"
SCRIPT_URL="$ADO_URL/ado.py"
FALLBACK_URL="https://github.com/diginoz-com-au/adado-cli/releases/latest/download/ado.py"

echo ""
echo "  Installing ado — AdaDo CLI"
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "  ✗ Python 3 is required. Install it and try again."
    echo "    Ubuntu/Debian: sudo apt install python3"
    echo "    macOS:         brew install python3"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$PY_VER" -lt 10 ]; then
    echo "  ✗ Python 3.10+ required (you have 3.$PY_VER)"
    exit 1
fi

# Download
TMP=$(mktemp)
curl -sL "$SCRIPT_URL" -o "$TMP" 2>/dev/null || curl -sL "$FALLBACK_URL" -o "$TMP" || { echo "  ✗ Download failed"; exit 1; }

# Install
if [ -w "$INSTALL_DIR" ]; then
    cp "$TMP" "$INSTALL_DIR/ado"
    chmod +x "$INSTALL_DIR/ado"
else
    sudo cp "$TMP" "$INSTALL_DIR/ado"
    sudo chmod +x "$INSTALL_DIR/ado"
fi

rm -f "$TMP"

# Create shebang wrapper if needed (for python3 path differences)
if ! head -1 "$INSTALL_DIR/ado" | grep -q python; then
    WRAPPER="#!/usr/bin/env python3
import sys
sys.argv[0] = 'ado'
exec(open('$INSTALL_DIR/ado.py').read())"
fi

echo "  ✓ ado installed to $INSTALL_DIR/ado"
echo ""
echo "  Get started:"
echo "    ado status           — check your Ada connection"
echo "    ado                  — start chatting"
echo "    ado config instance <url>  — point to your Ada instance"
echo ""
