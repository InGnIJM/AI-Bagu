package io.github.ingnijm.baguhelper;

import android.content.Context;
import android.app.Activity;
import android.app.Application;
import android.app.Instrumentation;
import android.content.ComponentName;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ActivityInfo;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.TextView;
import androidx.test.core.app.ActivityScenario;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.zip.*;
import static org.junit.Assert.*;

/** Isolated-device diagnostics only: no installer, network, or live user data. */
@RunWith(AndroidJUnit4.class)
public class DiagnosticAcceptanceTest {
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private static Object field(Object owner, String name) {
        try { java.lang.reflect.Field field = owner.getClass().getDeclaredField(name); field.setAccessible(true); return field.get(owner); }
        catch (ReflectiveOperationException error) { throw new AssertionError("Missing acceptance boundary", error); }
    }
    private static void invoke(Object owner, String name) {
        try { java.lang.reflect.Method method = owner.getClass().getDeclaredMethod(name); method.setAccessible(true); method.invoke(owner); }
        catch (ReflectiveOperationException error) { throw new AssertionError("Acceptance boundary failed", error); }
    }
    private static int documentRequestCode(MainActivity activity) {
        return (Integer)field(field(activity,"state"),"documentRequestCode");
    }
    /** Freeze the slow Python boundary. Every Activity, callback, view and SAF operation remains real. */
    private final class StartupFixture implements AutoCloseable {
        final CountDownLatch release = new CountDownLatch(1);
        final java.lang.reflect.Field runtime;
        final Object previousRuntime;
        ActivityScenario<MainActivity> scenario;
        MainActivity restored;
        StartupFixture() throws Exception {
            Context app = instrumentation.getTargetContext();
            assertTrue(app.getSharedPreferences("bagu-native-updates", Context.MODE_PRIVATE).edit().putBoolean("automatic", false).commit());
            CountDownLatch entered = new CountDownLatch(1);
            RuntimeHost.WORKER.execute(() -> { entered.countDown(); try { release.await(30, TimeUnit.SECONDS); } catch (InterruptedException error) { Thread.currentThread().interrupt(); } });
            assertTrue("Python worker pause", entered.await(5, TimeUnit.SECONDS));
            runtime = RuntimeHost.class.getDeclaredField("started"); runtime.setAccessible(true);
            previousRuntime = runtime.get(null);
            // The unsafe local port fails closed without sending a network request or opening a DB.
            runtime.set(null, new JSONObject().put("url", "http://127.0.0.1:1/").put("port", 1));
            scenario = ActivityScenario.launch(MainActivity.class);
        }
        @Override public void close() throws Exception {
            if (restored != null) instrumentation.runOnMainSync(() -> instrumentation.callActivityOnDestroy(restored));
            if (scenario != null) scenario.close();
            release.countDown();
            RuntimeHost.WORKER.submit(() -> {}).get(10, TimeUnit.SECONDS);
            instrumentation.waitForIdleSync();
            runtime.set(null, previousRuntime);
        }
    }
    private Instrumentation.ActivityMonitor interceptPicker() {
        Instrumentation.ActivityMonitor monitor = new Instrumentation.ActivityMonitor(new IntentFilter(Intent.ACTION_CREATE_DOCUMENT), null, true);
        instrumentation.addMonitor(monitor); return monitor;
    }
    private void mainFrameFailure(MainActivity activity) {
        WebView web = (WebView) field(activity, "web");
        assertNotNull(web);
        WebResourceResponse response = new WebResourceResponse("text/html", "UTF-8", new ByteArrayInputStream(new byte[0]));
        response.setStatusCodeAndReasonPhrase(502, "Synthetic failure");
        web.getWebViewClient().onReceivedHttpError(web, new WebResourceRequest() {
            public Uri getUrl() { return Uri.parse("http://127.0.0.1:1/"); }
            public boolean isForMainFrame() { return true; }
            public boolean isRedirect() { return false; }
            public boolean hasGesture() { return false; }
            public String getMethod() { return "GET"; }
            public Map<String,String> getRequestHeaders() { return Collections.emptyMap(); }
        }, response);
    }
    private void awaitNativeMessage(StartupFixture fixture, String expected) {
        long deadline = SystemClock.uptimeMillis() + 10000;
        while (SystemClock.uptimeMillis() < deadline) {
            AtomicReference<String> message = new AtomicReference<>();
            fixture.scenario.onActivity(activity -> message.set(((TextView)field(activity,"diagnosticsResult")).getText().toString()));
            if (message.get().contains(expected)) return;
            SystemClock.sleep(50);
        }
        fail("Native diagnostic result timed out");
    }

