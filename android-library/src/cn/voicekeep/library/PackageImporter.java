package cn.voicekeep.library;

import android.content.ContentValues;
import android.content.Context;
import android.database.sqlite.SQLiteDatabase;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

public final class PackageImporter {
    public interface Progress { void update(int completed,int total); }
    public static final class Result { public int added, duplicates, repaired; }
    private static final long MAX_AUDIO=32L*1024*1024, MAX_TOTAL=2L*1024*1024*1024;
    private static final int MAX_MANIFEST=32*1024*1024;
    private final Context context;
    private final VoiceStore store;
    public PackageImporter(Context context,VoiceStore store){this.context=context;this.store=store;}

    public synchronized Result read(InputStream input,Progress progress) throws Exception {
        return read(input,"语音数据包",progress);
    }
    public synchronized Result read(InputStream input,String sourceName,Progress progress) throws Exception {
        File stage=new File(context.getCacheDir(),"import-"+UUID.randomUUID());
        if(!stage.mkdirs())throw new IOException("无法创建导入缓存");
        List<File> moved=new ArrayList<>();Map<File,File> backups=new HashMap<>();boolean committed=false;
        try {
            File archive=new File(stage,"bundle.zip");
            try(OutputStream out=new FileOutputStream(archive)){copy(input,out,MAX_TOTAL+MAX_MANIFEST+16*1024*1024);}
            Result result=new Result();
            try(ZipFile zip=new ZipFile(archive)) {
                Map<String,ZipEntry> entries=new HashMap<>();
                Enumeration<? extends ZipEntry> enumeration=zip.entries();
                while(enumeration.hasMoreElements()) {
                    ZipEntry entry=enumeration.nextElement();
                    String name=entry.getName();
                    if(entry.isDirectory() || !(name.equals("manifest.json") || name.matches("audio/[0-9a-f]{24}\\.mp3")) || entries.put(name,entry)!=null || entries.size()>50001)
                        throw new IOException("数据包包含无效或重复文件");
                }
                ZipEntry index=entries.get("manifest.json");
                if(index==null)throw new IOException("缺少语音索引，请导入电脑生成的 voicepack.zip");
                ByteArrayOutputStream manifestBytes=new ByteArrayOutputStream();
                try(InputStream stream=zip.getInputStream(index)){copy(stream,manifestBytes,MAX_MANIFEST);}
                JSONObject manifest=new JSONObject(new String(manifestBytes.toByteArray(),StandardCharsets.UTF_8));
                if(!manifest.getString("format").equals("voicekeep") || manifest.getInt("version")!=1 || !manifest.getString("timezone").equals("Asia/Shanghai"))throw new IOException("不支持的数据包版本");
                JSONArray chats=manifest.getJSONArray("chats"), rows=manifest.getJSONArray("voices");
                if(rows.length()<1 || rows.length()>50000 || entries.size()!=rows.length()+1 || chats.length()>rows.length())throw new IOException("数据包文件数量不一致");
                Map<String,ContentValues> chatValues=new HashMap<>();
                for(int i=0;i<chats.length();i++) {
                    JSONObject chat=chats.getJSONObject(i);String id=id(chat,"id"),kind=chat.getString("kind");
                    if(!kind.equals("group")&&!kind.equals("contact"))throw new IOException("会话类型无效");
                    ContentValues values=new ContentValues();values.put("id",id);values.put("name",text(chat,"name"));values.put("kind",kind);
                    if(chatValues.put(id,values)!=null)throw new IOException("会话重复");
                }
                List<VoiceStore.Voice> voices=new ArrayList<>();Set<String> ids=new HashSet<>();Set<String> usedChats=new HashSet<>();long bytes=0;
                SimpleDateFormat day=new SimpleDateFormat("yyyy-MM-dd",Locale.US),clock=new SimpleDateFormat("HH:mm:ss",Locale.US);
                day.setTimeZone(TimeZone.getTimeZone("Asia/Shanghai"));clock.setTimeZone(day.getTimeZone());
                for(int i=0;i<rows.length();i++) {
                    JSONObject row=rows.getJSONObject(i);VoiceStore.Voice voice=new VoiceStore.Voice();
                    voice.id=id(row,"id");voice.chatId=id(row,"chat_id");voice.senderId=row.getString("sender_id");
                    if(!voice.senderId.equals("unknown")&&!voice.senderId.matches("[0-9a-f]{24}"))throw new IOException("发送人标识无效");
                    if(!ids.add(voice.id)||!chatValues.containsKey(voice.chatId))throw new IOException("语音重复或缺少会话");
                    usedChats.add(voice.chatId);
                    voice.sender=text(row,"sender");if(voice.senderId.equals("unknown"))voice.sender="未知发送人";
                    voice.timestamp=row.getLong("timestamp");if(voice.timestamp<0||voice.timestamp>32503680000L)throw new IOException("语音时间无效");
                    Date date=new Date(voice.timestamp*1000);voice.date=day.format(date);voice.time=clock.format(date);
                    voice.seconds=row.optDouble("seconds",0);if(!Double.isFinite(voice.seconds)||voice.seconds<0)voice.seconds=0;
                    voice.hash=row.getString("sha256");if(!voice.hash.matches("[0-9a-f]{64}"))throw new IOException("校验信息无效");
                    String name="audio/"+voice.id+".mp3";if(!name.equals(row.getString("file")))throw new IOException("音频路径无效");
                    ZipEntry entry=entries.get(name);if(entry==null)throw new IOException("数据包缺少音频");
                    File extracted=new File(stage,voice.id+".mp3");
                    MessageDigest digest=MessageDigest.getInstance("SHA-256");
                    try(InputStream stream=zip.getInputStream(entry);OutputStream output=new FileOutputStream(extracted)) {
                        byte[] buffer=new byte[65536];int n;long size=0;
                        while((n=stream.read(buffer))!=-1){size+=n;bytes+=n;if(size>MAX_AUDIO||bytes>MAX_TOTAL)throw new IOException("数据包过大，请在电脑缩小范围");digest.update(buffer,0,n);output.write(buffer,0,n);}
                        if(size==0)throw new IOException("音频为空");
                    }
                    if(!hex(digest.digest()).equals(voice.hash))throw new IOException("音频校验失败，数据包可能已损坏");
                    VoiceStore.Voice existing=store.find(voice.id);
                    if(existing!=null) {
                        if(!existing.hash.equals(voice.hash)||!existing.chatId.equals(voice.chatId)||existing.timestamp!=voice.timestamp ||
                            (!existing.senderId.equals("unknown")&&!voice.senderId.equals("unknown")&&!existing.senderId.equals(voice.senderId)))
                            throw new IOException("已导入语音与数据包冲突");
                        if(voice.senderId.equals("unknown")&&!existing.senderId.equals("unknown")){voice.senderId=existing.senderId;voice.sender=existing.sender;}
                    }
                    voices.add(voice);if(progress!=null)progress.update(i+1,rows.length());
                }
                if(usedChats.size()!=chatValues.size())throw new IOException("存在未使用的会话");
                SQLiteDatabase db=store.getWritableDatabase();db.beginTransaction();
                try {
                    for(ContentValues chat:chatValues.values())db.insertWithOnConflict("chats",null,chat,SQLiteDatabase.CONFLICT_REPLACE);
                    for(VoiceStore.Voice voice:voices) {
                        VoiceStore.Voice existing=store.find(voice.id);
                        File destination=store.audioFile(voice);
                        if(destination.exists()&&!destination.isFile())throw new IOException("本地音频路径异常");
                        boolean healthy=destination.isFile()&&sha256(destination).equals(voice.hash);
                        if(!healthy) {
                            if(destination.exists()) {
                                File backup=new File(stage,"old-"+voice.id+".mp3");
                                if(!destination.renameTo(backup))throw new IOException("无法修复本地音频");
                                backups.put(destination,backup);
                            }
                            if(!new File(stage,voice.id+".mp3").renameTo(destination))throw new IOException("保存音频失败，请检查手机空间");
                            moved.add(destination);
                        }
                        if(db.insertWithOnConflict("voices",null,VoiceStore.values(voice),SQLiteDatabase.CONFLICT_REPLACE)==-1)throw new IOException("无法保存语音索引");
                        if(existing==null)result.added++;else if(!healthy)result.repaired++;else result.duplicates++;
                    }
                    ContentValues history=new ContentValues();
                    history.put("source",clean(sourceName,160));history.put("package_created",clean(manifest.optString("created_at",""),80));
                    history.put("imported",System.currentTimeMillis()/1000);history.put("total",voices.size());
                    history.put("added",result.added);history.put("repaired",result.repaired);history.put("duplicates",result.duplicates);
                    if(db.insert("imports",null,history)==-1)throw new IOException("无法保存导入记录");
                    db.setTransactionSuccessful();
                } finally { db.endTransaction(); }
                committed=true;
            }
            moved.clear();return result;
        } finally {
            if(!committed){for(File file:moved)file.delete();for(Map.Entry<File,File> backup:backups.entrySet())backup.getValue().renameTo(backup.getKey());}
            removeStage(stage);
        }
    }
    private static String clean(String text,int maximum){String value=(text==null?"":text).replaceAll("[\\p{Cntrl}]"," ").trim();return value.substring(0,Math.min(maximum,value.length()));}
    static String sha256(File file) throws Exception {
        MessageDigest digest=MessageDigest.getInstance("SHA-256");
        try(InputStream in=new FileInputStream(file)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)digest.update(b,0,n);}
        return hex(digest.digest());
    }
    private static String id(JSONObject object,String key)throws Exception{String value=object.getString(key);if(!value.matches("[0-9a-f]{24}"))throw new IOException("数据标识无效");return value;}
    private static String text(JSONObject object,String key)throws Exception{String value=object.getString(key);if(value.length()<1||value.length()>4096||value.indexOf('\u0000')>=0)throw new IOException("名称无效");return value;}
    private static long copy(InputStream input,OutputStream output,long limit)throws IOException{byte[] buffer=new byte[65536];long size=0;int n;while((n=input.read(buffer))!=-1){size+=n;if(size>limit)throw new IOException("文件过大，请缩小导出范围");output.write(buffer,0,n);}return size;}
    private static String hex(byte[] bytes){StringBuilder out=new StringBuilder();for(byte value:bytes)out.append(String.format(Locale.US,"%02x",value&255));return out.toString();}
    private static void removeStage(File directory){File[] files=directory.listFiles();if(files!=null)for(File file:files)file.delete();directory.delete();}
}
