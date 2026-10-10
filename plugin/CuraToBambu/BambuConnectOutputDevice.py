# Copyright (c) 2026. Released under the terms of the LGPLv3 or higher.
"""'Send to Bambu Connect' button in Cura's output-device menu (next to 'Save to Disk')."""
import io
import os
import re
import time

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices

from UM.Application import Application
from UM.FileHandler.WriteFileJob import WriteFileJob
from UM.Logger import Logger
from UM.Message import Message
from UM.OutputDevice import OutputDeviceError
from UM.OutputDevice.OutputDevice import OutputDevice
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin
from UM.Mesh.MeshWriter import MeshWriter

from . import CuraBridge
from .bbl import connect

DEVICE_ID = "bambu_connect"
KEEP_FILES = 25  # how many of our own exports to keep in the export folder


def export_folder() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    folder = os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "Cura Bambu Prints")
    os.makedirs(folder, exist_ok=True)
    return folder


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name or "").strip(" .")
    return name[:80] or "cura_print"


class BambuConnectOutputDevice(OutputDevice):
    def __init__(self, writer) -> None:
        super().__init__(DEVICE_ID)
        self._writer = writer
        self.setName("Bambu Connect")
        self.setShortDescription("Send to Bambu Connect")
        self.setDescription("Send to Bambu Lab printer (via Bambu Connect)")
        self.setIconName("print")
        self.setPriority(5)  # above 'Save to Disk' so it is the default button for Bambu printers
        self._busy = False
        self._message = None
        self._job = None
        self._pending = ("", "")  # (path, job name) of the export in progress
        self._result_path = ""

    def requestWrite(self, nodes, file_name=None, limit_mimetypes=None, file_handler=None, filter_by_machine=False,
                     **kwargs) -> None:
        if self._busy:
            raise OutputDeviceError.DeviceBusyError()
        app = Application.getInstance()
        job_name = _safe_name(file_name or app.getPrintInformation().jobName)
        path = os.path.join(export_folder(), job_name + ".gcode.3mf")
        if os.path.exists(path):
            path = os.path.join(export_folder(), "%s_%s.gcode.3mf" % (job_name, time.strftime("%Y%m%d-%H%M%S")))

        self._busy = True
        self.writeStarted.emit(self)
        self._message = Message("Preparing the Bambu print file...", lifetime=0, dismissable=False, progress=-1,
                                title="Bambu Connect")
        self._message.show()
        stream = io.BytesIO()
        job = WriteFileJob(self._writer, stream, nodes, MeshWriter.OutputMode.BinaryMode)
        job.setFileName(path)
        job.setAddToRecentFiles(False)
        # UM signals only keep weak references, so connect bound methods (a lambda would be garbage-collected).
        job.finished.connect(self._onWriteFinished)
        self._job = job
        self._pending = (path, job_name)
        job.start()

    def _onWriteFinished(self, job) -> None:
        path, job_name = self._pending
        self._job = None
        self._result_path = path
        self._busy = False
        if self._message:
            self._message.hide()
            self._message = None
        if not job.getResult():
            info = self._writer.getInformation() or "Unknown error while creating the print file."
            Message(info, title="Bambu Connect", message_type=Message.MessageType.ERROR).show()
            self.writeError.emit(self)
            return
        try:
            with open(path, "wb") as f:
                f.write(job.getStream().getvalue())
        except OSError as e:
            Message("Could not save %s: %s" % (path, e), title="Bambu Connect",
                    message_type=Message.MessageType.ERROR).show()
            self.writeError.emit(self)
            return
        self._cleanup_old_exports()
        self._writer.showWarnings()
        self.writeFinished.emit(self)

        if not connect.is_installed():
            msg = Message("Saved <b>%s</b>, but Bambu Connect doesn't seem to be installed. Install Bambu Connect, "
                          "then open this file with it (or click Send again)." % os.path.basename(path),
                          title="Bambu Connect not found", message_type=Message.MessageType.WARNING, lifetime=0)
            msg.addAction("download", "Get Bambu Connect", "", "Open the Bambu Connect download page")
            msg.addAction("folder", "Open folder", "", "Show the exported file")
            msg.actionTriggered.connect(self._onMessageAction)
            msg.show()
            return
        try:
            connect.open_url(connect.build_import_url(path, job_name))
        except OSError as e:
            Logger.logException("w", "Could not launch Bambu Connect")
            Message("Saved %s but could not open Bambu Connect: %s" % (path, e), title="Bambu Connect",
                    message_type=Message.MessageType.ERROR).show()
            return
        msg = Message("Sent <b>%s</b> to Bambu Connect. Choose the printer, plate and AMS slot there and press Print."
                      % job_name, title="Bambu Connect", message_type=Message.MessageType.POSITIVE)
        msg.addAction("folder", "Open folder", "", "Show the exported file")
        msg.actionTriggered.connect(self._onMessageAction)
        msg.show()

    def _onMessageAction(self, message, action: str) -> None:
        if action == "download":
            QDesktopServices.openUrl(QUrl(connect.DOWNLOAD_PAGE))
        elif action == "folder":
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self._result_path) or export_folder()))
        message.hide()

    @staticmethod
    def _cleanup_old_exports() -> None:
        try:
            folder = export_folder()
            files = sorted((os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".gcode.3mf")),
                           key=os.path.getmtime, reverse=True)
            for old in files[KEEP_FILES:]:
                os.remove(old)
        except OSError:
            Logger.logException("w", "Could not clean up old Bambu exports")


class BambuConnectOutputDevicePlugin(OutputDevicePlugin):
    """Shows the 'Send to Bambu Connect' device only while a Bambu printer is the active machine."""

    def __init__(self, writer) -> None:
        super().__init__()
        self._writer = writer
        self._device = None

    def start(self) -> None:
        app = Application.getInstance()
        app.globalContainerStackChanged.connect(self._update)
        self._update()

    def stop(self) -> None:
        self._remove()

    def _update(self) -> None:
        stack = Application.getInstance().getGlobalContainerStack()
        if CuraBridge.bambu_printer_id(stack):
            if self._device is None:
                self._device = BambuConnectOutputDevice(self._writer)
                self.getOutputDeviceManager().addOutputDevice(self._device)
        else:
            self._remove()

    def _remove(self) -> None:
        if self._device is not None:
            self.getOutputDeviceManager().removeOutputDevice(DEVICE_ID)
            self._device = None
