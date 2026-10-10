# Copyright (c) 2026. Released under the terms of the LGPLv3 or higher.
"""Build a Bambu Lab '.gcode.3mf' (a sliced plate file) around a G-code string.

The layout mirrors what BambuStudio's "Export plate sliced file" writes (bbs_3mf.cpp):

    [Content_Types].xml
    _rels/.rels
    3D/3dmodel.model
    Metadata/plate_1.gcode            the G-code the printer runs
    Metadata/plate_1.gcode.md5        upper-case hex MD5 of plate_1.gcode
    Metadata/plate_1.png              plate thumbnail (printer screen / Bambu Connect)
    Metadata/plate_1_small.png
    Metadata/model_settings.config    plate -> gcode/thumbnail mapping
    Metadata/slice_info.config        printer model, nozzle, time, weight, filaments (used for AMS mapping)
    Metadata/project_settings.config  key printer/filament/plate settings as JSON

Pure Python (no Cura imports) so it can be unit-tested outside Cura.
"""
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional
from xml.sax.saxutils import quoteattr

# Bambu filament ids for Bambu's "Generic <type>" profiles (tray_info_idx). Bambu Connect uses
# type/colour/id to suggest an AMS slot.
GENERIC_FILAMENT_IDS = {
    "PLA": "GFL99", "PLA-CF": "GFL98", "PETG": "GFG99", "PETG-CF": "GFG98", "PCTG": "GFG97",
    "ABS": "GFB99", "ASA": "GFB98", "TPU": "GFU99", "TPU-AMS": "GFU98", "PVA": "GFS99", "BVOH": "GFS97",
    "HIPS": "GFS98", "PA": "GFN99", "PA-CF": "GFN98", "PC": "GFC99", "PP": "GFP97", "PE": "GFP99",
    "PPS": "GFT97", "PPA-CF": "GFN97", "EVA": "GFR99", "PHA": "GFR98",
}

# Version string written into slice_info's client header. BambuStudio writes its own version
# here; we mirror the Bambu Studio release this plugin's templates were taken from.
CLIENT_VERSION = "01.09.07.52"


@dataclass
class PlateInfo:
    printer_model: str            # "Bambu Lab P1S"
    printer_model_id: str         # "C12"
    printer_settings_id: str      # "Bambu Lab P1S 0.4 nozzle"
    nozzle_diameter: float
    bed_type: str                 # "Textured PEI Plate"
    filament_type: str            # "PLA"
    filament_color: str           # "#RRGGBB" or "#RRGGBBAA"
    filament_used_m: float
    filament_used_g: float
    prediction_s: int
    weight_g: float
    nozzle_temp: int
    bed_temp: int
    layer_height: float
    plate_name: str = ""
    object_names: List[str] = field(default_factory=list)
    support_used: bool = False
    application: str = "Cura"


def _md5_upper(data: bytes) -> str:
    return hashlib.md5(data).hexdigest().upper()


def _norm_color(color: str) -> str:
    c = (color or "#FFFFFF").strip()
    if not c.startswith("#"):
        c = "#" + c
    if len(c) == 4:  # #RGB
        c = "#" + "".join(ch * 2 for ch in c[1:])
    c = c.upper()
    if len(c) == 7:
        c += "FF"
    return c if len(c) == 9 else "#FFFFFFFF"


def _short_model(printer_model: str) -> str:
    return {"Bambu Lab X1 Carbon": "X1C", "Bambu Lab X1E": "X1E", "Bambu Lab P1S": "P1S",
            "Bambu Lab P1P": "P1P"}.get(printer_model, printer_model.replace("Bambu Lab ", ""))


def content_types() -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
            ' <Default Extension="png" ContentType="image/png"/>\n'
            ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n'
            '</Types>')