    @Test public void failedMainFrameKeepsNativeExportAndNullWebViewLifecycleSafe() throws Exception {
        try (StartupFixture fixture = new StartupFixture()) {
            fixture.scenario.onActivity(activity -> {
                mainFrameFailure(activity);
                assertEquals(View.VISIBLE, ((View)field(activity,"progressPanel")).getVisibility());
                assertTrue(((Button)field(activity,"diagnostics")).isEnabled());
                invoke(activity,"discardWebView");
                assertNull(field(activity,"web"));
                assertTrue(((TextView)field(activity,"progressText")).getText().toString().contains("n_"));
            });
            // Closing this real Activity exercises onPause/onDestroy with web == null.
        }
    }
    @Test public void pendingStartupRecreationKeepsTheFailureFeedbackOperation() throws Exception {
        try (StartupFixture fixture = new StartupFixture()) {
            AtomicReference<Object> original = new AtomicReference<>();
            AtomicReference<String> operation = new AtomicReference<>();
            fixture.scenario.onActivity(activity -> {
                Object state = field(activity,"state"); original.set(state);
                operation.set((String)field(state,"startupOperationId"));
                assertEquals(true,field(state,"starting"));
            });
            fixture.scenario.recreate();
            fixture.scenario.onActivity(activity -> {
                assertSame(original.get(),field(activity,"state"));
                assertEquals(operation.get(),field(field(activity,"state"),"startupOperationId"));
                mainFrameFailure(activity);
                assertTrue(((TextView)field(activity,"progressText")).getText().toString().contains(operation.get()));
            });
            Map<String,String> archive = unzip(AndroidDiagnostics.EXPORTER.submit(() -> AndroidDiagnostics.export(instrumentation.getTargetContext())).get(5,TimeUnit.SECONDS));
            assertTrue(archive.get("native.jsonl").contains(operation.get()));
        }
    }
    @Test public void repeatedExportClickOpensOnePickerAndCancellationRestoresNativeButton() throws Exception {
        Instrumentation.ActivityMonitor monitor = interceptPicker();
        try (StartupFixture fixture = new StartupFixture()) {
            fixture.scenario.onActivity(activity -> {
                mainFrameFailure(activity);
                activity.openDocument("diagnostics",null);
                assertFalse(((Button)field(activity,"diagnostics")).isEnabled());
                activity.openDocument("diagnostics",null);
                assertEquals("diagnostics",field(field(activity,"state"),"operation"));
            });
            assertEquals(1,monitor.getHits());
            fixture.scenario.onActivity(activity -> {
                activity.onActivityResult(documentRequestCode(activity),Activity.RESULT_CANCELED,null);
                assertNull(field(field(activity,"state"),"operation"));
                assertTrue(((Button)field(activity,"diagnostics")).isEnabled());
                assertTrue(((TextView)field(activity,"diagnosticsResult")).getText().toString().contains("已取消"));
            });
        } finally { instrumentation.removeMonitor(monitor); }
    }
    @Test public void pickerRecreationRetainsOneOperationWithoutLaunchingAgain() throws Exception {
        Instrumentation.ActivityMonitor monitor = interceptPicker();
        try (StartupFixture fixture = new StartupFixture()) {
            AtomicReference<String> operation = new AtomicReference<>();
            fixture.scenario.onActivity(activity -> { activity.openDocument("diagnostics",null); operation.set((String)field(field(activity,"state"),"diagnosticsId")); });
            fixture.scenario.recreate();
            fixture.scenario.onActivity(activity -> {
                assertEquals("diagnostics",field(field(activity,"state"),"operation"));
                assertEquals(operation.get(),field(field(activity,"state"),"diagnosticsId"));
                assertFalse(((Button)field(activity,"diagnostics")).isEnabled());
                activity.onActivityResult(documentRequestCode(activity),Activity.RESULT_CANCELED,null);
            });
            assertEquals(1,monitor.getHits());
        } finally { instrumentation.removeMonitor(monitor); }
    }
    @Test public void newActivityFromLostProcessStateCancelsPickerAndNeverReplaysWorkingSave() throws Exception {
        for (boolean working : new boolean[]{false,true}) {
            Instrumentation.ActivityMonitor monitor = interceptPicker();
            try (StartupFixture fixture = new StartupFixture()) {
                Bundle saved = new Bundle();
                fixture.scenario.onActivity(activity -> { activity.openDocument("diagnostics",null); instrumentation.callActivityOnSaveInstanceState(activity,saved); });
                fixture.scenario.close(); fixture.scenario = null;
                if (working) { saved.putString("documentOperation",null); saved.putBoolean("documentWorking",true); saved.putString("workingOperation","diagnostics"); }
                Context app = instrumentation.getTargetContext();
                ActivityInfo info = app.getPackageManager().getActivityInfo(new ComponentName(app,MainActivity.class),0);
                instrumentation.runOnMainSync(() -> {
                    try {
                        fixture.restored = (MainActivity)instrumentation.newActivity(MainActivity.class,app,new Binder(),(Application)app.getApplicationContext(),new Intent(app,MainActivity.class),info,"Bagu diagnostic fixture",null,null,null);
                        fixture.restored.setTheme(info.getThemeResource());
                        instrumentation.callActivityOnCreate(fixture.restored,saved);
                    } catch (Exception error) { throw new AssertionError("Fresh Activity saved-state restore failed",error); }
                    Object state = field(fixture.restored,"state");
                    assertNull(field(state,"operation")); assertEquals(false,field(state,"working"));
                    String message = ((TextView)field(fixture.restored,"diagnosticsResult")).getText().toString();
                    assertTrue(message.contains(working?"中断":"已取消"));
                    // A late picker result from the lost process is ignored.
                    fixture.restored.onActivityResult(41,Activity.RESULT_OK,new Intent().setData(Uri.parse("content://"+DiagnosticOutputProvider.AUTHORITY+"/success.zip")));
                    assertEquals(false,field(state,"working"));
                });
                assertEquals(1,monitor.getHits());
            } finally { instrumentation.removeMonitor(monitor); }
        }
    }
    @Test public void freshProcessPackPendingMarkerCancelsWithoutBytesPreviewOrLateReplay() throws Exception {
        try (StartupFixture fixture = new StartupFixture()) {
            Bundle saved = new Bundle();
            saved.putString("documentPendingImportKind", "pack-import");
            Context app = instrumentation.getTargetContext();
            ActivityInfo info = app.getPackageManager().getActivityInfo(new ComponentName(app,MainActivity.class),0);
            instrumentation.runOnMainSync(() -> {
                try {
                    fixture.restored = (MainActivity)instrumentation.newActivity(MainActivity.class,app,new Binder(),
                        (Application)app.getApplicationContext(),new Intent(app,MainActivity.class),info,
                        "Bagu pack marker fixture",null,null,null);
                    fixture.restored.setTheme(info.getThemeResource());
                    instrumentation.callActivityOnCreate(fixture.restored,saved);
                } catch (Exception error) { throw new AssertionError("Fresh pack marker restore failed",error); }
                Object state = field(fixture.restored,"state");
                assertNull(field(state,"pendingImport"));
                assertNull(field(state,"documentLease"));
                @SuppressWarnings("unchecked") ArrayList<JSONObject> results = (ArrayList<JSONObject>)field(state,"results");
                assertEquals(1,results.size());
                JSONObject result = results.get(0);
                assertEquals("pack-import",result.optString("operation"));
                assertEquals("cancelled",result.optString("status"));
                assertFalse(result.toString().contains("archive_base64"));
                fixture.restored.onActivityResult(41,Activity.RESULT_OK,
                    new Intent().setData(Uri.parse("content://"+DiagnosticOutputProvider.AUTHORITY+"/success.zip")));
                assertNull(field(state,"pendingImport"));
                assertEquals(false,field(state,"working"));
            });
        }
    }
    @Test public void outputFailureThenSuccessfulClosedZipAreReportedWithoutJavaScriptOrPython() throws Exception {
        Instrumentation.ActivityMonitor monitor = interceptPicker();
        Context app = instrumentation.getTargetContext();
        Uri success = Uri.parse("content://"+DiagnosticOutputProvider.AUTHORITY+"/success.zip");
        AtomicReference<Integer> staleRequest = new AtomicReference<>();
        try (StartupFixture fixture = new StartupFixture()) {
            fixture.scenario.onActivity(activity -> {
                mainFrameFailure(activity); invoke(activity,"discardWebView");
                activity.openDocument("diagnostics",null);
                staleRequest.set(documentRequestCode(activity));
                activity.onActivityResult(documentRequestCode(activity),Activity.RESULT_OK,new Intent().setData(Uri.parse("content://"+DiagnosticOutputProvider.AUTHORITY+"/failure.zip")));
            });
            awaitNativeMessage(fixture,"保存失败");
            fixture.scenario.onActivity(activity -> {
                assertTrue(((Button)field(activity,"diagnostics")).isEnabled());
                activity.openDocument("diagnostics",null);
                int currentRequest=documentRequestCode(activity);
                assertNotEquals(staleRequest.get().intValue(),currentRequest);
                activity.onActivityResult(staleRequest.get(),Activity.RESULT_CANCELED,null);
                assertEquals("diagnostics",field(field(activity,"state"),"operation"));
                assertNotNull(field(field(activity,"state"),"documentLease"));
                activity.onActivityResult(currentRequest,Activity.RESULT_OK,new Intent().setData(success));
            });
            awaitNativeMessage(fixture,"已保存");
            long deadline = SystemClock.uptimeMillis()+5000; boolean closed = false;
            while (!closed && SystemClock.uptimeMillis()<deadline) {
                try(Cursor cursor=app.getContentResolver().query(success,null,null,null,null)){ assertNotNull(cursor); assertTrue(cursor.moveToFirst()); closed=cursor.getInt(0)==1; }
                if(!closed)SystemClock.sleep(50);
            }
            assertTrue("Provider descriptor received close",closed);
            try(InputStream input=app.getContentResolver().openInputStream(success)){
                assertNotNull(input); byte[] bytes=HostPolicy.readBounded(input,8*1024*1024);
                assertEquals(5,unzip(bytes).size());
            }
            assertEquals(1,fixture.release.getCount());
        } finally { instrumentation.removeMonitor(monitor); app.getContentResolver().delete(success,null,null); }
    }
    private Map<String, String> unzip(byte[] bytes) throws Exception {
        Map<String, String> entries = new HashMap<>();
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(bytes))) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] chunk = new byte[8192]; int n;
                while ((n = zip.read(chunk)) != -1) out.write(chunk, 0, n);
                entries.put(entry.getName(), new String(out.toByteArray(), StandardCharsets.UTF_8));
            }
        }
        return entries;
    }
    @Test public void bridgeAndExporterWorkWhilePythonWorkerCannotRun() throws Exception {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        assertTrue(app.getApplicationContext() instanceof BaguApplication);
        CountDownLatch blocked = new CountDownLatch(1), release = new CountDownLatch(1);
        RuntimeHost.WORKER.execute(() -> {
            blocked.countDown();
            try { release.await(10, TimeUnit.SECONDS); } catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
        });
        assertTrue(blocked.await(2, TimeUnit.SECONDS));
        try {
            AndroidDiagnostics.reportWeb("{\"event\":\"web.api\",\"request_id\":\"r_abcdef123456\",\"status\":502,\"message\":\"sk-test-diagnostics-secret\",\"route\":\"/api/models/private-name\"}");
            AndroidDiagnostics.reportWeb("{\"event\":\"native.crash\",\"message\":\"sk-test-diagnostics-secret\"}");
            AndroidDiagnostics.reportWeb("{\"event\":\"web.error\"}{}");
            byte[] bytes = AndroidDiagnostics.EXPORTER.submit(() -> AndroidDiagnostics.export(app)).get(5, TimeUnit.SECONDS);
            Map<String, String> entries = unzip(bytes);
            assertEquals(5, entries.size());
            assertTrue(entries.get("web.jsonl").contains("r_abcdef123456"));
            assertFalse(entries.toString().contains("sk-test-diagnostics-secret"));
            assertFalse(entries.toString().contains("private-name"));
            assertFalse(entries.get("web.jsonl").contains("native.crash"));
            assertTrue(entries.get("manifest.json").contains("android_api"));
        } finally { release.countDown(); }
    }
    @Test public void symlinkSourcesAndSymlinkLogDirectoryAreRejectedWithoutReadingTarget() throws Exception {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        File temp = Files.createTempDirectory(app.getCacheDir().toPath(), "diagnostic-test-").toFile();
        File logs = new File(temp, "logs"); assertTrue(logs.mkdir());
        File outside = new File(temp, "outside.log");
        Files.write(outside.toPath(), "{\"event\":\"native.crash\",\"count\":997}\n".getBytes(StandardCharsets.UTF_8));
        android.system.Os.symlink(outside.getAbsolutePath(), new File(logs, "bagu-native.log").getAbsolutePath());
        DiagnosticStore.Codec codec = new DiagnosticStore.Codec() {
            public Map<String, Object> parse(String text) {
                try { JSONObject object = new JSONObject(text); Map<String, Object> result = new HashMap<>(); Iterator<String> keys = object.keys(); while(keys.hasNext()){String key=keys.next();result.put(key,object.get(key));} return result; }
                catch(Exception error){throw new IllegalArgumentException("Invalid diagnostic JSON");}
            }
            public String json(Object value) { return new JSONObject((Map<?,?>)value).toString(); }
        };
        Map<String, String> files = unzip(new DiagnosticStore(logs, codec).export(Collections.emptyMap()));
        assertEquals("", files.get("native.jsonl"));
        assertTrue(files.get("manifest.json").contains("\"unreadable\":true"));
        File alias = new File(temp, "alias"); android.system.Os.symlink(logs.getAbsolutePath(), alias.getAbsolutePath());
        assertEquals("", unzip(new DiagnosticStore(alias, codec).export(Collections.emptyMap())).get("native.jsonl"));
        Files.delete(new File(logs, "bagu-native.log").toPath()); Files.delete(alias.toPath());
        Files.delete(outside.toPath()); Files.delete(logs.toPath()); Files.delete(temp.toPath());
    }
}
