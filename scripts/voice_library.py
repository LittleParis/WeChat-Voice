"""Local conversation/sender index and on-demand original voice conversion."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import tempfile
import threading

from export_original_voices import SHANGHAI, date_start, encode_blob, inventory, load_contacts, readonly


def opaque(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def silk_seconds(blob):
    if blob.startswith(b"\x02#!SILK_V3"):
        position = 10
    elif blob.startswith(b"#!SILK_V3"):
        position = 9
    else:
        return None
    frames = 0
    while position + 2 <= len(blob):
        length = int.from_bytes(blob[position:position + 2], "little", signed=True)
        position += 2
        if length == -1:
            return round(frames * .02, 2) if position == len(blob) else None
        if length < 0 or position + length > len(blob):
            return None
        position += length
        frames += 1
    return round(frames * .02, 2) if position == len(blob) else None


def sender_metadata(root, voices):
    """Resolve each message shard's own sender map in bulk, without reading text."""
    result, conflicts = {}, set()
    tables = {"Msg_" + hashlib.md5(voice.chat.encode()).hexdigest(): voice.chat for voice in voices}
    for path in sorted((root / "message").glob("message_*.db")):
        if not re.fullmatch(r"message_\d+\.db", path.name):
            continue
        with closing(readonly(path)) as db:
            available = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "Name2Id" not in available:
                continue
            for table in tables.keys() & available:
                rows = db.execute(f'''SELECT m.local_id,m.server_id,m.create_time,n.user_name AS sender
                    FROM "{table}" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id
                    WHERE (m.local_type & 4294967295)=34''')
                for row in rows:
                    key = (tables[table], row["local_id"], row["server_id"] or 0, row["create_time"])
                    sender = row["sender"] or ""
                    if key in result and result[key] != sender:
                        conflicts.add(key)
                    result[key] = sender
    for key in conflicts:
        result.pop(key, None)
    return result


