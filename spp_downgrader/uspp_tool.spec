# PyInstaller spec for uspp_tool.exe (the bundled converter the Painter plugin calls).
# Build:  pyinstaller uspp_tool.spec --noconfirm
# Output: dist/uspp_tool.exe  (one file)  -> copy into universal_spp_plugin/bin/
#
# Bundles all data into a single "profiles/" dir inside the exe; uspp_tool sets
# SPP_PROFILE_DIR to it at startup, and the frozen-aware loaders read from there.
import os
from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = os.path.abspath(os.getcwd())          # run from spp_downgrader/
PROFILES = os.path.join(ROOT, "profiles")
LIB = os.path.join(ROOT, "spp_extractor", "lib")

# All json data flattened into the bundle's profiles/ dir (schemas, defaults, baking
# schema, primitive sizes, profiles, decisions, lossiness messages).
datas = [
    (os.path.join(PROFILES, "*.json"), "profiles"),
    (os.path.join(PROFILES, "decisions", "*.json"), "profiles/decisions"),
    (os.path.join(LIB, "v10_schema.json"), "profiles"),
    (os.path.join(LIB, "v10_baking_schema.json"), "profiles"),
    (os.path.join(ROOT, "lossiness_messages.json"), "profiles"),
]
# auto-generated per-version schemas/defaults (v8.1_schema.json, v9_defaults.json, ...)
for fn in os.listdir(LIB):
    if fn.endswith(("_schema.json", "_defaults.json")):
        datas.append((os.path.join(LIB, fn), "profiles"))

# h5py's HDF5 runtime DLLs (the top packaging risk — collect explicitly).
binaries = collect_dynamic_libs("h5py")

a = Analysis(
    ["uspp_tool.py"],
    pathex=[ROOT, os.path.join(ROOT, "spp_extractor"), os.path.join(ROOT, "spp_builder")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["h5py", "h5py.defs", "h5py.utils", "h5py._proxy", "h5py.h5ac",
                   "numpy", "mmh3", "yaml",
                   "lib.migration_profile", "lib.hbo_decode",
                   "lib.lossiness", "lib.config_manager", "lib.type_code_mapper",
                   "lib.hbo_parser", "lib.dict_remover", "hbo_encoder", "spp_builder",
                   "spp_extractor", "spp_ext_models", "spp_ext_decoder",
                   # hbo_reserializer is now a package; list its submodules explicitly.
                   "lib.hbo_reserializer", "lib.hbo_reserializer.runtime",
                   "lib.hbo_reserializer.models", "lib.hbo_reserializer.serializer",
                   "lib.hbo_reserializer._readers", "lib.hbo_reserializer._write_inline",
                   "lib.hbo_reserializer._write_registry", "lib.hbo_reserializer._transforms",
                   "lib.hbo_reserializer._schema", "lib.hbo_reserializer._helpers"],
    hookspath=[], runtime_hooks=[], excludes=["tkinter", "PySide2", "PySide6", "PyQt5"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="uspp_tool", debug=False, strip=False, upx=False, console=True,
)
