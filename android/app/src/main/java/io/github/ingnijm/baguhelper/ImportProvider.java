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

/** Read-only app-private CSV snapshots returned to the system WebView file chooser. */
public final class ImportProvider extends ContentProvider {
    @Override public boolean onCreate() { return true; }

    private File file(Uri uri) throws FileNotFoundException {
        String name = uri.getLastPathSegment();
        if (getContext() == null || name == null || !name.matches("[0-9a-f-]{36}\\.csv") || uri.getPathSegments().size() != 1) {
            throw new FileNotFoundException("Invalid import URI");
        }
        return new File(new File(getContext().getCacheDir(), "csv-imports"), name);
    }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("Read only");
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public String getType(Uri uri) { return "text/csv"; }

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        try {
            File target = file(uri);
            String[] columns = projection == null ? new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE} : projection;
            MatrixCursor cursor = new MatrixCursor(columns);
            MatrixCursor.RowBuilder row = cursor.newRow();
            for (String column : columns) {
                if (OpenableColumns.DISPLAY_NAME.equals(column)) row.add("questions.csv");
                else if (OpenableColumns.SIZE.equals(column)) row.add(target.length());
                else row.add(null);
            }
            return cursor;
        } catch (FileNotFoundException ignored) { return null; }
    }

    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
}
