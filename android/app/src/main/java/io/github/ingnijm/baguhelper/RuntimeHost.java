package io.github.ingnijm.baguhelper;

import android.content.Context;
import android.content.res.AssetManager;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Process-owned Python runtime. All startup and archive work uses the worker. */
final class RuntimeHost {
    static final ExecutorService WORKER = Executors.newSingleThreadExecutor();
    private static JSONObject started;

    private RuntimeHost() {}

    static synchronized JSONObject start(Context context) throws Exception {
        if (started != null) return started;
        Context app = context.getApplicationContext();
        if (!Python.isStarted()) Python.start(new AndroidPlatform(app));
        File bundled = app.getDir("bundled", Context.MODE_PRIVATE);
        copyAssetTree(app.getAssets(), "static", new File(bundled, "static"));
        copyAssetTree(app.getAssets(), "seed", new File(bundled, "seed"));
        String result = Python.getInstance().getModule("android_runtime").callAttr("start",
            app.getFilesDir().getAbsolutePath(), new File(bundled, "static").getAbsolutePath(),
            new File(bundled, "seed/bagu-seed.db").getAbsolutePath(), BuildConfig.FLAVOR).toString();
        started = new JSONObject(result);
        return started;
    }

    static byte[] exportArchive() {
        return Python.getInstance().getModule("android_runtime").callAttr("export_archive").toJava(byte[].class);
    }

    static JSONObject restoreArchive(byte[] data) throws Exception {
        return new JSONObject(Python.getInstance().getModule("android_runtime").callAttr("restore_archive", data).toString());
    }

    private static void copyAssetTree(AssetManager assets, String source, File target) throws IOException {
        String[] children = assets.list(source);
        if (children != null && children.length > 0) {
            if (!target.isDirectory() && !target.mkdirs()) throw new IOException("Cannot create bundled directory");
            for (String child : children) copyAssetTree(assets, source + "/" + child, new File(target, child));
        } else {
            try (InputStream input = assets.open(source); FileOutputStream output = new FileOutputStream(target)) {
                byte[] buffer = new byte[16384];
                int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            }
        }
    }
}
