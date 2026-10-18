"""Authenticate and decrypt only databases needed for original voice export."""
import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import re
from runtime_paths import resources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Account's db_storage directory")
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    destination = args.output.resolve()
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        parser.error("Output must be separate from the original WeChat data tree")
    raw = bytes.fromhex(args.key_file.read_text(encoding="ascii").strip())
    if len(raw) != 32:
        parser.error("Invalid key file; expected a verified 32-byte raw key")
    upstream = resources() / "third_party/wechat-decrypt/scripts/windows/decrypt_all.py"
    spec = importlib.util.spec_from_file_location("reviewed_decrypt", upstream)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected = sorted(path for path in source.glob("message/*.db")
                      if re.fullmatch(r"(?:media|message)_\d+\.db", path.name))
    contact = source / "contact/contact.db"
    if contact.exists():
        selected.append(contact)
    if not selected:
        parser.error("No supported message or media databases found")
    reports = []
    for path in selected:
        relative = path.relative_to(source)
        target = destination / relative
        print("Authenticating and decrypting:", relative, flush=True)
        if not module.decrypt_db(raw, str(path), str(target)):
            raise RuntimeError(f"Authentication or stable snapshot failed: {relative}")
        with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as db:
            checked = db.execute("PRAGMA quick_check").fetchone()[0]
            if checked != "ok":
                raise RuntimeError(f"SQLite quick_check failed: {relative}")
        reports.append({"database": str(relative), "bytes": target.stat().st_size, "authenticated": True, "quick_check": "ok"})
        print("Verified:", relative, flush=True)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "source-manifest.json").write_text(json.dumps({"databases": reports}, indent=2), encoding="utf-8")
    print(f"SUCCESS: {len(reports)} verified database copies; no message contents printed.")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    try:
        main()
    except Exception as error:
        print("Preparation failed:", type(error).__name__, str(error))
        raise SystemExit(1)
