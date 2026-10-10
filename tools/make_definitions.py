#!/usr/bin/env python3
"""Generate the Cura machine/extruder definitions and quality profiles for the Bambu Lab printers.

Usage: python3 make_definitions.py <plugin dir>
Reads <plugin dir>/bbl/printers.json (from extract_bambu_profiles.py).
"""
import json
import os
import sys

SETTING_VERSION = 23  # Cura 5.8.x

START_GCODE = """;BBL_START_GCODE_BEGIN
; Placeholder. When you export with the Cura-to-Bambu plugin ("Send to Bambu Connect" or saving a
; .gcode.3mf), this block is replaced by Bambu Lab's own start sequence for the printer and nozzle:
; AMS filament load, nozzle wipe, first-layer-area bed levelling, (X1) flow calibration, purge line.
; Keep the BEGIN/END marker lines. The commands below only run if you print a plain .gcode export.
M140 S{material_bed_temperature_layer_0}
M104 S{material_print_temperature_layer_0}
G28
M190 S{material_bed_temperature_layer_0}
M109 S{material_print_temperature_layer_0}
G90
M83
;BBL_START_GCODE_END
"""

END_GCODE = """;BBL_END_GCODE_BEGIN
; Placeholder. Replaced by Bambu Lab's end sequence (unload filament to AMS, park, cool down) on export.
M400
M104 S0
M140 S0
M106 S0
G91
G1 Z5 F600
G90
;BBL_END_GCODE_END
"""

QUALITIES = [  # (quality_type, name, layer height, weight)
    ("extra_fine", "0.08mm Extra Fine", 0.08, -1),
    ("fine", "0.12mm Fine", 0.12, -2),
    ("optimal", "0.16mm Optimal", 0.16, -3),
    ("standard", "0.20mm Standard", 0.20, -4),
    ("draft", "0.24mm Draft", 0.24, -5),
    ("extra_draft", "0.28mm Extra Draft", 0.28, -6),
]

PLATE_OPTIONS = {
    "textured_pei": "Textured PEI Plate",
    "cool_plate": "Cool Plate (Smooth PEI)",
    "engineering_plate": "Engineering Plate",
    "high_temp_plate": "High Temp Plate",
    "supertack_plate": "Cool Plate SuperTack",
}

MODELS = {  # cura id: (name, heated chamber)
    "bambulab_p1s": ("Bambu Lab P1S", False),
    "bambulab_p1p": ("Bambu Lab P1P", False),
    "bambulab_x1c": ("Bambu Lab X1 Carbon", False),
    "bambulab_x1e": ("Bambu Lab X1E", True),
}


def disallowed_areas(machine):
    """Bambu bed_exclude_area ('XxY' strings, origin front-left) -> Cura polygon (origin centre, +y = front)."""
    area = machine.get("bed_exclude_area") or []
    printable = [tuple(float(v) for v in p.split("x")) for p in machine["printable_area"]]
    w = max(p[0] for p in printable)
    d = max(p[1] for p in printable)
    pts = [tuple(float(v) for v in p.split("x")) for p in area]
    if not pts:
        return []
    poly = [[round(x - w / 2, 3), round(d / 2 - y, 3)] for x, y in pts]
    return [poly]


