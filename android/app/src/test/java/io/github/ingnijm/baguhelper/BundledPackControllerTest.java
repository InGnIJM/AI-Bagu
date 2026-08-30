package io.github.ingnijm.baguhelper;

import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import android.webkit.JavascriptInterface;
import org.junit.Test;
import static org.junit.Assert.*;

public final class BundledPackControllerTest {
    @Test public void bridgeExposesOnlyTheExactBundledPackCapabilityAndActionSignatures() throws Exception {
        java.lang.reflect.Method capability = NativeBridge.class.getDeclaredMethod("hasBundledInterviewPack");
        java.lang.reflect.Method action = NativeBridge.class.getDeclaredMethod("importBundledInterviewPack");

        assertEquals(boolean.class, capability.getReturnType());
        assertEquals(void.class, action.getReturnType());
        assertNotNull(capability.getAnnotation(JavascriptInterface.class));
        assertNotNull(action.getAnnotation(JavascriptInterface.class));
    }

    @Test public void automaticPromptIsOncePerExactRetainedHashAndNewHashPromptsAgain() throws Exception {
        Fixture fixture = new Fixture("new", new byte[]{1, 2, 3});

        BundledPackController.Result first = fixture.controller.prepare(
            PendingImport.Source.BUNDLED_AUTO_PROMPT, true);
        assertEquals(BundledPackController.Policy.CONFIRMABLE, first.policy());
        assertNull("hash is not committed before the preview can become visible", fixture.preferences.value);
        assertTrue(first.activate());
        assertEquals(sha256(new byte[]{1, 2, 3}), fixture.preferences.value);
        assertEquals(PendingImport.Source.BUNDLED_AUTO_PROMPT, first.pending().source());
        assertEquals(1, fixture.leases.held);
        first.release();

        BundledPackController.Result repeated = fixture.controller.prepare(
            PendingImport.Source.BUNDLED_AUTO_PROMPT, true);
        assertEquals(BundledPackController.Policy.SUPPRESSED, repeated.policy());
        assertEquals("already_prompted", repeated.code());
        assertEquals(0, fixture.leases.held);

        fixture.source.bytes = new byte[]{1, 2, 4};
        BundledPackController.Result changed = fixture.controller.prepare(
            PendingImport.Source.BUNDLED_AUTO_PROMPT, true);
        assertEquals(BundledPackController.Policy.CONFIRMABLE, changed.policy());
        assertTrue(changed.activate());
        assertEquals(sha256(new byte[]{1, 2, 4}), fixture.preferences.value);
        changed.release();
    }

    @Test public void manualSettingsRequestBypassesAutomaticHashSuppression() throws Exception {
        Fixture fixture = new Fixture("new", new byte[]{4, 5, 6});
        BundledPackController.Result automatic = fixture.controller.prepare(
            PendingImport.Source.BUNDLED_AUTO_PROMPT, true);
        assertTrue(automatic.activate());
        assertEquals(sha256(new byte[]{4, 5, 6}), fixture.preferences.value);
        automatic.release();

        BundledPackController.Result manual = fixture.controller.prepare(
            PendingImport.Source.BUNDLED_SETTINGS, true);

        assertEquals(BundledPackController.Policy.CONFIRMABLE, manual.policy());
        assertEquals(PendingImport.Source.BUNDLED_SETTINGS, manual.pending().source());
        manual.release();
    }

    @Test public void previewPolicyIsStrictForEveryInspectorStatus() {
        assertPolicy("new", PendingImport.Source.BUNDLED_AUTO_PROMPT, BundledPackController.Policy.CONFIRMABLE);
        assertPolicy("upgrade", PendingImport.Source.BUNDLED_AUTO_PROMPT, BundledPackController.Policy.CONFIRMABLE);
        assertPolicy("installed", PendingImport.Source.BUNDLED_AUTO_PROMPT, BundledPackController.Policy.SUPPRESSED);
        assertPolicy("downgrade", PendingImport.Source.BUNDLED_AUTO_PROMPT, BundledPackController.Policy.SUPPRESSED);
        assertPolicy("conflict", PendingImport.Source.BUNDLED_AUTO_PROMPT, BundledPackController.Policy.SUPPRESSED);

        assertPolicy("new", PendingImport.Source.BUNDLED_SETTINGS, BundledPackController.Policy.CONFIRMABLE);
        assertPolicy("upgrade", PendingImport.Source.BUNDLED_SETTINGS, BundledPackController.Policy.CONFIRMABLE);
        assertPolicy("installed", PendingImport.Source.BUNDLED_SETTINGS, BundledPackController.Policy.CONFIRMABLE);
        assertPolicy("downgrade", PendingImport.Source.BUNDLED_SETTINGS, BundledPackController.Policy.READ_ONLY);
        assertPolicy("conflict", PendingImport.Source.BUNDLED_SETTINGS, BundledPackController.Policy.READ_ONLY);
        assertPolicy("unexpected", PendingImport.Source.BUNDLED_SETTINGS, BundledPackController.Policy.ERROR);
    }

