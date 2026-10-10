# Copyright (c) 2026. Released under the terms of the LGPLv3 or higher.
"""Convert Cura (Marlin-flavour) G-code into Bambu Lab printer G-code.

What it does:
  * swaps the marked start/end G-code blocks for Bambu's own machine templates
    (AMS load, nozzle wipe, bed levelling of the first-layer area, flow calibration, ...)
  * adds Bambu layer-change commands (M73 L / M991) so the printer screen and app show layer progress
  * adds M73 P/R progress + remaining-time updates
  * optionally adds the timelapse snapshot block
  * writes the Bambu HEADER_BLOCK the firmware and Bambu Connect read

Pure Python (no Cura imports) so it can be unit-tested outside Cura.
"""
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .template import render

START_BEGIN = ";BBL_START_GCODE_BEGIN"
START_END = ";BBL_START_GCODE_END"
END_BEGIN = ";BBL_END_GCODE_BEGIN"
END_END = ";BBL_END_GCODE_END"

_WORD = re.compile(r"([A-Z])(-?\d*\.?\d+)")
_F_WORD = re.compile(r"(?<=\s)F-?\d*\.?\d+|(?<=^G[01])F-?\d*\.?\d+")

# Max volumetric speeds (mm^3/s) of Bambu's "Generic <type>" filament profiles. Like BambuStudio, the
# converter slows any extrusion move that would exceed this (Cura has no such limit), and the start
# G-code's purge-line speed is derived from it.
MAX_VOLUMETRIC_SPEED = {
    "PLA": 12.0, "PLA-CF": 12.0, "PETG": 12.0, "PETG-CF": 12.0, "PCTG": 6.0, "ABS": 16.0, "ASA": 12.0,
    "TPU": 3.2, "TPU-AMS": 10.5, "PVA": 16.0, "BVOH": 8.0, "PA": 12.0, "PA-CF": 8.0, "PC": 16.0,
    "HIPS": 8.0, "PP": 12.0, "PE": 8.0, "PPS": 4.0, "PPA-CF": 6.0, "EVA": 12.0, "PHA": 6.0,
}
DEFAULT_MAX_VOLUMETRIC_SPEED = 12.0


def max_volumetric_speed(filament_type: str, override: float = 0.0) -> float:
    if override and override > 0:
        return float(override)
    return MAX_VOLUMETRIC_SPEED.get((filament_type or "").upper(), DEFAULT_MAX_VOLUMETRIC_SPEED)


@dataclass
class PrintSettings:
    """Everything the converter needs to know about the print (filled in from Cura's settings)."""
    nozzle_diameter: float = 0.4
    nozzle_temp_initial: float = 220
    nozzle_temp: float = 220
    bed_temp_initial: float = 55
    bed_temp: float = 55
    filament_type: str = "PLA"
    bed_type: str = "Textured PEI Plate"
    layer_height: float = 0.2
    outer_wall_speed: float = 200
    outer_wall_line_width: float = 0.42
    default_acceleration: float = 10000
    outer_wall_acceleration: float = 5000
    retraction_length: float = 0.8
    chamber_temperature: float = 0
    filament_diameter: float = 1.75
    filament_density: float = 1.24
    timelapse: bool = True
    max_volumetric_speed: float = 0.0  # mm^3/s; 0 = Bambu's generic value for the filament type
    generator: str = "Cura"


@dataclass
class GcodeAnalysis:
    layer_count: int = 0
    layer_z: List[Optional[float]] = field(default_factory=list)
    layer_elapsed: List[float] = field(default_factory=list)  # Cura ;TIME_ELAPSED at the END of each layer
    first_layer_min: Tuple[float, float] = (0.0, 0.0)
    first_layer_max: Tuple[float, float] = (0.0, 0.0)
    max_z: float = 0.0
    filament_mm: float = 0.0
    total_time: float = 0.0
    absolute_e: bool = False
    has_start_markers: bool = False
    has_end_markers: bool = False

    @property
    def first_layer_size(self) -> Tuple[float, float]:
        return (self.first_layer_max[0] - self.first_layer_min[0], self.first_layer_max[1] - self.first_layer_min[1])

    def filament_volume_cm3(self, diameter: float) -> float:
        return self.filament_mm * math.pi * (diameter / 2) ** 2 / 1000.0

    def filament_weight_g(self, diameter: float, density: float) -> float:
        return self.filament_volume_cm3(diameter) * density


def _strip_comment(line: str) -> str:
    i = line.find(";")
    return (line if i < 0 else line[:i]).strip()


