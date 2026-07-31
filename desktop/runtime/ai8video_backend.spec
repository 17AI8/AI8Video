from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
ENTRY_PATH = PROJECT_ROOT / "desktop" / "runtime" / "backend_entry.py"

datas = [
    item
    for item in collect_data_files("ai8video", include_py_files=False)
    if ".DS_Store" not in item[0]
]
datas.extend([
    (
        str(PROJECT_ROOT / "用户字体" / "内置字体"),
        "runtime-defaults/用户字体/内置字体",
    ),
    (str(PROJECT_ROOT / "FONT_LICENSES.md"), "runtime-defaults"),
    (str(PROJECT_ROOT / "licenses"), "runtime-defaults/licenses"),
])

hiddenimports = collect_submodules("faster_whisper")

analysis = Analysis(
    [str(ENTRY_PATH)],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["_pytest", "fsspec.conftest", "mykey", "pytest", "sherpa_onnx"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ai8video-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ai8video-backend",
)
