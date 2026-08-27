package io.github.ingnijm.baguhelper;

import android.app.Instrumentation;
import android.os.Build;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import android.view.KeyEvent;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Rect;
import com.chaquo.python.Python;
import com.chaquo.python.PyObject;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.uiautomator.UiDevice;
import androidx.test.uiautomator.By;
import androidx.test.uiautomator.UiObject2;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.io.File;
import java.util.TreeMap;
import org.json.JSONObject;
import org.json.JSONArray;
import static org.junit.Assert.*;

/** Real Activity/WebView assertions. No test code is included in the app APK. */
@RunWith(AndroidJUnit4.class)
public final class AndroidAcceptanceTest {
    private ActivityScenario<MainActivity> scenario;
    private MainActivity activity;
    private WebView web;
    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();

    @Before public void launch() throws Exception {
        scenario = ActivityScenario.launch(MainActivity.class);
        scenario.onActivity(value -> {
            activity = value;
            web = findWebView(value.getWindow().getDecorView());
        });
        assertNotNull("Actual Activity must contain its WebView", web);
        await("native app boot", "document.body.classList.contains('android-app') && typeof showView==='function' && typeof session==='object'", 45000);
        java.lang.reflect.Field ready = MainActivity.class.getDeclaredField("pageReady");
        ready.setAccessible(true);
        long deadline = SystemClock.uptimeMillis() + 30000;
        while (!ready.getBoolean(activity) && SystemClock.uptimeMillis() < deadline) SystemClock.sleep(100);
        assertTrue("Native page-finished gate", ready.getBoolean(activity));
        try {
            await("initial stats and session rendered", "/^[0-9]+$/.test(document.getElementById('st-total').textContent)", 30000);
        } catch (AssertionError failure) {
            android.os.Bundle diagnostic = new android.os.Bundle();
            diagnostic.putString("bootstrap", js("JSON.stringify({error:document.getElementById('q-err').textContent.replace(/https?:[^ ]+/g,'[URL]').replace(/[A-Za-z0-9_-]{24,}/g,'[redacted]'),tokenPresent:Boolean(accessToken),stats:document.getElementById('st-total').textContent,sessionPresent:Boolean(session.session_id)})"));
            scenario.onActivity(current -> diagnostic.putBoolean("sameActivityAndWebView", current == activity && findWebView(current.getWindow().getDecorView()) == web));
            diagnostic.putString("localApiProbe", qa("probe_bootstrap"));
            instrumentation.sendStatus(0, diagnostic);
            throw failure;
        }
    }

    @After public void close() {
        UiDevice device = UiDevice.getInstance(instrumentation);
        for (int i = 0; i < 3; i++) {
            String current = device.getCurrentPackageName();
            if (current == null || !current.contains("documentsui")) break;
            device.pressBack();
        }
        if (scenario != null) scenario.close();
    }

