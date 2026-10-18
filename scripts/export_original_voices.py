"""Export original WeChat 4.x voice BLOBs with exact message metadata to MP3.

Inputs are authenticated, decrypted SQLite copies. No recording, playback,
network, AI transcription, or chat-text export is involved.
"""
import argparse
from collections import Counter
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

SHANGHAI = timezone(timedelta(hours=8))


def readonly(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def columns(db, table):
    quoted = table.replace('"', '""')
    return {row[1] for row in db.execute(f'PRAGMA table_info("{quoted}")')}


@dataclass(frozen=True)
class Voice:
    database: Path
    rowid: int
    chat: str
    local_id: int
    server_id: int
    created: int
    size: int

    @property
    def identity(self):
        # Local IDs are not portable across chats or database shards.
        return (self.chat, self.server_id, self.created) if self.server_id else (
            self.chat, self.local_id, self.created, self.database.name, self.rowid)


def load_contacts(root):
    path = root / "contact/contact.db"
    if not path.exists():
        return {}
    with closing(readonly(path)) as db:
        required = {"username", "remark", "nick_name"}
        if not required <= columns(db, "contact"):
            raise ValueError("Unsupported contact schema; expected username/remark/nick_name")
        return {row["username"]: row["remark"] or row["nick_name"] or row["username"]
                for row in db.execute("SELECT username,remark,nick_name FROM contact") if row["username"]}


def inventory(root):
    voices, seen = [], set()
    media_paths = sorted(p for p in (root / "message").glob("media_*.db")
                         if re.fullmatch(r"media_\d+\.db", p.name))
    if not media_paths:
        raise ValueError("No decrypted media shards found")
    for path in media_paths:
        with closing(readonly(path)) as db:
            required = {"chat_name_id", "local_id", "svr_id", "create_time", "voice_data"}
            if not required <= columns(db, "VoiceInfo") or "user_name" not in columns(db, "Name2Id"):
                raise ValueError(f"Unsupported voice schema: {path.name}")
            sql = """SELECT v.rowid AS source_rowid,n.user_name AS chat,v.local_id,
                     v.svr_id,v.create_time,length(v.voice_data) AS bytes
                     FROM VoiceInfo v LEFT JOIN Name2Id n ON n.rowid=v.chat_name_id"""
            for row in db.execute(sql):
                if not row["chat"]:
                    raise ValueError(f"Missing chat mapping in {path.name}; refusing incorrect attribution")
                item = Voice(path, row["source_rowid"], row["chat"], row["local_id"],
                             row["svr_id"] or 0, row["create_time"], row["bytes"] or 0)
                if item.identity not in seen:
                    voices.append(item)
                    seen.add(item.identity)
    return sorted(voices, key=lambda voice: (voice.created, voice.server_id), reverse=True)


class Messages:
    def __init__(self, root):
        self.stack = ExitStack()
        self.shards = []
        for path in sorted((root / "message").glob("message_*.db")):
            if not re.fullmatch(r"message_\d+\.db", path.name):
                continue
            db = self.stack.enter_context(closing(readonly(path)))
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.shards.append((path.name, db, tables))

    def close(self):
        self.stack.close()

    def resolve(self, voice):
        table = "Msg_" + hashlib.md5(voice.chat.encode("utf-8")).hexdigest()
        candidates = []
        for name, db, tables in self.shards:
            if table not in tables:
                continue
            required = {"local_id", "server_id", "create_time", "local_type", "real_sender_id"}
            if not required <= columns(db, table) or "Name2Id" not in tables:
                raise ValueError(f"Unsupported message schema: {name}")
            sql = f'''SELECT m.local_id,m.server_id,m.create_time,n.user_name AS sender
                      FROM "{table}" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id
                      WHERE m.local_id=? AND m.server_id=? AND m.create_time=?
                      AND (m.local_type & 4294967295)=34'''
            for row in db.execute(sql, (voice.local_id, voice.server_id, voice.created)):
                candidates.append({"message_database": name, "sender": row["sender"] or "",
                                   "association": "exact-chat-local-server-time"})
        if not candidates:
            return {"message_database": "", "sender": "", "association": "media-only-no-exact-message"}
        senders = {item["sender"] for item in candidates}
        if len(senders) != 1:
            raise ValueError("Conflicting exact message matches; refusing incorrect sender attribution")
        return candidates[0]


def safe_name(value, maximum=42):
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")[:maximum].rstrip(" .")
    if not result:
        return "unknown"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", result):
        return "_" + result
    return result


def encode_blob(blob, original, mp3):
    import lameenc
    import pilk
    if blob.startswith(b"\x02#!SILK_V3"):
        normalized = blob[1:]
    elif blob.startswith(b"#!SILK_V3"):
        normalized = blob
    else:
        raise ValueError("Unsupported voice format: expected WeChat SILK v3")
    # The original file is byte-for-byte identical to the database BLOB.
    with original.open("xb") as output:
        output.write(blob)
    with tempfile.TemporaryDirectory(prefix="decode-", dir=mp3.parent) as temporary:
        temporary = Path(temporary)
        silk, pcm = temporary / "input.silk", temporary / "decoded.pcm"
        silk.write_bytes(normalized)
        pilk.decode(str(silk), str(pcm), pcm_rate=24000)
        pcm_size = pcm.stat().st_size
        if not pcm_size or pcm_size % 2:
            raise ValueError("SILK decoder produced invalid PCM16")
        encoder = lameenc.Encoder()
        encoder.set_in_sample_rate(24000)
        encoder.set_out_sample_rate(24000)
        encoder.set_channels(1)
        encoder.set_bit_rate(64)
        encoder.set_quality(2)
        staged = temporary / "encoded.mp3"
        with pcm.open("rb") as source, staged.open("xb") as destination:
            while True:
                chunk = source.read(48000)
                if not chunk:
                    break
                destination.write(encoder.encode(chunk))
            destination.write(encoder.flush())
        with staged.open("rb") as source:
            header = source.read(3)
        if staged.stat().st_size < 100 or not (header.startswith(b"ID3") or header[:1] == b"\xff"):
            raise ValueError("Encoder did not produce a valid MP3 header")
        with staged.open("rb") as source, mp3.open("xb") as destination:
            while True:
                chunk = source.read(65536)
                if not chunk:
                    break
                destination.write(chunk)
    return {"sample_rate": 24000, "channels": 1, "bitrate_kbps": 64,
            "decoded_seconds": pcm_size / 48000,
            "original_sha256": hashlib.sha256(blob).hexdigest(),
            "mp3_sha256": hashlib.sha256(mp3.read_bytes()).hexdigest()}


def date_start(value):
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=SHANGHAI).timestamp())


