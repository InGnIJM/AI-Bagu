package io.github.ingnijm.baguhelper;

import android.app.Instrumentation;
import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import androidx.lifecycle.Lifecycle;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.JSONArray;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import static org.junit.Assert.*;

/** Real bridge, Activity lifecycle, controller and JS event encoding; no microphone is opened. */
@RunWith(AndroidJUnit4.class)
public final class AndroidSpeechAcceptanceTest {
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private ActivityScenario<MainActivity> scenario;
    private MainActivity activity;
    private WebView web;
    private final Backend backend = new Backend();

    private static final class Backend implements SpeechInput.Backend {
        boolean available = true, permission = true;
        volatile int starts, stops, destroys;
        SpeechInput.Listener listener;
        volatile Consumer<Boolean> permissionReply;
        void mainThread() { assertSame(Looper.getMainLooper(), Looper.myLooper()); }
        @Override public boolean available() { mainThread(); return available; }
        @Override public boolean hasPermission() { mainThread(); return permission; }
        @Override public void requestPermission(Consumer<Boolean> reply) { mainThread(); permissionReply = reply; }
        @Override public SpeechInput.Engine create(SpeechInput.Listener value) {
            mainThread(); listener = value;
            return new SpeechInput.Engine() {
                @Override public void start() { mainThread(); starts++; }
                @Override public void stop() { mainThread(); stops++; }
                @Override public void cancel() { mainThread(); }
                @Override public void destroy() { mainThread(); destroys++; }
            };
        }
    }

    @Before public void launch() throws Exception {
        scenario = ActivityScenario.launch(MainActivity.class);
        scenario.onActivity(value -> { activity = value; web = findWeb(value.getWindow().getDecorView()); });
        Field ready = MainActivity.class.getDeclaredField("pageReady"); ready.setAccessible(true);
        long end = SystemClock.uptimeMillis() + 45000;
        while (!ready.getBoolean(activity) && SystemClock.uptimeMillis() < end) SystemClock.sleep(100);
        assertTrue("Native page must finish loading", ready.getBoolean(activity));
        end = SystemClock.uptimeMillis() + 30000;
        while (!"true".equals(js("/^[0-9]+$/.test(document.getElementById('st-total').textContent)"))
                && SystemClock.uptimeMillis() < end) SystemClock.sleep(100);
        assertEquals("Initial refresh must finish before installing the synthetic session", "true",
            js("/^[0-9]+$/.test(document.getElementById('st-total').textContent)"));
        Field controller = MainActivity.class.getDeclaredField("speechInput"); controller.setAccessible(true);
        Method publish = MainActivity.class.getDeclaredMethod("publishSpeech", SpeechInput.Event.class); publish.setAccessible(true);
        instrumentation.runOnMainSync(() -> {
            Handler handler = new Handler(Looper.getMainLooper());
            SpeechInput input = new SpeechInput(backend,
                (delay, job) -> { handler.postDelayed(job, delay); return () -> handler.removeCallbacks(job); },
                event -> {
                    try { publish.invoke(activity, event); } catch (ReflectiveOperationException error) { throw new AssertionError(error); }
                });
            try { controller.set(activity, input); } catch (ReflectiveOperationException error) { throw new AssertionError(error); }
        });
        js("window.__speechEvents=[];window.addEventListener('bagu-speech',function(e){window.__speechEvents.push(e.detail);});");
    }

    @After public void close() { if (scenario != null) scenario.close(); }

    @Test public void nativeBridgeReportsUnavailableServiceWithoutPermissionOrAudio() throws Exception {
        backend.available = false;
        js("BaguNative.startSpeech('missing-service');");
        JSONArray events = events(1);
        assertEquals("error", events.getJSONObject(0).getString("type"));
        assertEquals("missing-service", events.getJSONObject(0).getString("requestId"));
        assertTrue(events.getJSONObject(0).getString("message").contains("不可用"));
        assertEquals(0, backend.starts); assertNull(backend.permissionReply);
    }

    @Test public void realBridgeStopsAndSafelyEncodesFinalTextExactlyOnce() throws Exception {
        js("BaguNative.startSpeech('roundtrip');"); awaitStarted();
        instrumentation.runOnMainSync(() -> backend.listener.ready()); events(1);
        js("BaguNative.stopSpeech('roundtrip');");
        instrumentation.waitForIdleSync(); assertEquals(1, backend.stops);
        String text = "答案\"\\\n</script>\u2028\u2029";
        instrumentation.runOnMainSync(() -> backend.listener.result(text));
        JSONArray received = events(2);
        assertEquals("result", received.getJSONObject(1).getString("type"));
        assertEquals(text, received.getJSONObject(1).getString("text")); assertEquals(1, backend.destroys);
        instrumentation.runOnMainSync(() -> backend.listener.result("迟到答案"));
        assertEquals(2, new JSONArray(js("window.__speechEvents")).length());
    }

