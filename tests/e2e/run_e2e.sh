#!/bin/bash
# End-to-end test: real UltiMaker Cura (Linux build) + the plugin, under a virtual X display.
#   run_e2e.sh <extracted Cura AppImage dir> <work dir>
set -e
CURA_DIR="$1"; WORK="$2"; HERE="$(cd "$(dirname "$0")" && pwd)"; PLUGIN="$HERE/../../plugin/CuraToBambu"
rm -rf "$WORK"; mkdir -p "$WORK/home" "$WORK/out" "$WORK/models"
PLUG_DIR="$WORK/home/.local/share/cura/5.8/plugins"
mkdir -p "$PLUG_DIR/CuraToBambu" "$PLUG_DIR/E2EDriver"
cp -r "$PLUGIN" "$PLUG_DIR/CuraToBambu/CuraToBambu"          # same layout a .curapackage install creates
cp -r "$HERE/E2EDriver" "$PLUG_DIR/E2EDriver/E2EDriver"
python3 "$HERE/make_models.py" "$WORK/models"
export HOME="$WORK/home" XDG_CONFIG_HOME="$WORK/home/.config" XDG_DATA_HOME="$WORK/home/.local/share" XDG_CACHE_HOME="$WORK/home/.cache"
export E2E_OUT="$WORK/out" E2E_MODELS="$WORK/models/cube20.stl:$WORK/models/cylinder15.stl"
export E2E_SCENARIOS="${E2E_SCENARIOS:-$(cat "$HERE/scenarios.json")}"
export LIBGL_ALWAYS_SOFTWARE=1 QT_QUICK_BACKEND=software
cd "$CURA_DIR"
timeout 1200 xvfb-run -a -s "-screen 0 1920x1080x24" ./AppRun > "$WORK/cura_stdout.log" 2>&1 || echo "cura exited with $?"
ls -la "$WORK/out"
