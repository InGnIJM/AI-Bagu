package io.github.ingnijm.baguhelper;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/** Separate from CSV import: exactly one APK, read-only temporary installer URI grants. */
public final class UpdateApkProvider extends ContentProvider {
    static final String MIME="application/vnd.android.package-archive";
    @Override public boolean onCreate(){return true;}
    private File target(Uri uri)throws FileNotFoundException {
        if(getContext()==null||!uri.toString().equals("content://"+getContext().getPackageName()+".updates/candidate.apk"))
            throw new FileNotFoundException("Invalid update URI");
        return new File(new File(getContext().getCacheDir(),"updates"),"candidate.apk");
    }
    @Override public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException {
        if(!"r".equals(mode))throw new FileNotFoundException("Read only");
        return ParcelFileDescriptor.open(target(uri),ParcelFileDescriptor.MODE_READ_ONLY);
    }
    @Override public String getType(Uri uri){try{target(uri);return MIME;}catch(FileNotFoundException ignored){return null;}}
    @Override public Cursor query(Uri uri,String[] projection,String selection,String[] args,String sort){
        try{File file=target(uri);String[] columns=projection==null?new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE}:projection;
            MatrixCursor cursor=new MatrixCursor(columns);MatrixCursor.RowBuilder row=cursor.newRow();
            for(String column:columns)row.add(OpenableColumns.DISPLAY_NAME.equals(column)?"bagu-update.apk":OpenableColumns.SIZE.equals(column)?file.length():null);
            return cursor;
        }catch(FileNotFoundException ignored){return null;}
    }
    @Override public Uri insert(Uri uri,ContentValues values){throw new UnsupportedOperationException();}
    @Override public int delete(Uri uri,String where,String[] args){throw new UnsupportedOperationException();}
    @Override public int update(Uri uri,ContentValues values,String where,String[] args){throw new UnsupportedOperationException();}
}