    @Test public void activityPausePreservesPermissionButGrantCannotStartRecording() throws Exception {
        backend.permission = false; js("BaguNative.startSpeech('pending-permission');");
        long end = SystemClock.uptimeMillis() + 5000;
        while (backend.permissionReply == null && SystemClock.uptimeMillis() < end) SystemClock.sleep(50);
        assertNotNull(backend.permissionReply);
        scenario.moveToState(Lifecycle.State.CREATED);
        deliverPermission(true);
        assertEquals(0, backend.starts);
        scenario.moveToState(Lifecycle.State.RESUMED);
        assertEquals("cancelled", events(1).getJSONObject(0).getString("type"));
        assertEquals(0, backend.starts);
    }

    @Test public void activityPauseStillReportsPermissionDenial() throws Exception {
        backend.permission = false; js("BaguNative.startSpeech('denied-permission');");
        awaitPermission();
        scenario.moveToState(Lifecycle.State.CREATED);
        deliverPermission(false);
        scenario.moveToState(Lifecycle.State.RESUMED);
        JSONArray received = events(1);
        assertEquals(1, received.length());
        assertEquals("error", received.getJSONObject(0).getString("type"));
        assertTrue(received.getJSONObject(0).getString("message").contains("权限"));
        assertEquals(0, backend.starts);
    }

    @Test public void fullAnswerPageShowsPermissionDenialAfterActivityPause() throws Exception {
        backend.permission = false;
        // The synthetic session exists only in this WebView; no database/API write or grading.
        js("window.__speechQaPosts=0;window.__speechQaFetch=window.fetch;"
            + "window.fetch=function(url,options){if(options && options.method==='POST'){window.__speechQaPosts++;throw new Error('Test forbids POST');}return window.__speechQaFetch(url,options);};"
            + "session={session_id:'s_speech_permission_qa',items:[{id:7}],pending:[{id:7,question:'权限测试题',category:'测试'}]};"
            + "showView('quiz');renderQuiz();document.getElementById('ans').value='已有答案';startSpeechInput();");
        awaitPermission();
        scenario.moveToState(Lifecycle.State.CREATED);
        deliverPermission(false);
        scenario.moveToState(Lifecycle.State.RESUMED);
        events(1);
        assertEquals("true", js("document.getElementById('speech-error').textContent.indexOf('权限')>=0"
            + " && !document.getElementById('speech-error').classList.contains('hidden')"
            + " && !document.getElementById('ans').readOnly"
            + " && document.getElementById('ans').value==='已有答案'"
            + " && !document.getElementById('btn-submit').disabled && window.__speechQaPosts===0"));
        assertEquals(0, backend.starts);
    }

    private void awaitPermission() {
        long end = SystemClock.uptimeMillis() + 5000;
        while (backend.permissionReply == null && SystemClock.uptimeMillis() < end) SystemClock.sleep(50);
        assertNotNull(backend.permissionReply);
    }

    private void deliverPermission(boolean granted) throws Exception {
        Field pending = MainActivity.class.getDeclaredField("microphoneReply"); pending.setAccessible(true);
        instrumentation.runOnMainSync(() -> {
            try { pending.set(activity, backend.permissionReply); }
            catch (ReflectiveOperationException error) { throw new AssertionError(error); }
            activity.onRequestPermissionsResult(42, new String[]{Manifest.permission.RECORD_AUDIO},
                new int[]{granted ? PackageManager.PERMISSION_GRANTED : PackageManager.PERMISSION_DENIED});
        });
    }

    @Test public void activityPauseReleasesEngineAndIgnoresLateResult() throws Exception {
        js("BaguNative.startSpeech('pause');"); awaitStarted();
        scenario.moveToState(Lifecycle.State.CREATED);
        assertEquals(1, backend.destroys);
        instrumentation.runOnMainSync(() -> backend.listener.result("后台结果"));
        scenario.moveToState(Lifecycle.State.RESUMED);
        JSONArray received = events(1);
        assertEquals(1, received.length()); assertEquals("cancelled", received.getJSONObject(0).getString("type"));
    }

    private void awaitStarted() {
        long end = SystemClock.uptimeMillis() + 5000;
        while (backend.starts == 0 && SystemClock.uptimeMillis() < end) SystemClock.sleep(50);
        assertEquals(1, backend.starts);
    }

    private JSONArray events(int count) throws Exception {
        long end = SystemClock.uptimeMillis() + 5000;
        JSONArray events;
        do {
            events = new JSONArray(js("window.__speechEvents"));
            if (events.length() >= count) return events;
            SystemClock.sleep(50);
        } while (SystemClock.uptimeMillis() < end);
        fail("Missing native speech event"); return events;
    }

    private String js(String expression) throws Exception {
        AtomicReference<String> result = new AtomicReference<>(); CountDownLatch done = new CountDownLatch(1);
        instrumentation.runOnMainSync(() -> web.evaluateJavascript(expression, value -> { result.set(value); done.countDown(); }));
        assertTrue(done.await(5, TimeUnit.SECONDS)); return result.get();
    }

    private WebView findWeb(View view) {
        if (view instanceof WebView) return (WebView) view;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) { WebView found = findWeb(group.getChildAt(i)); if (found != null) return found; }
        }
        return null;
    }
}
