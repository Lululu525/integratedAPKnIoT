#!/usr/bin/env bash
# Generate the compile_commands.json clangd needs to resolve Arduino headers.
#
# arduino-cli knows where the core, the board's headers and ~/Arduino/libraries
# live; clangd is a generic C++ tool and knows none of it. This hands clangd the
# exact command line arduino-cli would use, so the editor and the compiler agree
# on what an include resolves to.
#
# Re-run after installing a library, bumping the esp32 core, or changing the
# board options below.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SKETCH_DIR="$REPO_ROOT/esp32/main"
BUILD_DIR="$REPO_ROOT/esp32/.cdb"

# Must match the flags in README's build command. A different partition scheme
# or CDCOnBoot changes which headers the core exposes.
FQBN="esp32:esp32:esp32s3"
BOARD_OPTIONS="PartitionScheme=custom,CDCOnBoot=cdc"

arduino-cli compile \
    --fqbn "$FQBN" \
    --board-options "$BOARD_OPTIONS" \
    --only-compilation-database \
    --build-path "$BUILD_DIR" \
    "$SKETCH_DIR"

# arduino-cli copies the sketch into the build path before compiling, so every
# sketch entry names a file under .cdb/sketch/ that clangd will never open.
# Point those entries back at the real sources, mapping the preprocessed
# main.ino.cpp onto main.ino so the .ino buffer gets a command too.
python3 - "$BUILD_DIR" "$SKETCH_DIR" <<'PY'
import json
import pathlib
import sys

build_dir, sketch_dir = (pathlib.Path(a) for a in sys.argv[1:3])
db_path = build_dir / "compile_commands.json"
entries = json.loads(db_path.read_text())

for entry in entries:
    old = pathlib.Path(entry["file"])
    if old.parent != build_dir / "sketch":
        continue
    name = old.name[: -len(".cpp")] if old.name.endswith(".ino.cpp") else old.name
    new = sketch_dir / name
    if not new.exists():
        continue
    entry["file"] = str(new)
    entry["directory"] = str(sketch_dir)
    if "command" in entry:
        entry["command"] = entry["command"].replace(str(old), str(new))
    if "arguments" in entry:
        entry["arguments"] = [str(new) if a == str(old) else a for a in entry["arguments"]]

db_path.write_text(json.dumps(entries, indent=2) + "\n")
print(f"rewrote sketch entries in {db_path}")
PY
