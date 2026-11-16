#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="Archivex"
DMG_NAME="${APP_NAME}.dmg"

export MACOSX_DEPLOYMENT_TARGET=10.15

# Never let an active conda env or stray dyld paths leak into the build.
unset CONDA_PREFIX DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH 2>/dev/null || true

echo "=== ${APP_NAME} build ==="

# --- venv (python.org framework Python only — conda's python3 ships
#     @rpath libs py2app can't bundle, producing the "py2app terminated"
#     crash) ---
find_python() {
    local cand
    for v in 3.13 3.12 3.11 3.10 3.9; do
        cand="/Library/Frameworks/Python.framework/Versions/$v/bin/python3"
        [ -x "$cand" ] && echo "$cand" && return 0
    done
    return 1
}
PYBIN=$(find_python) || {
    echo "ERROR: no usable framework Python found."
    echo "Install one from: https://www.python.org/downloads/macos/"
    exit 1
}
# Recreate the venv if missing OR built from a different interpreter (e.g. a
# stale conda venv, or an old venv from before this preferred python.org) —
# never trust bare 'python3', which resolves to conda here.
VENV_REAL=$( [ -x venv/bin/python3 ] && venv/bin/python3 -c 'import sys; print(sys.executable)' 2>/dev/null || echo none )
PYBIN_REAL=$("$PYBIN" -c 'import sys; print(sys.executable)')
if [ ! -d venv ] || [ "$VENV_REAL" != "$PYBIN_REAL" ]; then
    echo "Creating venv from ${PYBIN}…"
    rm -rf venv
    "$PYBIN" -m venv venv
fi
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# --- icon ---
if [ ! -f icon.icns ]; then
    echo "Generating icon…"
    python3 make_icon.py
fi

# --- backup to versions/ ---
mkdir -p versions
ts=$(date +%Y%m%d_%H%M%S)
cp archivex.py "versions/archivex_${ts}.py"
echo "Backed up to versions/archivex_${ts}.py"

# --- self-test ---
echo "Running self-test…"
python3 archivex.py --self-test

# --- py2app ---
echo "Building .app…"
rm -rf build dist
python3 setup.py py2app 2>&1 | tail -3

# --- verify portability: every @rpath/@loader_path dep must live in the bundle ---
echo "Checking portability…"
app_path="dist/${APP_NAME}.app"
if [ ! -d "$app_path" ]; then
    echo "ERROR: .app not found"
    exit 1
fi
echo "  Bundle exists: $app_path"
missing=0
while IFS= read -r dep; do
    [ -z "$dep" ] && continue
    base="${dep##*/}"
    if ! find "$app_path" -name "$base" 2>/dev/null | grep -q .; then
        echo "  MISSING: $dep"
        missing=1
    fi
done < <(find "$app_path" \( -name '*.so' -o -name '*.dylib' \) -exec otool -L {} \; 2>/dev/null \
         | awk '/^\t@(rpath|loader_path|executable_path)/{print $1}' | sort -u)
if [ "$missing" -ne 0 ]; then
    echo "ERROR: bundle is missing the dylibs listed above — it WILL crash on launch"
    echo "       with the py2app dialog. Likely a conda-built venv; delete venv and rebuild."
    exit 1
fi
echo "  OK: all @rpath dependencies are bundled."

# --- DMG ---
echo "Creating DMG…"
rm -f "$DMG_NAME"
dmg_tmp="dmg_staging"
rm -rf "$dmg_tmp"
mkdir -p "$dmg_tmp"
cp -R "$app_path" "$dmg_tmp/"
ln -s /Applications "$dmg_tmp/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$dmg_tmp" \
    -ov -format UDZO "$DMG_NAME" 2>/dev/null
rm -rf "$dmg_tmp"

# --- portable app ZIP for GitHub Releases ---
ZIP_NAME="${APP_NAME}-1.0.0-macos.zip"
rm -f "$ZIP_NAME"
ditto -c -k --sequesterRsrc --keepParent "$app_path" "$ZIP_NAME"
unzip -t "$ZIP_NAME" >/dev/null

echo ""
echo "=== Build complete ==="
echo "  App: $app_path"
echo "  DMG: $(pwd)/$DMG_NAME"
echo "  ZIP: $(pwd)/$ZIP_NAME"
echo "  Size: $(du -h "$DMG_NAME" | cut -f1)"
