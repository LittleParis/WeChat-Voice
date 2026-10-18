package cn.voicekeep.library;

import android.content.*;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.FileNotFoundException;

public final class AudioProvider extends ContentProvider {
    private VoiceStore store;
    @Override public boolean onCreate(){store=new VoiceStore(getContext());return true;}
    private VoiceStore.Voice voice(Uri uri)throws FileNotFoundException {
        String id=uri.getLastPathSegment();
        if(!"cn.voicekeep.library.audio".equals(uri.getAuthority()) || uri.getPathSegments().size()!=1 || id==null || !id.matches("[0-9a-f]{24}"))throw new FileNotFoundException();
        VoiceStore.Voice voice=store.find(id);if(voice==null)throw new FileNotFoundException();return voice;
    }
    @Override public String getType(Uri uri){return "audio/mpeg";}
    @Override public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException{if(!mode.equals("r"))throw new FileNotFoundException();return ParcelFileDescriptor.open(store.audioFile(voice(uri)),ParcelFileDescriptor.MODE_READ_ONLY);}
    @Override public Cursor query(Uri uri,String[] projection,String selection,String[] args,String sort){
        try {VoiceStore.Voice voice=voice(uri);String[] columns=projection==null?new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE}:projection;MatrixCursor result=new MatrixCursor(columns);Object[] values=new Object[columns.length];for(int i=0;i<columns.length;i++){if(columns[i].equals(OpenableColumns.DISPLAY_NAME))values[i]=AudioFiles.filename(voice);else if(columns[i].equals(OpenableColumns.SIZE))values[i]=store.audioFile(voice).length();}result.addRow(values);return result;}catch(FileNotFoundException error){return null;}
    }
    @Override public Uri insert(Uri uri,ContentValues values){throw new UnsupportedOperationException();}
    @Override public int delete(Uri uri,String where,String[] args){throw new UnsupportedOperationException();}
    @Override public int update(Uri uri,ContentValues values,String where,String[] args){throw new UnsupportedOperationException();}
}
