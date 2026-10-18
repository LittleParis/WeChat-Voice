# PyInstaller one-file build. Data inclusion is an explicit allowlist, never the workspace.
from pathlib import Path
import sys
from importlib.metadata import distribution
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

root = Path(SPECPATH)
upstream = root / "third_party/wechat-decrypt"
required = ["scripts/windows/extract_raw_key.py", "scripts/windows/decrypt_all.py",
            "scripts/common/crypto_backend.py", "scripts/common/wal_snapshot.py", "LICENSE"]
datas = [(str(root / "web"), "web"),
         (str(root / "scripts/Protect-PrivateDirectory.ps1"), "scripts"),
         (str(root / "THIRD_PARTY.md"), ".")]
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    datas.append((str(python_license), "licenses/python"))
for relative in required:
    path = upstream / relative
    if not path.is_file():
        raise SystemExit("Missing pinned desktop dependency: " + relative)
    datas.append((str(path), str(Path("third_party/wechat-decrypt") / Path(relative).parent)))
for name in ["frida", "pycryptodome", "pilk", "lameenc", "miniaudio", "pyinstaller", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6"]:
    package = distribution(name)
    for relative in package.files or []:
        if "license" in str(relative).lower() or "copying" in relative.name.lower() or "notice" in relative.name.lower():
            path = Path(package.locate_file(relative))
            if path.is_file():
                datas.append((str(path), "licenses/" + name + "/" + str(relative.parent)))
datas += collect_data_files("frida") + collect_data_files("pilk")
binaries = collect_dynamic_libs("frida") + collect_dynamic_libs("pilk")
a = Analysis([str(root / "scripts/desktop_app.py")], pathex=[str(root / "scripts")],
    binaries=binaries, datas=datas,
    hiddenimports=["Crypto.Cipher.AES", "Crypto.Hash.SHA512", "frida", "lameenc", "pilk", "miniaudio", "_miniaudio"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["pytest", "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"], noarchive=False)
# Use Qt's matching newer MSVC runtime at the bundle root as well. A preloaded
# older Python runtime DLL otherwise shadows the Qt copies in subdirectories.
qt_root = Path(distribution("PySide6_Essentials").locate_file("PySide6"))
crt = {name: qt_root / name for name in ["MSVCP140.dll", "MSVCP140_1.dll", "MSVCP140_2.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll"] if (qt_root / name).is_file()}
a.binaries = [entry for entry in a.binaries if entry[0].upper() not in {name.upper() for name in crt}]
a.binaries += [(name, str(path), "BINARY") for name, path in crt.items()]
# Qt expects the unversioned ICU API supplied by supported Windows versions.
# A third-party icuuc.dll can expose only suffixed symbols and prevent Qt loading.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() != "icuuc.dll"]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="VoiceKeep", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False, disable_windowed_traceback=True)
