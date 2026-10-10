# Test-only Cura plugin: drives a real Cura session through the Cura-to-Bambu plugin and records results.
import json
import os
import sys
import traceback

from PyQt6.QtCore import QTimer, QUrl

from UM.Extension import Extension
from UM.Logger import Logger

from cura.CuraApplication import CuraApplication

OUT = os.environ["E2E_OUT"]
MODELS = os.environ["E2E_MODELS"].split(os.pathsep)
SCENARIOS = json.loads(os.environ["E2E_SCENARIOS"])


def getMetaData():
    return {}


def register(app):
    return {"extension": Driver()}


class Driver(Extension):
    def __init__(self):
        super().__init__()
        self._app = CuraApplication.getInstance()
        self._results = {"scenarios": []}
        self._idx = -1
        self._waiting = False
        self._models_loaded = False
        self._app.initializationFinished.connect(self._onInit)  # bound method: UM signals hold weak refs
        self._watchdog = QTimer()
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._onTimeout)
        self._watchdog.start(480 * 1000)

    def _onInit(self):
        pkg = os.environ.get("E2E_INSTALL_PACKAGE")
        if pkg:  # install mode: let Cura install the .curapackage exactly like drag-and-drop does, then quit
            pid = self._app.getPackageManager().installPackage(pkg)
            self._results["installed_package_id"] = pid
            self._finish("installed")
            return
        QTimer.singleShot(4000, self._next)

    def _onTimeout(self):
        self._finish("timeout")

    def _onDeviceDone(self, device):
        QTimer.singleShot(500, self._next)

    # -------------------------------------------------------------------------------------------
    def _log(self, *a):
        Logger.log("i", "[E2E] " + " ".join(str(x) for x in a))

    def _finish(self, status="done"):
        self._results["status"] = status
        with open(os.path.join(OUT, os.environ.get("E2E_RESULTS", "results.json")), "w") as f:
            json.dump(self._results, f, indent=1, default=str)
        self._log("finished", status)
        sys.stdout.flush()
        os._exit(0)

    def _guard(fn):
        def wrapper(self, *a, **k):
            try:
                return fn(self, *a, **k)
            except Exception:
                self._results.setdefault("errors", []).append(traceback.format_exc())
                self._finish("exception")
        return wrapper

    @_guard
    def _next(self):
        self._idx += 1
        if self._idx >= len(SCENARIOS):
            self._finish()
            return
        sc = SCENARIOS[self._idx]
        self._log("scenario", sc)
        mm = self._app.getMachineManager()
        ok = mm.addMachine(sc["definition"], sc["name"])
        stack = self._app.getGlobalContainerStack()
        ext = stack.extruderList[0]
        if sc.get("nozzle"):
            ext.definitionChanges.setProperty("machine_nozzle_size", "value", sc["nozzle"])
        if sc.get("material"):
            mm.setMaterialById("0", sc["material"])
        for key, value in sc.get("global", {}).items():
            stack.userChanges.setProperty(key, "value", value)
        for key, value in sc.get("extruder", {}).items():
            ext.userChanges.setProperty(key, "value", value)
        rec = {"scenario": sc, "addMachine": ok, "definition": stack.definition.getId(),
               "quality": stack.quality.getName(), "quality_type": stack.quality.getMetaDataEntry("quality_type"),
               "material": ext.material.getId(), "material_type": ext.material.getMetaDataEntry("material"),
               "values": {k: stack.getProperty(k, "value") for k in (
                   "machine_width", "machine_depth", "machine_height", "layer_height", "layer_height_0",
                   "speed_print", "speed_wall_0", "speed_infill", "speed_travel", "acceleration_print",
                   "relative_extrusion", "material_bed_temp_prepend", "material_print_temp_prepend",
                   "bbl_plate_type", "bbl_timelapse", "retraction_amount", "retraction_hop_enabled",
                   "machine_disallowed_areas", "gantry_height", "adhesion_type")},
               "ext_values": {k: ext.getProperty(k, "value") for k in (
                   "machine_nozzle_size", "material_diameter", "material_print_temperature",
                   "material_print_temperature_layer_0", "material_bed_temperature", "speed_wall_0",
                   "wall_line_width_0")},
               "output_devices": [d.getId() for d in self._app.getOutputDeviceManager().getOutputDevices()],
               "qualities_available": sorted(
                   q.getMetaDataEntry("quality_type") for q in self._app.getContainerRegistry().findInstanceContainers(
                       type="quality", definition="bambulab_base"))}
        self._results["scenarios"].append(rec)
        if not self._models_loaded:
            for m in MODELS:
                self._app.readLocalFile(QUrl.fromLocalFile(m), add_to_recent_files=False)
            self._models_loaded = True
            QTimer.singleShot(8000, self._slice)
        else:
            QTimer.singleShot(3000, self._slice)

    @_guard
    def _slice(self):
        backend = self._app.getBackend()
        if not self._waiting:
            backend.backendStateChange.connect(self._onState)
            self._waiting = True
        self._log("force slice")
        backend.forceSlice()

    def _onState(self, state):
        from UM.Backend.Backend import BackendState
        self._log("backend state", state)
        if state == BackendState.Done:
            QTimer.singleShot(1500, self._export)
        elif state == BackendState.Error:
            self._results["scenarios"][-1]["slice_error"] = True
            QTimer.singleShot(500, self._next)

    @_guard
    def _export(self):
        rec = self._results["scenarios"][-1]
        if rec.get("exported"):
            return
        rec["exported"] = True
        sc = rec["scenario"]
        from io import BytesIO, StringIO
        from UM.PluginRegistry import PluginRegistry
        # 1) plain Cura gcode (reference)
        buf = StringIO()
        PluginRegistry.getInstance().getPluginObject("GCodeWriter").write(buf, None)
        with open(os.path.join(OUT, sc["name"] + ".cura.gcode"), "w") as f:
            f.write(buf.getvalue())
        # 2) the .gcode.3mf through the registered mesh writer (what 'Save to Disk' uses)
        writer = self._app.getMeshFileHandler().getWriterByMimeType("application/x-bambu-gcode-3mf")
        rec["writer_found"] = writer is not None
        data = BytesIO()
        ok = writer.write(data, None) if writer else False
        rec["writer_ok"] = ok
        rec["writer_info"] = writer.getInformation() if writer else None
        rec["warnings"] = getattr(writer, "last_warnings", None)
        if ok:
            with open(os.path.join(OUT, sc["name"] + ".gcode.3mf"), "wb") as f:
                f.write(data.getvalue())
        # 3) the 'Send to Bambu Connect' device, with the URL launch captured instead of executed
        device = self._app.getOutputDeviceManager().getOutputDevice("bambu_connect")
        rec["device_present"] = device is not None
        if device is not None and sc.get("test_device"):
            connect_mod = next(m for n, m in sys.modules.items() if n.endswith("bbl.connect"))
            captured = []
            connect_mod.open_url = captured.append
            connect_mod.is_installed = lambda: True  # plain module attribute, not a UM signal
            rec["device_urls"] = captured
            self._device_urls = captured
            device.writeFinished.connect(self._onDeviceDone)
            device.writeError.connect(self._onDeviceDone)
            device.requestWrite(self._app.getController().getScene().getRoot().getChildren(), sc["name"] + "_device")
            return
        QTimer.singleShot(500, self._next)
