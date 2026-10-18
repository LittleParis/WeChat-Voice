package cn.voicekeep.library;

import android.app.*;
import android.content.Intent;
import android.media.MediaPlayer;
import android.os.*;
import android.view.*;
import android.widget.*;
import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;

/** Optional foreground UI smoke check; requires a user-imported library with >=2 voices. */
public final class LibraryUiChecks extends Instrumentation {
    private MainActivity activity;
    private void require(boolean ok,String label){if(!ok)throw new AssertionError(label);}
    private void main(Runnable task)throws Exception{
        AtomicReference<Throwable> error=new AtomicReference<>();runOnMainSync(()->{try{task.run();}catch(Throwable t){error.set(t);}});
        if(error.get()!=null)throw new Exception(error.get());
    }
    private Object field(String name){try{Field f=MainActivity.class.getDeclaredField(name);f.setAccessible(true);return f.get(activity);}catch(Exception e){throw new RuntimeException(e);}}
    private View find(View root,String text){if(root instanceof TextView&&((TextView)root).getText().toString().equals(text))return root;if(root instanceof ViewGroup){ViewGroup group=(ViewGroup)root;for(int i=0;i<group.getChildCount();i++){View result=find(group.getChildAt(i),text);if(result!=null)return result;}}return null;}
    private View find(String text){View view=find(activity.getWindow().getDecorView(),text);require(view!=null,"visible control missing: "+text);return view;}
    private boolean playing(){return (Boolean)readMain(()->{MediaPlayer p=(MediaPlayer)field("player");return p!=null&&(Boolean)field("playerReady")&&p.isPlaying();});}
    private interface Read {Object get();}
    private Object readMain(Read task){AtomicReference<Object> value=new AtomicReference<>();try{main(()->value.set(task.get()));return value.get();}catch(Exception e){throw new RuntimeException(e);}}
    private void await(Read condition,String label)throws Exception{long deadline=SystemClock.uptimeMillis()+5000;while(SystemClock.uptimeMillis()<deadline){if(Boolean.TRUE.equals(readMain(condition)))return;SystemClock.sleep(100);}throw new AssertionError(label);}
    @Override public void onCreate(Bundle args){super.onCreate(args);start();}
    @Override public void onStart(){
        Bundle result=new Bundle();boolean originalContinuous=false;
        try{
            activity=(MainActivity)startActivitySync(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TASK));
            originalContinuous=(Boolean)readMain(()->field("continuous"));
            main(()->{ListView list=(ListView)field("list");require(list.getCount()>0,"empty imported library");list.performItemClick(list.getChildAt(0),0,0);});
            main(()->{List<?> shown=(List<?>)field("shown");require(shown.size()>=2,"need two imported voices");find("多选").performClick();});
            main(()->{List<?> shown=(List<?>)field("shown");VoiceStore.Voice first=(VoiceStore.Voice)shown.get(0);CheckBox box=(CheckBox)findByDescription(activity.getWindow().getDecorView(),"选择 "+first.date+" "+first.time+" "+first.sender);require(box!=null,"selection checkbox");box.performClick();require(((Set<?>)field("selected")).size()==1,"checkbox selection");require(((Button)field("saveAll")).isEnabled(),"selected save not enabled");find("完成选择").performClick();find("从新到旧 ↓").performClick();});
            main(()->{List<VoiceStore.Voice> rows=(List<VoiceStore.Voice>)field("shown");require(rows.get(0).timestamp<=rows.get(rows.size()-1).timestamp,"ascending UI order");find("从旧到新 ↑").performClick();});
            main(()->{View play=find("试听");require(play.performClick(),"play listener");});
            await(()->field("playerReady"),"player did not prepare");require(playing(),"real audio did not start");
            int first=(Integer)readMain(()->((MediaPlayer)field("player")).getCurrentPosition());SystemClock.sleep(500);
            int later=(Integer)readMain(()->((MediaPlayer)field("player")).getCurrentPosition());require(later>first+100,"playback position did not advance");
            main(()->((Button)field("playPause")).performClick());require(!playing(),"pause did not stop playback");
            int paused=(Integer)readMain(()->((MediaPlayer)field("player")).getCurrentPosition());SystemClock.sleep(400);
            require(Math.abs((Integer)readMain(()->((MediaPlayer)field("player")).getCurrentPosition())-paused)<150,"pause position moved");
            String before=(String)readMain(()->field("playingId"));
            main(()->{((CheckBox)find("连续播放")).setChecked(true);MediaPlayer p=(MediaPlayer)field("player");p.seekTo(Math.max(0,p.getDuration()-400));});SystemClock.sleep(250);
            main(()->((Button)field("playPause")).performClick());
            await(()->!before.equals(field("playingId")),"continuous playback did not advance");await(()->field("playerReady"),"next voice not prepared");require(playing(),"next voice not playing");
            main(()->callActivityOnPause(activity));require(!playing(),"background did not pause playback");
            result.putString("stream","PASS: imported library preserved; conversation navigation; multi-select button; UI sort; real MP3 playback position advances; pause preserves position; continuous playback advances; background pauses.\n");finish(-1,result);
        }catch(Throwable t){result.putString("stream","FAIL: "+t+"\n");finish(0,result);}
        finally{if(activity!=null){final boolean restore=originalContinuous;try{main(()->{activity.getPreferences(0).edit().putBoolean("continuous",restore).apply();activity.finish();});}catch(Exception ignored){}}}
    }
    private View findByDescription(View root,String value){if(root.getContentDescription()!=null&&value.contentEquals(root.getContentDescription()))return root;if(root instanceof ViewGroup){ViewGroup group=(ViewGroup)root;for(int i=0;i<group.getChildCount();i++){View result=findByDescription(group.getChildAt(i),value);if(result!=null)return result;}}return null;}
}
