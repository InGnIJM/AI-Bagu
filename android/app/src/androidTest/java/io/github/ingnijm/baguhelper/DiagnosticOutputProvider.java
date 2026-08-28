package io.github.ingnijm.baguhelper;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;

/** Test-APK-only SAF boundary. No arbitrary paths, real backups or application data. */
public final class DiagnosticOutputProvider extends ContentProvider {
    static final String AUTHORITY = "io.github.ingnijm.baguhelper.test.diagnostics";
    private volatile boolean closed;
    @Override public boolean onCreate() { return true; }
    private File output() { return new File(getContext().getCacheDir(), "synthetic-diagnostics-output.zip"); }
    private boolean valid(Uri uri) { return AUTHORITY.equals(uri.getAuthority()) && uri.getQuery() == null && uri.getFragment() == null && "/success.zip".equals(uri.getPath()); }
    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!valid(uri)) throw new FileNotFoundException("Synthetic destination rejected");
        if ("r".equals(mode)) return ParcelFileDescriptor.open(output(), ParcelFileDescriptor.MODE_READ_ONLY);
        if (!"wt".equals(mode) && !"w".equals(mode)) throw new FileNotFoundException("Unsupported test mode");
        closed = false;
        try {
            return ParcelFileDescriptor.open(output(), ParcelFileDescriptor.MODE_CREATE | ParcelFileDescriptor.MODE_TRUNCATE | ParcelFileDescriptor.MODE_WRITE_ONLY,
                new Handler(Looper.getMainLooper()), error -> closed = true);
        } catch (IOException error) { throw new FileNotFoundException("Synthetic output unavailable"); }
    }
    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String order) {
        if (!valid(uri)) throw new IllegalArgumentException("Invalid test output");
        MatrixCursor result = new MatrixCursor(new String[]{"closed", "size"});
        result.addRow(new Object[]{closed ? 1 : 0, output().length()});
        return result;
    }
    @Override public String getType(Uri uri) { return "application/zip"; }
    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) {
        if (!valid(uri)) throw new IllegalArgumentException("Invalid test output");
        closed = false; return output().delete() ? 1 : 0;
    }
}
