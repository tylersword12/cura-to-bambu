"""Unit tests for the pure-Python core (template rendering, G-code conversion, packaging, Bambu Connect URL).

Run:  python3 -m unittest discover -s tests -v      (from the repository root)
Fixtures are real G-code produced by UltiMaker Cura 5.8.1 with this plugin's printer definitions.
"""
import gzip
import hashlib
import io
import json
import math
import os
import re
import sys
import unittest
import zipfile
from xml.etree import ElementTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugin", "CuraToBambu"))

import bbl  # noqa: E402
from bbl import connect  # noqa: E402
from bbl.gcode import (PrintSettings, analyze, convert, max_volumetric_speed,  # noqa: E402
                       outer_wall_volumetric_speed)
from bbl.package import PlateInfo, build_gcode_3mf  # noqa: E402
from bbl.template import TemplateError, render  # noqa: E402

_W = re.compile(r"([A-Z])(-?\d*\.?\d+)")


def fixture(name):
    with gzip.open(os.path.join(HERE, "fixtures", name + ".cura.gcode.gz"), "rt", encoding="utf-8") as f:
        return f.read()


def sample_vars(**over):
    v = dict(bed_temperature_initial_layer_single=55, bed_temperature=[55], bed_temperature_initial_layer=[55],
             nozzle_temperature_initial_layer=[220], nozzle_temperature=[220], filament_type=["PLA"],
             initial_extruder=0, initial_no_support_extruder=0, flush_volumetric_speeds=[0], flush_temperatures=[0],
             outer_wall_volumetric_speed=12.0, first_layer_print_min=[100, 90], first_layer_print_size=[50, 60],
             curr_bed_type="Textured PEI Plate", scan_first_layer=True, default_acceleration=10000,
             outer_wall_acceleration=5000, retraction_length=[0.8], overall_chamber_temperature=0, max_layer_z=20.2,
             layer_num=3, total_layer_count=101, layer_z=0.8, timelapse_type=0)
    v.update(over)
    return v


def max_flow(gcode, area=math.pi * 0.875 ** 2):
    """Independent check: highest volumetric flow (mm^3/s) of any extrusion move during the print."""
    rel = False
    x = y = e = f = 0.0
    in_print = False
    worst = 0.0
    for raw in gcode.split("\n"):
        line = raw.split(";")[0].strip()
        if raw.startswith(";LAYER:0"):
            in_print = True
        if "Bambu Lab end G-code" in raw:
            in_print = False
        if not line:
            continue
        cmd = line.split()[0]
        if cmd == "M83":
            rel = True
        elif cmd == "M82":
            rel = False
        elif cmd == "G92":
            w = dict(_W.findall(line[3:]))
            e = float(w.get("E", e))
        elif cmd in ("G0", "G1"):
            w = dict(_W.findall(line[2:]))
            f = float(w.get("F", f))
            nx, ny = float(w.get("X", x)), float(w.get("Y", y))
            de = 0.0
            if "E" in w:
                de = float(w["E"]) if rel else float(w["E"]) - e
                e = e + float(w["E"]) if rel else float(w["E"])
            d = math.hypot(nx - x, ny - y)
            if in_print and de > 0 and d > 1e-6:
                worst = max(worst, de * area / d * f / 60.0)
            x, y = nx, ny
    return worst


class TemplateTests(unittest.TestCase):
    def test_all_bambu_templates_render(self):
        for pid, printer in bbl.printers().items():
            for nozzle, entry in printer["nozzles"].items():
                for key in ("machine_start_gcode", "machine_end_gcode", "layer_change_gcode", "time_lapse_gcode"):
                    with self.subTest(printer=pid, nozzle=nozzle, template=key):
                        out = render(entry["templates"][key], sample_vars())
                        self.assertNotIn("{", out)
                        self.assertNotRegex(out, r"\[(?:nozzle|bed|initial)_[a-z_]+\]")

    def test_conditionals(self):
        tpl = "{if filament_type[0]==\"PLA\"}A{elsif x > 1 && !flag}B{else}C{endif}|[n]|{n[0]*2}|{v/3}"
        self.assertEqual(render(tpl, dict(filament_type=["PLA"], x=2, flag=False, n=[7], v=1.0)), "A|7|14|0.333333")
        self.assertEqual(render(tpl, dict(filament_type=["ABS"], x=2, flag=False, n=[7], v=3.0)), "B|7|14|1")
        self.assertEqual(render(tpl, dict(filament_type=["ABS"], x=0, flag=False, n=[7], v=3.0)), "C|7|14|1")

    def test_textured_plate_offset_and_pla_fan(self):
        start = bbl.nozzle_entry("bambulab_p1s", 0.4)["templates"]["machine_start_gcode"]
        out = render(start, sample_vars())
        self.assertIn("G29.1 Z-0.04", out)
        self.assertIn("M106 P3 S180", out)
        out = render(start, sample_vars(curr_bed_type="Cool Plate", filament_type=["PETG"]))
        self.assertNotIn("G29.1 Z-0.04", out)
        self.assertNotIn("M106 P3 S180", out)

    def test_unknown_variable_is_an_error(self):
        with self.assertRaises(TemplateError):
            render("{nope + 1}", {})

    def test_nozzle_selection(self):
        self.assertEqual(bbl.nozzle_entry("bambulab_x1c", 0.6)["printer_settings_id"], "Bambu Lab X1 Carbon 0.6 nozzle")
        self.assertEqual(bbl.nozzle_entry("bambulab_p1s", 0.45)["printer_settings_id"], "Bambu Lab P1S 0.4 nozzle")


class ConvertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cura = fixture("p1s_pla_standard")
        cls.printer = bbl.printers()["bambulab_p1s"]
        cls.entry = bbl.nozzle_entry("bambulab_p1s", 0.4)

    def convert(self, cura=None, **settings):
        s = PrintSettings(**settings)
        return convert(cura or self.cura, self.printer, self.entry, s)

    def test_analysis_matches_cura_header(self):
        a = analyze(self.cura.split("\n"))
        header = dict(re.findall(r"^;(MINX|MINY|MAXZ|LAYER_COUNT|TIME):(\S+)", self.cura, re.M))
        self.assertEqual(a.layer_count, int(header["LAYER_COUNT"]))
        self.assertAlmostEqual(a.first_layer_min[0], float(header["MINX"]), places=2)
        self.assertAlmostEqual(a.first_layer_min[1], float(header["MINY"]), places=2)
        self.assertAlmostEqual(a.max_z, float(header["MAXZ"]), places=2)
        self.assertEqual(a.total_time, float(header["TIME"]))
        self.assertFalse(a.absolute_e)
        self.assertTrue(a.has_start_markers and a.has_end_markers)
        self.assertEqual(a.layer_z[0], 0.2)
        self.assertAlmostEqual(a.layer_z[-1], 30.0, places=3)

    def test_structure(self):
        g, a, warnings = self.convert()
        lines = g.split("\n")
        self.assertEqual(lines[0], "; HEADER_BLOCK_START")
        self.assertIn("; total layer number: 150", lines)
        self.assertEqual([l for l in lines if l.strip()][-1], "; EXECUTABLE_BLOCK_END")
        self.assertNotIn(";BBL_START_GCODE", g)
        self.assertNotIn(";BBL_END_GCODE", g)
        self.assertNotIn("Placeholder.", g)  # the fallback start/end blocks are replaced
        self.assertEqual(sum(1 for l in lines if l.startswith("M73 L")), 150)
        self.assertEqual([l for l in lines if l.startswith("M991 S0 P")][:3],
                         ["M991 S0 P0 ;notify layer change", "M991 S0 P1 ;notify layer change",
                          "M991 S0 P2 ;notify layer change"])
        self.assertEqual(sum(1 for l in lines if l.startswith("M971 S11 C10 O0")), 149)  # timelapse, layers 2..150
        g29 = [l for l in lines if "G29 A " in l][0]
        self.assertRegex(g29, r"G29 A X105\.752 Y114 I\d+(\.\d+)? J\d+(\.\d+)?")
        self.assertIn("M620 S0A", g)        # AMS load of the selected slot
        self.assertIn("M140 S55", g)        # bed temperature from settings
        self.assertIn("G1 Z30.5 F900", g)   # end G-code uses the real max Z
        start = g.index("Bambu Lab start G-code")
        self.assertLess(start, g.index(";LAYER:0"))
        self.assertLess(g.index(";LAYER:149"), g.index("Bambu Lab end G-code"))
        progress = [int(m) for m in re.findall(r"^M73 P(\d+) R\d+$", g, re.M)]
        self.assertEqual(progress, sorted(progress))
        self.assertEqual((progress[0], progress[-1]), (0, 100))

    def test_no_timelapse(self):
        g, _, _ = self.convert(timelapse=False)
        self.assertNotIn("M971 S11 C10 O0", g)

    def test_volumetric_limit_pla(self):
        cura_flow = max_flow(self.cura)
        self.assertGreater(cura_flow, 12.5)  # fixture was sliced fast enough to need limiting
        g, a, warnings = self.convert()
        self.assertLessEqual(max_flow(g), 12.0 * 1.01)
        self.assertGreater(a.total_time, float(re.search(r"^;TIME:(\S+)", self.cura, re.M).group(1)))
        g2, _, _ = self.convert(max_volumetric_speed=30)
        self.assertAlmostEqual(max_flow(g2), cura_flow, places=3)  # high limit: untouched

    def test_volumetric_limit_tpu(self):
        cura = fixture("p1p_tpu_brim")
        g, a, warnings = convert(cura, bbl.printers()["bambulab_p1p"], bbl.nozzle_entry("bambulab_p1p", 0.4),
                                 PrintSettings(filament_type="TPU", bed_temp=35, bed_temp_initial=35))
        self.assertLessEqual(max_flow(g), 3.2 * 1.01)
        self.assertTrue(any("external spool" in w for w in warnings))
        self.assertTrue(any("slowed" in w for w in warnings))

    def test_x1c_06_nozzle(self):
        cura = fixture("x1c_petg_06")
        g, a, warnings = convert(cura, bbl.printers()["bambulab_x1c"], bbl.nozzle_entry("bambulab_x1c", 0.6),
                                 PrintSettings(nozzle_diameter=0.6, filament_type="PETG", bed_type="Engineering Plate",
                                               bed_temp=70, bed_temp_initial=70))
        self.assertIn("X1 Carbon 0.6 nozzle", g)
        self.assertIn("M140 S70", g)
        self.assertNotIn("G29.1 Z-0.04", g)  # only for the textured plate
        self.assertLessEqual(max_flow(g), 12.0 * 1.01)

    def test_missing_markers_still_work(self):
        cura = re.sub(r";BBL_(START|END)_GCODE_(BEGIN|END)\n", "", self.cura)
        g, a, warnings = self.convert(cura)
        self.assertEqual(len([w for w in warnings if "markers" in w]), 2)
        self.assertLess(g.index("Bambu Lab start") if "Bambu Lab start" in g else g.index("M620 S0A"),
                        g.index(";LAYER:0"))
        self.assertTrue(g.rstrip().endswith("; EXECUTABLE_BLOCK_END"))

    def test_outer_wall_volumetric_speed(self):
        s = PrintSettings(outer_wall_speed=200, outer_wall_line_width=0.42, layer_height=0.2, filament_type="PLA")
        self.assertAlmostEqual(outer_wall_volumetric_speed(s), 12.0)  # capped
        s.outer_wall_speed = 60
        self.assertAlmostEqual(outer_wall_volumetric_speed(s), round(60 * (0.42 - 0.2 * (1 - math.pi / 4)) * 0.2, 4))
        self.assertEqual(max_volumetric_speed("TPU"), 3.2)
        self.assertEqual(max_volumetric_speed("PLA", 21), 21)


