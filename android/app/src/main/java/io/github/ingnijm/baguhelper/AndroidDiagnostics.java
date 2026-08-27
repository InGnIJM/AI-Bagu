package io.github.ingnijm.baguhelper;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.os.Build;
import android.os.SystemClock;
import android.webkit.WebView;
import org.json.JSONArray;
import org.json.JSONObject;
import org.json.JSONTokener;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Failure-safe diagnostic boundary. This executor never waits for the Python worker. */
final class AndroidDiagnostics {
    static final ExecutorService EXPORTER = Executors.newSingleThreadExecutor();
    private static volatile DiagnosticStore store;
    private static final DiagnosticPolicy.RateLimit WEB_LIMIT = new DiagnosticPolicy.RateLimit();
    private static long writeFailures;
    private AndroidDiagnostics() {}

    static void initialize(Context context) {
        try {
            store = new DiagnosticStore(new File(context.getFilesDir(), "logs"), CODEC);
            event("native.start", "initialize", null, null, null);
        } catch (Throwable ignored) { /* Diagnostic failure cannot prevent app startup. */ }
    }

    static void reportWeb(String json) {
        try {
            if (!WEB_LIMIT.accept(SystemClock.elapsedRealtime())) return;
            if (json == null || json.length() > DiagnosticPolicy.EVENT_BYTES || json.getBytes(StandardCharsets.UTF_8).length > DiagnosticPolicy.EVENT_BYTES) { WEB_LIMIT.drop(); return; }
            Map<String, Object> value = DiagnosticPolicy.sanitize(CODEC.parse(json), "web");
            if (value.isEmpty()) { WEB_LIMIT.drop(); return; }
            flushDropped();
            write("web", value);
        } catch (Throwable ignored) { WEB_LIMIT.drop(); }
    }

    static synchronized void flushDropped() {
        long dropped = WEB_LIMIT.takeDropped();
        if (dropped > 0) {
            Map<String, Object> value = new LinkedHashMap<>();
            value.put("event", "web.dropped"); value.put("level", "WARNING"); value.put("dropped", dropped);
            write("web", value);
        }
    }
    static void event(String event, String stage, Throwable failure, String operationId, Integer code) {
        try {
            Map<String, Object> value = new LinkedHashMap<>();
            value.put("time", Instant.now().toString()); value.put("event", event); value.put("stage", stage);
            value.put("level", failure != null || "error".equals(stage) ? "ERROR" : "INFO");
            if (operationId != null) value.put("operation_id", operationId);
            if (code != null) value.put("error_code", code);
            if (failure != null) {
                value.put("error_type", DiagnosticPolicy.errorType(failure.getClass().getSimpleName()));
                List<Map<String, Object>> frames = new ArrayList<>();
                for (StackTraceElement frame : failure.getStackTrace()) {
                    if (frames.size() >= 16) break;
                    if (!DiagnosticPolicy.FILES.contains(frame.getFileName()) || frame.getLineNumber() <= 0) continue;
                    Map<String, Object> location = new LinkedHashMap<>();
                    location.put("file", frame.getFileName()); location.put("line", frame.getLineNumber()); frames.add(location);
                }
                value.put("frames", frames);
            }
            write("native", value);
        } catch (Throwable ignored) { /* Never log the logger failure recursively. */ }
    }
    private static synchronized void write(String source, Map<String, Object> value) {
        try { if (store == null) { writeFailures++; return; } store.append(source, value); }
        catch (Throwable ignored) { if (writeFailures < Integer.MAX_VALUE) writeFailures++; }
    }
    static byte[] export(Context context) throws Exception {
        flushDropped();
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("version_name", BuildConfig.VERSION_NAME); metadata.put("version_code", BuildConfig.VERSION_CODE);
        metadata.put("channel", BuildConfig.UPDATE_CHANNEL); metadata.put("flavor", BuildConfig.FLAVOR);
        metadata.put("android_api", Build.VERSION.SDK_INT);
        synchronized (AndroidDiagnostics.class) { metadata.put("write_failures", writeFailures); }
        // This static package query does not instantiate or initialize a WebView.
        try {
            PackageInfo webview = WebView.getCurrentWebViewPackage();
            if (webview != null && webview.versionName != null && webview.versionName.matches("[0-9.]{1,80}")) metadata.put("webview_version", webview.versionName);
        } catch (Throwable ignored) { metadata.put("webview_unavailable", true); }
        DiagnosticStore current = store;
        if (current == null) current = new DiagnosticStore(new File(context.getFilesDir(), "logs"), CODEC);
        return current.export(metadata);
    }

    private static final DiagnosticStore.Codec CODEC = new DiagnosticStore.Codec() {
        @Override public Map<String, Object> parse(String text) {
            try {
                JSONTokener parser = new JSONTokener(text);
                Object value = parser.nextValue();
                if (!(value instanceof JSONObject) || parser.nextClean() != 0) throw new IllegalArgumentException("Invalid diagnostic JSON");
                return object((JSONObject) value, 0);
            } catch (Exception failure) { throw new IllegalArgumentException("Invalid diagnostic JSON"); }
        }
        @Override public String json(Object value) { return value instanceof Map ? new JSONObject((Map<?, ?>) value).toString() : "{}"; }
        private Map<String, Object> object(JSONObject value, int depth) throws Exception {
            if (depth > 4) throw new IllegalArgumentException("Diagnostic nesting limit");
            Map<String, Object> out = new LinkedHashMap<>();
            Iterator<String> keys = value.keys();
            while (keys.hasNext()) {
                String key = keys.next(); Object item = value.get(key);
                if (item instanceof JSONObject) item = object((JSONObject) item, depth + 1);
                else if (item instanceof JSONArray) {
                    List<Object> list = new ArrayList<>(); JSONArray array = (JSONArray) item;
                    if (array.length() > 32) throw new IllegalArgumentException("Diagnostic list limit");
                    for (int n = 0; n < array.length(); n++) { Object entry = array.get(n); list.add(entry instanceof JSONObject ? object((JSONObject) entry, depth + 1) : null); }
                    item = list;
                }
                out.put(key, item == JSONObject.NULL ? null : item);
            }
            return out;
        }
    };
}