    private WebView findWebView(View view) {
        if (view instanceof WebView) return (WebView) view;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                WebView found = findWebView(group.getChildAt(i));
                if (found != null) return found;
            }
        }
        return null;
    }

    private String js(String expression) throws Exception {
        AtomicReference<String> result = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        instrumentation.runOnMainSync(() -> web.evaluateJavascript(expression, value -> { result.set(value); done.countDown(); }));
        assertTrue("WebView callback timed out (expression withheld)", done.await(8, TimeUnit.SECONDS));
        return result.get();
    }

    private void await(String label, String expression, long timeout) throws Exception {
        long end = SystemClock.uptimeMillis() + timeout;
        while (SystemClock.uptimeMillis() < end) {
            if ("true".equals(js("Boolean(" + expression + ")"))) return;
            SystemClock.sleep(150);
        }
        fail("Timed out: " + label + " (runtime details withheld)");
    }

    private String qa(String function) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try (InputStream input = instrumentation.getContext().getAssets().open("acceptance_fixture.py")) {
            byte[] bytes = new byte[4096]; int count;
            while ((count = input.read(bytes)) != -1) out.write(bytes, 0, count);
        }
        PyObject builtins = Python.getInstance().getModule("builtins");
        PyObject globals = builtins.callAttr("dict");
        builtins.callAttr("exec", new String(out.toByteArray(), StandardCharsets.UTF_8), globals);
        return globals.callAttr("__getitem__", function).call().toString();
    }

    private void clickDom(String id) throws Exception {
        js("window.__qaLayoutReady=false;requestAnimationFrame(function(){requestAnimationFrame(function(){window.__qaLayoutReady=true;});});");
        await("stable layout before touch", "window.__qaLayoutReady", 3000);
        JSONArray rect = new JSONArray(js("(function(){var r=document.getElementById('" + id + "').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2,window.innerWidth];})()"));
        int[] location = new int[2];
        instrumentation.runOnMainSync(() -> web.getLocationOnScreen(location));
        double scale = web.getWidth() / rect.getDouble(2);
        UiDevice.getInstance(instrumentation).click(location[0] + (int) (rect.getDouble(0) * scale), location[1] + (int) (rect.getDouble(1) * scale));
    }

    private void paintedView(String view) throws Exception {
        js("showView('" + view + "');window.__qaPainted=false;requestAnimationFrame(function(){requestAnimationFrame(function(){window.__qaPainted=true;});});");
        await("painted " + view, "window.__qaPainted && currentView==='" + view + "'", 5000);
        CountDownLatch visual = new CountDownLatch(1);
        instrumentation.runOnMainSync(() -> web.postVisualStateCallback(1L, new WebView.VisualStateCallback() {
            @Override public void onComplete(long requestId) { visual.countDown(); }
        }));
        assertTrue("WebView visual commit", visual.await(8, TimeUnit.SECONDS));
        UiDevice.getInstance(instrumentation).waitForIdle();
        // Screenshot compositor trails the JS animation-frame callback on WebView74.
        SystemClock.sleep(400);
    }

    private boolean imeShown() throws Exception {
        String state = UiDevice.getInstance(instrumentation).executeShellCommand("dumpsys input_method");
        return state.contains("mInputShown=true") || state.contains("mImeWindowVis=0x3") || state.contains("mImeWindowVis=3");
    }

    private void waitIme(boolean visible) throws Exception {
        long end = SystemClock.uptimeMillis() + 8000;
        while (SystemClock.uptimeMillis() < end) {
            if (imeShown() == visible) return;
            SystemClock.sleep(150);
        }
        fail("Actual IME did not reach expected visibility " + visible);
    }

    @Test public void paleSystemBarsUseDarkIcons() {
        if (Build.VERSION.SDK_INT > 29) return;
        instrumentation.runOnMainSync(() -> {
            int flags = activity.getWindow().getDecorView().getSystemUiVisibility();
            assertTrue("API29 status icons need light-background mode", (flags & View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR) != 0);
            assertTrue("API29 navigation icons need light-background mode", (flags & View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR) != 0);
        });
    }

    @Test public void standaloneAndroidDoesNotAdvertiseDesktopSharedSession() throws Exception {
        assertEquals("false", js("document.getElementById('idle').textContent.includes('Hermes')"));
        assertEquals("true", js("document.getElementById('idle').textContent.includes('本机')"));
    }

    @Test public void dismissingRealImeRestoresNavigationWhileSearchKeepsFocus() throws Exception {
        js("showView('questions'); window.__qaLoaded=false;loadQuestions(1).then(function(){window.__qaLoaded=true;});");
        await("question bank rendered", "window.__qaLoaded", 10000);
        js("document.getElementById('q-search').scrollIntoView();");
        clickDom("q-search");
        waitIme(true);
        UiDevice.getInstance(instrumentation).pressBack();
        waitIme(false);
        android.os.Bundle evidence = new android.os.Bundle();
        instrumentation.runOnMainSync(() -> {
            android.view.WindowInsets insets = activity.getWindow().getDecorView().getRootWindowInsets();
            evidence.putInt("systemBottom", insets.getSystemWindowInsetBottom());
            evidence.putInt("stableBottom", insets.getStableInsetBottom());
        });
        evidence.putString("keyboardClass", js("document.body.classList.contains('keyboard-open')"));
        java.lang.reflect.Field visibleField = MainActivity.class.getDeclaredField("imeVisible");
        visibleField.setAccessible(true);
        evidence.putBoolean("nativeImeVisible", visibleField.getBoolean(activity));
        java.lang.reflect.Field readyField = MainActivity.class.getDeclaredField("pageReady");
        readyField.setAccessible(true);
        evidence.putBoolean("nativePageReady", readyField.getBoolean(activity));
        instrumentation.sendStatus(0, evidence);
        assertEquals("true", js("document.activeElement.id==='q-search'"));
        await("bottom navigation after single system Back", "getComputedStyle(document.querySelector('.mobile-nav')).display!=='none'", 2500);
        clickDom("q-search");
        waitIme(true);
        // A hardware Enter key is not an IME Done action. Operate the actual
        // keyboard action if exposed; Search may intentionally keep IME open.
        UiDevice device = UiDevice.getInstance(instrumentation);
        UiObject2 action = device.findObject(By.desc(java.util.regex.Pattern.compile("(?i)(search|done|go|enter|搜索|完成)")));
        if (action != null) action.click();
        SystemClock.sleep(400);
        android.os.Bundle actionEvidence = new android.os.Bundle();
        actionEvidence.putBoolean("actualImeActionAvailable", action != null);
        actionEvidence.putBoolean("actualImeActionHidKeyboard", !imeShown());
        instrumentation.sendStatus(0, actionEvidence);
        if (imeShown()) device.pressBack();
        waitIme(false);
        await("navigation after final IME dismissal", "getComputedStyle(document.querySelector('.mobile-nav')).display!=='none'", 2500);
    }

    @Test public void offlineReviewAndMockModelFailuresPreserveUngradedState() throws Exception {
        JSONObject result = new JSONObject(qa("offline_and_model_errors"));
        assertEquals(1, result.getInt("seen"));
        assertTrue(result.getBoolean("mock_stream_error"));
    }

    @Test public void preparePrivatePersistenceFixture() throws Exception {
        JSONObject active = new JSONObject(qa("prepare_persistence"));
        String sid = active.getString("session_id");
        int qid = active.getInt("question_id");
        SharedPreferences prefs = activity.getSharedPreferences("bagu-ui-state", Context.MODE_PRIVATE);
        assertTrue(prefs.edit().putString("bagu-study-mode", "answer")
            .putString("bagu-cats-collapsed", "true").putString("bagu-session-mode:" + sid, "answer")
            .putString("bagu-draft:" + sid + ":" + qid, "Task6 保留中文草稿")
            .putString("bagu-active-submission", active.toString()).commit());
        js("window.location.reload()");
        await("pending draft rendered", "document.getElementById('ans').value==='Task6 保留中文草稿'", 20000);
        android.os.Bundle snapshotEvidence = new android.os.Bundle();
        snapshotEvidence.putString("privateSnapshot", qa("save_snapshot"));
        snapshotEvidence.putInt("nativePreferenceCount", prefs.getAll().size());
        instrumentation.sendStatus(0, snapshotEvidence);
        TreeMap<String, Object> state = new TreeMap<>(prefs.getAll());
        Files.write(new File(activity.getFilesDir(), "task6-prefs-baseline.json").toPath(), new JSONObject(state).toString().getBytes(StandardCharsets.UTF_8));
    }

    @Test public void privateStateSurvivesProcessDeathOrUpgrade() throws Exception {
        android.os.Bundle snapshotEvidence = new android.os.Bundle();
        snapshotEvidence.putString("privateSnapshot", qa("assert_snapshot"));
        SharedPreferences prefs = activity.getSharedPreferences("bagu-ui-state", Context.MODE_PRIVATE);
        JSONObject expected = new JSONObject(new String(Files.readAllBytes(new File(activity.getFilesDir(), "task6-prefs-baseline.json").toPath()), StandardCharsets.UTF_8));
        JSONObject actual = new JSONObject(new TreeMap<>(prefs.getAll()));
        assertEquals("Native preference key count", expected.length(), actual.length());
        snapshotEvidence.putInt("nativePreferenceCount", actual.length());
        instrumentation.sendStatus(0, snapshotEvidence);
        java.util.Iterator<String> keys = expected.keys();
        while (keys.hasNext()) { String key = keys.next(); assertEquals("Native state mismatch", expected.getString(key), actual.getString(key)); }
        await("draft after restart", "document.getElementById('ans').value==='Task6 保留中文草稿'", 10000);
        assertEquals("true", js("JSON.parse(BaguNative.getItem('bagu-active-submission')).submission_id===readActiveSubmission().submission_id"));
        String expectedFlavor = InstrumentationRegistry.getArguments().getString("expectedFlavor");
        if (expectedFlavor != null) assertEquals(JSONObject.quote(expectedFlavor), js("JSON.parse(BaguNative.getAppInfo()).flavor"));
    }

    @Test public void realWebViewCspBlocksRemoteFramesObjectsAndScripts() throws Exception {
        js("window.__qaViolations=[];window.__qaRemoteExecuted=false;window.addEventListener('securitypolicyviolation',function(e){window.__qaViolations.push(e.effectiveDirective);});"
            + "['iframe','object','script'].forEach(function(tag){var n=document.createElement(tag);if(tag==='object')n.data='https://example.invalid/probe';else n.src='https://example.invalid/probe';document.body.appendChild(n);});");
        await("CSP frame/object/script rejection", "['frame-src','object-src'].every(function(d){return window.__qaViolations.indexOf(d)>=0;}) && window.__qaViolations.some(function(d){return d.indexOf('script-src')===0;})", 5000);
        // srcdoc written by trusted same-origin script is not remote HTML.
        // The app never constructs it from question content (which is escaped).
        js("BaguNative.removeItem('bagu-qa-remote');");
        js("window.__qaOriginalOrigin=location.origin;location.href='https://example.invalid/untrusted';");
        SystemClock.sleep(300);
        assertEquals("true", js("location.origin===window.__qaOriginalOrigin && typeof BaguNative==='object'"));
    }

    @Test public void nativeWindowLayoutAndMarkdownLinkTargets() throws Exception {
        String size = js("[innerWidth,innerHeight]").replaceAll("[^0-9,]", "").replace(',', 'x');
        File shots = new File(activity.getExternalFilesDir(null), "task6-qa");
        assertTrue(shots.isDirectory() || shots.mkdirs());
        String captureViews = InstrumentationRegistry.getArguments().getString("captureViews", "quiz,questions,overview,settings,lib,edit,question-edit");
        for (String view : captureViews.split(",")) {
            paintedView(view);
            assertEquals("Horizontal overflow in " + view, "true", js("document.documentElement.scrollWidth<=innerWidth+1"));
            assertTrue("Screenshot " + view, UiDevice.getInstance(instrumentation).takeScreenshot(new File(shots, "api" + Build.VERSION.SDK_INT + "-" + size + "-" + view + ".png")));
        }
        String html = Python.getInstance().getModule("bagu").callAttr("render_answer_html", "[实际文本链接](https://example.com/reference)").toString();
        js("showView('quiz');var fixture=document.createElement('div');fixture.className='markdown-body';fixture.id='qa-answer';fixture.innerHTML=" + JSONObject.quote(html) + ";document.body.appendChild(fixture);");
        assertEquals("true", js("(function(){var r=document.querySelector('#qa-answer a').getBoundingClientRect();return r.height>=44 && r.width>=44;})()"));
        js("document.getElementById('qa-answer').remove();");
        UiDevice device = UiDevice.getInstance(instrumentation);
        device.setOrientationLeft();
        try {
            await("rotated layout", "innerWidth>innerHeight", 5000);
            assertEquals("true", js("document.documentElement.scrollWidth<=innerWidth+1"));
        } finally { device.setOrientationNatural(); device.unfreezeRotation(); }
        android.os.Bundle result = new android.os.Bundle(); result.putString("verifiedCssSize", size); instrumentation.sendStatus(0, result);
    }

    private void waitPicker() throws Exception {
        long until = SystemClock.uptimeMillis() + 10000;
        while (SystemClock.uptimeMillis() < until) {
            String name = UiDevice.getInstance(instrumentation).getCurrentPackageName();
            if (name != null && name.contains("documentsui")) return;
            SystemClock.sleep(150);
        }
        fail("Real DocumentsUI picker did not open");
    }

    private void pickQaDocument(String filename) throws Exception {
        UiDevice device = UiDevice.getInstance(instrumentation);
        waitPicker();
        device.waitForIdle();
        if (!device.hasObject(By.text(filename))) {
            if (device.hasObject(By.desc("Show roots"))) clickPickerNode(By.desc("Show roots"));
            clickPickerNode(By.text("Downloads"));
        }
        clickPickerNode(By.text(filename));
        assertTrue("Document selection returns to target Activity", device.wait(androidx.test.uiautomator.Until.hasObject(By.pkg(activity.getPackageName())), 10000));
    }

    private void saveQaDocument(String filename) throws Exception {
        UiDevice device = UiDevice.getInstance(instrumentation);
        waitPicker();
        device.waitForIdle();
        if (device.hasObject(By.desc("Show roots"))) {
            clickPickerNode(By.desc("Show roots"));
            clickPickerNode(By.text("Downloads"));
        }
        UiObject2 name = device.wait(androidx.test.uiautomator.Until.findObject(By.clazz("android.widget.EditText")), 10000);
        assertNotNull("SAF destination filename", name);
        name.setText(filename);
        clickPickerNode(By.text(java.util.regex.Pattern.compile("(?i)save")));
        assertTrue("Save returns to target", device.wait(androidx.test.uiautomator.Until.hasObject(By.pkg(activity.getPackageName())), 10000));
    }

    @Test public void rejectedExportDoesNotWriteAndSafRetryIsParseable() throws Exception {
        qa("assert_snapshot");
        UiDevice device = UiDevice.getInstance(instrumentation);
        String prefix = "final-fix1-" + System.currentTimeMillis();
        String rejected = prefix + "-rejected.bagu-backup";
        String accepted = prefix + "-accepted.bagu-backup";
        paintedView("settings");
        js("window.__qaFileResult=null;window.addEventListener('bagu-native-result',function(e){window.__qaFileResult=e.detail;});");
        qa("reject_export_temporarily");
        try {
            js("document.getElementById('btn-backup-export').click();");
            saveQaDocument(rejected);
            await("rejected native export", "window.__qaFileResult && window.__qaFileResult.status==='error'", 15000);
            assertEquals("true", js("['10000','50 MiB','20 MiB','检查'].every(function(s){return window.__qaFileResult.message.includes(s);})"));
            assertEquals("0", device.executeShellCommand("stat -c %s /sdcard/Download/" + rejected).trim());
            qa("assert_snapshot");
        } finally { qa("restore_export_limit"); }
        js("window.__qaFileResult=null;document.getElementById('btn-backup-export').click();");
        saveQaDocument(accepted);
        await("successful native export retry", "window.__qaFileResult && window.__qaFileResult.status==='ok'", 15000);
        File exported = new File(activity.getExternalFilesDir(null), accepted);
        device.executeShellCommand("cp /sdcard/Download/" + accepted + " " + exported.getAbsolutePath());
        byte[] archive = Files.readAllBytes(exported.toPath());
        PyObject raw = Python.getInstance().getModule("builtins").callAttr("bytes", archive);
        PyObject parsed = Python.getInstance().getModule("bagu").callAttr("parse_backup", raw);
        int count = Python.getInstance().getModule("builtins").callAttr("len", parsed).toInt();
        assertEquals(Integer.parseInt(js("Number(document.getElementById('st-total').textContent)")), count);
        qa("assert_snapshot");
        android.os.Bundle evidence = new android.os.Bundle();
        evidence.putString("rejectedFile", rejected); evidence.putInt("rejectedBytes", 0);
        evidence.putString("acceptedFile", accepted); evidence.putInt("acceptedBytes", archive.length);
        evidence.putInt("parseableQuestions", count); instrumentation.sendStatus(0, evidence);
        privateStateSurvivesProcessDeathOrUpgrade();
    }

    @Test public void coverViewportConsumesInsetsOnce() throws Exception {
        assertEquals("true", js("document.querySelector('meta[name=viewport]').content.includes('viewport-fit=cover')"));
        paintedView("settings");
        assertFalse("No IME in inset proof", imeShown());
        android.os.Bundle evidence = new android.os.Bundle();
        instrumentation.runOnMainSync(() -> {
            View parent = (View) web.getParent();
            android.view.WindowInsets insets = activity.getWindow().getDecorView().getRootWindowInsets();
            int top, bottom;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets safe = insets.getInsets(android.view.WindowInsets.Type.systemBars() | android.view.WindowInsets.Type.displayCutout());
                top = safe.top; bottom = safe.bottom;
            } else { top = insets.getSystemWindowInsetTop(); bottom = insets.getSystemWindowInsetBottom(); }
            assertEquals(top, parent.getPaddingTop()); assertEquals(bottom, parent.getPaddingBottom());
            assertEquals(parent.getHeight() - top - bottom, web.getHeight());
            evidence.putInt("nativeTopPixels", top); evidence.putInt("nativeBottomPixels", bottom);
            evidence.putInt("rootHeightPixels", parent.getHeight()); evidence.putInt("webHeightPixels", web.getHeight());
        });
        assertEquals("94px", new JSONArray("[" + js("getComputedStyle(document.querySelector('.app')).paddingBottom") + "]").getString(0));
        assertEquals("\"8px\"", js("getComputedStyle(document.querySelector('.mobile-nav')).paddingBottom"));
        assertEquals("true", js("Math.abs(document.querySelector('.mobile-nav').getBoundingClientRect().bottom-innerHeight)<=1"));
        evidence.putString("cssBottomPadding", "app94px/nav8px; no duplicate safe-area padding");
        File shot = new File(activity.getExternalFilesDir(null), "final-fix1-insets-api" + Build.VERSION.SDK_INT + ".png");
        assertTrue(deviceScreenshot(shot));
        instrumentation.sendStatus(0, evidence);
    }

    private boolean deviceScreenshot(File destination) {
        return UiDevice.getInstance(instrumentation).takeScreenshot(destination);
    }

    private void clickPickerNode(androidx.test.uiautomator.BySelector selector) {
        UiDevice device = UiDevice.getInstance(instrumentation);
        boolean clicked = false;
        for (int attempt = 0; attempt < 3 && !clicked; attempt++) {
            device.waitForIdle();
            UiObject2 doc = device.wait(androidx.test.uiautomator.Until.findObject(selector), 10000);
            assertNotNull("Synthetic QA document listed in Downloads", doc);
            try { doc.click(); clicked = true; }
            catch (androidx.test.uiautomator.StaleObjectException refreshedList) {
                // Downloads refreshes rows asynchronously; never reuse a stale node.
            }
        }
        assertTrue("Stable document row selected", clicked);
    }

    @Test public void actualHttpsAnswerImageLoads() throws Exception {
        AtomicReference<android.webkit.WebViewClient> original = new AtomicReference<>();
        java.util.concurrent.atomic.AtomicInteger networkError = new java.util.concurrent.atomic.AtomicInteger(0);
        instrumentation.runOnMainSync(() -> {
            original.set(web.getWebViewClient());
            web.setWebViewClient(new android.webkit.WebViewClient() {
                @Override public boolean shouldOverrideUrlLoading(WebView view, android.webkit.WebResourceRequest request) {
                    return original.get().shouldOverrideUrlLoading(view, request);
                }
                @Override public void onPageFinished(WebView view, String url) { original.get().onPageFinished(view, url); }
                @Override public void onReceivedError(WebView view, android.webkit.WebResourceRequest request, android.webkit.WebResourceError error) {
                    if (!request.isForMainFrame()) networkError.set(error.getErrorCode());
                    original.get().onReceivedError(view, request, error);
                }
                @Override public void onReceivedSslError(WebView view, android.webkit.SslErrorHandler handler, android.net.http.SslError error) {
                    networkError.set(ERROR_FAILED_SSL_HANDSHAKE);
                    original.get().onReceivedSslError(view, handler, error);
                }
            });
        });
        String html = Python.getInstance().getModule("bagu").callAttr("render_answer_html", "![QA HTTPS image](https://www.python.org/static/img/python-logo.png)").toString();
        js("window.__qaImageStatus='pending';window.__qaImageCsp=false;window.addEventListener('securitypolicyviolation',function(e){if(e.effectiveDirective==='img-src')window.__qaImageCsp=true;});var fixture=document.createElement('div');fixture.id='qa-network-image';fixture.innerHTML=" + JSONObject.quote(html) + ";document.body.prepend(fixture);var image=fixture.querySelector('img');image.onload=function(){window.__qaImageStatus='loaded';};image.onerror=function(){window.__qaImageStatus='error';};image.loading='eager';image.scrollIntoView();if(image.complete && image.naturalWidth>0)window.__qaImageStatus='loaded';");
        try {
            long deadline = SystemClock.uptimeMillis() + 20000;
            while ("\"pending\"".equals(js("window.__qaImageStatus")) && SystemClock.uptimeMillis() < deadline) SystemClock.sleep(200);
            android.os.Bundle evidence = new android.os.Bundle();
            String imageStatus = js("window.__qaImageStatus");
            evidence.putString("httpsImageStatus", imageStatus);
            evidence.putInt("webViewNetworkErrorCode", networkError.get());
            evidence.putString("imageCspViolation", js("window.__qaImageCsp"));
            instrumentation.sendStatus(0, evidence);
            assertEquals("HTTPS image must not be rejected by CSP", "false", js("window.__qaImageCsp"));
            org.junit.Assume.assumeTrue("External HTTPS unavailable; actual image load remains unverified", "\"loaded\"".equals(imageStatus));
            assertEquals("true", js("document.querySelector('#qa-network-image img').naturalWidth>0"));
        } finally {
            js("document.getElementById('qa-network-image').remove();");
            instrumentation.runOnMainSync(() -> web.setWebViewClient(original.get()));
        }
    }

    @Test public void openSessionRejectsActualSafRestore() throws Exception {
        qa("assert_snapshot");
        File valid = new File(activity.getExternalFilesDir(null), "task6-open-session.bagu-backup");
        Files.write(valid.toPath(), RuntimeHost.exportArchive());
        UiDevice.getInstance(instrumentation).executeShellCommand("cp " + valid.getAbsolutePath() + " /sdcard/Download/task6-open-session.bagu-backup");
        js("showView('settings');window.__qaFileResult=null;window.addEventListener('bagu-native-result',function(e){window.__qaFileResult=e.detail;});document.getElementById('btn-backup-import').click();");
        assertEquals("true", js("document.getElementById('native-message').textContent.includes('结束本轮')"));
        // Exercise defense in depth through the existing native method; no release hook.
        js("BaguNative.importBackup();");
        pickQaDocument("task6-open-session.bagu-backup");
        await("open-session import rejected", "window.__qaFileResult && window.__qaFileResult.status==='error'", 10000);
        qa("assert_snapshot");
    }

    @Test public void actualSafExportImportCancellationAndCorruption() throws Exception {
        UiDevice device = UiDevice.getInstance(instrumentation);
        qa("save_snapshot");
        js("showView('settings');window.__qaFileResult=null;window.addEventListener('bagu-native-result',function(e){window.__qaFileResult=e.detail;});document.getElementById('btn-backup-export').click();");
        waitPicker(); device.pressBack();
        await("SAF export cancelled", "window.__qaFileResult && window.__qaFileResult.status==='cancelled'", 10000);
        qa("assert_snapshot");
        js("window.__qaFileResult=null;document.getElementById('btn-backup-import').click();");
        waitPicker(); device.pressBack();
        await("SAF import cancelled", "window.__qaFileResult && window.__qaFileResult.status==='cancelled'", 10000);
        qa("assert_snapshot");
        File corrupt = new File(activity.getExternalFilesDir(null), "task6-invalid.bagu-backup");
        Files.write(corrupt.toPath(), "not a zip".getBytes(StandardCharsets.UTF_8));
        device.executeShellCommand("cp " + corrupt.getAbsolutePath() + " /sdcard/Download/task6-invalid.bagu-backup");
        js("window.__qaFileResult=null;document.getElementById('btn-backup-import').click();");
        pickQaDocument("task6-invalid.bagu-backup");
        await("SAF corruption rejection", "window.__qaFileResult && window.__qaFileResult.status==='error'", 10000);
        qa("assert_snapshot");
        assertEquals("true", js("!document.getElementById('btn-backup-import').disabled"));
        File valid = new File(activity.getExternalFilesDir(null), "task6-valid.bagu-backup");
        Files.write(valid.toPath(), RuntimeHost.exportArchive());
        device.executeShellCommand("cp " + valid.getAbsolutePath() + " /sdcard/Download/task6-valid.bagu-backup");
        js("window.__qaFileResult=null;document.getElementById('btn-backup-import').click();");
        pickQaDocument("task6-valid.bagu-backup");
        await("valid SAF import after failures", "window.__qaFileResult && window.__qaFileResult.status==='ok'", 10000);
        qa("assert_snapshot");
        assertEquals("0", js("window.__qaFileResult.added"));
    }
}
