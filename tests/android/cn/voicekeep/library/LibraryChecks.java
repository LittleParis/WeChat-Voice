package cn.voicekeep.library;

import android.app.Instrumentation;
import android.content.*;
import android.content.pm.ProviderInfo;
import android.database.*;
import android.database.sqlite.SQLiteDatabase;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.*;
import android.provider.MediaStore;
import org.json.*;
import java.io.*;
import java.util.*;
import java.util.zip.*;

/** Synthetic checks use a disposable app-private database and never touch the user's library. */
public final class LibraryChecks extends Instrumentation {
    @Override public void onCreate(Bundle arguments){super.onCreate(arguments);start();}
    private void require(boolean condition,String message){if(!condition)throw new AssertionError(message);}
    @Override public void onStart(){
        Bundle result=new Bundle();File base=new File(getTargetContext().getCacheDir(),"checks-"+UUID.randomUUID());base.mkdirs();
        Context isolated=new ContextWrapper(getTargetContext()){
            @Override public File getFilesDir(){File d=new File(base,"files");d.mkdirs();return d;}
            @Override public File getCacheDir(){File d=new File(base,"cache");d.mkdirs();return d;}
            @Override public File getDatabasePath(String name){File d=new File(base,"db");d.mkdirs();return new File(d,name);}
            @Override public SQLiteDatabase openOrCreateDatabase(String name,int mode,SQLiteDatabase.CursorFactory factory){return SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name),factory);}
            @Override public SQLiteDatabase openOrCreateDatabase(String name,int mode,SQLiteDatabase.CursorFactory factory,DatabaseErrorHandler handler){return SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name).getPath(),factory,handler);}
        };
        try(VoiceStore store=new VoiceStore(isolated)){
            // Load fixtures BEFORE expected-rejection catches: missing test resources cannot pass a test.
            byte[] valid=fixture("valid.voicepack.zip"),corrupt=fixture("corrupt.voicepack.zip"),traversal=fixture("traversal.voicepack.zip"),conflict=fixture("conflict.voicepack.zip");
            PackageImporter importer=new PackageImporter(isolated,store);
            reject(importer,corrupt);reject(importer,traversal);require(store.count()==0&&store.imports().isEmpty(),"invalid bundle wrote data/history");
            JSONObject manifest=manifest(valid);JSONObject last=manifest.getJSONArray("voices").getJSONObject(3);
            File blocker=new File(isolated.getFilesDir(),"audio/"+last.getString("id")+"-"+last.getString("sha256")+".mp3");require(blocker.mkdir(),"cannot set atomicity fixture");
            reject(importer,valid);require(store.count()==0&&store.imports().isEmpty(),"partial import committed");require(blocker.getParentFile().list().length==1,"failed transaction retained audio");require(blocker.delete(),"cannot release fixture");
            PackageImporter.Result first=read(importer,valid);require(first.added==4&&first.repaired==0&&first.duplicates==0,"initial count");
            PackageImporter.Result again=read(importer,valid);require(again.added==0&&again.duplicates==4&&again.repaired==0&&store.count()==4,"duplicate import");
            reject(importer,conflict);require(store.count()==4&&store.imports().size()==2,"conflicting import changed data/history");
            List<VoiceStore.Chat> groups=store.chats("group",""),contacts=store.chats("contact","");require(groups.size()==1&&contacts.size()==1,"conversation grouping");
            VoiceStore.Chat group=groups.get(0);List<VoiceStore.Sender> senders=store.senders(group.id);require(senders.size()==3,"sender count");
            Set<String> names=new HashSet<>();for(VoiceStore.Sender sender:senders)names.add(sender.toString());require(names.size()==3,"same-name members indistinguishable");
            List<VoiceStore.Voice> day=store.voices(group.id,"","2026-10-01","2026-10-01",true);require(day.size()==2&&day.get(0).time.equals("00:00:00")&&day.get(1).time.equals("23:59:59"),"inclusive dates");
            require(store.voices(group.id,day.get(0).senderId,"","",false).size()==1,"sender filter");
            require(store.voices(contacts.get(0).id,day.get(0).senderId,"","",false).isEmpty(),"sender escaped conversation");
            require(store.voices(group.id,"unknown","","",false).size()==1,"unknown sender");
            List<VoiceStore.Voice> asc=store.voices(group.id,"","","",true),desc=store.voices(group.id,"","","",false);require(asc.get(0).id.equals(desc.get(2).id),"sort order");
            require(store.audioFile(day.get(0)).delete(),"cannot create missing audio fixture");
            try(OutputStream output=new FileOutputStream(store.audioFile(day.get(1)))){output.write(new byte[]{1,2,3});}
            require(store.storageInfo().missing==1,"missing audio detection");PackageImporter.Result repaired=read(importer,valid);
            require(repaired.added==0&&repaired.repaired==2&&repaired.duplicates==2&&store.count()==4,"repair accounting");
            require(store.imports().size()==3&&store.imports().get(0).repaired==2&&store.storageInfo().missing==0,"import history/storage");
            for(VoiceStore.Chat c:store.chats("",""))for(VoiceStore.Voice v:store.voices(c.id,"","","",false))require(PackageImporter.sha256(store.audioFile(v)).equals(v.hash),"audio SHA256");
            MediaPlayer player=new MediaPlayer();try{player.setDataSource(store.audioFile(day.get(0)).getAbsolutePath());player.prepare();require(player.getDuration()>=950&&player.getDuration()<=1200,"Android MP3 decode metadata");}finally{player.release();}
            AudioProvider provider=new AudioProvider();ProviderInfo info=new ProviderInfo();info.authority="cn.voicekeep.library.audio";provider.attachInfo(isolated,info);
            Uri shared=Uri.parse("content://cn.voicekeep.library.audio/"+day.get(0).id);
            try(ParcelFileDescriptor descriptor=provider.openFile(shared,"r")){require(descriptor.getStatSize()==store.audioFile(day.get(0)).length(),"share provider file");}
            try(Cursor cursor=provider.query(shared,null,null,null,null)){require(cursor!=null&&cursor.moveToFirst()&&cursor.getString(0).endsWith(".mp3"),"share metadata");}
            verifySave(isolated,store,group,day);
            require(!new File(base,"escape.mp3").exists(),"zip path escaped");
            result.putString("stream","PASS: invalid ZIP/hash/traversal/conflict rejection; transaction rollback; dedup; missing/corrupt audio repair; import history; grouping/senders/dates/sorting; SHA256; Android MP3 metadata; share provider; MediaStore save/readback/repeat skip.\n");finish(-1,result);
        }catch(Throwable error){StringWriter stack=new StringWriter();error.printStackTrace(new PrintWriter(stack));result.putString("stream","FAIL: "+stack+"\n");finish(0,result);}finally{remove(base);}
    }
    private void verifySave(Context context,VoiceStore store,VoiceStore.Chat chat,List<VoiceStore.Voice> voices)throws Exception{
        VoiceStore.Chat exportChat=new VoiceStore.Chat();exportChat.id=chat.id;exportChat.name="VoiceKeep检查-"+UUID.randomUUID().toString().substring(0,8);
        Uri collection=MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
        String relative=Environment.DIRECTORY_MUSIC+"/VoiceKeep/"+exportChat.name+"_"+chat.id.substring(0,8)+"/";
        ContentResolver resolver=context.getContentResolver();
        try{
            require(AudioFiles.save(context,store,exportChat,voices,null)==2,"MediaStore initial save");require(AudioFiles.save(context,store,exportChat,voices,null)==0,"MediaStore repeated save duplicated files");
            int count=0;
            try(Cursor cursor=resolver.query(collection,new String[]{MediaStore.Audio.Media._ID,MediaStore.Audio.Media.DISPLAY_NAME},MediaStore.Audio.Media.RELATIVE_PATH+"=?",new String[]{relative},null)){
                while(cursor!=null&&cursor.moveToNext()){
                    Uri uri=ContentUris.withAppendedId(collection,cursor.getLong(0));VoiceStore.Voice expected=null;for(VoiceStore.Voice voice:voices)if(AudioFiles.filename(voice).equals(cursor.getString(1)))expected=voice;
                    require(expected!=null,"unexpected public file");File copy=new File(context.getCacheDir(),"readback-"+count+".mp3");
                    try(InputStream in=resolver.openInputStream(uri);OutputStream out=new FileOutputStream(copy)){byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1)out.write(b,0,n);}
                    require(PackageImporter.sha256(copy).equals(expected.hash),"MediaStore readback hash");count++;
                }
            }
            require(count==2,"MediaStore file count");
        }finally{
            // Delete only synthetic files created under this run's unique path.
            resolver.delete(collection,MediaStore.Audio.Media.RELATIVE_PATH+"=?",new String[]{relative});
        }
    }
    private byte[] fixture(String name)throws Exception{
        try(InputStream input=getContext().getAssets().open("fixtures/"+name)){return bytes(input);}
        catch(FileNotFoundException missing){
            String path=getTargetContext().getApplicationInfo().sourceDir;
            try(ZipFile apk=new ZipFile(path)){
                String expected="assets/fixtures/"+name;ZipEntry entry=apk.getEntry(expected);List<String> names=new ArrayList<>();
                if(entry==null){Enumeration<? extends ZipEntry> entries=apk.entries();while(entries.hasMoreElements()){ZipEntry item=entries.nextElement();names.add(item.getName()+" ["+item.getName().length()+"]");if(expected.equals(item.getName()))entry=item;}}
                if(entry==null)throw new IOException("Synthetic fixture absent: expected="+expected+" ["+expected.length()+"]; APK="+path+"; entries="+names,missing);
                try(InputStream input=apk.getInputStream(entry)){return bytes(input);}
            }
        }
    }
    private byte[] bytes(InputStream input)throws IOException{ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[8192];int n;while((n=input.read(b))!=-1)out.write(b,0,n);return out.toByteArray();}
    private JSONObject manifest(byte[] bundle)throws Exception{try(ZipInputStream zip=new ZipInputStream(new ByteArrayInputStream(bundle))){ZipEntry e;while((e=zip.getNextEntry())!=null)if(e.getName().equals("manifest.json"))return new JSONObject(new String(bytes(zip),"UTF-8"));}throw new IOException("fixture manifest absent");}
    private PackageImporter.Result read(PackageImporter importer,byte[] bundle)throws Exception{return importer.read(new ByteArrayInputStream(bundle),"合成测试数据包",null);}
    private void reject(PackageImporter importer,byte[] bundle)throws Exception{try{read(importer,bundle);}catch(IOException expected){return;}throw new AssertionError("invalid import accepted");}
    private void remove(File file){if(file.isDirectory()){File[] children=file.listFiles();if(children!=null)for(File child:children)remove(child);}file.delete();}
}