    @Test public void fixedAssetLimitRejectsOneByteOverTwentyMiBAndAcceptsBoundary() {
        Fixture over = new Fixture("new", new byte[BundledPackController.MAX_BYTES + 1]);
        BundledPackController.Result rejected = over.controller.prepare(PendingImport.Source.BUNDLED_SETTINGS, true);
        assertEquals(BundledPackController.Policy.ERROR, rejected.policy());
        assertEquals("too_large", rejected.code());
        assertEquals(0, over.inspector.inspections);
        assertEquals(0, over.leases.held);

        Fixture exact = new Fixture("new", new byte[BundledPackController.MAX_BYTES]);
        BundledPackController.Result accepted = exact.controller.prepare(PendingImport.Source.BUNDLED_SETTINGS, true);
        assertEquals(BundledPackController.Policy.CONFIRMABLE, accepted.policy());
        accepted.release();
    }

    @Test public void hashAndPendingSnapshotUseTheSameBytesEvenIfSourceMutatesLater() throws Exception {
        byte[] sourceBytes = new byte[]{9, 8, 7, 6};
        Fixture fixture = new Fixture("new", sourceBytes);

        BundledPackController.Result result = fixture.controller.prepare(
            PendingImport.Source.BUNDLED_AUTO_PROMPT, true);
        assertTrue(result.activate());
        sourceBytes[0] = 0;

        assertEquals(sha256(new byte[]{9, 8, 7, 6}), fixture.preferences.value);
        assertArrayEquals(new byte[]{9, 8, 7, 6}, result.pending().snapshot());
        result.release();
    }

    @Test public void foregroundSessionAndNativeBusyGatesDoNotRetainLeaseOrInspect() {
        Fixture background = new Fixture("new", new byte[]{1});
        BundledPackController.Result notForeground = background.controller.prepare(
            PendingImport.Source.BUNDLED_AUTO_PROMPT, false);
        assertEquals(BundledPackController.Policy.BUSY, notForeground.policy());
        assertEquals("not_foreground", notForeground.code());
        assertEquals(0, background.leases.acquisitions);

        Fixture nativeBusy = new Fixture("new", new byte[]{1});
        nativeBusy.leases.busy = true;
        BundledPackController.Result busy = nativeBusy.controller.prepare(
            PendingImport.Source.BUNDLED_SETTINGS, true);
        assertEquals(BundledPackController.Policy.BUSY, busy.policy());
        assertEquals("native_busy", busy.code());

        Fixture openSession = new Fixture("new", new byte[]{1});
        openSession.sessions.open = true;
        BundledPackController.Result session = openSession.controller.prepare(
            PendingImport.Source.BUNDLED_SETTINGS, true);
        assertEquals(BundledPackController.Policy.BUSY, session.policy());
        assertEquals("open_session", session.code());
        assertEquals(0, openSession.inspector.inspections);
        assertEquals(0, openSession.leases.held);
    }

    @Test public void everyFailureReleasesLeaseAndSuccessfulPreviewOwnsItUntilDismissed() {
        Fixture invalid = new Fixture("new", new byte[]{1});
        invalid.inspector.failure = new Exception("PRIVATE_ANSWER_SENTINEL");
        BundledPackController.Result failed = invalid.controller.prepare(PendingImport.Source.BUNDLED_SETTINGS, true);
        assertEquals(BundledPackController.Policy.ERROR, failed.policy());
        assertEquals("invalid", failed.code());
        assertEquals(0, invalid.leases.held);
        assertFalse(failed.toString().contains("PRIVATE_ANSWER_SENTINEL"));

        Fixture persistence = new Fixture("new", new byte[]{2});
        persistence.preferences.writable = false;
        BundledPackController.Result notStored = persistence.controller.prepare(
            PendingImport.Source.BUNDLED_AUTO_PROMPT, true);
        assertFalse(notStored.activate());
        assertEquals(BundledPackController.Policy.ERROR, notStored.policy());
        assertEquals("preference_write", notStored.code());
        assertEquals(0, persistence.leases.held);

        Fixture throwingPersistence = new Fixture("new", new byte[]{4});
        throwingPersistence.preferences.failure = new IllegalStateException("PRIVATE_PREF_SENTINEL");
        BundledPackController.Result thrown = throwingPersistence.controller.prepare(
            PendingImport.Source.BUNDLED_AUTO_PROMPT, true);
        assertFalse(thrown.activate());
        assertEquals(BundledPackController.Policy.ERROR, thrown.policy());
        assertEquals("preference_write", thrown.code());
        assertEquals(0, throwingPersistence.leases.held);
        assertFalse(thrown.toString().contains("PRIVATE_PREF_SENTINEL"));

        Fixture successful = new Fixture("upgrade", new byte[]{3});
        BundledPackController.Result preview = successful.controller.prepare(
            PendingImport.Source.BUNDLED_SETTINGS, true);
        assertEquals(1, successful.leases.held);
        preview.release();
        preview.release();
        assertEquals(0, successful.leases.held);
        assertEquals(1, successful.leases.releases);
    }