class PackageTests(unittest.TestCase):
    def test_gcode_3mf_layout(self):
        info = PlateInfo(printer_model="Bambu Lab P1S", printer_model_id="C12",
                         printer_settings_id="Bambu Lab P1S 0.4 nozzle", nozzle_diameter=0.4,
                         bed_type="Textured PEI Plate", filament_type="PLA", filament_color="#ffc924",
                         filament_used_m=1.99, filament_used_g=5.94, prediction_s=945, weight_g=5.94, nozzle_temp=220,
                         bed_temp=55, layer_height=0.2, plate_name="Cube & <cylinder>", object_names=["cube", "cyl"])
        gcode = "; HEADER_BLOCK_START\nG28\n"
        png = b"\x89PNG\r\n\x1a\nfake"
        data = build_gcode_3mf(gcode, info, png, png)
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
        for required in ("[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model", "Metadata/plate_1.gcode",
                         "Metadata/plate_1.gcode.md5", "Metadata/plate_1.png", "Metadata/plate_1_small.png",
                         "Metadata/model_settings.config", "Metadata/slice_info.config",
                         "Metadata/project_settings.config"):
            self.assertIn(required, names)
        g = z.read("Metadata/plate_1.gcode")
        self.assertEqual(z.read("Metadata/plate_1.gcode.md5").decode(), hashlib.md5(g).hexdigest().upper())
        for xml_name in ("[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model", "Metadata/model_settings.config",
                         "Metadata/slice_info.config"):
            ElementTree.fromstring(z.read(xml_name))  # well-formed
        si = ElementTree.fromstring(z.read("Metadata/slice_info.config"))
        meta = {m.get("key"): m.get("value") for m in si.iter("metadata")}
        self.assertEqual(meta["printer_model_id"], "C12")
        self.assertEqual(meta["prediction"], "945")
        fil = si.find("plate/filament")
        self.assertEqual((fil.get("type"), fil.get("color"), fil.get("tray_info_idx")), ("PLA", "#FFC924FF", "GFL99"))
        ps = json.loads(z.read("Metadata/project_settings.config"))
        self.assertEqual(ps["curr_bed_type"], "Textured PEI Plate")
        self.assertEqual(ps["filament_type"], ["PLA"])
        self.assertEqual(z.getinfo("Metadata/plate_1.png").compress_type, zipfile.ZIP_STORED)