def analyze(lines: List[str]) -> GcodeAnalysis:
    """Walk Cura's G-code once and collect what the Bambu templates and metadata need."""
    a = GcodeAnalysis()
    relative_e = False
    x = y = z = 0.0
    e = 0.0
    in_print = False  # between the start and end G-code blocks
    layer_idx = -1
    fl_min = [math.inf, math.inf]
    fl_max = [-math.inf, -math.inf]
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            if line.startswith(START_BEGIN):
                a.has_start_markers = True
            elif line.startswith(START_END):
                in_print = True
            elif line.startswith(END_BEGIN):
                a.has_end_markers = True
                in_print = False
            elif line.startswith(";LAYER:"):
                in_print = True if not a.has_start_markers else in_print
                layer_idx += 1
                a.layer_z.append(None)
                a.layer_elapsed.append(a.layer_elapsed[-1] if a.layer_elapsed else 0.0)
            elif line.startswith(";TIME_ELAPSED:") and layer_idx >= 0:
                try:
                    a.layer_elapsed[layer_idx] = float(line.split(":", 1)[1])
                except ValueError:
                    pass
            elif line.startswith(";TIME:"):
                try:
                    a.total_time = float(line.split(":", 1)[1])
                except ValueError:
                    pass
            continue
        code = _strip_comment(line)
        if not code:
            continue
        cmd = code.split(None, 1)[0].upper()
        if cmd == "M82":
            relative_e = False
        elif cmd == "M83":
            relative_e = True
        elif cmd == "G92":
            for letter, val in _WORD.findall(code.upper()):
                if letter == "E":
                    e = float(val)
        elif cmd in ("G0", "G1", "G2", "G3"):
            words = dict(_WORD.findall(code.upper()[len(cmd):]))
            nx = float(words["X"]) if "X" in words else x
            ny = float(words["Y"]) if "Y" in words else y
            nz = float(words["Z"]) if "Z" in words else z
            de = 0.0
            if "E" in words:
                ev = float(words["E"])
                if in_print and not relative_e and layer_idx >= 0:
                    a.absolute_e = True  # extruding in absolute mode during the print itself
                de = ev if relative_e else ev - e
                e = e + ev if relative_e else ev
            if in_print:
                a.filament_mm += de
                if de > 0 and (nx != x or ny != y):
                    a.max_z = max(a.max_z, nz)
                    if layer_idx >= 0 and a.layer_z[layer_idx] is None:
                        a.layer_z[layer_idx] = nz  # print height = Z of the layer's first extrusion (not a Z-hop)
                    if layer_idx == 0:
                        for px, py in ((x, y), (nx, ny)):
                            fl_min[0], fl_min[1] = min(fl_min[0], px), min(fl_min[1], py)
                            fl_max[0], fl_max[1] = max(fl_max[0], px), max(fl_max[1], py)
            x, y, z = nx, ny, nz
    a.layer_count = layer_idx + 1
    prev = 0.0
    for i, lz in enumerate(a.layer_z):  # layers without their own Z move keep the previous height
        a.layer_z[i] = prev = lz if lz is not None else prev
    if math.isfinite(fl_min[0]):
        a.first_layer_min = (fl_min[0], fl_min[1])
        a.first_layer_max = (fl_max[0], fl_max[1])
    if not a.total_time and a.layer_elapsed:
        a.total_time = a.layer_elapsed[-1]
    return a


