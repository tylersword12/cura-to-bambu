#!/usr/bin/env python3
"""Extract Bambu Lab machine data + G-code templates from a BambuStudio source checkout.

Usage:  python3 extract_bambu_profiles.py <BambuStudio>/resources/profiles/BBL/machine  <out.json>

Resolves `inherits` chains and `include` templates exactly like BambuStudio does, and
writes one compact JSON file the Cura plugin ships with (bbl/printers.json).
"""
import json
import os
import sys

MODELS = {
    # Cura definition id : (BambuStudio printer_model name, short name)
    "bambulab_p1s": ("Bambu Lab P1S", "P1S"),
    "bambulab_p1p": ("Bambu Lab P1P", "P1P"),
    "bambulab_x1c": ("Bambu Lab X1 Carbon", "X1C"),
    "bambulab_x1e": ("Bambu Lab X1E", "X1E"),
}
NOZZLES = ["0.2", "0.4", "0.6", "0.8"]
TEMPLATE_KEYS = ["machine_start_gcode", "machine_end_gcode", "layer_change_gcode",
                 "time_lapse_gcode", "change_filament_gcode"]
MACHINE_KEYS = ["printable_area", "printable_height", "bed_exclude_area", "extruder_clearance_radius",
                "extruder_clearance_height_to_rod", "extruder_clearance_height_to_lid", "scan_first_layer",
                "nozzle_type", "auxiliary_fan", "support_chamber_temp_control", "support_air_filtration",
                "retraction_length", "retraction_speed", "deretraction_speed", "z_hop",
                "machine_max_acceleration_extruding", "machine_max_acceleration_travel",
                "machine_max_speed_x", "machine_max_speed_y", "machine_max_speed_z", "machine_max_speed_e",
                "machine_max_acceleration_x", "machine_max_acceleration_y", "machine_max_acceleration_z",
                "machine_max_acceleration_e", "machine_max_jerk_x", "machine_max_jerk_y", "machine_max_jerk_z",
                "machine_max_jerk_e", "nozzle_height"]


def load_resolved(folder, name, cache={}):
    if name in cache:
        return dict(cache[name])
    with open(os.path.join(folder, name + ".json"), encoding="utf-8") as f:
        data = json.load(f)
    resolved = load_resolved(folder, data["inherits"]) if data.get("inherits") else {}
    for inc in data.get("include", []):
        with open(os.path.join(folder, inc + ".json"), encoding="utf-8") as f:
            inc_data = json.load(f)
        for k, v in inc_data.items():
            if k not in ("name", "type", "from", "instantiation", "inherits"):
                resolved[k] = v
    for k, v in data.items():
        if k != "include":
            resolved[k] = v
    cache[name] = resolved
    return dict(resolved)


def first(v):
    return v[0] if isinstance(v, list) and v else v


def main(folder, out_path):
    out = {"source": "BambuStudio resources/profiles/BBL/machine (AGPL-3.0)", "printers": {}}
    for cura_id, (model, short) in MODELS.items():
        with open(os.path.join(folder, model + ".json"), encoding="utf-8") as f:
            model_json = json.load(f)
        printer = {"printer_model": model, "short_name": short, "model_id": model_json["model_id"], "nozzles": {}}
        for nozzle in NOZZLES:
            name = f"{model} {nozzle} nozzle"
            if not os.path.exists(os.path.join(folder, name + ".json")):
                continue
            cfg = load_resolved(folder, name)
            entry = {"setting_id": cfg.get("setting_id"), "printer_settings_id": name, "templates": {}, "machine": {}}
            for k in TEMPLATE_KEYS:
                entry["templates"][k] = cfg.get(k, "")
            for k in MACHINE_KEYS:
                if k in cfg:
                    entry["machine"][k] = cfg[k] if k in ("printable_area", "bed_exclude_area") else first(cfg[k])
            printer["nozzles"][nozzle] = entry
        out["printers"][cura_id] = printer
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print("wrote", out_path, os.path.getsize(out_path), "bytes")
    for pid, p in out["printers"].items():
        print(pid, p["model_id"], sorted(p["nozzles"]), {k: p["nozzles"]["0.4"]["machine"].get(k) for k in
              ("printable_height", "bed_exclude_area", "scan_first_layer", "extruder_clearance_height_to_rod")})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