class ConnectTests(unittest.TestCase):
    def test_url_matches_encodeURIComponent(self):
        url = connect.build_import_url("/tmp/cube.gcode.3mf", "Cube")
        self.assertEqual(url, "bambu-connect://import-file?path=%2Ftmp%2Fcube.gcode.3mf&name=Cube&version=1.0.0")
        self.assertEqual(connect.encode_uri_component(r"C:\Users\Tyler\Cura Bambu Prints\my part(1).gcode.3mf"),
                         "C%3A%5CUsers%5CTyler%5CCura%20Bambu%20Prints%5Cmy%20part(1).gcode.3mf")


if __name__ == "__main__":
    unittest.main()


class BambuConnectContractTests(unittest.TestCase):
    """Mirrors what Bambu Connect 2.x's importFile/Oj/jj code reads from a .gcode.3mf (from its renderer bundle).
    A missing field there surfaces as "Cannot read properties of undefined (reading 'map')"."""
    SUPPORTED = {"C11": "P1P", "C12": "P1S", "BL-P001": "X1 Carbon", "C13": "X1E"}  # Connect's model table
    BED_TYPES = {"Cool Plate / PLA Plate", "Engineering Plate", "High Temp Plate", "Textured PEI Plate",
                 "Supertack Plate", "Cool Plate", "Textured Cool Plate"}  # keys of Connect's bed-type map

    def check(self, data):
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        for required in ("Metadata/slice_info.config", "Metadata/model_settings.config",
                         "Metadata/project_settings.config"):
            self.assertIn(required, names)
        ps = json.loads(z.read("Metadata/project_settings.config"))
        models = [re.match(r"Bambu Lab(\s[\s\S]*?\s)0\.", p).group(1).strip() for p in ps["print_compatible_printers"]]
        self.assertTrue(set(models) & set(self.SUPPORTED.values()), models)
        self.assertIn(ps["curr_bed_type"], self.BED_TYPES)
        self.assertGreaterEqual(len(ps["filament_ids"]), 1)
        si = ElementTree.fromstring(z.read("Metadata/slice_info.config"))
        ms = ElementTree.fromstring(z.read("Metadata/model_settings.config"))
        for plate in si.iter("plate"):
            meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
            self.assertEqual(self.SUPPORTED[meta["printer_model_id"]], models[0])
            [float(x) for x in meta["nozzle_diameters"].split(",")]
            self.assertTrue(plate.findall("filament"))
            mplate = [p for p in ms.iter("plate")
                      if {m.get("key"): m.get("value") for m in p.findall("metadata")}["plater_id"] == meta["index"]]
            self.assertEqual(len(mplate), 1)
            mmeta = {m.get("key"): m.get("value") for m in mplate[0].findall("metadata")}
            self.assertIn(mmeta["gcode_file"], names)
            self.assertIn(mmeta["gcode_file"] + ".md5", names)
            self.assertIn(mmeta["thumbnail_file"], names)  # read unconditionally by Connect

    def test_all_printers_and_plates(self):
        for pid, printer in bbl.printers().items():
            for plate in ("Textured PEI Plate", "Cool Plate", "Engineering Plate", "High Temp Plate",
                          "Supertack Plate"):
                with self.subTest(printer=pid, plate=plate):
                    info = PlateInfo(printer_model=printer["printer_model"], printer_model_id=printer["model_id"],
                                     printer_settings_id=bbl.nozzle_entry(pid, 0.4)["printer_settings_id"],
                                     nozzle_diameter=0.4, bed_type=plate, filament_type="PLA",
                                     filament_color="#FFFFFF", filament_used_m=1, filament_used_g=3, prediction_s=60,
                                     weight_g=3, nozzle_temp=220, bed_temp=55, layer_height=0.2,
                                     object_names=["benchy"])
                    self.check(build_gcode_3mf("G28\n", info))  # no thumbnail -> placeholder must be added
