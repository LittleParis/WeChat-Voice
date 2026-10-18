"""Capture only the local WeChat DB key, without killing or restarting WeChat.

The user exits WeChat and relaunches it from their own desktop. The reviewed,
MIT-licensed upstream module installs narrowly scoped SQLCipher startup hooks.
Raw key material is never printed or passed on a command line.
"""
import argparse
import hashlib
import hmac
import importlib.util
import os
from pathlib import Path
import struct
import sys
import time


def load_extractor():
    path = Path(__file__).resolve().parents[1] / "third_party/wechat-decrypt/scripts/windows/extract_raw_key.py"
    spec = importlib.util.spec_from_file_location("reviewed_extractor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def authenticate_first_page(key, page):
    if len(page) != 4096 or len(key) != 32:
        return False
    salt = page[:16]
    derived = hashlib.pbkdf2_hmac("sha512", key, salt, 256000, 32)
    mac_key = hashlib.pbkdf2_hmac("sha512", derived, bytes(x ^ 0x3a for x in salt), 2, 32)
    expected = hmac.new(mac_key, page[16:4032] + struct.pack("<I", 1), hashlib.sha512).digest()
    return hmac.compare_digest(expected, page[4032:4096])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows is required")
    if args.timeout < 1:
        parser.error("timeout must be positive")
    sys.stdout.reconfigure(line_buffering=True)
    with args.db.open("rb") as source:
        page = source.read(4096)
    if len(page) != 4096:
        parser.error("Selected database does not contain a full SQLCipher page")
    extractor = load_extractor()
    import frida
    if args.output.exists():
        try:
            key = bytes.fromhex(args.output.read_text(encoding="ascii").strip())
            if authenticate_first_page(key, page):
                print("Existing key authenticates the selected database; no restart needed.")
                return 0
        except (OSError, ValueError):
            pass
        parser.error("Output already exists and does not authenticate this database; refusing to overwrite")
    deadline = time.monotonic() + args.timeout
    if extractor.main_pid() is not None:
        print("READY: Exit WeChat from its tray menu, then reopen it manually. Waiting for exit...")
        extractor.wait_for_process(max(1, deadline - time.monotonic()), stopped=True)
    print("READY: Reopen WeChat from the desktop and log in. Waiting for startup...")
    pid = extractor.wait_for_process(max(1, deadline - time.monotonic()))
    print("Desktop WeChat detected; attaching startup-only database key verification.")
    key_hex = extractor.capture_key(frida.get_local_device(), pid, page, min(120, max(1, deadline - time.monotonic())))
    if not key_hex:
        print("No authenticated key captured. No database was decrypted.")
        return 1
    key = bytes.fromhex(key_hex)
    if not authenticate_first_page(key, page):
        print("Captured candidate failed SQLCipher page authentication; nothing saved.")
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as output:
        output.write(key_hex + "\n")
    print("SUCCESS: Local database key captured and page-authenticated. Key not displayed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # No key candidates or memory bytes are attached to exceptions by our wrapper.
        print("Capture failed:", type(error).__name__, str(error))
        raise SystemExit(1)
