# Copyright (c) 2026. Released under the terms of the LGPLv3 or higher.
"""Mesh writer that produces a Bambu Lab '.gcode.3mf' sliced plate file from Cura's G-code."""
from typing import List, Optional

from UM.Logger import Logger
from UM.Mesh.MeshWriter import MeshWriter
from UM.Message import Message
from UM.MimeTypeDatabase import MimeType, MimeTypeDatabase
from UM.i18n import i18nCatalog

from cura.CuraApplication import CuraApplication

from . import CuraBridge, bbl
from .bbl.gcode import convert
from .bbl.package import PlateInfo, build_gcode_3mf

catalog = i18nCatalog("cura")
MIME_TYPE = "application/x-bambu-gcode-3mf"


class BambuGcode3mfWriter(MeshWriter):
    def __init__(self) -> None:
        super().__init__(add_to_recent_files=False)
        MimeTypeDatabase.addMimeType(MimeType(name=MIME_TYPE, comment="Bambu Lab sliced plate",
                                              suffixes=["gcode.3mf"]))
        self.last_warnings: List[str] = []

    def write(self, stream, nodes, mode=MeshWriter.OutputMode.BinaryMode) -> bool:
        if mode != MeshWriter.OutputMode.BinaryMode:
            self.setInformation("The Bambu .gcode.3mf writer only supports binary mode.")
            return False
        data = self.build(nodes)
        if data is None:
            return False
        stream.write(data)
        return True

    def build(self, nodes=None) -> Optional[bytes]:
        """Build the .gcode.3mf bytes for the current slice, or None (see getInformation())."""
        self.last_warnings = []
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        try:
            printer_id, nozzle_entry, settings, extra = CuraBridge.collect_settings(global_stack)
        except ValueError as e:
            self.setInformation(str(e))
            return None
        gcode, error = CuraBridge.cura_gcode()
        if gcode is None:
            self.setInformation(error)
            return None
        try:
            bambu_gcode, analysis, warnings = convert(gcode, bbl.printers()[printer_id], nozzle_entry, settings)
        except Exception as e:  # noqa: BLE001 - show the user what went wrong instead of failing silently
            Logger.logException("e", "Converting Cura G-code for Bambu failed")
            self.setInformation("Could not convert the G-code for the Bambu printer: %s" % e)
            return None
        self.last_warnings = warnings
        for w in warnings:
            Logger.log("w", w)

        printer = bbl.printers()[printer_id]
        print_info = app.getPrintInformation()
        try:
            weights = list(print_info.materialWeights)
            weight = float(weights[0]) if weights and weights[0] else None
        except Exception:  # noqa: BLE001
            weight = None
        if weight is None:
            weight = analysis.filament_weight_g(settings.filament_diameter, settings.filament_density)
        names = CuraBridge.object_names(nodes) or [print_info.jobName or "object"]
        info = PlateInfo(
            printer_model=printer["printer_model"],
            printer_model_id=printer["model_id"],
            printer_settings_id=nozzle_entry["printer_settings_id"],
            nozzle_diameter=settings.nozzle_diameter,
            bed_type=settings.bed_type,
            filament_type=settings.filament_type,
            filament_color=extra["color"],
            filament_used_m=analysis.filament_mm / 1000.0,
            filament_used_g=weight,
            prediction_s=int(analysis.total_time),
            weight_g=weight,
            nozzle_temp=int(round(settings.nozzle_temp)),
            bed_temp=int(round(settings.bed_temp)),
            layer_height=settings.layer_height,
            plate_name=print_info.jobName or "",
            object_names=names,
            support_used=extra["support_used"],
            application="UltiMaker Cura %s (Cura-to-Bambu plugin)" % app.getVersion(),
        )
        thumb, small = CuraBridge.thumbnails()
        return build_gcode_3mf(bambu_gcode, info, thumb, small)

    def showWarnings(self) -> None:
        if self.last_warnings:
            Message("\n\n".join(self.last_warnings), title="Bambu print file: please check",
                    message_type=Message.MessageType.WARNING).show()
