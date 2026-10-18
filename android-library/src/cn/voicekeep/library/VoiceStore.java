package cn.voicekeep.library;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.io.File;
import java.util.ArrayList;
import java.util.List;

public final class VoiceStore extends SQLiteOpenHelper {
    public static final class Chat {
        public String id, name, kind, date;
        public int count;
    }
    public static final class Voice {
        public String id, chatId, senderId, sender, date, time, hash;
        public long timestamp;
        public double seconds;
    }
    public static final class Sender {
        public String id, name;
        public int count;
        @Override public String toString() { return name + " · " + count; }
    }
    public static final class ImportRecord {
        public String source, packageCreated;
        public long imported;
        public int total, added, repaired, duplicates;
    }
    public static final class StorageInfo {
        public int voices, chats, missing;
        public long bytes;
    }
    private final File audio;

    public VoiceStore(Context context) {
        super(context, "voices.db", null, 2);
        audio = new File(context.getFilesDir(), "audio");
        if (!audio.isDirectory() && !audio.mkdirs()) throw new IllegalStateException("无法创建音频目录");
        setWriteAheadLoggingEnabled(true);
    }
    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE chats(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL)");
        db.execSQL("CREATE TABLE voices(id TEXT PRIMARY KEY,chat_id TEXT NOT NULL,sender_id TEXT NOT NULL,sender TEXT NOT NULL,timestamp INTEGER NOT NULL,date TEXT NOT NULL,time TEXT NOT NULL,seconds REAL NOT NULL,sha256 TEXT NOT NULL)");
        db.execSQL("CREATE INDEX voice_order ON voices(chat_id,timestamp,id)");
        db.execSQL("CREATE INDEX voice_sender ON voices(chat_id,sender_id,date)");
        createImports(db);
    }
    private void createImports(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE imports(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,package_created TEXT NOT NULL,imported INTEGER NOT NULL,total INTEGER NOT NULL,added INTEGER NOT NULL,repaired INTEGER NOT NULL,duplicates INTEGER NOT NULL)");
    }
    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        if (oldVersion < 2) createImports(db);
    }
    public List<ImportRecord> imports() {
        List<ImportRecord> result = new ArrayList<>();
        try (Cursor c = getReadableDatabase().rawQuery("SELECT source,package_created,imported,total,added,repaired,duplicates FROM imports ORDER BY id DESC LIMIT 100", null)) {
            while (c.moveToNext()) {
                ImportRecord r = new ImportRecord(); r.source=c.getString(0); r.packageCreated=c.getString(1);
                r.imported=c.getLong(2); r.total=c.getInt(3); r.added=c.getInt(4); r.repaired=c.getInt(5); r.duplicates=c.getInt(6); result.add(r);
            }
        }
        return result;
    }
    public StorageInfo storageInfo() {
        StorageInfo info = new StorageInfo(); info.voices=count(); info.chats=chats("", "").size();
        try (Cursor c = getReadableDatabase().rawQuery("SELECT id,chat_id,sender_id,sender,timestamp,date,time,seconds,sha256 FROM voices", null)) {
            while (c.moveToNext()) { File file=audioFile(readVoice(c)); if(file.isFile()&&file.length()>0)info.bytes+=file.length();else info.missing++; }
        }
        return info;
    }
    public File audioFile(Voice voice) { return new File(audio, voice.id + "-" + voice.hash + ".mp3"); }
    public int count() {
        try (Cursor cursor = getReadableDatabase().rawQuery("SELECT count(*) FROM voices", null)) { cursor.moveToFirst(); return cursor.getInt(0); }
    }
    public List<Chat> chats(String kind, String search) {
        List<Chat> result = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().rawQuery("SELECT c.id,c.name,c.kind,count(v.id),max(v.date) FROM chats c JOIN voices v ON v.chat_id=c.id WHERE (?='' OR c.kind=?) AND instr(lower(c.name),lower(?))>0 GROUP BY c.id ORDER BY max(v.timestamp) DESC,c.id", new String[]{kind,kind,search})) {
            while (cursor.moveToNext()) {
                Chat chat = new Chat(); chat.id=cursor.getString(0); chat.name=cursor.getString(1); chat.kind=cursor.getString(2); chat.count=cursor.getInt(3); chat.date=cursor.getString(4); result.add(chat);
            }
        }
        return result;
    }
    public List<Sender> senders(String chat) {
        List<Sender> result = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().rawQuery("SELECT sender_id,max(sender),count(*) FROM voices WHERE chat_id=? GROUP BY sender_id ORDER BY count(*) DESC,sender_id",new String[]{chat})) {
            while (cursor.moveToNext()) {
                Sender sender=new Sender();sender.id=cursor.getString(0);sender.name=cursor.getString(1);sender.count=cursor.getInt(2);result.add(sender);
            }
        }
        // Keep same-name members distinct and recognizable in the picker.
        for (Sender sender : result) {
            int same=0; for (Sender other : result) if (other.name.equals(sender.name)) same++;
            if (same>1) sender.name += " · " + sender.id.substring(Math.max(0,sender.id.length()-5));
        }
        return result;
    }
    public List<Voice> voices(String chat, String sender, String since, String until, boolean ascending) {
        List<Voice> result=new ArrayList<>();
        String order=ascending?"ASC":"DESC";
        try (Cursor cursor=getReadableDatabase().rawQuery("SELECT id,chat_id,sender_id,sender,timestamp,date,time,seconds,sha256 FROM voices WHERE chat_id=? AND (?='' OR sender_id=?) AND (?='' OR date>=?) AND (?='' OR date<=?) ORDER BY timestamp "+order+",id "+order,new String[]{chat,sender,sender,since,since,until,until})) {
            while(cursor.moveToNext())result.add(readVoice(cursor));
        }
        return result;
    }
    public Voice find(String id) {
        try(Cursor cursor=getReadableDatabase().rawQuery("SELECT id,chat_id,sender_id,sender,timestamp,date,time,seconds,sha256 FROM voices WHERE id=?",new String[]{id})) { return cursor.moveToFirst()?readVoice(cursor):null; }
    }
    private Voice readVoice(Cursor cursor) {
        Voice v=new Voice();v.id=cursor.getString(0);v.chatId=cursor.getString(1);v.senderId=cursor.getString(2);v.sender=cursor.getString(3);v.timestamp=cursor.getLong(4);v.date=cursor.getString(5);v.time=cursor.getString(6);v.seconds=cursor.getDouble(7);v.hash=cursor.getString(8);return v;
    }
    public static ContentValues values(Voice voice) {
        ContentValues values=new ContentValues();values.put("id",voice.id);values.put("chat_id",voice.chatId);values.put("sender_id",voice.senderId);values.put("sender",voice.sender);values.put("timestamp",voice.timestamp);values.put("date",voice.date);values.put("time",voice.time);values.put("seconds",voice.seconds);values.put("sha256",voice.hash);return values;
    }
}
