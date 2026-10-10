# Copyright (c) 2026. Released under the terms of the LGPLv3 or higher.
"""Cura to Bambu Lab (unofficial).

* Registers this plugin's resources folder so Cura finds the Bambu Lab printer definitions and quality profiles.
* Adds a '.gcode.3mf' writer (Bambu sliced-plate format, usable from 'Save to Disk').
* Adds a 'Send to Bambu Connect' output device for Bambu printers.
"""
import os

from UM.Logger import Logger
from UM.Resources import Resources

from . import BambuConnectOutputDevice, BambuGcode3mfWriter

_HERE = os.path.dirname(os.path.abspath(__file__))
_QUALITIES = [  # (quality_type, name, layer height, weight)
    ("extra_fine", "0.08mm Extra Fine", 0.08, -1),
    ("fine", "0.12mm Fine", 0.12, -2),
    ("optimal", "0.16mm Optimal", 0.16, -3),
    ("standard", "0.20mm Standard", 0.20, -4),
    ("draft", "0.24mm Draft", 0.24, -5),
    ("extra_draft", "0.28mm Extra Draft", 0.28, -6),
]


def _write_quality_profiles() -> None:
    """Cura only loads quality profiles whose setting_version matches the running Cura exactly,
    so (re)write them for whatever Cura version is loading the plugin."""
    try:
        from cura.CuraApplication import CuraApplication
        version = CuraApplication.SettingVersion
        folder = os.path.join(_HERE, "resources", "quality", "bambulab")
        os.makedirs(folder, exist_ok=True)
        for qtype, name, layer_height, weight in _QUALITIES:
            text = ("[general]\ndefinition = bambulab_base\nname = %s\nversion = 4\n\n"
                    "[metadata]\nglobal_quality = True\nquality_type = %s\nsetting_version = %d\ntype = quality\n"
                    "weight = %d\n\n[values]\nlayer_height = %s\n" % (name, qtype, version, weight, layer_height))
            path = os.path.join(folder, "bambulab_global_%s.inst.cfg" % qtype)
            old = None
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    old = f.read()
            if old != text:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
    except Exception:  # noqa: BLE001
        Logger.logException("w", "Could not update the Bambu quality profiles")


def getMetaData():
    return {
        "mesh_writer": {
            "output": [{
                "extension": "gcode.3mf",
                "description": "Bambu Lab sliced plate (.gcode.3mf)",
                "mime_type": BambuGcode3mfWriter.MIME_TYPE,
                "mode": BambuGcode3mfWriter.BambuGcode3mfWriter.OutputMode.BinaryMode,
            }]
        }
    }


def register(app):
    _write_quality_profiles()
    Resources.addSearchPath(os.path.join(_HERE, "resources"))
    writer = BambuGcode3mfWriter.BambuGcode3mfWriter()
    return {
        "mesh_writer": writer,
        "output_device": BambuConnectOutputDevice.BambuConnectOutputDevicePlugin(writer),
    }