def relationships(has_thumbnail: bool) -> str:
    rels = [' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>']
    if has_thumbnail:
        rels += [
            ' <Relationship Target="/Metadata/plate_1.png" Id="rel-2" '
            'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>',
            ' <Relationship Target="/Metadata/plate_1.png" Id="rel-4" '
            'Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"/>',
            ' <Relationship Target="/Metadata/plate_1_small.png" Id="rel-5" '
            'Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-small"/>',
        ]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            + "\n".join(rels) + "\n</Relationships>")


def model_xml(info: PlateInfo) -> str:
    """Geometry is not needed to print a sliced plate, so (like a G-code-only export) the model is empty."""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<model unit="millimeter" xml:lang="en-US" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">\n'
            ' <metadata name="Application">%s</metadata>\n'
            ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
            ' <metadata name="Title">%s</metadata>\n'
            ' <resources>\n </resources>\n <build>\n </build>\n</model>\n'
            % (_xml_text(info.application), _xml_text(info.plate_name or "Cura print")))


def _xml_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _meta(key: str, value) -> str:
    return '    <metadata key=%s value=%s/>\n' % (quoteattr(key), quoteattr(str(value)))


def model_settings(info: PlateInfo, has_thumbnail: bool) -> str:
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n')
    for idx, name in enumerate(info.object_names, start=1):
        out.write('  <object id="%d">\n' % idx)
        out.write(_meta("name", name))
        out.write(_meta("extruder", 1))
        out.write('  </object>\n')
    out.write('  <plate>\n')
    out.write(_meta("plater_id", 1))
    out.write(_meta("plater_name", info.plate_name))
    out.write(_meta("locked", "false"))
    out.write(_meta("gcode_file", "Metadata/plate_1.gcode"))
    if has_thumbnail:
        out.write(_meta("thumbnail_file", "Metadata/plate_1.png"))
    for idx, _ in enumerate(info.object_names, start=1):
        out.write('    <model_instance>\n')
        out.write('  ' + _meta("object_id", idx))
        out.write('  ' + _meta("instance_id", 0))
        out.write('  ' + _meta("identify_id", 100 + idx))
        out.write('    </model_instance>\n')
    out.write('  </plate>\n</config>\n')
    return out.getvalue()


def slice_info(info: PlateInfo) -> str:
    fid = GENERIC_FILAMENT_IDS.get(info.filament_type.upper(), "GFL99")
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n')
    out.write('  <header>\n')
    out.write('    <header_item key="X-BBL-Client-Type" value="slicer"/>\n')
    out.write('    <header_item key="X-BBL-Client-Version" value="%s"/>\n' % CLIENT_VERSION)
    out.write('  </header>\n')
    out.write('  <plate>\n')
    out.write(_meta("index", 1))
    out.write(_meta("extruder_type", 0))          # Direct Drive
    out.write(_meta("nozzle_volume_type", 0))     # Standard
    out.write(_meta("printer_model_id", info.printer_model_id))
    out.write(_meta("nozzle_diameters", "%g" % info.nozzle_diameter))
    out.write(_meta("timelapse_type", 0))
    out.write(_meta("prediction", int(info.prediction_s)))
    out.write(_meta("weight", "%.2f" % info.weight_g))
    out.write(_meta("pause_count", 0))
    out.write(_meta("outside", "false"))
    out.write(_meta("support_used", "true" if info.support_used else "false"))
    out.write(_meta("label_object_enabled", "false"))
    out.write(_meta("support_material_on_wipe_tower", "false"))
    out.write(_meta("enable_filament_dynamic_map", "false"))
    out.write(_meta("has_filament_switcher", "false"))
    out.write(_meta("filament_maps", 1))
    for idx, name in enumerate(info.object_names, start=1):
        out.write('    <object identify_id=%s name=%s skipped="false" />\n'
                  % (quoteattr(str(100 + idx)), quoteattr(name)))
    out.write('    <filament id="1" tray_info_idx=%s type=%s color=%s used_m="%.2f" used_g="%.2f" '
              'group_id="0" nozzle_diameter="%.2f" volume_type="Standard" used_for_object="true" '
              'used_for_support="false" total_load_time="0" total_unload_time="0"/>\n'
              % (quoteattr(fid), quoteattr(info.filament_type.upper()), quoteattr(_norm_color(info.filament_color)),
                 info.filament_used_m, info.filament_used_g, info.nozzle_diameter))
    out.write('  </plate>\n</config>\n')
    return out.getvalue()