    @Test public void controllerAndPendingPreviewNeverExposeArchiveContentPathOrHash() {
        Fixture fixture = new Fixture("upgrade", new byte[]{7, 7, 7});
        fixture.inspector.preview.put("answer", "PRIVATE_ANSWER_SENTINEL");
        fixture.inspector.preview.put("question", "PRIVATE_QUESTION_SENTINEL");
        fixture.inspector.preview.put("path", "content://private/provider/archive");
        fixture.inspector.preview.put("questions_sha256", "f".repeat(64));

        BundledPackController.Result result = fixture.controller.prepare(
            PendingImport.Source.BUNDLED_SETTINGS, true);
        String visible = result.code() + result.policy() + result.pending().preview() + result;

        for (String secret : new String[]{"PRIVATE_ANSWER_SENTINEL", "PRIVATE_QUESTION_SENTINEL",
                "content://", "questions_sha256", "ffffffff"}) {
            assertFalse("native result must redact " + secret, visible.contains(secret));
        }
        result.release();
    }

    @Test public void missingAssetIsCapabilityFalseAndSafeErrorWithoutLease() {
        Fixture fixture = new Fixture("new", new byte[]{1});
        fixture.source.available = false;

        assertFalse(fixture.controller.available());
        BundledPackController.Result result = fixture.controller.prepare(
            PendingImport.Source.BUNDLED_SETTINGS, true);
        assertEquals(BundledPackController.Policy.ERROR, result.policy());
        assertEquals("unavailable", result.code());
        assertEquals(0, fixture.leases.acquisitions);
    }

    private static void assertPolicy(String status, PendingImport.Source source,
            BundledPackController.Policy expected) {
        Fixture fixture = new Fixture(status, new byte[]{1, 2});
        BundledPackController.Result result = fixture.controller.prepare(source, true);
        assertEquals(status + " for " + source, expected, result.policy());
        result.release();
        assertEquals(0, fixture.leases.held);
    }

    private static String sha256(byte[] bytes) throws Exception {
        StringBuilder value = new StringBuilder();
        for (byte item : MessageDigest.getInstance("SHA-256").digest(bytes)) {
            value.append(String.format(Locale.ROOT, "%02x", item & 0xff));
        }
        return value.toString();
    }

    private static final class Fixture {
        final FakeSource source;
        final FakeInspector inspector;
        final FakePreferences preferences = new FakePreferences();
        final FakeLeases leases = new FakeLeases();
        final FakeSessions sessions = new FakeSessions();
        final BundledPackController controller;

        Fixture(String status, byte[] bytes) {
            source = new FakeSource(bytes);
            inspector = new FakeInspector(status);
            controller = new BundledPackController(source, inspector, preferences, leases, sessions);
        }
    }

    private static final class FakeSource implements BundledPackController.ByteSource {
        byte[] bytes;
        boolean available = true;
        FakeSource(byte[] bytes) { this.bytes = bytes; }
        @Override public boolean available() { return available; }
        @Override public byte[] read() { return bytes; }
    }

    private static final class FakeInspector implements BundledPackController.Inspector {
        final Map<String,Object> preview = new LinkedHashMap<>();
        Exception failure;
        int inspections;
        FakeInspector(String status) {
            preview.put("pack_id", "safe-pack");
            preview.put("name", "Safe pack");
            preview.put("revision", 2);
            preview.put("display_version", "2.0");
            preview.put("question_count", 3);
            preview.put("experience_count", 1);
            preview.put("installed_revision", 1);
            preview.put("status", status);
        }
        @Override public Map<String,Object> inspect(byte[] bytes) throws Exception {
            inspections++;
            if (failure != null) throw failure;
            return preview;
        }
    }

    private static final class FakePreferences implements BundledPackController.PreferenceStore {
        String value;
        boolean writable = true;
        RuntimeException failure;
        @Override public String get(String key) { return value; }
        @Override public boolean put(String key, String newValue) {
            if (failure != null) throw failure;
            if (!writable) return false;
            value = newValue;
            return true;
        }
    }

    private static final class FakeLeases implements BundledPackController.LeaseGateway {
        boolean busy;
        int acquisitions;
        int releases;
        int held;
        @Override public BundledPackController.Lease acquire() {
            acquisitions++;
            if (busy || held != 0) return null;
            held++;
            return () -> { releases++; held--; };
        }
    }

    private static final class FakeSessions implements BundledPackController.SessionGate {
        boolean open;
        @Override public boolean hasOpenSession() { return open; }
    }
}
