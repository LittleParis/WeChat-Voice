import array
import hashlib
import importlib.util
import math
from pathlib import Path
import sqlite3
import sys

import miniaudio
import pilk
import pytest

SPEC = importlib.util.spec_from_file_location("voice_export", Path(__file__).parents[1] / "scripts/export_original_voices.py")
exporter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)


def media_db(root, shard, chat_rowid, chat, voice_id, server, created, blob=b"\x02#!SILK_V3synthetic"):
    directory = root / "message"
    directory.mkdir(exist_ok=True)
    path = directory / f"media_{shard}.db"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE Name2Id(user_name TEXT); CREATE TABLE VoiceInfo(chat_name_id INTEGER,local_id INTEGER,svr_id INTEGER,create_time INTEGER,voice_data BLOB);")
        db.execute("INSERT INTO Name2Id(rowid,user_name) VALUES(?,?)", (chat_rowid, chat))
        db.execute("INSERT INTO VoiceInfo VALUES(?,?,?,?,?)", (chat_rowid, voice_id, server, created, blob))
    return path


def message_db(root, shard, chat, sender_rowid, sender, local_id, server, created):
    table = "Msg_" + hashlib.md5(chat.encode()).hexdigest()
    with sqlite3.connect(root / f"message/message_{shard}.db") as db:
        db.execute("CREATE TABLE Name2Id(user_name TEXT)")
        db.execute("INSERT INTO Name2Id(rowid,user_name) VALUES(?,?)", (sender_rowid, sender))
        db.execute(f'CREATE TABLE "{table}"(local_id INTEGER,server_id INTEGER,create_time INTEGER,local_type INTEGER,real_sender_id INTEGER)')
        db.execute(f'INSERT INTO "{table}" VALUES(?,?,?,?,?)', (local_id, server, created, 34, sender_rowid))


def test_sender_mapping_uses_each_message_shards_own_name2id(tmp_path):
    media_db(tmp_path, 0, 7, "chat-a", 11, 9001, 1000)
    media_db(tmp_path, 1, 7, "chat-b", 11, 9001, 1000)
    message_db(tmp_path, 0, "chat-a", 7, "sender-a", 11, 9001, 1000)
    message_db(tmp_path, 1, "chat-b", 7, "sender-b", 11, 9001, 1000)
    voices = exporter.inventory(tmp_path)
    assert len(voices) == 2  # Same server ID in different chats is not a duplicate.
    reader = exporter.Messages(tmp_path)
    try:
        assert {voice.chat: reader.resolve(voice)["sender"] for voice in voices} == {"chat-a": "sender-a", "chat-b": "sender-b"}
    finally:
        reader.close()


def test_exact_matching_refuses_local_id_collision(tmp_path):
    media_db(tmp_path, 0, 1, "chat-a", 11, 9001, 1000)
    message_db(tmp_path, 0, "chat-a", 1, "wrong-sender", 11, 9999, 1000)
    reader = exporter.Messages(tmp_path)
    try:
        result = reader.resolve(exporter.inventory(tmp_path)[0])
        assert result["sender"] == ""
        assert result["association"] == "media-only-no-exact-message"
    finally:
        reader.close()


def test_duplicate_shards_deduplicated_by_chat_server_time(tmp_path):
    media_db(tmp_path, 0, 1, "chat-a", 11, 9001, 1000)
    media_db(tmp_path, 1, 99, "chat-a", 99, 9001, 1000)
    assert len(exporter.inventory(tmp_path)) == 1


def test_missing_chat_mapping_is_not_silently_exported(tmp_path):
    path = media_db(tmp_path, 0, 1, "chat-a", 11, 9001, 1000)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM Name2Id")
    with pytest.raises(ValueError, match="Missing chat mapping"):
        exporter.inventory(tmp_path)


def test_silk_blob_to_real_decodable_mp3_and_original_bytes_retained(tmp_path):
    pcm = tmp_path / "tone.pcm"
    raw = array.array("h", (int(9000 * math.sin(i * 2 * math.pi * 440 / 24000)) for i in range(24000)))
    if sys.byteorder != "little":
        raw.byteswap()
    pcm.write_bytes(raw.tobytes())
    silk = tmp_path / "source.silk"
    pilk.SilkEncoder(pcm_rate=24000, silk_rate=24000).encode(str(pcm), str(silk), tencent=True)
    blob = silk.read_bytes()
    original, mp3 = tmp_path / "original.silk", tmp_path / "result.mp3"
    result = exporter.encode_blob(blob, original, mp3)
    assert original.read_bytes() == blob
    decoded = miniaudio.decode(mp3.read_bytes(), nchannels=1, sample_rate=24000)
    duration = len(decoded.samples) / 24000
    rms = math.sqrt(sum(int(sample) ** 2 for sample in decoded.samples) / len(decoded.samples))
    assert 0.9 < result["decoded_seconds"] < 1.1
    assert 0.9 < duration < 1.2
    assert 2000 < rms < 10000
    assert result["original_sha256"] == hashlib.sha256(blob).hexdigest()


def test_unknown_blob_rejected_without_creating_audio(tmp_path):
    with pytest.raises(ValueError, match="Unsupported voice format"):
        exporter.encode_blob(b"not audio", tmp_path / "bad.silk", tmp_path / "bad.mp3")
    assert not (tmp_path / "bad.mp3").exists()
    assert not (tmp_path / "bad.silk").exists()


@pytest.mark.parametrize("name", ["../../bad/name", "CON", "NUL.mp3", "a:b?c*", " "])
def test_windows_filename_sanitization(name):
    result = exporter.safe_name(name)
    assert result and not any(character in result for character in '<>:"/\\|?*')
    assert result not in ("CON", "NUL.mp3", ".", "..")
