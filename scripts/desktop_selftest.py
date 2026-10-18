"""Synthetic checks that also run from the frozen EXE without a system Python."""
import array
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from runtime_paths import resources, worker_command


def run(report):
    import frida
    import lameenc
    import miniaudio
    import pilk
    from Crypto.Cipher import AES
    from capture_desktop_key import load_extractor
    from desktop_connection import DesktopConnection
    checks = {}
    report = Path(report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        assert (resources() / "web/index.html").is_file()
        assert (resources() / "scripts/Protect-PrivateDirectory.ps1").is_file()
        extractor = load_extractor()
        extractor.main_pid()  # Read-only tasklist probe; no attachment or restart.
        checks["hidden_system_process_probe"] = True
        upstream = resources() / "third_party/wechat-decrypt/scripts/windows/decrypt_all.py"
        spec = importlib.util.spec_from_file_location("selfcheck_decrypt", upstream)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        checks["bundled_resources_and_upstream"] = True
        assert frida.get_local_device().type == "local"
        checks["frida_native_runtime"] = True
        with tempfile.TemporaryDirectory(prefix="voicekeep-selftest-", dir=report.parent) as folder:
            folder = Path(folder)
            source, silk, pcm = folder / "tone.pcm", folder / "tone.silk", folder / "decoded.pcm"
            samples = array.array("h", (int(6000 * math.sin(index * 2 * math.pi * 440 / 24000)) for index in range(24000)))
            if sys.byteorder != "little":
                samples.byteswap()
            source.write_bytes(samples.tobytes())
            pilk.SilkEncoder(pcm_rate=24000, silk_rate=24000).encode(str(source), str(silk), tencent=True)
            pilk.decode(str(silk), str(pcm), pcm_rate=24000)
            encoder = lameenc.Encoder()
            encoder.set_in_sample_rate(24000)
            encoder.set_channels(1)
            encoder.set_bit_rate(64)
            encoded = bytes(encoder.encode(pcm.read_bytes()) + encoder.flush())
            decoded = miniaudio.decode(encoded, nchannels=1, sample_rate=24000)
            assert .95 < len(decoded.samples) / 24000 < 1.25 and max(decoded.samples) > 1000
            checks["silk_mp3_native_roundtrip"] = True
            # Build a synthetic authenticated SQLCipher page, not a user's database.
            key, salt, iv = b"k" * 32, b"s" * 16, b"i" * 16
            derived = hashlib.pbkdf2_hmac("sha512", key, salt, 256000, 32)
            plain = bytearray(4000)
            plain[:8] = bytes([0x10, 0, 1, 1, 0x50, 0x40, 0x20, 0x20])
            page = bytearray(salt + AES.new(derived, AES.MODE_CBC, iv).encrypt(bytes(plain)) + iv + bytes(64))
            import hmac
            mac = hashlib.pbkdf2_hmac("sha512", derived, bytes(value ^ 0x3a for value in salt), 2, 32)
            page[4032:] = hmac.new(mac, page[16:4032] + struct.pack("<I", 1), hashlib.sha512).digest()
            database, keyfile = folder / "synthetic.db", folder / "key.txt"
            database.write_bytes(page)
            keyfile.write_text(key.hex(), encoding="ascii")
            assert module.decrypt_db(key, str(database), str(folder / "plain.db"))
            checks["sqlcipher_authentication_and_decrypt"] = True
            manager = DesktopConnection(folder, lambda _: None)
            manager.job = {"state": "running", "stage": "key", "completed": 0}
            try:
                manager.run_child("capture_desktop_key.py", ["--db", database, "--output", keyfile, "--timeout", 1])
                output = subprocess.run(worker_command("capture_desktop_key.py", ["--db", database, "--output", keyfile, "--timeout", 1]),
                                        capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                assert output.returncode == 0 and b"Existing key authenticates" in output.stdout
                checks["frozen_worker_and_redirected_output"] = True
            finally:
                manager.job["state"] = "done"
                manager.close()
        checks["passed"] = True
        return_code = 0
    except Exception as error:
        import traceback
        site = traceback.extract_tb(error.__traceback__)[-1]
        checks.update(passed=False, error=type(error).__name__, error_at=Path(site.filename).name + ":" + str(site.lineno))
        return_code = 1
    report.write_text(json.dumps(checks, indent=2), encoding="utf-8")
    return return_code