class VoiceLibrary:
    def __init__(self, root, cache, allow_empty=False):
        self.root, self.cache = Path(root).resolve(), Path(cache).resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.contacts = load_contacts(self.root)
        self.voices = [] if allow_empty and not list((self.root / "message").glob("media_*.db")) else inventory(self.root)
        mapping = sender_metadata(self.root, self.voices)
        durations = {}
        for path in {voice.database for voice in self.voices}:
            with closing(readonly(path)) as db:
                for row in db.execute("SELECT rowid,voice_data FROM VoiceInfo"):
                    durations[(path.name, row["rowid"])] = silk_seconds(bytes(row["voice_data"] or b""))
        self.records, self.by_id, chats = [], {}, {}
        self.by_chat = defaultdict(list)
        self.lock = threading.Lock()
        self.audio_locks = {}
        self.built_at = datetime.now(SHANGHAI).isoformat()
        for voice in self.voices:
            sender = mapping.get((voice.chat, voice.local_id, voice.server_id, voice.created), "")
            chat_id = opaque("chat:" + voice.chat)
            # Server message identity survives desktop imports and database shard moves.
            # A local-only record has no portable server identity, so keep its scoped fallback.
            voice_id = opaque(json.dumps(["server", voice.chat, voice.server_id, voice.created]
                if voice.server_id else ["local", voice.chat, voice.local_id, voice.created, voice.database.name, voice.rowid]))
            sender_id = opaque("sender:" + sender) if sender else "unknown"
            stamp = datetime.fromtimestamp(voice.created, SHANGHAI)
            record = {"id": voice_id, "chat_id": chat_id, "sender_id": sender_id,
                      "sender": self.contacts.get(sender, sender) if sender else "未知发送人",
                      "sender_known": bool(sender), "timestamp": voice.created,
                      "time": stamp.strftime("%H:%M:%S"), "date": stamp.strftime("%Y-%m-%d"),
                      "datetime": stamp.isoformat(), "seconds": durations.get((voice.database.name, voice.rowid))}
            self.records.append(record)
            self.by_id[voice_id] = (record, voice)
            self.by_chat[chat_id].append(record)
            if chat_id not in chats:
                chats[chat_id] = {"id": chat_id, "name": self.contacts.get(voice.chat, voice.chat),
                                  "kind": "group" if voice.chat.endswith("@chatroom") else "contact",
                                  "count": 0, "latest": voice.created, "latest_date": record["date"]}
            chats[chat_id]["count"] += 1
            chats[chat_id]["latest"] = max(chats[chat_id]["latest"], voice.created)
        self.chats = sorted(chats.values(), key=lambda chat: (chat["latest"], chat["id"]), reverse=True)
        self.chat_index = {chat["id"]: chat for chat in self.chats}

    def overview(self):
        return {"voice_count": len(self.records), "chat_count": len(self.chats),
                "group_count": sum(chat["kind"] == "group" for chat in self.chats),
                "sender_unknown": sum(not item["sender_known"] for item in self.records),
                "built_at": self.built_at, "chats": self.chats}

    def senders(self, chat_id):
        if chat_id not in self.chat_index:
            raise ValueError("会话不存在")
        counts = Counter(row["sender_id"] for row in self.by_chat[chat_id])
        names = {row["sender_id"]: row["sender"] for row in self.by_chat[chat_id]}
        return sorted(({"id": key, "name": names[key], "count": count} for key, count in counts.items()),
                      key=lambda item: (-item["count"], item["id"]))

    def filtered(self, query):
        chat = query.get("chat", "")
        if chat not in self.chat_index:
            raise ValueError("请选择有效会话")
        rows = list(self.by_chat[chat])
        sender = query.get("sender", "")
        if sender:
            valid = {row["sender_id"] for row in rows}
            if sender not in valid:
                raise ValueError("发送人不属于当前会话")
            rows = [row for row in rows if row["sender_id"] == sender]
        since, until = query.get("since", ""), query.get("until", "")
        if since and until and since > until:
            raise ValueError("开始日期不能晚于结束日期")
        if since:
            beginning = date_start(since)
            rows = [row for row in rows if row["timestamp"] >= beginning]
        if until:
            end = date_start(until) + 86400
            rows = [row for row in rows if row["timestamp"] < end]
        order = query.get("sort", "desc")
        if order not in ("asc", "desc"):
            raise ValueError("不支持的排序方式")
        rows.sort(key=lambda row: (row["timestamp"], row["id"]), reverse=order == "desc")
        return rows

    def page(self, query):
        rows = self.filtered(query)
        offset, limit = int(query.get("offset", 0)), int(query.get("limit", 50))
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("分页参数无效")
        return {"total": len(rows), "offset": offset, "limit": limit,
                "items": [{**row, "ready": self.audio_path(row["id"]).exists()} for row in rows[offset:offset + limit]]}

    def audio_path(self, voice_id):
        if voice_id not in self.by_id:
            raise ValueError("语音不存在")
        return self.cache / (voice_id + ".mp3")

    def prepare(self, voice_id):
        target = self.audio_path(voice_id)
        with self.lock:
            lock = self.audio_locks.setdefault(voice_id, threading.Lock())
        with lock:
            if target.exists():
                return target
            record, voice = self.by_id[voice_id]
            with closing(readonly(voice.database)) as db:
                row = db.execute("SELECT voice_data FROM VoiceInfo WHERE rowid=?", (voice.rowid,)).fetchone()
            if row is None:
                raise ValueError("原始语音记录已不可用，请刷新数据库副本")
            with tempfile.TemporaryDirectory(prefix="prepare-", dir=self.cache) as temporary:
                temporary = Path(temporary)
                encoded = temporary / "audio.mp3"
                encode_blob(bytes(row[0]), temporary / "original.silk", encoded)
                encoded.replace(target)
        return target

    def download_name(self, voice_id):
        from export_original_voices import safe_name
        record, _ = self.by_id[voice_id]
        name = self.chat_index[record["chat_id"]]["name"]
        return f"{record['date']}_{record['time'].replace(':', '')}_{safe_name(name, 28)}_{safe_name(record['sender'], 24)}_{voice_id[:6]}.mp3"