def export(root, output, voices, contacts, progress=None, should_cancel=None, verbose=True):
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"method": "original-database-blob", "recording_used": False,
                "timezone": "Asia/Shanghai", "selected": len(voices), "exported": [], "failed": []}
    staged = output / "manifest.partial.json"
    staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    messages = Messages(root)
    try:
        for number, voice in enumerate(voices, 1):
            if should_cancel and should_cancel():
                manifest["cancelled"] = True
                staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                break
            record = {"chat": voice.chat, "chat_display": contacts.get(voice.chat, voice.chat),
                      "local_id": str(voice.local_id), "server_id": str(voice.server_id),
                      "create_time": voice.created,
                      "time": datetime.fromtimestamp(voice.created, SHANGHAI).isoformat(),
                      "media_database": voice.database.name, "media_rowid": voice.rowid}
            try:
                record.update(messages.resolve(voice))
                record["sender_display"] = contacts.get(record["sender"], record["sender"] or "未知发送者")
                folder = output / (safe_name(record["chat_display"]) + "_" + hashlib.md5(voice.chat.encode()).hexdigest()[:8])
                folder.mkdir(exist_ok=True)
                timestamp = datetime.fromtimestamp(voice.created, SHANGHAI).strftime("%Y%m%d_%H%M%S")
                stem = timestamp + "_" + safe_name(record["sender_display"], 24) + "_" + str(voice.server_id or voice.local_id)
                original, mp3 = folder / (stem + ".silk"), folder / (stem + ".mp3")
                if original.exists() or mp3.exists():
                    stem += "_" + voice.database.stem + "_" + str(voice.rowid)
                    original, mp3 = folder / (stem + ".silk"), folder / (stem + ".mp3")
                with closing(readonly(voice.database)) as db:
                    row = db.execute("SELECT voice_data FROM VoiceInfo WHERE rowid=?", (voice.rowid,)).fetchone()
                    if row is None:
                        raise ValueError("Source voice row disappeared")
                    blob = bytes(row[0])
                record.update(encode_blob(blob, original, mp3))
                record["original_file"] = str(original.relative_to(output))
                record["mp3_file"] = str(mp3.relative_to(output))
                record["mp3_bytes"] = mp3.stat().st_size
                manifest["exported"].append(record)
                if verbose:
                    print(f"Exported {number}/{len(voices)}: {record['decoded_seconds']:.2f}s, {record['association']}", flush=True)
            except Exception as error:
                record["error"] = type(error).__name__ + ": " + str(error)
                manifest["failed"].append(record)
                if verbose:
                    print(f"Failed {number}/{len(voices)}: {type(error).__name__}", flush=True)
            staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            if progress:
                progress(number, len(voices), len(manifest["failed"]))
        staged.replace(output / "manifest.json")
    finally:
        messages.close()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Authenticated decrypted db_storage copy")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--contact", help="Exact username, remark, or nickname")
    parser.add_argument("--since", help="Inclusive date, YYYY-MM-DD, Asia/Shanghai")
    parser.add_argument("--until", help="Exclusive date, YYYY-MM-DD, Asia/Shanghai")
    parser.add_argument("--limit", type=int, help="Export newest N matching voices")
    parser.add_argument("--all", action="store_true", help="Explicitly export all matching voices")
    parser.add_argument("--inventory-only", action="store_true", help="Only report counts; no audio export")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be positive")
    if args.limit is not None and args.all:
        parser.error("choose --limit or --all")
    root = args.input.resolve(strict=True)
    contacts = load_contacts(root)
    voices = inventory(root)
    total = len(voices)
    if args.contact:
        usernames = {username for username, name in contacts.items() if args.contact == username or args.contact == name}
        usernames.add(args.contact)
        voices = [voice for voice in voices if voice.chat in usernames]
    if args.since:
        since = date_start(args.since)
        voices = [voice for voice in voices if voice.created >= since]
    if args.until:
        until = date_start(args.until)
        voices = [voice for voice in voices if voice.created < until]
    print(json.dumps({"available_voices": total, "matching_voices": len(voices),
                      "matching_chats": len({voice.chat for voice in voices})}))
    if args.inventory_only:
        return 0
    if args.limit is None and not args.all:
        parser.error("Use --limit N or --all to select the batch size")
    if not voices:
        parser.error("No matching original voice records")
    selected = voices[:args.limit] if args.limit else voices
    target = args.output or Path("exports") / datetime.now(SHANGHAI).strftime("original-voices-%Y%m%d-%H%M%S")
    target = target.resolve()
    if target == root or target.is_relative_to(root) or root.is_relative_to(target):
        parser.error("Output must be separate from the input database tree")
    if target.exists():
        parser.error("Output already exists; choose a fresh directory")
    report = export(root, target, selected, contacts)
    print(json.dumps({"exported": len(report["exported"]), "failed": len(report["failed"]),
                      "metadata_linked": sum(item["association"].startswith("exact-") for item in report["exported"]),
                      "output": str(target)}, ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    try:
        raise SystemExit(main())
    except (ValueError, sqlite3.DatabaseError) as error:
        print("Cannot export:", type(error).__name__, str(error), file=sys.stderr)
        raise SystemExit(1)