def base_definition(printers):
    m = printers["bambulab_p1s"]["nozzles"]["0.4"]["machine"]
    printable = [tuple(float(v) for v in p.split("x")) for p in m["printable_area"]]
    width = max(p[0] for p in printable)
    depth = max(p[1] for p in printable)
    clearance = float(m.get("extruder_clearance_radius", 57))
    spd_warn = {"maximum_value_warning": "500"}
    acc_warn = {"maximum_value_warning": "20000"}
    overrides = {
        "machine_name": {"default_value": "Bambu Lab printer"},
        "machine_width": {"default_value": width},
        "machine_depth": {"default_value": depth},
        "machine_height": {"default_value": float(m.get("printable_height", 250))},
        "machine_heated_bed": {"default_value": True},
        "machine_center_is_zero": {"default_value": False},
        "machine_gcode_flavor": {"default_value": "RepRap (Marlin/Sprinter)"},
        "machine_start_gcode": {"default_value": START_GCODE},
        "machine_end_gcode": {"default_value": END_GCODE},
        "machine_disallowed_areas": {"default_value": disallowed_areas(m)},
        "machine_head_with_fans_polygon": {"default_value": [[-clearance, clearance], [clearance, clearance],
                                                             [clearance, -clearance], [-clearance, -clearance]]},
        "gantry_height": {"value": "%g" % float(m.get("extruder_clearance_height_to_rod", 34))},
        "machine_max_feedrate_x": {"default_value": float(m.get("machine_max_speed_x", 500))},
        "machine_max_feedrate_y": {"default_value": float(m.get("machine_max_speed_y", 500))},
        "machine_max_feedrate_z": {"default_value": float(m.get("machine_max_speed_z", 20))},
        "machine_max_feedrate_e": {"default_value": float(m.get("machine_max_speed_e", 30))},
        "machine_max_acceleration_x": {"default_value": float(m.get("machine_max_acceleration_x", 20000))},
        "machine_max_acceleration_y": {"default_value": float(m.get("machine_max_acceleration_y", 20000))},
        "machine_max_acceleration_z": {"default_value": float(m.get("machine_max_acceleration_z", 500))},
        "machine_max_acceleration_e": {"default_value": float(m.get("machine_max_acceleration_e", 5000))},
        "machine_acceleration": {"default_value": 10000},
        "machine_max_jerk_xy": {"default_value": float(m.get("machine_max_jerk_x", 9))},
        "machine_max_jerk_z": {"default_value": float(m.get("machine_max_jerk_z", 3))},
        "machine_max_jerk_e": {"default_value": float(m.get("machine_max_jerk_e", 2.5))},
        "machine_nozzle_heat_up_speed": {"default_value": 2.0},
        "machine_nozzle_cool_down_speed": {"default_value": 2.0},
        # Bambu firmware expects relative E, and the Bambu start sequence sets the temperatures itself.
        "relative_extrusion": {"default_value": True, "value": "True"},
        "material_bed_temp_prepend": {"default_value": False},
        "material_print_temp_prepend": {"default_value": False},
        # Bambu-like defaults (all still editable in Cura).
        "layer_height_0": {"value": "max(0.2, layer_height)"},
        "retraction_amount": {"default_value": float(m.get("retraction_length", 0.8))},
        "retraction_speed": {"default_value": float(m.get("retraction_speed", 30)), "maximum_value_warning": "70"},
        "retraction_hop_enabled": {"default_value": True},
        "retraction_hop": {"default_value": float(m.get("z_hop", 0.4))},
        "retraction_min_travel": {"value": "1"},
        # Target speeds follow the max volumetric speed (12 mm^3/s = Bambu's generic PLA, PETG) so Cura's time
        # estimate is realistic; the plugin additionally caps every extrusion move per filament type.
        "speed_print": dict(spd_warn, default_value=150,
                            value="max(30, min(250, round((bbl_max_volumetric_speed if bbl_max_volumetric_speed > 0 "
                                  "else 12) / max(0.01, line_width * layer_height))))"),
        "speed_infill": dict(spd_warn, value="speed_print"),
        "speed_wall": dict(spd_warn, value="speed_print"),
        "speed_wall_0": dict(spd_warn, value="min(200, speed_wall)"),
        "speed_wall_x": dict(spd_warn, value="speed_wall"),
        "speed_topbottom": dict(spd_warn, value="min(200, speed_print)"),
        "speed_support": dict(spd_warn, value="speed_print"),
        "speed_travel": dict(spd_warn, value="speed_print if magic_spiralize else 500"),
        "speed_layer_0": dict(spd_warn, value="50"),
        "speed_travel_layer_0": dict(spd_warn, value="150"),
        "skirt_brim_speed": dict(spd_warn, value="speed_layer_0"),
        "acceleration_enabled": {"default_value": True},
        "acceleration_print": dict(acc_warn, default_value=10000),
        "acceleration_wall": dict(acc_warn, value="acceleration_print"),
        "acceleration_wall_0": dict(acc_warn, value="5000"),
        "acceleration_topbottom": dict(acc_warn, value="2000"),
        "acceleration_travel": dict(acc_warn, value="acceleration_print if magic_spiralize else 10000"),
        "acceleration_layer_0": dict(acc_warn, value="500"),
        "acceleration_travel_layer_0": dict(acc_warn, value="500"),
        "jerk_enabled": {"default_value": False},
        "adhesion_type": {"default_value": "skirt"},
        "infill_sparse_density": {"default_value": 15},
        "cool_min_layer_time": {"default_value": 4},
    }
    settings = {
        "bambu_lab": {
            "label": "Bambu Lab",
            "type": "category",
            "icon": "Printer",
            "description": "Settings used when the Cura-to-Bambu plugin builds the Bambu Lab print file.",
            "children": {
                "bbl_plate_type": {
                    "label": "Build Plate Type",
                    "description": "The build plate on the printer. It is written into the print file (the printer "
                                   "and Bambu Connect check it) and the Textured PEI plate gets Bambu's -0.04 mm "
                                   "Z-offset. Set the bed temperature for this plate in the material settings.",
                    "type": "enum",
                    "options": PLATE_OPTIONS,
                    "default_value": "textured_pei",
                    "settable_per_mesh": False,
                    "settable_per_extruder": False,
                    "settable_per_meshgroup": False,
                },
                "bbl_max_volumetric_speed": {
                    "label": "Max Volumetric Speed",
                    "description": "Highest melt rate the hotend may be asked for, in mm³/s. Every extrusion move that "
                                   "would go faster is slowed down in the exported file, like Bambu Studio does. "
                                   "0 = automatic: Bambu's generic value for the filament type (PLA/PETG 12, ABS 16, "
                                   "ASA 12, PC 16, TPU 3.2). Bambu PLA Basic handles about 21. This also sets the "
                                   "default print speeds.",
                    "unit": "mm³/s",
                    "type": "float",
                    "default_value": 0,
                    "minimum_value": "0",
                    "maximum_value_warning": "35",
                    "settable_per_mesh": False,
                    "settable_per_extruder": False,
                    "settable_per_meshgroup": False,
                },
                "bbl_timelapse": {
                    "label": "Timelapse Snapshots",
                    "description": "Add Bambu's timelapse snapshot command at every layer change. The printer only "
                                   "records a timelapse if you also turn timelapse on when starting the print.",
                    "type": "bool",
                    "default_value": True,
                    "settable_per_mesh": False,
                    "settable_per_extruder": False,
                    "settable_per_meshgroup": False,
                },
            },
        }
    }
    return {
        "version": 2,
        "name": "Bambu Lab Base",
        "inherits": "fdmprinter",
        "metadata": {
            "visible": False,
            "author": "Cura-to-Bambu plugin (unofficial)",
            "manufacturer": "Bambu Lab",
            "file_formats": "application/x-bambu-gcode-3mf;text/x-gcode",
            "has_materials": True,
            "has_machine_quality": True,
            "quality_definition": "bambulab_base",
            "preferred_quality_type": "standard",
            "preferred_material": "generic_pla_175",
            "machine_extruder_trains": {"0": "bambulab_extruder_0"},
            "supports_usb_connection": False,
        },
        "settings": settings,
        "overrides": overrides,
    }


