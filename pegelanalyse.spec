# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for pegelanalyse.
# Build with:  pyinstaller pegelanalyse.spec
#
# Produces a single-file executable in dist/:
#   Windows : dist/pegelanalyse.exe
#   Linux   : dist/pegelanalyse
#   macOS   : dist/pegelanalyse

from PyInstaller.utils.hooks import collect_data_files

# tkinterdnd2 ships Tcl/Tk extension binaries (DLL/SO/dylib) that are loaded
# via Tcl's 'package require' mechanism — PyInstaller won't find them through
# normal import analysis.  collect_data_files preserves the tkdnd/<platform>/
# subdirectory layout that TkinterDnD.py expects at runtime.
datas = collect_data_files('tkinterdnd2')

# matplotlib needs its mpl-data directory (fonts, style sheets, backends).
datas += collect_data_files('matplotlib')

a = Analysis(
    ['pegelanalyse.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # matplotlib backends: tkagg for the interactive window,
        # agg for PNG/SVG file export via the subprocess worker.
        'matplotlib.backends.backend_tkagg',
        'matplotlib.backends.backend_agg',
        # Pillow needs this to bridge PIL's ImageTk with the Tk interpreter;
        # it is not discovered automatically by PyInstaller's import analysis.
        'PIL._tkinter_finder',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='pegelanalyse',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX disabled: causes antivirus false positives on Windows and does not
    # support Apple Silicon (arm64) binaries on macOS.
    upx=False,
    # Keep the console window so that CLI output (progress, errors) is visible.
    # In GUI mode a console window briefly appears — accepted trade-off.
    console=True,
    disable_windowed_traceback=False,
    # argv_emulation interferes with our own sys.argv check; leave it off.
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