def limit_volumetric_speed(lines: List[str], a: GcodeAnalysis, max_vol: float, filament_diameter: float
                           ) -> Tuple[List[str], int, float]:
    """Slow down extrusion moves whose volumetric flow (mm^3/s) exceeds max_vol, like BambuStudio's
    'max volumetric speed'. Returns (lines, number of moves slowed, seconds added). Also updates
    a.layer_elapsed / a.total_time so progress and time estimates stay right."""
    if max_vol <= 0:
        return lines, 0, 0.0
    area = math.pi * (filament_diameter / 2.0) ** 2
    out: List[str] = []
    relative_e = False
    x = y = e = 0.0
    f_cura = 0.0      # feedrate Cura asked for (modal)
    f_out = None      # feedrate currently in effect on the printer
    in_print = not a.has_start_markers
    layer_idx = -1
    limited = 0
    added = 0.0
    added_by_layer = [0.0] * max(a.layer_count, 1)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(";"):
            if stripped.startswith(START_END):
                in_print = True
            elif stripped.startswith(END_BEGIN):
                in_print = False
            elif stripped.startswith(";LAYER:"):
                layer_idx += 1
            out.append(line)
            continue
        code = _strip_comment(stripped)
        cmd = code.split(None, 1)[0].upper() if code else ""
        if cmd == "M82":
            relative_e = False
        elif cmd == "M83":
            relative_e = True
        elif cmd == "G92":
            for letter, val in _WORD.findall(code.upper()):
                if letter == "E":
                    e = float(val)
        if cmd not in ("G0", "G1"):
            out.append(line)
            continue
        words = dict(_WORD.findall(code.upper()[len(cmd):]))
        if "F" in words:
            f_cura = float(words["F"])
        nx = float(words["X"]) if "X" in words else x
        ny = float(words["Y"]) if "Y" in words else y
        de = 0.0
        if "E" in words:
            ev = float(words["E"])
            de = ev if relative_e else ev - e
            e = e + ev if relative_e else ev
        dist = math.hypot(nx - x, ny - y)
        x, y = nx, ny
        want = f_cura
        if in_print and layer_idx >= 0 and de > 0 and dist > 1e-6 and f_cura > 0:
            vol = de * area / dist * f_cura / 60.0
            if vol > max_vol * 1.001:
                want = math.floor(max_vol * dist / (de * area) * 60.0)
                limited += 1
                dt = dist / want * 60.0 - dist / f_cura * 60.0
                added += dt
                if layer_idx < len(added_by_layer):
                    added_by_layer[layer_idx] += dt
        fmt = "%d" % want if float(want).is_integer() else "%.1f" % want
        if "F" in words:
            if want != f_cura:  # this move was slowed: rewrite its F word
                line = _F_WORD.sub("F" + fmt, line, count=1)
        elif want != f_out and want > 0:  # restore/set the modal feedrate the printer should use
            head, sep, comment = line.partition(";")
            line = head.rstrip() + " F" + fmt + ((" ;" + comment) if sep else "")
        f_out = want
        out.append(line)
    if added > 0:
        cumulative = 0.0
        for i in range(min(len(a.layer_elapsed), len(added_by_layer))):
            cumulative += added_by_layer[i]
            a.layer_elapsed[i] += cumulative
        a.total_time += added
    return out, limited, added


def outer_wall_volumetric_speed(s: PrintSettings) -> float:
    """BambuStudio's formula: outer wall speed x Slic3r flow cross-section, capped by the filament max."""
    w, h = s.outer_wall_line_width, s.layer_height
    area = (w - h * (1.0 - 0.25 * math.pi)) * h
    vol = s.outer_wall_speed * area
    return round(min(vol, max_volumetric_speed(s.filament_type, s.max_volumetric_speed)), 4)


def template_variables(machine: Dict[str, Any], s: PrintSettings, a: GcodeAnalysis) -> Dict[str, Any]:
    bed0 = int(round(s.bed_temp_initial))
    return {
        "bed_temperature_initial_layer_single": bed0,
        "bed_temperature_initial_layer": [bed0],
        "bed_temperature": [int(round(s.bed_temp))],
        "nozzle_temperature_initial_layer": [int(round(s.nozzle_temp_initial))],
        "nozzle_temperature": [int(round(s.nozzle_temp))],
        "filament_type": [s.filament_type],
        "initial_extruder": 0,
        "initial_no_support_extruder": 0,
        "initial_tool": 0,
        "flush_volumetric_speeds": [0],  # 0 = firmware default, same as Bambu's generic filament profiles
        "flush_temperatures": [0],
        "outer_wall_volumetric_speed": outer_wall_volumetric_speed(s),
        "first_layer_print_min": [round(a.first_layer_min[0], 3), round(a.first_layer_min[1], 3)],
        "first_layer_print_max": [round(a.first_layer_max[0], 3), round(a.first_layer_max[1], 3)],
        "first_layer_print_size": [round(a.first_layer_size[0], 3), round(a.first_layer_size[1], 3)],
        "curr_bed_type": s.bed_type,
        "scan_first_layer": str(machine.get("scan_first_layer", "0")) == "1",
        "default_acceleration": int(round(s.default_acceleration)),
        "outer_wall_acceleration": int(round(s.outer_wall_acceleration)),
        "retraction_length": [s.retraction_length],
        "overall_chamber_temperature": int(round(s.chamber_temperature)),
        "max_layer_z": round(a.max_z, 3),
        "total_layer_count": a.layer_count,
        "timelapse_type": 0,  # "traditional" timelapse (no wipe tower)
        "layer_num": 0,
        "layer_z": 0.0,
    }


