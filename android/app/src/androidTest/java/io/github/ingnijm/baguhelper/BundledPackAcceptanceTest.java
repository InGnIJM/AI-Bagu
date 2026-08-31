package io.github.ingnijm.baguhelper;

import android.app.Instrumentation;
import android.content.Context;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.uiautomator.By;
import androidx.test.uiautomator.UiDevice;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;

import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.Assert.*;

/**
 * Device-only checks for the descriptor-bound pack. Assertions expose identity,
 * counts and scheduling aggregates only; question/answer content never leaves
 * the target process. The PowerShell gate runs each method in a disposable AVD.
 */
@RunWith(AndroidJUnit4.class)
public final class BundledPackAcceptanceTest {
    private static final String PACK_ID = "autumn-recruit-interviews-2026";
    private static final int REVISION = 1;
    private static final int QUESTION_COUNT = 748;
    private static final int EXPERIENCE_COUNT = 27;

    private final Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
    private final UiDevice device = UiDevice.getInstance(instrumentation);
    private ActivityScenario<MainActivity> scenario;
    private MainActivity activity;
    private WebView web;

    @Before public void launch() throws Exception {
        assertTrue(instrumentation.getTargetContext()
            .getSharedPreferences("bagu-native-updates", Context.MODE_PRIVATE)
            .edit().putBoolean("automatic", false).commit());
        launchActivity();
    }

    @After public void close() {
        if (scenario != null) scenario.close();
    }

    private void launchActivity() throws Exception {
        scenario = ActivityScenario.launch(MainActivity.class);
        bindActivity();
        await("trusted local page", "document.body.classList.contains('android-app')", 45000);
        await("runtime stats", "/^[0-9]+$/.test(document.getElementById('st-total').textContent)", 30000);
    }

    private void bindActivity() {
        scenario.onActivity(current -> {
            activity = current;
            web = findWebView(current.getWindow().getDecorView());
        });
        assertNotNull("Target Activity must own a WebView", web);
    }