def project_settings(info: PlateInfo) -> str:
    short = _short_model(info.printer_model)
    ftype = info.filament_type.upper()
    data = {
        "name": "project_settings",
        "from": "project",
        "printer_model": info.printer_model,
        "printer_settings_id": info.printer_settings_id,
        # Bambu Connect requires this list: it runs print_compatible_printers.map(...) and pulls the model name
        # out of entries shaped like "Bambu Lab X1 Carbon 0.4 nozzle" to check the selected printer.
        "print_compatible_printers": [info.printer_settings_id],
        "enable_arc_fitting": "0",
        "printer_variant": "%g" % info.nozzle_diameter,
        "nozzle_diameter": ["%g" % info.nozzle_diameter],
        "curr_bed_type": info.bed_type,
        "filament_type": [ftype],
        "filament_colour": [_norm_color(info.filament_color)[:7]],
        "filament_ids": [GENERIC_FILAMENT_IDS.get(ftype, "GFL99")],
        "filament_settings_id": ["Generic %s @BBL %s" % (ftype, short)],
        "filament_vendor": ["Generic"],
        "nozzle_temperature": [str(info.nozzle_temp)],
        "nozzle_temperature_initial_layer": [str(info.nozzle_temp)],
        "hot_plate_temp": [str(info.bed_temp)],
        "textured_plate_temp": [str(info.bed_temp)],
        "cool_plate_temp": [str(info.bed_temp)],
        "eng_plate_temp": [str(info.bed_temp)],
        "layer_height": "%g" % info.layer_height,
        "print_settings_id": "Cura",
        "timelapse_type": "0",
        "version": CLIENT_VERSION,
    }
    return json.dumps(data, indent=4)


def placeholder_png(size: int = 128, rgb=(0x9A, 0xA0, 0xA6)) -> bytes:
    """A plain grey PNG, used when Cura can't render a snapshot (Bambu Connect needs a thumbnail file)."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    row = b"\x00" + bytes(rgb) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size, 9)) + chunk(b"IEND", b""))


def build_gcode_3mf(gcode: str, info: PlateInfo, thumbnail_png: Optional[bytes] = None,
                    small_thumbnail_png: Optional[bytes] = None) -> bytes:
    gcode_bytes = gcode.encode("utf-8")
    if not thumbnail_png:  # Bambu Connect reads model_settings' thumbnail_file unconditionally
        thumbnail_png = placeholder_png(512)
        small_thumbnail_png = placeholder_png(128)
    has_thumb = True
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types())
        z.writestr("_rels/.rels", relationships(has_thumb))
        z.writestr("3D/3dmodel.model", model_xml(info))
        z.writestr("Metadata/plate_1.gcode", gcode_bytes)
        z.writestr("Metadata/plate_1.gcode.md5", _md5_upper(gcode_bytes))
        if has_thumb:
            z.writestr(zipfile.ZipInfo("Metadata/plate_1.png"), thumbnail_png, compress_type=zipfile.ZIP_STORED)
            z.writestr(zipfile.ZipInfo("Metadata/plate_1_small.png"), small_thumbnail_png or thumbnail_png,
                       compress_type=zipfile.ZIP_STORED)
        z.writestr("Metadata/model_settings.config", model_settings(info, has_thumb))
        z.writestr("Metadata/slice_info.config", slice_info(info))
        z.writestr("Metadata/project_settings.config", project_settings(info))
    return buf.getvalue()