def machine_definition(cura_id, name, heated_chamber, printers):
    overrides = {"machine_name": {"default_value": name}}
    if heated_chamber:
        overrides["machine_heated_build_volume"] = {"default_value": True}
        overrides["build_volume_temperature"] = {"default_value": 0}
    return {
        "version": 2,
        "name": name,
        "inherits": "bambulab_base",
        "metadata": {
            "visible": True,
            "author": "Cura-to-Bambu plugin (unofficial)",
            "manufacturer": "Bambu Lab",
            "bbl_printer_id": cura_id,
            "bbl_model_id": printers[cura_id]["model_id"],
            "quality_definition": "bambulab_base",
        },
        "overrides": overrides,
    }


def extruder_definition():
    return {
        "version": 2,
        "name": "Extruder 1",
        "inherits": "fdmextruder",
        "metadata": {"machine": "bambulab_base", "position": "0"},
        "overrides": {
            "extruder_nr": {"default_value": 0},
            "machine_nozzle_size": {"default_value": 0.4},
            "material_diameter": {"default_value": 1.75},
            "machine_nozzle_offset_x": {"default_value": 0.0},
            "machine_nozzle_offset_y": {"default_value": 0.0},
        },
    }


def quality_cfg(qtype, name, layer_height, weight):
    return ("[general]\ndefinition = bambulab_base\nname = %s\nversion = 4\n\n"
            "[metadata]\nglobal_quality = True\nquality_type = %s\nsetting_version = %d\ntype = quality\nweight = %d\n\n"
            "[values]\nlayer_height = %s\n" % (name, qtype, SETTING_VERSION, weight, layer_height))


def main(plugin_dir):
    with open(os.path.join(plugin_dir, "bbl", "printers.json"), encoding="utf-8") as f:
        printers = json.load(f)["printers"]
    res = os.path.join(plugin_dir, "resources")
    defs = os.path.join(res, "definitions")
    exts = os.path.join(res, "extruders")
    qual = os.path.join(res, "quality", "bambulab")
    for p in (defs, exts, qual):
        os.makedirs(p, exist_ok=True)

    def dump(path, data):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=4)
            f.write("\n")

    dump(os.path.join(defs, "bambulab_base.def.json"), base_definition(printers))
    for cura_id, (name, chamber) in MODELS.items():
        dump(os.path.join(defs, cura_id + ".def.json"), machine_definition(cura_id, name, chamber, printers))
    dump(os.path.join(exts, "bambulab_extruder_0.def.json"), extruder_definition())
    for qtype, name, lh, weight in QUALITIES:
        with open(os.path.join(qual, "bambulab_global_%s.inst.cfg" % qtype), "w", encoding="utf-8", newline="\n") as f:
            f.write(quality_cfg(qtype, name, lh, weight))
    print("definitions written to", res)


if __name__ == "__main__":
    main(sys.argv[1])