def _fmt_time(seconds: float) -> str:
    seconds = int(round(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    parts = (["%dd" % d] if d else []) + (["%dh" % h] if h or d else []) + ["%dm" % m, "%ds" % sec]
    return " ".join(parts)


def header_block(s: PrintSettings, a: GcodeAnalysis) -> List[str]:
    return [
        "; HEADER_BLOCK_START",
        "; generated by %s with the Cura-to-Bambu plugin" % s.generator,
        "; model printing time: %s; total estimated time: %s" % (_fmt_time(a.total_time), _fmt_time(a.total_time)),
        "; total layer number: %d" % a.layer_count,
        "; total filament length [mm] : %.2f" % a.filament_mm,
        "; total filament volume [cm^3] : %.2f" % a.filament_volume_cm3(s.filament_diameter),
        "; total filament weight [g] : %.2f" % a.filament_weight_g(s.filament_diameter, s.filament_density),
        "; filament_density: %s" % s.filament_density,
        "; filament_diameter: %s" % s.filament_diameter,
        "; max_z_height: %.2f" % a.max_z,
        "; filament: 1",
        "; HEADER_BLOCK_END",
        "",
    ]


def convert(cura_gcode: str, printer: Dict[str, Any], nozzle_entry: Dict[str, Any], s: PrintSettings
            ) -> Tuple[str, GcodeAnalysis, List[str]]:
    """Return (bambu_gcode, analysis, warnings)."""
    warnings: List[str] = []
    lines = cura_gcode.replace("\r\n", "\n").split("\n")
    a = analyze(lines)
    if not a.has_start_markers:
        warnings.append("The start G-code has no ;BBL_START_GCODE_BEGIN/END markers, so Bambu's start "
                        "sequence was inserted before the first layer instead. Restore the default start G-code "
                        "in Machine Settings to fix this.")
    if a.absolute_e:
        warnings.append("Absolute extrusion (M82) is used. Bambu printers expect relative extrusion; "
                        "enable 'Relative Extrusion' in the Cura settings.")
    if a.layer_count == 0:
        raise ValueError("The G-code contains no layers (;LAYER: markers) - nothing to print.")
    max_vol = max_volumetric_speed(s.filament_type, s.max_volumetric_speed)
    lines, limited, added = limit_volumetric_speed(lines, a, max_vol, s.filament_diameter)
    if limited and added >= 30:
        warnings.append("%d extrusion moves were slowed to stay under %g mm³/s, the max volumetric speed for %s "
                        "(Bambu Lab category of the print settings). This adds about %d min, so the print will take "
                        "longer than Cura's estimate. Lower the print speeds in Cura to avoid this."
                        % (limited, max_vol, s.filament_type, int(round(added / 60.0))))
    if s.filament_type.upper().startswith("TPU"):
        warnings.append("TPU is flexible: feed it from the external spool holder, not the AMS, "
                        "and choose the external spool in Bambu Connect.")

    tpl = nozzle_entry["templates"]
    machine = nozzle_entry["machine"]
    variables = template_variables(machine, s, a)
    start = render(tpl["machine_start_gcode"], variables)
    end = render(tpl["machine_end_gcode"], variables)
    total = max(a.total_time, 1.0)

    out: List[str] = header_block(s, a)
    out.append("; EXECUTABLE_BLOCK_START")
    out.append("M73 P0 R%d" % int(math.ceil(total / 60.0)))

    skipping = False
    start_done = False
    layer_idx = -1
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(START_BEGIN):
            skipping = True
            out.append(";===== Bambu Lab start G-code (%s) =====" % nozzle_entry["printer_settings_id"])
            out.extend(start.split("\n"))
            out.append(";===== end of Bambu Lab start G-code =====")
            start_done = True
            continue
        if stripped.startswith(START_END):
            skipping = False
            continue
        if stripped.startswith(END_BEGIN):
            skipping = True
            out.append("M73 P100 R0")
            out.append(";===== Bambu Lab end G-code =====")
            out.extend(end.split("\n"))
            continue
        if stripped.startswith(END_END):
            skipping = False
            continue
        if skipping:
            continue
        if stripped.startswith(";LAYER:"):
            layer_idx += 1
            if not start_done:  # markers missing: put the Bambu start sequence before the first layer
                out.extend(start.split("\n"))
                start_done = True
            out.append(line)
            elapsed = a.layer_elapsed[layer_idx - 1] if layer_idx > 0 else 0.0
            out.append("M73 P%d R%d" % (min(99, int(100.0 * elapsed / total)),
                                         int(math.ceil(max(0.0, total - elapsed) / 60.0))))
            lv = dict(variables, layer_num=layer_idx, layer_z=a.layer_z[layer_idx])
            out.extend(render(tpl["layer_change_gcode"], lv).split("\n"))
            if s.timelapse and layer_idx > 0 and tpl.get("time_lapse_gcode"):
                out.extend(render(tpl["time_lapse_gcode"], lv).split("\n"))
            continue
        out.append(line)

    if a.has_end_markers:
        out.append("; EXECUTABLE_BLOCK_END")
    else:
        out.append("M73 P100 R0")
        out.extend(end.split("\n"))
        out.append("; EXECUTABLE_BLOCK_END")
        warnings.append("The end G-code has no ;BBL_END_GCODE_BEGIN/END markers, so Bambu's end sequence was "
                        "appended at the end of the file.")
    return "\n".join(out) + "\n", a, warnings
