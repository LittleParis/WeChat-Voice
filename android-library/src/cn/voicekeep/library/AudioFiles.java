package cn.voicekeep.library;

import android.content.*;
import android.database.Cursor;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;
import java.io.*;
import java.util.List;
import java.security.MessageDigest;
import java.util.Locale;

public final class AudioFiles {
    public interface Progress { void update(int complete,int total); }
    public static String filename(VoiceStore.Voice voice) {
        return voice.date+"_"+voice.time.replace(":", "")+"_"+safe(voice.sender)+"_"+voice.id+".mp3";
    }
    private static String safe(String value){String safe=value.replaceAll("[\\p{Cntrl}<>:\"/\\\\|?*]","_").trim();return safe.isEmpty()?"语音":safe.substring(0,Math.min(32,safe.length()));}
    public static int save(Context context,VoiceStore store,VoiceStore.Chat chat,List<VoiceStore.Voice> voices,Progress progress) throws Exception {
        ContentResolver resolver=context.getContentResolver();Uri collection=MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
        String relative=Environment.DIRECTORY_MUSIC+"/VoiceKeep/"+safe(chat.name)+"_"+chat.id.substring(0,8)+"/";
        int saved=0,index=0;
        for(VoiceStore.Voice voice:voices) {
            if(!PackageImporter.sha256(store.audioFile(voice)).equals(voice.hash))throw new IOException("本地音频已损坏，请重新导入数据包补齐");
            String name=filename(voice);boolean exists=false,conflict=false;
            try(Cursor cursor=resolver.query(collection,new String[]{MediaStore.Audio.Media._ID},MediaStore.Audio.Media.DISPLAY_NAME+"=? AND "+MediaStore.Audio.Media.RELATIVE_PATH+"=? AND "+MediaStore.Audio.Media.IS_PENDING+"=0",new String[]{name,relative},null)){
                while(cursor!=null&&cursor.moveToNext()){
                    Uri old=ContentUris.withAppendedId(collection,cursor.getLong(0));
                    try(InputStream input=resolver.openInputStream(old)){if(input!=null&&hash(input).equals(voice.hash))exists=true;else conflict=true;}
                    catch(IOException error){conflict=true;}
                }
            }
            if(!exists&&conflict)throw new IOException("保存位置已有内容不同的同名文件，请先在文件管理中重命名该文件");
            if(!exists) {
                ContentValues values=new ContentValues();values.put(MediaStore.Audio.Media.DISPLAY_NAME,name);values.put(MediaStore.Audio.Media.MIME_TYPE,"audio/mpeg");values.put(MediaStore.Audio.Media.RELATIVE_PATH,relative);values.put(MediaStore.Audio.Media.IS_PENDING,1);
                Uri uri=resolver.insert(collection,values);if(uri==null)throw new IOException("无法创建保存文件");
                try {
                    try(InputStream input=new FileInputStream(store.audioFile(voice));OutputStream output=resolver.openOutputStream(uri)) {
                        if(output==null)throw new IOException("无法写入保存目录");
                        byte[] bytes=new byte[65536];int n;while((n=input.read(bytes))!=-1)output.write(bytes,0,n);
                    }
                    values.clear();values.put(MediaStore.Audio.Media.IS_PENDING,0);
                    if(resolver.update(uri,values,null,null)!=1)throw new IOException("无法完成文件保存");saved++;
                } catch(Exception error){resolver.delete(uri,null,null);throw error;}
            }
            if(progress!=null)progress.update(++index,voices.size());
        }
        return saved;
    }
    private static String hash(InputStream input)throws Exception{
        MessageDigest digest=MessageDigest.getInstance("SHA-256");byte[] bytes=new byte[65536];int n;
        while((n=input.read(bytes))!=-1)digest.update(bytes,0,n);
        StringBuilder value=new StringBuilder();for(byte b:digest.digest())value.append(String.format(Locale.US,"%02x",b&255));return value.toString();
    }
}
