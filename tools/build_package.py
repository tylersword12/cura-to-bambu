#!/usr/bin/env python3
"""Build CuraToBambu-<version>.curapackage (drag-and-drop installable into Cura) and a plain .zip.

Usage: python3 build_package.py <plugin dir> <output dir>
"""
import json
import os
import sys
import zipfile

EXCLUDE_DIRS = {"__pycache__"}


def main(plugin_dir, out_dir):
    plugin_dir = os.path.abspath(plugin_dir)
    with open(os.path.join(plugin_dir, "plugin.json"), encoding="utf-8") as f:
        meta = json.load(f)
    version = meta["version"]
    package_json = {
        "package_id": "CuraToBambu",
        "package_type": "plugin",
        "display_name": meta["name"],
        "description": meta["description"],
        "package_version": version,
        "sdk_version": "8.0.0",
        "website": "https://wiki.bambulab.com/en/software/bambu-connect",
        "author": {"author_id": "CuraToBambu", "display_name": "Cura-to-Bambu (community, unofficial)"},
        "tags": ["bambu", "bambu lab", "printer", "output device"],
    }
    os.makedirs(out_dir, exist_ok=True)
    files = []
    for root, dirs, names in os.walk(plugin_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for n in names:
            if n.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(root, n)
            files.append((full, os.path.relpath(full, plugin_dir).replace(os.sep, "/")))

    pkg = os.path.join(out_dir, "CuraToBambu-%s.curapackage" % version)
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("package.json", json.dumps(package_json, indent=4))
        for full, rel in files:
            z.write(full, "files/plugins/CuraToBambu/" + rel)
    plain = os.path.join(out_dir, "CuraToBambu-%s-plugin-folder.zip" % version)
    with zipfile.ZipFile(plain, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in files:
            z.write(full, "CuraToBambu/" + rel)
    print(pkg, os.path.getsize(pkg))
    print(plain, os.path.getsize(plain))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
