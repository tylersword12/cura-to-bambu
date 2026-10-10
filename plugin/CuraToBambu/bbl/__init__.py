# Pure-Python helpers (no Cura imports): Bambu template rendering, G-code conversion, .gcode.3mf packaging.
import json
import os

_PRINTERS = None


def printers() -> dict:
    """Bambu machine data + G-code templates, extracted from BambuStudio's BBL profiles."""
    global _PRINTERS
    if _PRINTERS is None:
        with open(os.path.join(os.path.dirname(__file__), "printers.json"), encoding="utf-8") as f:
            _PRINTERS = json.load(f)["printers"]
    return _PRINTERS


def nozzle_entry(printer_id: str, nozzle_diameter: float) -> dict:
    """Pick the template set for the closest supported nozzle size (0.2 / 0.4 / 0.6 / 0.8)."""
    nozzles = printers()[printer_id]["nozzles"]
    best = min(nozzles, key=lambda n: abs(float(n) - float(nozzle_diameter)))
    return nozzles[best]
