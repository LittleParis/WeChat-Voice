package cn.voicekeep.library;

import android.app.*;
import android.content.*;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.*;
import android.provider.OpenableColumns;
import android.database.Cursor;
import java.text.SimpleDateFormat;
import android.text.*;
import android.view.*;
import android.widget.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int INK=0xff24353a,MUTED=0xff506168,TEAL=0xff006676,SURFACE=0xfff1f6f7;
    private VoiceStore store;
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final Handler handler=new Handler(Looper.getMainLooper());
    private LinearLayout root,body,playerBox;
    private TextView status,playerTitle,playerTime;
    private ProgressBar progress;
    private Button importButton,playPause;
    private SeekBar seek;
    private ListView list;
    private VoiceStore.Chat chat;
    private String kind="",search="",sender="",since="",until="",playingId="";
    private boolean ascending=false,busy=false,dragging=false,playerReady=false;
    private int importDone=0,importTotal=0;
    private MediaPlayer player;
    private List<VoiceStore.Voice> shown=new ArrayList<>();
    private boolean dataScreen=false,selecting=false,continuous=false,foreground=false,playbackRequested=false;
    private final Set<String> selected=new HashSet<>();
    private final List<Button> navigation=new ArrayList<>();
    private List<VoiceStore.Voice> playQueue=new ArrayList<>();
    private Button selectButton,selectAllButton,nextButton;
    private AudioManager audioManager;
    private AudioFocusRequest focusRequest;
    private final AudioAttributes speech=new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build();
    private final Runnable ticker=new Runnable(){public void run(){if(player!=null&&playerReady){try{if(!dragging)seek.setProgress(player.getCurrentPosition());String label=duration(player.getCurrentPosition()/1000.0)+" / "+duration(player.getDuration()/1000.0);if(!label.contentEquals(playerTime.getText()))playerTime.setText(label);}catch(IllegalStateException ignored){}handler.postDelayed(this,300);}}};

    @Override public void onCreate(Bundle saved){super.onCreate(saved);store=new VoiceStore(this);continuous=getPreferences(0).getBoolean("continuous",false);audioManager=(AudioManager)getSystemService(AUDIO_SERVICE);focusRequest=new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(speech).setWillPauseWhenDucked(true).setOnAudioFocusChangeListener(change->{if(change<0)pausePlayback();},handler).build();if(saved!=null){dataScreen=saved.getBoolean("dataScreen");kind=saved.getString("kind","");search=saved.getString("search","");sender=saved.getString("sender","");since=saved.getString("since","");until=saved.getString("until","");ascending=saved.getBoolean("ascending");String id=saved.getString("chat","");for(VoiceStore.Chat item:store.chats("",""))if(item.id.equals(id))chat=item;}build();}
    @Override public void onSaveInstanceState(Bundle state){super.onSaveInstanceState(state);state.putBoolean("dataScreen",dataScreen);state.putString("kind",kind);state.putString("search",search);state.putString("sender",sender);state.putString("since",since);state.putString("until",until);state.putBoolean("ascending",ascending);state.putString("chat",chat==null?"":chat.id);}
    @Override public void onConfigurationChanged(android.content.res.Configuration config){super.onConfigurationChanged(config);}
    private int dp(float n){return Math.round(n*getResources().getDisplayMetrics().density);}
    private TextView text(String value,int size,int color){TextView view=new TextView(this);view.setText(value);view.setTextSize(size);view.setTextColor(color);view.setLineSpacing(dp(2),1);return view;}
    private LinearLayout column(){LinearLayout view=new LinearLayout(this);view.setOrientation(LinearLayout.VERTICAL);return view;}
    private LinearLayout row(){LinearLayout view=new LinearLayout(this);view.setOrientation(LinearLayout.HORIZONTAL);view.setGravity(Gravity.CENTER_VERTICAL);return view;}
    private Button button(String label,boolean primary){Button button=new Button(this);button.setText(label);button.setAllCaps(false);button.setTextSize(14);button.setTextColor(primary?Color.WHITE:TEAL);button.setMinHeight(dp(48));button.setMinimumWidth(0);button.setPadding(dp(13),dp(6),dp(13),dp(6));GradientDrawable background=new GradientDrawable();background.setColor(primary?TEAL:SURFACE);background.setCornerRadius(dp(8));button.setBackground(background);button.setStateListAnimator(null);return button;}
    private void gap(LinearLayout parent,int height){View spacer=new View(this);parent.addView(spacer,new LinearLayout.LayoutParams(1,dp(height)));}
    private void line(LinearLayout parent){View line=new View(this);line.setBackgroundColor(0xffe1e8eb);parent.addView(line,new LinearLayout.LayoutParams(-1,dp(1)));}
    private void build(){
        importButton=null;saveAll=null;navigation.clear();
        root=column();root.setBackgroundColor(Color.WHITE);root.setFitsSystemWindows(true);setContentView(root);
        LinearLayout header=row();header.setPadding(dp(20),dp(15),dp(20),dp(12));LinearLayout title=column();TextView name=text(chat==null?(dataScreen?"数据管理":"语音档案"):chat.name,chat==null?26:21,INK);name.setTypeface(null,Typeface.BOLD);name.setMaxLines(2);title.addView(name);title.addView(text(chat==null?(dataScreen?"导入、补齐与更新记录":"按对话，收好每一段声音。"):(chat.kind.equals("group")?"群聊":"联系人")+" · 离线语音",13,MUTED));header.addView(title,new LinearLayout.LayoutParams(0,-2,1));
        if(chat==null&&!dataScreen){importButton=button("导入",true);importButton.setContentDescription("导入手机语音数据包");importButton.setEnabled(!busy);importButton.setOnClickListener(v->choosePackage());header.addView(importButton);}else if(chat!=null){Button back=button("会话",false);back.setEnabled(!busy);back.setOnClickListener(v->{leaveChat();build();});navigation.add(back);header.addView(back);}root.addView(header);
        status=text(busy?"正在处理，请保持 App 开启。":"",13,MUTED);status.setPadding(dp(20),0,dp(20),dp(8));status.setVisibility(busy?View.VISIBLE:View.GONE);root.addView(status);progress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);progress.setVisibility(busy?View.VISIBLE:View.GONE);root.addView(progress,new LinearLayout.LayoutParams(-1,dp(3)));
        body=column();root.addView(body,new LinearLayout.LayoutParams(-1,0,1));if(chat!=null)voicesScreen();else if(dataScreen)dataScreen();else chatsScreen();buildPlayer();
        line(root);LinearLayout tabs=row();tabs.setPadding(dp(16),dp(6),dp(16),dp(8));
        for(int i=0;i<2;i++){final boolean data=i==1;Button tab=button(data?"数据管理":"会话",dataScreen==data);tab.setEnabled(!busy);tab.setOnClickListener(v->{leaveChat();dataScreen=data;build();});LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,dp(48),1);if(i==0)p.setMarginEnd(dp(8));tabs.addView(tab,p);navigation.add(tab);}root.addView(tabs);
    }
    private void leaveChat(){chat=null;selecting=false;selected.clear();}
    private String stamp(long value){SimpleDateFormat date=new SimpleDateFormat("yyyy-MM-dd HH:mm",Locale.US);date.setTimeZone(TimeZone.getTimeZone("Asia/Shanghai"));return date.format(new Date(value*1000));}
    private void dataScreen(){
        LinearLayout intro=column();intro.setPadding(dp(20),0,dp(20),dp(12));
        TextView summary=text("正在读取本机语音库…",14,INK);intro.addView(summary);gap(intro,10);
        intro.addView(text("在电脑选择会话并生成手机数据包，将 ZIP 传到手机后导入。再次导入会跳过已有语音，并补齐缺失或损坏的音频。",14,MUTED));gap(intro,12);
        importButton=button("导入语音数据包",true);importButton.setEnabled(!busy);importButton.setOnClickListener(v->choosePackage());intro.addView(importButton,new LinearLayout.LayoutParams(-1,dp(48)));gap(intro,14);
        TextView heading=text("最近导入记录",16,INK);heading.setTypeface(null,Typeface.BOLD);intro.addView(heading);body.addView(intro);line(body);
        List<VoiceStore.ImportRecord> records=store.imports();ListView history=new ListView(this);history.setDividerHeight(dp(1));history.setPadding(dp(20),0,dp(20),0);body.addView(history,new LinearLayout.LayoutParams(-1,0,1));
        TextView empty=text("还没有导入记录\n首次导入后，会在这里显示新增、补齐和已存在的数量。",14,MUTED);empty.setGravity(Gravity.CENTER);empty.setPadding(dp(24),dp(24),dp(24),dp(24));body.addView(empty,new LinearLayout.LayoutParams(-1,0,1));history.setEmptyView(empty);
        history.setAdapter(new BaseAdapter(){public int getCount(){return records.size();}public Object getItem(int n){return records.get(n);}public long getItemId(int n){return n;}public View getView(int n,View reuse,ViewGroup parent){VoiceStore.ImportRecord r=records.get(n);LinearLayout item=column();item.setPadding(0,dp(15),0,dp(15));TextView source=text(r.source,15,INK);source.setMaxLines(2);item.addView(source);item.addView(text(stamp(r.imported)+" · 共 "+r.total+" 条",12,MUTED));item.addView(text("新增 "+r.added+" · 补齐 "+r.repaired+" · 已存在 "+r.duplicates,13,TEAL));return item;}});
        worker.execute(()->{VoiceStore.StorageInfo info=store.storageInfo();ui(()->{if(summary.isAttachedToWindow())summary.setText(info.chats+" 个会话 · "+info.voices+" 条语音 · "+String.format(Locale.US,"%.1f MB",info.bytes/1048576.0)+(info.missing>0?"\n有 "+info.missing+" 条音频缺失，请重新导入对应数据包。":"\n音频保存在本机，可离线使用。"));});});
    }
    private void chatsScreen(){
        LinearLayout controls=column();controls.setPadding(dp(20),0,dp(20),dp(10));EditText query=new EditText(this);query.setSingleLine(true);query.setTextSize(15);query.setTextColor(INK);query.setHintTextColor(MUTED);query.setHint("搜索群聊或联系人");query.setText(search);query.setContentDescription("搜索群聊或联系人");controls.addView(query,new LinearLayout.LayoutParams(-1,dp(48)));gap(controls,10);
        LinearLayout tabs=row();for(String[] tab:new String[][]{{"全部",""},{"群聊","group"},{"联系人","contact"}}){Button b=button(tab[0],kind.equals(tab[1]));LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(0,dp(46),1);params.setMarginEnd(dp(6));tabs.addView(b,params);b.setOnClickListener(v->{kind=tab[1];build();});}controls.addView(tabs);body.addView(controls);line(body);
        list=new ListView(this);list.setDividerHeight(dp(1));list.setClipToPadding(false);list.setPadding(dp(12),0,dp(12),0);body.addView(list,new LinearLayout.LayoutParams(-1,0,1));
        TextView empty=text("还没有导入语音\n\n电脑端选好会话，生成手机数据包。\n把 ZIP 传到手机，再点击“导入”。\n\n之后可离线筛选、播放和保存 MP3。",15,MUTED);empty.setGravity(Gravity.CENTER);empty.setPadding(dp(26),dp(30),dp(26),dp(30));body.addView(empty,new LinearLayout.LayoutParams(-1,0,1));list.setEmptyView(empty);
        TextView count=text("仅存本机 · 已导入 "+store.count()+" 条语音",12,MUTED);count.setPadding(dp(20),dp(10),dp(20),dp(12));body.addView(count);
        Runnable refresh=()->{List<VoiceStore.Chat> chats=store.chats(kind,search);list.setAdapter(new BaseAdapter(){public int getCount(){return chats.size();}public Object getItem(int n){return chats.get(n);}public long getItemId(int n){return n;}public View getView(int n,View reuse,android.view.ViewGroup parent){VoiceStore.Chat item=chats.get(n);LinearLayout row=row();row.setPadding(dp(8),dp(17),dp(8),dp(17));LinearLayout names=column();TextView name=text(item.name,16,INK);name.setTypeface(null,Typeface.BOLD);names.addView(name);names.addView(text((item.kind.equals("group")?"群聊":"联系人")+" · "+item.date,12,MUTED));row.addView(names,new LinearLayout.LayoutParams(0,-2,1));row.addView(text(item.count+" 条  ›",13,MUTED));return row;}});list.setOnItemClickListener((parent,view,n,id)->{chat=chats.get(n);sender=since=until="";ascending=false;build();});if(store.count()>0)empty.setText("没有匹配的会话\n换一个名称，或切换会话类型。");};refresh.run();
        query.addTextChangedListener(new TextWatcher(){public void beforeTextChanged(CharSequence s,int a,int c,int f){}public void onTextChanged(CharSequence s,int a,int b,int c){search=s.toString();refresh.run();}public void afterTextChanged(Editable e){}});
    }
    private Button senderButton,sinceButton,untilButton,orderButton,saveAll;
    private TextView resultCount;
    private void voicesScreen(){
        LinearLayout filters=column();filters.setPadding(dp(20),0,dp(20),dp(12));
        senderButton=button("发送人：全部",false);senderButton.setGravity(Gravity.START|Gravity.CENTER_VERTICAL);senderButton.setOnClickListener(v->pickSender());if(chat.kind.equals("group")){filters.addView(senderButton,new LinearLayout.LayoutParams(-1,dp(48)));gap(filters,8);}
        LinearLayout dates=row();sinceButton=button("开始日期",false);untilButton=button("结束日期",false);LinearLayout.LayoutParams dateParams=new LinearLayout.LayoutParams(0,dp(46),1);dateParams.setMarginEnd(dp(8));dates.addView(sinceButton,dateParams);dates.addView(untilButton,new LinearLayout.LayoutParams(0,dp(46),1));sinceButton.setOnClickListener(v->pickDate(true));untilButton.setOnClickListener(v->pickDate(false));filters.addView(dates);gap(filters,8);
        LinearLayout actions=row();orderButton=button("从新到旧 ↓",false);orderButton.setOnClickListener(v->{ascending=!ascending;refreshVoices();});actions.addView(orderButton);Button clear=button("清除筛选",false);LinearLayout.LayoutParams clearParams=new LinearLayout.LayoutParams(0,dp(46),1);clearParams.setMarginStart(dp(8));actions.addView(clear,clearParams);clear.setOnClickListener(v->{sender=since=until="";refreshVoices();});filters.addView(actions);body.addView(filters);line(body);
        LinearLayout selection=row();selection.setPadding(dp(20),0,dp(20),dp(8));selectButton=button(selecting?"完成选择":"多选",false);selectButton.setOnClickListener(v->{selecting=!selecting;selected.clear();refreshVoices();});selection.addView(selectButton);selectAllButton=button("全选",false);selectAllButton.setOnClickListener(v->{if(selected.size()==shown.size())selected.clear();else for(VoiceStore.Voice voice:shown)selected.add(voice.id);updateSelectionActions();refreshListState();});LinearLayout.LayoutParams allParams=new LinearLayout.LayoutParams(-2,dp(46));allParams.setMarginStart(dp(8));selection.addView(selectAllButton,allParams);body.addView(selection);
        LinearLayout results=row();results.setPadding(dp(20),dp(8),dp(20),dp(8));resultCount=text("",13,MUTED);results.addView(resultCount,new LinearLayout.LayoutParams(0,-2,1));saveAll=button("保存当前结果",true);saveAll.setOnClickListener(v->{List<VoiceStore.Voice> batch=new ArrayList<>();for(VoiceStore.Voice voice:shown)if(!selecting||selected.contains(voice.id))batch.add(voice);save(batch);});results.addView(saveAll);body.addView(results);
        list=new ListView(this);list.setDividerHeight(dp(1));list.setPadding(dp(12),0,dp(12),0);list.setClipToPadding(false);body.addView(list,new LinearLayout.LayoutParams(-1,0,1));
        TextView empty=text("当前条件没有语音\n可清除筛选，查看完整会话。",15,MUTED);empty.setGravity(Gravity.CENTER);empty.setPadding(dp(20),dp(30),dp(20),dp(30));body.addView(empty,new LinearLayout.LayoutParams(-1,0,1));list.setEmptyView(empty);refreshVoices();
    }
    private void refreshVoices(){
        shown=store.voices(chat.id,sender,since,until,ascending);Set<String> visible=new HashSet<>();for(VoiceStore.Voice voice:shown)visible.add(voice.id);selected.retainAll(visible);updateSelectionActions();orderButton.setText(ascending?"从旧到新 ↑":"从新到旧 ↓");sinceButton.setText(since.isEmpty()?"开始日期":since);untilButton.setText(until.isEmpty()?"结束日期":until);
        String senderName="全部";for(VoiceStore.Sender item:store.senders(chat.id))if(item.id.equals(sender))senderName=item.name;senderButton.setText("发送人："+senderName);
        final List<VoiceStore.Voice> rows=new ArrayList<>(shown);
        list.setAdapter(new BaseAdapter(){public int getCount(){return rows.size();}public Object getItem(int n){return rows.get(n);}public long getItemId(int n){return n;}public View getView(int n,View reuse,android.view.ViewGroup parent){VoiceStore.Voice voice=rows.get(n);LinearLayout wrap=column();wrap.setPadding(dp(8),dp(10),dp(8),dp(10));if(n==0||!rows.get(n-1).date.equals(voice.date)){TextView date=text(voice.date,12,TEAL);date.setPadding(0,dp(8),0,dp(10));wrap.addView(date);}LinearLayout line=row();if(selecting){CheckBox check=new CheckBox(MainActivity.this);check.setChecked(selected.contains(voice.id));check.setContentDescription("选择 "+voice.date+" "+voice.time+" "+voice.sender);check.setOnCheckedChangeListener((b,checked)->{if(checked)selected.add(voice.id);else selected.remove(voice.id);updateSelectionActions();});line.addView(check,new LinearLayout.LayoutParams(dp(44),dp(48)));}Button play=button(playingId.equals(voice.id)&&player!=null&&playerReady&&player.isPlaying()?"暂停":"试听",false);play.setContentDescription("试听 "+voice.sender+" "+voice.date+" "+voice.time);play.setEnabled(store.audioFile(voice).isFile());play.setOnClickListener(v->playFromList(voice));line.addView(play,new LinearLayout.LayoutParams(dp(62),dp(48)));LinearLayout names=column();names.setPadding(dp(12),0,dp(8),0);TextView name=text(voice.sender,15,INK);name.setMaxLines(2);names.addView(name);names.addView(text(voice.time+" · "+duration(voice.seconds),12,MUTED));if(!store.audioFile(voice).isFile())names.addView(text("音频缺失，请重新导入",12,MUTED));line.addView(names,new LinearLayout.LayoutParams(0,-2,1));Button more=button("更多",false);more.setContentDescription("保存或分享 "+voice.date+" "+voice.time);more.setOnClickListener(v->new AlertDialog.Builder(MainActivity.this).setTitle("这条语音").setItems(new String[]{"保存 MP3 到手机","分享 MP3"},(d,which)->{if(which==0)save(Collections.singletonList(voice));else share(voice);}).show());line.addView(more,new LinearLayout.LayoutParams(dp(60),dp(48)));wrap.addView(line);return wrap;}});
        list.setOnItemLongClickListener((parent,view,n,id)->{if(!selecting){selecting=true;selected.clear();}selected.add(rows.get(n).id);refreshVoices();return true;});
    }
    private void updateSelectionActions(){resultCount.setText(selecting?"已选 "+selected.size()+" / "+shown.size()+" 条":shown.size()+" 条语音 · 北京时间");saveAll.setText(selecting?"保存所选":"保存当前结果");saveAll.setEnabled(!busy&&(selecting?!selected.isEmpty():!shown.isEmpty()));selectButton.setText(selecting?"完成选择":"多选");selectAllButton.setVisibility(selecting?View.VISIBLE:View.GONE);selectAllButton.setText(!shown.isEmpty()&&selected.size()==shown.size()?"取消全选":"全选");}
    private void pickSender(){List<VoiceStore.Sender> senders=store.senders(chat.id);String[] names=new String[senders.size()+1];names[0]="全部发送人";int current=0;for(int i=0;i<senders.size();i++){names[i+1]=senders.get(i).toString();if(sender.equals(senders.get(i).id))current=i+1;}new AlertDialog.Builder(this).setTitle("选择发送人").setSingleChoiceItems(names,current,(dialog,n)->{sender=n==0?"":senders.get(n-1).id;dialog.dismiss();refreshVoices();}).setNegativeButton("取消",null).show();}
    private void pickDate(boolean start){String value=start?since:until;Calendar calendar=Calendar.getInstance(TimeZone.getTimeZone("Asia/Shanghai"));if(!value.isEmpty()){String[] parts=value.split("-");calendar.set(Integer.parseInt(parts[0]),Integer.parseInt(parts[1])-1,Integer.parseInt(parts[2]));}new DatePickerDialog(this,(view,year,month,day)->{String date=String.format(Locale.US,"%04d-%02d-%02d",year,month+1,day);if(start){if(!until.isEmpty()&&date.compareTo(until)>0){message("开始日期不能晚于结束日期");return;}since=date;}else{if(!since.isEmpty()&&date.compareTo(since)<0){message("结束日期不能早于开始日期");return;}until=date;}refreshVoices();},calendar.get(Calendar.YEAR),calendar.get(Calendar.MONTH),calendar.get(Calendar.DAY_OF_MONTH)).show();}
    private void choosePackage(){if(busy)return;Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT);intent.setType("*/*");intent.addCategory(Intent.CATEGORY_OPENABLE);intent.putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"application/zip","application/octet-stream","application/x-zip-compressed"});startActivityForResult(intent,10);}
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(request==10&&result==RESULT_OK&&data!=null&&data.getData()!=null)importPackage(data.getData());}
    private void importPackage(Uri uri){
        String filename="语音数据包";
        try(Cursor cursor=getContentResolver().query(uri,new String[]{OpenableColumns.DISPLAY_NAME},null,null,null)){if(cursor!=null&&cursor.moveToFirst())filename=cursor.getString(0);}catch(Exception ignored){}
        final String sourceName=filename;setBusy(true,"正在校验数据包…");
        worker.execute(()->{try(InputStream input=getContentResolver().openInputStream(uri)){
            if(input==null)throw new IOException("无法打开文件");
            PackageImporter.Result result=new PackageImporter(this,store).read(input,sourceName,(done,total)->{if(done==1||done==total||done%10==0)ui(()->{progress.setIndeterminate(false);progress.setMax(total);progress.setProgress(done);status.setText("正在校验 "+done+" / "+total+" 条语音…");});});
            ui(()->{setBusy(false,"");leaveChat();build();showStatus("导入完成：新增 "+result.added+" 条，补齐 "+result.repaired+" 条，已存在 "+result.duplicates+" 条。");});
        }catch(Exception error){ui(()->{setBusy(false,"");showStatus("导入失败："+readable(error));});}});
    }
    private void save(List<VoiceStore.Voice> voices){if(busy||voices.isEmpty())return;VoiceStore.Chat selected=chat;new AlertDialog.Builder(this).setTitle("保存 "+voices.size()+" 条 MP3").setMessage("保存到手机 Music/VoiceKeep 下的会话文件夹，日期范围包含起止当天。已保存的同一语音会跳过。").setPositiveButton("保存",(dialog,which)->{setBusy(true,"正在保存 MP3…");worker.execute(()->{try{int added=AudioFiles.save(this,store,selected,voices,(done,total)->ui(()->{progress.setIndeterminate(false);progress.setMax(total);progress.setProgress(done);status.setText("正在保存 "+done+" / "+total+" 条…");}));ui(()->{setBusy(false,"");showStatus("保存完成：新增 "+added+" 条，跳过 "+(voices.size()-added)+" 条。位置：Music/VoiceKeep");});}catch(Exception error){ui(()->{setBusy(false,"");showStatus("保存未完成："+readable(error)+"。已完成的文件保留，可重试。");});}});}).setNegativeButton("取消",null).show();}
    private void share(VoiceStore.Voice voice){Uri uri=Uri.parse("content://cn.voicekeep.library.audio/"+voice.id);Intent intent=new Intent(Intent.ACTION_SEND);intent.setType("audio/mpeg");intent.putExtra(Intent.EXTRA_STREAM,uri);intent.setClipData(ClipData.newUri(getContentResolver(),"语音 MP3",uri));intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);startActivity(Intent.createChooser(intent,"分享 MP3"));}
    private void buildPlayer(){
        playerBox=column();playerBox.setBackgroundColor(SURFACE);playerBox.setPadding(dp(20),dp(8),dp(20),dp(8));
        playerTitle=text("",14,INK);playerTitle.setMaxLines(2);playerBox.addView(playerTitle);
        LinearLayout controls=row();playPause=button("播放",false);playPause.setOnClickListener(v->togglePlayback());
        controls.addView(playPause,new LinearLayout.LayoutParams(dp(60),dp(44)));
        seek=new SeekBar(this);seek.setContentDescription("播放进度");controls.addView(seek,new LinearLayout.LayoutParams(0,dp(44),1));
        Button close=button("关闭",false);close.setOnClickListener(v->stopPlayer());controls.addView(close,new LinearLayout.LayoutParams(dp(60),dp(44)));playerBox.addView(controls);
        LinearLayout options=row();CheckBox auto=new CheckBox(this);auto.setText("连续播放");auto.setTextColor(INK);auto.setTextSize(13);auto.setChecked(continuous);
        auto.setOnCheckedChangeListener((button,checked)->{continuous=checked;getPreferences(0).edit().putBoolean("continuous",checked).apply();});
        options.addView(auto,new LinearLayout.LayoutParams(0,dp(44),1));nextButton=button("下一条",false);nextButton.setOnClickListener(v->playNext());nextButton.setEnabled(hasNext());options.addView(nextButton);playerBox.addView(options);
        playerTime=text("",12,MUTED);playerBox.addView(playerTime);
        seek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener(){public void onStartTrackingTouch(SeekBar bar){dragging=true;}public void onStopTrackingTouch(SeekBar bar){if(player!=null&&playerReady)player.seekTo(bar.getProgress());dragging=false;}public void onProgressChanged(SeekBar bar,int value,boolean fromUser){}});
        root.addView(playerBox);playerBox.setVisibility(player==null?View.GONE:View.VISIBLE);
        if(player!=null){VoiceStore.Voice current=store.find(playingId);if(current!=null)playerTitle.setText(current.sender+" · "+current.date+" "+current.time);playPause.setEnabled(playerReady);seek.setEnabled(playerReady);if(playerReady){seek.setMax(player.getDuration());seek.setProgress(player.getCurrentPosition());playPause.setText(player.isPlaying()?"暂停":"播放");}}
    }
    private void playFromList(VoiceStore.Voice voice){playQueue=new ArrayList<>(shown);play(voice);}
    private int queuePosition(){for(int i=0;i<playQueue.size();i++)if(playQueue.get(i).id.equals(playingId))return i;return -1;}
    private boolean hasNext(){int position=queuePosition();return position>=0&&position+1<playQueue.size();}
    private boolean playNext(){if(!hasNext())return false;play(playQueue.get(queuePosition()+1));return true;}
    private void togglePlayback(){if(player==null||!playerReady)return;if(player.isPlaying())pausePlayback();else startPlayback();}
    private void startPlayback(){
        if(player==null||!playerReady||!foreground)return;
        if(audioManager.requestAudioFocus(focusRequest)!=AudioManager.AUDIOFOCUS_REQUEST_GRANTED){playbackRequested=false;showStatus("其他应用正在使用音频，请稍后点击播放。");return;}
        playbackRequested=true;
        try{if(player.getCurrentPosition()>=player.getDuration())player.seekTo(0);player.start();playPause.setText("暂停");handler.removeCallbacks(ticker);handler.post(ticker);refreshListState();}
        catch(IllegalStateException error){stopPlayer();showStatus("播放状态已重置，请重新点击试听。");}
    }
    private void pausePlayback(){
        handler.removeCallbacks(ticker);
        playbackRequested=false;
        if(player!=null&&playerReady){try{if(player.isPlaying())player.pause();if(playPause!=null)playPause.setText("播放");}catch(IllegalStateException ignored){}}
        if(audioManager!=null&&focusRequest!=null)audioManager.abandonAudioFocusRequest(focusRequest);refreshListState();
    }
    private void play(VoiceStore.Voice voice){
        if(player!=null&&playingId.equals(voice.id)){togglePlayback();return;}
        stopPlayer();MediaPlayer next=new MediaPlayer();player=next;playingId=voice.id;playbackRequested=true;
        playerBox.setVisibility(View.VISIBLE);playerTitle.setText(voice.sender+" · "+voice.date+" "+voice.time);playPause.setEnabled(false);playPause.setText("载入");seek.setEnabled(false);nextButton.setEnabled(hasNext());
        next.setAudioAttributes(speech);
        next.setOnPreparedListener(p->{if(player!=p)return;playerReady=true;seek.setMax(p.getDuration());seek.setEnabled(true);playPause.setEnabled(true);playPause.setText("播放");if(playbackRequested&&foreground)startPlayback();refreshListState();});
        next.setOnCompletionListener(p->{if(player!=p)return;if(continuous&&foreground&&playbackRequested&&playNext())return;handler.removeCallbacks(ticker);seek.setProgress(p.getDuration());playerTime.setText(duration(p.getDuration()/1000.0)+" / "+duration(p.getDuration()/1000.0));playbackRequested=false;playPause.setText("播放");audioManager.abandonAudioFocusRequest(focusRequest);refreshListState();});
        next.setOnErrorListener((p,what,extra)->{if(player==p){stopPlayer();showStatus("无法播放这条音频，请重新导入数据包补齐。");}return true;});
        try{next.setDataSource(store.audioFile(voice).getAbsolutePath());next.prepareAsync();}catch(Exception error){stopPlayer();showStatus("无法打开音频，请重新导入数据包。");}
    }
    private void refreshListState(){if(chat!=null&&list!=null&&list.getAdapter() instanceof BaseAdapter)((BaseAdapter)list.getAdapter()).notifyDataSetChanged();}
    private void stopPlayer(){handler.removeCallbacks(ticker);if(player!=null){player.release();player=null;}if(audioManager!=null&&focusRequest!=null)audioManager.abandonAudioFocusRequest(focusRequest);playingId="";playerReady=false;playbackRequested=false;if(playerBox!=null)playerBox.setVisibility(View.GONE);refreshListState();}
    private void setBusy(boolean value,String message){busy=value;progress.setVisibility(value?View.VISIBLE:View.GONE);progress.setIndeterminate(value);status.setVisibility(value?View.VISIBLE:View.GONE);status.setText(message);if(importButton!=null)importButton.setEnabled(!value);if(saveAll!=null)saveAll.setEnabled(!value&&(selecting?!selected.isEmpty():!shown.isEmpty()));for(Button tab:navigation)tab.setEnabled(!value);if(value)getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);}
    private void showStatus(String message){status.setText(message);status.setVisibility(View.VISIBLE);status.setTextColor(INK);}
    private void message(String message){Toast.makeText(this,message,Toast.LENGTH_LONG).show();}
    private void ui(Runnable work){handler.post(()->{if(!isDestroyed())work.run();});}
    private String readable(Exception error){String message=error.getMessage();return error instanceof org.json.JSONException?"索引格式不正确":message==null?"请检查数据包和手机可用空间":message;}
    static String duration(double seconds){int value=(int)Math.round(seconds);return String.format(Locale.US,"%d:%02d",value/60,value%60);}
    @Override protected void onResume(){super.onResume();foreground=true;}
    @Override protected void onPause(){foreground=false;pausePlayback();super.onPause();}
    @Override public void onBackPressed(){if(busy){message("正在处理数据，请等待完成。");return;}if(selecting){selecting=false;selected.clear();refreshVoices();}else if(chat!=null){leaveChat();build();}else if(dataScreen){dataScreen=false;build();}else super.onBackPressed();}
    @Override protected void onDestroy(){stopPlayer();worker.execute(()->store.close());worker.shutdown();handler.removeCallbacksAndMessages(null);super.onDestroy();}
}
