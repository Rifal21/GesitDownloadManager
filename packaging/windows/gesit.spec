# PyInstaller spec: one folder holding gesit.exe (GUI) and gesit-native-host.exe (console, stdlib only).
#   pyinstaller --noconfirm --distpath build/windows/dist --workpath build/windows/work packaging/windows/gesit.spec
# The two executables share one `_internal` folder (python DLL and libraries), so the host adds only a few MB.
import os

root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
icon = os.path.join(SPECPATH, "gesit.ico")
on_windows = os.name == "nt"

# The GUI never needs these; leaving them out keeps the installer small.
gui_excludes = [
    "tkinter", "unittest", "pydoc", "test", "distutils", "setuptools", "pip", "pytest",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtHelp", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtMultimedia", "PySide6.QtNetwork", "PySide6.QtPrintSupport",
]

gui = Analysis(
    [os.path.join(SPECPATH, "entry_gui.py")],
    pathex=[root],
    hiddenimports=["gesit.core.manager", "gesit.engine.media", "gesit.hostinstall", "winreg"] if on_windows
    else ["gesit.core.manager", "gesit.engine.media", "gesit.hostinstall"],
    excludes=gui_excludes,
    noarchive=False,
)
# The native host must stay import-light: it is started once per browser message.
host = Analysis(
    [os.path.join(SPECPATH, "entry_host.py")],
    pathex=[root],
    excludes=gui_excludes + ["PySide6", "shiboken6", "httpx", "httpcore", "anyio", "h11", "certifi", "idna", "sniffio"],
    noarchive=False,
)

gui_pyz = PYZ(gui.pure)
host_pyz = PYZ(host.pure)

gui_exe = EXE(
    gui_pyz, gui.scripts, [],
    exclude_binaries=True, name="gesit", icon=icon if on_windows else None,
    console=False, disable_windowed_traceback=False, upx=False,
)
host_exe = EXE(
    host_pyz, host.scripts, [],
    exclude_binaries=True, name="gesit-native-host", icon=icon if on_windows else None,
    console=True, upx=False,
)

coll = COLLECT(
    gui_exe, gui.binaries, gui.datas,
    host_exe, host.binaries, host.datas,
    strip=False, upx=False, name="Gesit",
)