    private static WebView findWebView(View value) {
        if (value instanceof WebView) return (WebView)value;
        if (value instanceof ViewGroup) {
            ViewGroup group = (ViewGroup)value;
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
        instrumentation.runOnMainSync(() -> web.evaluateJavascript(expression, value -> {
            result.set(value);
            done.countDown();
        }));
        assertTrue("WebView result timeout", done.await(8, TimeUnit.SECONDS));
        return result.get();
    }

    private void await(String label, String expression, long timeoutMillis) throws Exception {
        long deadline = SystemClock.uptimeMillis() + timeoutMillis;
        while (SystemClock.uptimeMillis() < deadline) {
            if ("true".equals(js("Boolean(" + expression + ")"))) return;
            SystemClock.sleep(150);
        }
        fail("Timed out: " + label + " (details withheld)");
    }

    private void settleNativeWorker() throws Exception {
        instrumentation.runOnMainSync(() -> {});
        CountDownLatch worker = new CountDownLatch(1);
        RuntimeHost.WORKER.execute(worker::countDown);
        assertTrue("Native worker timeout", worker.await(60, TimeUnit.SECONDS));
        instrumentation.runOnMainSync(() -> {});
    }

    private static Object field(Object owner, String name) {
        try {
            java.lang.reflect.Field result = owner.getClass().getDeclaredField(name);
            result.setAccessible(true);
            return result.get(owner);
        } catch (ReflectiveOperationException error) {
            throw new AssertionError("Acceptance field unavailable: " + name, error);
        }
    }

    private boolean bundledOperationIdle() {
        try {
            java.lang.reflect.Method result = MainActivity.class.getDeclaredMethod("bundledOperationIdle");
            result.setAccessible(true);
            return (Boolean)result.invoke(activity);
        } catch (ReflectiveOperationException error) {
            throw new AssertionError("Acceptance bundled idle state unavailable", error);
        }
    }

    private PendingImport pending() {
        return (PendingImport)field(field(activity, "state"), "pendingImport");
    }

    private void waitForPreview() throws Exception {
        long deadline = SystemClock.uptimeMillis() + 60000;
        while (pending() == null && SystemClock.uptimeMillis() < deadline) {
            settleNativeWorker();
            SystemClock.sleep(100);
        }
        assertNotNull("Expected native bundled preview", pending());
        assertTrue("Expected native confirmation dialog",
            device.wait(androidx.test.uiautomator.Until.hasObject(By.text("确认安装")), 10000));
        assertPreviewIdentity(pending());
    }

    private static void assertPreviewIdentity(PendingImport value) {
        Map<String,Object> preview = value.preview();
        assertEquals("pack-import", value.operation());
        assertEquals(PACK_ID, preview.get("pack_id"));
        assertEquals(REVISION, ((Number)preview.get("revision")).intValue());
        assertEquals(QUESTION_COUNT, ((Number)preview.get("question_count")).intValue());
        assertEquals(EXPERIENCE_COUNT, ((Number)preview.get("experience_count")).intValue());
    }

    private void cancelPreview() throws Exception {
        assertTrue(device.findObject(By.text("取消")) != null);
        device.findObject(By.text("取消")).click();
        long deadline = SystemClock.uptimeMillis() + 10000;
        while (pending() != null && SystemClock.uptimeMillis() < deadline) SystemClock.sleep(100);
        assertNull(pending());
    }

    private void confirmPreview() throws Exception {
        assertTrue(device.findObject(By.text("确认安装")) != null);
        device.findObject(By.text("确认安装")).click();
        long deadline = SystemClock.uptimeMillis() + 10000;
        while (pending() != null && SystemClock.uptimeMillis() < deadline) SystemClock.sleep(100);
        assertNull("Confirm click was not processed", pending());
        // The click has enqueued the install. Wait behind that exact worker task,
        // then inspect SQLite only after its transaction and main-thread result.
        settleNativeWorker();
        JSONObject current = state();
        assertEquals(1, current.getInt("packs"));
        assertEquals(QUESTION_COUNT, current.getInt("pack_questions"));
        assertEquals(EXPERIENCE_COUNT, current.getInt("experiences"));
    }

    private void requestBundledFromRealBridge() throws Exception {
        assertEquals("true", js("BaguNative.hasBundledInterviewPack()"));
        assertEquals("true", js("(function(){BaguNative.importBundledInterviewPack();return true;})()"));
        waitForPreview();
        assertEquals(PendingImport.Source.BUNDLED_SETTINGS, pending().source());
    }

    private void requestBundledAfterSettlingAutomaticPreview() throws Exception {
        long deadline = SystemClock.uptimeMillis() + 60000;
        while (SystemClock.uptimeMillis() < deadline) {
            settleNativeWorker();
            PendingImport automatic = pending();
            if (automatic != null) {
                assertEquals(PendingImport.Source.BUNDLED_AUTO_PROMPT, automatic.source());
                cancelPreview();
                continue;
            }
            if (bundledOperationIdle()) {
                requestBundledFromRealBridge();
                return;
            }
            SystemClock.sleep(100);
        }
        fail("Bundled host did not become idle (details withheld)");
    }

    private JSONObject state() throws Exception {
        String code = "import json, android_runtime\n"
            + "def bundled_safe_state():\n"
            + " c=android_runtime._connection()\n"
            + " try:\n"
            + "  one=lambda sql:c.execute(sql).fetchone()[0]\n"
            + "  return json.dumps({'questions':one('SELECT COUNT(*) FROM questions'),'pack_questions':one('SELECT COUNT(*) FROM questions WHERE pack_id IS NOT NULL'),'packs':one('SELECT COUNT(*) FROM question_packs'),'experiences':one('SELECT COUNT(*) FROM experiences'),'experience_items':one('SELECT COUNT(*) FROM experience_items'),'sessions_open':one(\"SELECT COUNT(*) FROM sessions WHERE status='open'\"),'session_items':one('SELECT COUNT(*) FROM session_items'),'include_in_review':one('SELECT COALESCE(MIN(include_in_review),-1) FROM question_packs'),'pack_seen':one('SELECT COALESCE(SUM(times_seen),0) FROM questions WHERE pack_id IS NOT NULL'),'local_seen':one('SELECT COALESCE(SUM(times_seen),0) FROM questions WHERE pack_id IS NULL'),'next_due':one('SELECT COUNT(*) FROM questions WHERE next_due IS NOT NULL')},sort_keys=True,separators=(',',':'))\n"
            + " finally:c.close()\n";
        PyObject builtins = Python.getInstance().getModule("builtins");
        PyObject globals = builtins.callAttr("dict");
        builtins.callAttr("exec", code, globals);
        return new JSONObject(globals.callAttr("__getitem__", "bundled_safe_state").call().toString());
    }

    private void python(String body) {
        PyObject builtins = Python.getInstance().getModule("builtins");
        PyObject globals = builtins.callAttr("dict");
        builtins.callAttr("exec", body, globals);
    }

    private void createOpenSession() {
        python("import bagu, android_runtime\n"
            + "c=android_runtime._connection()\n"
            + "try:\n"
            + " bagu.create_question(c,{'category':'qa-gate','question':'qa-open','answer':'qa-local','url':''})\n"
            + " bagu.draw(c,1,'qa-gate')\n"
            + "finally:c.close()\n");
        assertTrue(RuntimeHost.hasOpenSession());
    }

    private void closeOpenSession() {
        python("import bagu, android_runtime\n"
            + "c=android_runtime._connection()\n"
            + "try:\n"
            + " s=bagu.get_open_session(c)\n"
            + " if s:bagu.skip_session(c,s['id'])\n"
            + "finally:c.close()\n");
        assertFalse(RuntimeHost.hasOpenSession());
    }

    private void prepareLocalProgress() {
        python("import bagu, android_runtime\n"
            + "c=android_runtime._connection()\n"
            + "try:\n"
            + " q=bagu.create_question(c,{'category':'qa-upgrade','question':'qa-progress','answer':'qa-local','url':''})['id']\n"
            + " c.execute(\"UPDATE questions SET level=1,times_seen=2,times_right=1,next_due='2030-01-01' WHERE id=?\",(q,))\n"
            + " c.commit()\n"
            + "finally:c.close()\n");
    }

    @Test public void cleanCancelLeavesEmptyAndSuppressesSameHashRestart() throws Exception {
        assertEquals("true", js("BaguNative.hasBundledInterviewPack()"));
        waitForPreview();
        assertEquals(PendingImport.Source.BUNDLED_AUTO_PROMPT, pending().source());
        assertEquals(0, state().getInt("questions"));
        cancelPreview();
        scenario.close();
        scenario = null;
        launchActivity();
        settleNativeWorker();
        SystemClock.sleep(1200);
        assertNull("Same hash must not auto-prompt again", pending());
        assertFalse(device.hasObject(By.text("确认安装")));
        assertEquals(0, state().getInt("questions"));
    }

    @Test public void settingsInstallDisablesDailyReviewWithoutHidingSimulation() throws Exception {
        js("window.__qaPackResult=null;window.addEventListener('bagu-native-result',function(e){if(e.detail.operation==='pack-import')window.__qaPackResult=e.detail;});");
        requestBundledAfterSettlingAutomaticPreview();
        confirmPreview();
        await("redacted native result", "window.__qaPackResult&&window.__qaPackResult.status==='ok'", 30000);
        assertEquals("true", js("Object.keys(window.__qaPackResult).every(function(k){return ['operation','status','message','operation_id','pack_id','name','revision','display_version','question_count','experience_count','installed_revision'].includes(k);})"));
        js("window.__qaToggleDone=false;api('PUT','/api/packs/'+encodeURIComponent('" + PACK_ID + "'),{include_in_review:false}).then(function(){return api('GET','/api/experiences');}).then(function(v){window.__qaExperienceCount=v.experiences.length;return api('POST','/api/experiences/'+v.experiences[0].id+'/start',{});}).then(function(v){window.__qaSimulationSession=v.session_id;return api('POST','/api/skip',{session_id:v.session_id});}).then(function(){window.__qaToggleDone=true;});");
        await("daily toggle and simulation", "window.__qaToggleDone===true", 30000);
        assertEquals(String.valueOf(EXPERIENCE_COUNT), js("window.__qaExperienceCount"));
        assertEquals(0, state().getInt("include_in_review"));
        assertEquals(0, state().getInt("sessions_open"));
    }

    @Test public void recreationOpenSessionAndOperationsNeverImplicitlyInstall() throws Exception {
        requestBundledAfterSettlingAutomaticPreview();
        PendingImport before = pending();
        scenario.recreate();
        bindActivity();
        await("recreated trusted page", "document.body.classList.contains('android-app')", 30000);
        assertSame("Activity recreation retains only the in-memory preview", before, pending());
        assertEquals(0, state().getInt("packs"));
        assertTrue(device.wait(androidx.test.uiautomator.Until.hasObject(By.text("确认安装")), 10000));
        device.pressBack();
        long deadline = SystemClock.uptimeMillis() + 10000;
        while (pending() != null && SystemClock.uptimeMillis() < deadline) SystemClock.sleep(100);
        assertNull(pending());

        createOpenSession();
        JSONObject beforeSchedule = state();
        js("window.__qaBlocked=null;window.addEventListener('bagu-native-result',function(e){if(e.detail.operation==='pack-import')window.__qaBlocked=e.detail;});BaguNative.importBundledInterviewPack();");
        await("open session block", "window.__qaBlocked&&window.__qaBlocked.status==='error'", 15000);
        JSONObject afterSchedule = state();
        assertEquals(beforeSchedule.getInt("local_seen"), afterSchedule.getInt("local_seen"));
        assertEquals(beforeSchedule.getInt("next_due"), afterSchedule.getInt("next_due"));
        closeOpenSession();

        requestBundledFromRealBridge();
        assertEquals("false", js("BaguNative.checkForUpdate('qa-bundled-busy')"));
        assertEquals("true", js("(function(){BaguNative.importInterviewPack();return true;})()"));
        assertNotNull("File operation cannot replace retained bundled preview", pending());
        device.pressBack();
        deadline = SystemClock.uptimeMillis() + 10000;
        while (pending() != null && SystemClock.uptimeMillis() < deadline) SystemClock.sleep(100);
        assertNull(pending());
        assertEquals(0, state().getInt("packs"));
    }

    @Test public void processDeathStagePersistsOnlyPromptMarker() throws Exception {
        waitForPreview();
        assertEquals(PendingImport.Source.BUNDLED_AUTO_PROMPT, pending().source());
        assertEquals(0, state().getInt("packs"));
    }

    @Test public void processDeathRestartDoesNotRestoreBytesOrReprompt() throws Exception {
        settleNativeWorker();
        SystemClock.sleep(1200);
        assertNull("Process death must not restore PendingImport", pending());
        assertFalse(device.hasObject(By.text("确认安装")));
        assertEquals(0, state().getInt("packs"));
    }

    @Test public void prepareInstalledBeta5UpgradeState() throws Exception {
        requestBundledAfterSettlingAutomaticPreview();
        confirmPreview();
        python("import android_runtime\n"
            + "c=android_runtime._connection()\n"
            + "try:\n"
            + " c.execute(\"UPDATE question_packs SET include_in_review=0 WHERE pack_id='" + PACK_ID + "'\")\n"
            + " c.execute(\"UPDATE questions SET level=1,times_seen=2,times_right=1,next_due='2030-01-01' WHERE id=(SELECT MIN(id) FROM questions WHERE pack_id='" + PACK_ID + "')\")\n"
            + " c.commit()\n"
            + "finally:c.close()\n");
        assertTrue(state().getInt("pack_seen") >= 2);
    }

    @Test public void verifyInstalledBeta5UpgradeDoesNotDuplicateOrPrompt() throws Exception {
        settleNativeWorker();
        SystemClock.sleep(1200);
        JSONObject current = state();
        assertNull(pending());
        assertEquals(1, current.getInt("packs"));
        assertEquals(QUESTION_COUNT, current.getInt("pack_questions"));
        assertEquals(EXPERIENCE_COUNT, current.getInt("experiences"));
        assertEquals(0, current.getInt("include_in_review"));
        assertTrue(current.getInt("pack_seen") >= 2);
    }

    @Test public void prepareUninstalledBeta5UpgradeState() throws Exception {
        requestBundledAfterSettlingAutomaticPreview();
        cancelPreview();
        prepareLocalProgress();
        assertTrue(instrumentation.getTargetContext()
            .getSharedPreferences("bagu-native-bundled-pack", Context.MODE_PRIVATE)
            .edit().remove(BundledPackController.PROMPTED_HASH_KEY).commit());
        assertEquals(0, state().getInt("packs"));
        assertTrue(state().getInt("local_seen") >= 2);
    }

    @Test public void verifyUninstalledBeta5UpgradePromptsOnceAndPreservesLocalProgress() throws Exception {
        waitForPreview();
        assertEquals(PendingImport.Source.BUNDLED_AUTO_PROMPT, pending().source());
        assertEquals(0, state().getInt("packs"));
        assertTrue(state().getInt("local_seen") >= 2);
        cancelPreview();
        scenario.close();
        scenario = null;
        launchActivity();
        settleNativeWorker();
        SystemClock.sleep(1200);
        assertNull(pending());
        assertTrue(state().getInt("local_seen") >= 2);
    }
}
