# Copyright (c) 2026. Released under the terms of the LGPLv3 or higher.
"""Everything that reads Cura state: settings, scene objects, thumbnails, G-code."""
from io import StringIO
from typing import List, Optional, Tuple

from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator

from cura.CuraApplication import CuraApplication
from cura.Utils.Threading import call_on_qt_thread

from . import bbl
from .bbl.gcode import PrintSettings

PLATE_NAMES = {
    "textured_pei": "Textured PEI Plate",
    "cool_plate": "Cool Plate",
    "engineering_plate": "Engineering Plate",
    "high_temp_plate": "High Temp Plate",
    "supertack_plate": "Supertack Plate",
}


def bambu_printer_id(global_stack) -> Optional[str]:
    """The Bambu printer this Cura machine is, or None if it isn't one of ours."""
    if global_stack is None:
        return None
    definition = global_stack.definition
    pid = definition.getMetaDataEntry("bbl_printer_id") if definition else None
    if pid in bbl.printers():
        return pid
    return definition.getId() if definition and definition.getId() in bbl.printers() else None


def normalize_material_type(cura_type: str, name: str = "") -> str:
    """Map a Cura material type ('PLA', 'Tough PLA', 'CPE', 'Nylon', 'TPU 95A', ...) to Bambu's names."""
    text = ("%s %s" % (cura_type or "", name or "")).upper()
    t = (cura_type or "").upper()
    cf = "CF" in text.replace("CFF", "CF")
    if "PETG" in t or "CPE" in t or "PET" in t:
        return "PETG-CF" if cf else "PETG"
    if "PLA" in t:
        return "PLA-CF" if cf else "PLA"
    if "NYLON" in t or t.startswith("PA"):
        return "PA-CF" if cf else "PA"
    if "TPU" in t or "TPE" in t or "FLEX" in t:
        return "TPU"
    for known in ("ABS", "ASA", "PVA", "BVOH", "HIPS", "PCTG", "PC", "PPS", "PP"):
        if t.startswith(known):
            return known
    return "PLA"


def _value(stack, key, default):
    try:
        v = stack.getProperty(key, "value")
        return default if v is None else v
    except Exception:  # noqa: BLE001
        return default


def collect_settings(global_stack) -> Tuple[str, dict, PrintSettings, dict]:
    """Return (printer_id, nozzle template entry, PrintSettings, extra info for the 3MF metadata)."""
    printer_id = bambu_printer_id(global_stack)
    if printer_id is None:
        raise ValueError("The active printer is not a Bambu Lab printer from the Cura-to-Bambu plugin.")
    ext = global_stack.extruderList[0]
    material = ext.material
    cura_type = material.getMetaDataEntry("material", "PLA") if material else "PLA"
    material_name = material.getName() if material else ""
    ftype = normalize_material_type(cura_type, material_name)
    color = material.getMetaDataEntry("color_code", "#FFFFFF") if material else "#FFFFFF"
    props = (material.getMetaDataEntry("properties", {}) if material else {}) or {}
    try:
        density = float(props.get("density", 1.24))
    except (TypeError, ValueError):
        density = 1.24

    nozzle = float(_value(ext, "machine_nozzle_size", 0.4))
    accel_on = bool(_value(global_stack, "acceleration_enabled", True))
    s = PrintSettings(
        nozzle_diameter=nozzle,
        nozzle_temp_initial=float(_value(ext, "material_print_temperature_layer_0", 220)),
        nozzle_temp=float(_value(ext, "material_print_temperature", 220)),
        bed_temp_initial=float(_value(ext, "material_bed_temperature_layer_0", 55)),
        bed_temp=float(_value(ext, "material_bed_temperature", 55)),
        filament_type=ftype,
        bed_type=PLATE_NAMES.get(_value(global_stack, "bbl_plate_type", "textured_pei"), "Textured PEI Plate"),
        layer_height=float(_value(global_stack, "layer_height", 0.2)),
        outer_wall_speed=float(_value(ext, "speed_wall_0", 200)),
        outer_wall_line_width=float(_value(ext, "wall_line_width_0", nozzle)),
        default_acceleration=float(_value(global_stack, "acceleration_print", 10000)) if accel_on else 10000.0,
        outer_wall_acceleration=float(_value(ext, "acceleration_wall_0", 5000)) if accel_on else 5000.0,
        retraction_length=float(_value(ext, "retraction_amount", 0.8)),
        chamber_temperature=float(_value(global_stack, "build_volume_temperature", 0))
        if _value(global_stack, "machine_heated_build_volume", False) else 0.0,
        filament_diameter=float(_value(ext, "material_diameter", 1.75)),
        filament_density=density,
        timelapse=bool(_value(global_stack, "bbl_timelapse", True)),
        max_volumetric_speed=float(_value(global_stack, "bbl_max_volumetric_speed", 0) or 0),
        generator="UltiMaker Cura %s" % CuraApplication.getInstance().getVersion(),
    )
    extra = {
        "color": color,
        "support_used": bool(_value(global_stack, "support_enable", False)),
        "material_name": material_name,
    }
    return printer_id, bbl.nozzle_entry(printer_id, nozzle), s, extra


def object_names(nodes=None) -> List[str]:
    app = CuraApplication.getInstance()
    roots = nodes or [app.getController().getScene().getRoot()]
    names = []
    for root in roots:
        for node in DepthFirstIterator(root):
            if node.callDecoration("isSliceable") and node.getMeshData() and node.isVisible() \
                    and not getattr(node, "_outside_buildarea", False):
                names.append(node.getName() or "object")
    return names


def cura_gcode() -> Tuple[Optional[str], str]:
    """Return (gcode, error). Uses Cura's own G-code writer so all post-processing scripts still apply."""
    writer = PluginRegistry.getInstance().getPluginObject("GCodeWriter")
    if writer is None:
        return None, "Cura's G-code writer plugin is disabled."
    buf = StringIO()
    if not writer.write(buf, None):
        return None, writer.getInformation() or "Please slice the model before exporting."
    return buf.getvalue(), ""


@call_on_qt_thread
def thumbnails() -> Tuple[Optional[bytes], Optional[bytes]]:
    """Plate thumbnails (512 px + 128 px PNG) like Bambu Studio puts in the file."""
    try:
        from PyQt6.QtCore import QBuffer, Qt
        from cura.Snapshot import Snapshot
        app = CuraApplication.getInstance()
        if not app.isVisible:
            return None, None
        image = Snapshot.snapshot(512, 512)
        if image is None:
            return None, None
        out = []
        for img in (image, image.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)):
            buf = QBuffer()
            buf.open(QBuffer.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            out.append(bytes(buf.data()))
        return out[0], out[1]
    except Exception:  # noqa: BLE001
        Logger.logException("w", "Could not create the plate thumbnail")
        return None, None
