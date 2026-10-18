"""Portable offline MP3 + metadata bundles. No database or keys are included."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from export_original_voices import SHANGHAI
from voice_library import VoiceLibrary


class PackageCancelled(Exception):
    pass


def create_package(library, target, records, progress=None, should_cancel=None):
    target = Path(target)
    if target.exists():
        raise FileExistsError("数据包已存在，请使用新的文件名")
    records = list(records)
    if not records or len(records) > 50000:
        raise ValueError("数据包需要包含 1—50000 条语音")
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("数据包存在重复语音")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="package-", dir=target.parent) as directory:
        temporary = Path(directory) / "bundle.zip"
        manifest = {"format": "voicekeep", "version": 1, "timezone": "Asia/Shanghai",
                    "created_at": datetime.now(SHANGHAI).isoformat(), "chats": [], "voices": []}
        chat_ids = {row["chat_id"] for row in records}
        manifest["chats"] = [{key: chat[key] for key in ("id", "name", "kind")}
                             for chat in library.chats if chat["id"] in chat_ids]
        total_bytes = 0
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as bundle:
            for index, record in enumerate(records):
                if should_cancel and should_cancel():
                    raise PackageCancelled()
                audio = library.prepare(record["id"])
                size = audio.stat().st_size
                total_bytes += size
                if not 0 < size <= 32 * 1024 * 1024 or total_bytes > 2 * 1024**3:
                    raise ValueError("数据包过大，请缩小筛选范围")
                entry = "audio/" + record["id"] + ".mp3"
                bundle.write(audio, entry)
                manifest["voices"].append({**record, "file": entry,
                    "sha256": hashlib.sha256(audio.read_bytes()).hexdigest()})
                if progress:
                    progress(index + 1, len(records), 0)
            if should_cancel and should_cancel():
                raise PackageCancelled()
            payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(payload) > 32 * 1024**2:
                raise ValueError("数据包索引过大，请缩小筛选范围")
            bundle.writestr("manifest.json", payload)
        # Hard-link creates the final name atomically without overwriting an existing bundle.
        import os
        os.link(temporary, target)
    return {"exported": len(records), "bytes": target.stat().st_size}


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=project / ".private/decrypted")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chat", help="Exact opaque chat ID; omit to package all local conversations")
    parser.add_argument("--sender", default="")
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="", help="Inclusive date in Asia/Shanghai")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    library = VoiceLibrary(args.input, project / ".private/library-audio")
    if args.sender and not args.chat:
        parser.error("--sender requires --chat")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    chats = [args.chat] if args.chat else [chat["id"] for chat in library.chats]
    records = []
    for chat in chats:
        records.extend(library.filtered({"chat": chat, "sender": args.sender,
            "since": args.since, "until": args.until, "sort": "desc"}))
    records.sort(key=lambda row: (row["timestamp"], row["id"]), reverse=True)
    if args.limit:
        records = records[:args.limit]
    print(json.dumps(create_package(library, args.output, records), ensure_ascii=False))


if __name__ == "__main__":
    main()
