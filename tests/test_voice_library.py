import array
from contextlib import contextmanager
from datetime import datetime
import hashlib
from http.cookiejar import CookieJar
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import miniaudio
import pilk
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from export_original_voices import SHANGHAI
from local_library_server import LibraryServer
from voice_library import VoiceLibrary, opaque, silk_seconds


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "db"
    (root / "message").mkdir(parents=True)
    (root / "contact").mkdir()
    pcm, silk = tmp_path / "tone.pcm", tmp_path / "tone.silk"
    samples = array.array("h", (int(8000 * math.sin(i * 2 * math.pi * 440 / 24000)) for i in range(24000)))
    if sys.byteorder != "little": samples.byteswap()
    pcm.write_bytes(samples.tobytes())
    pilk.SilkEncoder(pcm_rate=24000, silk_rate=24000).encode(str(pcm), str(silk), tencent=True)
    blob = silk.read_bytes()
    start = int(datetime(2026, 10, 1, tzinfo=SHANGHAI).timestamp())
    for shard, chat, records in [(0, "team@chatroom", [(10,100,start),(11,101,start+86399),(12,102,start+86400)]),
                                  (1, "direct-chat", [(20,200,start+172800)])]:
        with sqlite3.connect(root / f"message/media_{shard}.db") as db:
            db.executescript("CREATE TABLE Name2Id(user_name TEXT); CREATE TABLE VoiceInfo(chat_name_id INTEGER,local_id INTEGER,svr_id INTEGER,create_time INTEGER,voice_data BLOB)")
            db.execute("INSERT INTO Name2Id(rowid,user_name) VALUES(1,?)", (chat,))
            db.executemany("INSERT INTO VoiceInfo VALUES(1,?,?,?,?)", [(local,server,stamp,blob) for local,server,stamp in records])
        table = "Msg_" + hashlib.md5(chat.encode()).hexdigest()
        with sqlite3.connect(root / f"message/message_{shard}.db") as db:
            db.executescript("CREATE TABLE Name2Id(user_name TEXT)")
            db.execute("INSERT INTO Name2Id(rowid,user_name) VALUES(7,?)", ("alice" if shard==0 else "charlie",))
            if shard == 0: db.execute("INSERT INTO Name2Id(rowid,user_name) VALUES(8,'bob')")
            db.execute(f'CREATE TABLE "{table}"(local_id INTEGER,server_id INTEGER,create_time INTEGER,local_type INTEGER,real_sender_id INTEGER)')
            for index,(local,server,stamp) in enumerate(records[:2] if shard==0 else records):
                db.execute(f'INSERT INTO "{table}" VALUES(?,?,?,?,?)',(local,server,stamp,34,7+index if shard==0 else 7))
    with sqlite3.connect(root / "contact/contact.db") as db:
        db.execute("CREATE TABLE contact(username TEXT,remark TEXT,nick_name TEXT)")
        db.executemany("INSERT INTO contact VALUES(?,?,?)", [("team@chatroom","测试群",""),("direct-chat","联系人",""),
                       ("alice","同名成员",""),("bob","同名成员",""),("charlie","好友","\"")])
    return VoiceLibrary(root, tmp_path / "cache")


def test_conversation_grouping_and_sender_identity_not_display_name(library):
    overview = library.overview()
    assert overview["voice_count"] == 4
    assert overview["chat_count"] == 2 and overview["group_count"] == 1
    group = next(chat for chat in library.chats if chat["kind"] == "group")
    senders = library.senders(group["id"])
    same_name = [sender for sender in senders if sender["name"] == "同名成员"]
    assert len(same_name) == 2 and same_name[0]["id"] != same_name[1]["id"]
    assert overview["sender_unknown"] == 1
    assert len(library.filtered({"chat":group["id"],"sender":opaque("sender:alice")})) == 1


def test_voice_identity_survives_local_id_and_row_reassignment(library):
    before={row['id'] for row in library.records}
    for path in (library.root/'message').glob('media_*.db'):
        with sqlite3.connect(path) as db:
            db.execute('UPDATE VoiceInfo SET local_id=local_id+10000,rowid=rowid+50')
    for path in (library.root/'message').glob('message_*.db'):
        with sqlite3.connect(path) as db:
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall():
                db.execute(f'UPDATE "{table}" SET local_id=local_id+10000')
    refreshed=VoiceLibrary(library.root,library.cache)
    assert {row['id'] for row in refreshed.records}==before
    assert refreshed.overview()['sender_unknown']==1


def test_inclusive_dates_and_time_sorting(library):
    group = next(chat for chat in library.chats if chat["kind"] == "group")
    rows = library.filtered({"chat":group["id"],"since":"2026-10-01","until":"2026-10-01","sort":"asc"})
    assert len(rows) == 2
    assert rows[0]["time"] == "00:00:00" and rows[1]["time"] == "23:59:59"
    assert rows[::-1] == library.filtered({"chat":group["id"],"since":"2026-10-01","until":"2026-10-01","sort":"desc"})


