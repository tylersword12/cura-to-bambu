# Copyright (c) 2026. Released under the terms of the LGPLv3 or higher.
"""Hand a sliced file to Bambu Connect, Bambu Lab's official bridge for third-party slicers.

Bambu Connect registers the URL scheme documented at https://wiki.bambulab.com/en/software/bambu-connect :

    bambu-connect://import-file?path=<encodeURIComponent(abs path)>&name=<encodeURIComponent(name)>&version=1.0.0
"""
import os
import subprocess
import sys
from urllib.parse import quote

DOWNLOAD_PAGE = "https://wiki.bambulab.com/en/software/bambu-connect"
_JS_SAFE = "-_.!~*'()"  # characters JavaScript's encodeURIComponent leaves alone


def encode_uri_component(text: str) -> str:
    return quote(text, safe=_JS_SAFE)


def build_import_url(file_path: str, name: str) -> str:
    return "bambu-connect://import-file?path=%s&name=%s&version=1.0.0" % (
        encode_uri_component(os.path.abspath(file_path)), encode_uri_component(name))


def is_installed() -> bool:
    """Best-effort check that something handles bambu-connect:// links. Returns True when unsure."""
    if sys.platform.startswith("win"):
        try:
            import winreg
        except ImportError:
            return True
        try:  # HKEY_CLASSES_ROOT merges the per-user and machine-wide URL-scheme registrations
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "bambu-connect"))
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return any(os.path.isdir(p) for p in ("/Applications/Bambu Connect.app",
                                               os.path.expanduser("~/Applications/Bambu Connect.app")))
    return True


def open_url(url: str) -> None:
    """Open a custom-scheme URL with the OS handler (raises OSError on failure)."""
    if sys.platform.startswith("win"):
        os.startfile(url)  # type: ignore[attr-defined]  # passes the URL untouched to ShellExecute
    elif sys.platform == "darwin":
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url])
