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
for name in ["frida", "pycryptodome", "pilk", "lameenc", "miniaudio", "pyinstaller"]:
    package = distribution(name)
    for relative in package.files or []:
        if "license" in relative.name.lower() or "copying" in relative.name.lower() or "notice" in relative.name.lower():
            path = Path(package.locate_file(relative))
            if path.is_file():
                datas.append((str(path), "licenses/" + name + "/" + str(relative.parent)))
datas += collect_data_files("frida") + collect_data_files("pilk")
binaries = collect_dynamic_libs("frida") + collect_dynamic_libs("pilk")
a = Analysis([str(root / "scripts/desktop_app.py")], pathex=[str(root / "scripts")],
    binaries=binaries, datas=datas,
    hiddenimports=["Crypto.Cipher.AES", "Crypto.Hash.SHA512", "frida", "lameenc", "pilk", "miniaudio", "_miniaudio"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="VoiceKeep", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False, disable_windowed_traceback=True)