def test_sender_from_other_conversation_rejected(library):
    group = next(chat for chat in library.chats if chat["kind"] == "group")
    with pytest.raises(ValueError,match="不属于"):
        library.filtered({"chat":group["id"],"sender":opaque("sender:charlie")})
    with pytest.raises(ValueError): library.filtered({"chat":group["id"],"since":"2026-10-02","until":"2026-10-01"})


def test_pagination_and_missing_sender_are_explicit(library):
    group = next(chat for chat in library.chats if chat["kind"] == "group")
    page = library.page({"chat":group["id"],"offset":"0","limit":"1"})
    assert page["total"] == 3 and len(page["items"]) == 1
    unknown = library.filtered({"chat":group["id"],"sender":"unknown"})
    assert len(unknown) == 1 and unknown[0]["sender"] == "未知发送人"
    assert all(row["seconds"] == 1.0 for row in library.records)
    with pytest.raises(ValueError): library.page({"chat":group["id"],"offset":"-1"})


def test_silk_duration_handles_truncation_and_terminator():
    assert silk_seconds(b"\x02#!SILK_V3\x03\x00abc") == .02
    assert silk_seconds(b"\x02#!SILK_V3\x03\x00abc\xff\xff") == .02
    assert silk_seconds(b"\x02#!SILK_V3\x03\x00ab") is None
    assert silk_seconds(b"\x02#!SILK_V3\x03\x00abcX") is None
    assert silk_seconds(b"not audio") is None


@contextmanager
def serve(library):
    project = Path(__file__).parents[1]
    server = LibraryServer(("127.0.0.1",0),library,project/"web",library.cache.parent/"exports")
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        server.executor.shutdown(wait=True)


def browser(base):
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    html = opener.open(base).read().decode("utf-8")
    token = re.search(r'name="voice-token" content="([a-f0-9]+)"',html)[1]
    return opener,token


def post(opener,base,path,payload,token):
    request = Request(base+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json","X-Voice-Token":token})
    with opener.open(request) as response: return json.load(response)


def test_local_session_host_and_csrf_protection(library):
    with serve(library) as (server,base):
        with pytest.raises(HTTPError) as error: build_opener().open(base+"/api/overview")
        assert error.value.code == 403
        opener,token = browser(base)
        assert json.load(opener.open(base+"/api/overview"))["voice_count"] == 4
        with pytest.raises(HTTPError) as error: opener.open(Request(base,headers={"Host":"untrusted.invalid"}))
        assert error.value.code == 403
        with pytest.raises(HTTPError) as error: post(opener,base,"/api/prepare",{"id":library.records[0]["id"]},"wrong-token")
        assert error.value.code == 403
        assert not list(library.cache.glob("*.mp3"))


def test_on_demand_audio_and_range_requests_are_real_mp3(library):
    with serve(library) as (_,base):
        opener,token = browser(base)
        voice = library.records[0]
        prepared = post(opener,base,"/api/prepare",{"id":voice["id"]},token)
        with opener.open(base+prepared["url"]) as response:
            encoded = response.read()
            assert response.headers["Content-Type"] == "audio/mpeg"
        decoded = miniaudio.decode(encoded,nchannels=1,sample_rate=24000)
        assert .95 < len(decoded.samples)/24000 < 1.2
        with opener.open(Request(base+prepared["url"],headers={"Range":"bytes=10-29"})) as response:
            assert response.status == 206 and response.read() == encoded[10:30]
        with opener.open(Request(base+prepared["url"],headers={"Range":"bytes=-12"})) as response:
            assert response.read() == encoded[-12:]
        with pytest.raises(HTTPError) as error: opener.open(Request(base+prepared["url"],headers={"Range":"bytes=999999-"}))
        assert error.value.code == 416
        with opener.open(base+prepared["download"]) as response:
            assert "attachment" in response.headers["Content-Disposition"]
            assert response.read() == encoded


def test_filtered_export_scope_matches_ui_and_preserves_original(library):
    with serve(library) as (_,base):
        opener,token = browser(base)
        group = next(chat for chat in library.chats if chat["kind"] == "group")
        job = post(opener,base,"/api/export",{"chat":group["id"],"sender":opaque("sender:alice"),"since":"2026-10-01","until":"2026-10-01","sort":"asc"},token)
        deadline = time.monotonic()+10
        while job["state"] not in ("done","error") and time.monotonic()<deadline:
            time.sleep(.03)
            job = json.load(opener.open(base+"/api/jobs/"+job["id"]))
        assert job["state"] == "done" and job["exported"] == 1 and job["total"] == 1
        manifest = json.loads((Path(job["output"])/"manifest.json").read_text(encoding="utf-8"))
        assert manifest["exported"][0]["sender"] == "alice"
        original = Path(job["output"])/manifest["exported"][0]["original_file"]
        assert hashlib.sha256(original.read_bytes()).hexdigest() == manifest["exported"][0]["original_sha256"]
