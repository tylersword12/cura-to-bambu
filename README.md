# Cura → Bambu Lab (unofficial plugin)

Slice in **UltiMaker Cura** (all of Cura's settings) and print on a **Bambu Lab P1S, P1P, X1 Carbon or X1E**.
The plugin uses Bambu's own start/end G-code and file format, and hands the file to **Bambu Connect**,
Bambu Lab's official app for sending files from other slicers.

```
Cura  ──slice──▶  plugin: Bambu start/end G-code + .gcode.3mf  ──▶  Bambu Connect  ──▶  printer (AMS slot, plate, print)
```

Tested with Cura 5.8.1. It should also load in other Cura 5.x versions.

## What you get in Cura

* **4 printers** under *Add printer → Non-UltiMaker printer → Bambu Lab*: P1S, P1P, X1 Carbon, X1E.
  They have a 256 × 256 × 250 mm volume, the purge-chute corner is blocked off, and Bambu motion limits are set.
  Cura's generic 1.75 mm materials are used.
* **Quality profiles** to match Bambu's: 0.08 / 0.12 / 0.16 / 0.20 / 0.24 / 0.28 mm.
* **"Send to Bambu Connect"** button (bottom right, where *Save to Disk* is) whenever a Bambu printer is selected.
* **"Bambu Lab sliced plate (.gcode.3mf)"** in *Save to Disk*, for saving to an SD card or dragging into Bambu Connect yourself.
* A **"Bambu Lab"** settings category (type *plate* or *volumetric* in the settings search box, or set
  visibility to *All*):
  * **Build Plate Type**: Textured PEI (default), Cool/Smooth PEI, Engineering, High Temp, SuperTack.
  * **Max Volumetric Speed**: 0 = automatic per filament type, using Bambu's generic values
    (PLA/PETG 12, ABS 16, PC 16, TPU 3.2 mm³/s). Use about **21** for Bambu PLA Basic. Cura has no such
    limit, so the plugin slows any faster extrusion move, as Bambu Studio does. The default print speeds follow this value.
  * **Timelapse Snapshots**: on/off.

## What happens when you export

1. Cura slices as usual. Every Cura setting, per-object setting and post-processing script applies.
2. The plugin swaps Cura's placeholder start/end G-code for **Bambu Lab's official sequence** for your printer
   and nozzle size, taken unchanged from Bambu Studio's printer profiles. That covers the AMS load, nozzle wipe,
   bed levelling of just the first-layer area, (X1) lidar and flow calibration, the purge line, and filament
   unload at the end.
3. It adds what the printer screen and app need: layer progress (`M73 L`, `M991`), % done and time left
   (`M73 P/R`), timelapse snapshots, and Bambu's header block.
4. It packs everything into a `.gcode.3mf`: the G-code plus its MD5, plate thumbnail, printer model, nozzle,
   plate type, filament type and colour (for AMS slot matching), weight and time.
5. **Send to Bambu Connect** saves the file to `Documents\Cura Bambu Prints\` and opens it in Bambu Connect.
   There you pick the printer, AMS slot or external spool, and options (bed levelling, flow calibration,
   timelapse), then press Print.

## Install

1. Install **Bambu Connect** (Windows/macOS) from https://wiki.bambulab.com/en/software/bambu-connect and log in,
   or add your printer in LAN mode.
2. Download **`CuraToBambu-0.1.1.curapackage`** from this repo's **Releases** page (or build it:
   `python3 tools/build_package.py plugin/CuraToBambu dist`), drag it onto the Cura window and confirm,
   then **restart Cura**.
   * Manual alternative: unzip `CuraToBambu-0.1.1-plugin-folder.zip` into
     `%APPDATA%\cura\5.8\plugins\` so that you have `...\plugins\CuraToBambu\plugin.json`, then restart Cura.
     (Help → Show Configuration Folder opens `%APPDATA%\cura\5.8`.)
3. *Settings → Printer → Add printer → Non-UltiMaker printer → Bambu Lab → P1S* (or X1 Carbon, etc.).
4. If your nozzle isn't 0.4: *Preferences → Printers → Machine Settings → Extruder 1 → Nozzle size*.
   The plugin picks Bambu's start G-code for 0.2, 0.4, 0.6 or 0.8.

## First print checklist

* Choose the material in Cura (e.g. *Generic PLA 1.75*), and **check the temperatures**. Cura's generic PLA
  is 200 °C / 60 °C bed; Bambu uses 220 °C for PLA.
* Set **Build Plate Type** to the plate you're using.
* Slice → **Send to Bambu Connect** → choose the AMS slot or external spool → Print.
* Watch the first layer. The default speeds are moderate. Raise *Max Volumetric Speed* once you trust a filament.

## Limitations (v0.1)

* **Single colour only.** You can print from any AMS slot, but there's no multi-colour or multi-material
  switching yet. Cura can model this (4 "extruders" sharing one nozzle), and Bambu's filament-change G-code
  is already bundled, but it needs careful testing on a real printer first.
* No skip-object support (Bambu's object labels), and no printer status or camera in Cura. Bambu Connect
  and Bambu Handy handle that.
* The thumbnail is Cura's top-view snapshot.
* Time estimates come from Cura, plus the time added by the volumetric-speed limit.

## For developers

```
plugin/CuraToBambu/          the plugin (what gets installed)
  bbl/template.py            renderer for Bambu/PrusaSlicer G-code macros ({if}, [var], {expr})
  bbl/gcode.py               Cura G-code → Bambu G-code (start/end swap, layer/progress/timelapse, volumetric limit)
  bbl/package.py             .gcode.3mf writer (layout matches BambuStudio's bbs_3mf.cpp)
  bbl/connect.py             bambu-connect:// URL launch
  bbl/printers.json          Bambu machine data + G-code templates (generated)
  resources/                 Cura definitions, extruder, quality profiles (generated)
tools/extract_bambu_profiles.py   regenerate printers.json from a BambuStudio checkout
tools/make_definitions.py         regenerate the Cura definitions
tools/build_package.py            build the .curapackage
tests/test_bbl.py                 unit tests (python3 -m unittest discover -s tests)
tests/e2e/                        end-to-end tests that drive a real Cura 5.8.1 (Linux AppImage) under Xvfb
```

## Licence

This repository is distributed under the **AGPL-3.0** (see `LICENSE`), because `plugin/CuraToBambu/bbl/printers.json`
bundles G-code templates from BambuStudio's printer profiles, which are AGPL-3.0. The plugin's own source files
are also available under LGPL-3.0 (like Cura), as marked in their headers.

Not affiliated with or endorsed by Bambu Lab or UltiMaker.
