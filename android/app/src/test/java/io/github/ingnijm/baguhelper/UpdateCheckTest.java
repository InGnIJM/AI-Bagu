package io.github.ingnijm.baguhelper;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class UpdateCheckTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();
    static final class Preferences implements UpdateEngine.Preferences {
        final Map<String,Long> numbers = new HashMap<>();
        boolean enabled = true, failSummary;
        String status = "never", summary = "";
        public long number(String key) { return numbers.getOrDefault(key, 0L); }
        public void putNumber(String key, long value) { numbers.put(key, value); }
        public boolean enabled() { return enabled; }
        public void enable(boolean value) { enabled = value; }
        public String lastStatus() { return status; }
        public void lastStatus(String value) { status = value; }
        public String lastCheck() { return summary; }
        public void lastCheck(String value) { if (failSummary) throw new IllegalStateException("sk-test /private/summary"); summary = value; }
    }
    final Preferences preferences = new Preferences();
    UpdateEngineTest.Queue queue = new UpdateEngineTest.Queue();
    final UpdateEngineTest.Device device = new UpdateEngineTest.Device();
    final List<Map<String,Object>> snapshots = new ArrayList<>();
    final long[] now = {1000};
    int betaHttp = 200, stableHttp = 200, requests;
    String beta = empty("beta"), stable = empty("stable");
    UpdateEngine engine;

    static String empty(String channel) { return "{\"schema_version\":1,\"channel\":\"" + channel + "\",\"release\":null}"; }
    void start(String channel) throws Exception {
        engine = new UpdateEngine(channel, temporary.newFolder(), preferences, device, new UpdateIO(url -> {
            requests++; now[0] += 10;
            boolean isBeta = url.endsWith("beta.json");
            byte[] body = (isBeta ? beta : stable).getBytes(StandardCharsets.UTF_8);
            return new UpdateIO.Response(isBeta ? betaHttp : stableHttp, null, body.length, new ByteArrayInputStream(body), () -> {});
        }), queue, () -> now[0], snapshots::add, (op, candidate) -> {});
        queue.run();
    }
    @SuppressWarnings("unchecked") Map<String,Object> summary() {
        Object value = engine.state().get("lastCheck");
        assertNotNull("Every snapshot must include the safe lastCheck object", value);
        return (Map<String,Object>) value;
    }
    @SuppressWarnings("unchecked") Map<String,Object> channel(String key) {
        return (Map<String,Object>) ((Map<?,?>) summary().get("channels")).get(key);
    }

    @Test public void acceptedCheckPersistsCheckingBeforeNetworkAndSnapshotsDoNotMutate() throws Exception {
        start("beta");
        assertEquals("unknown", summary().get("status"));
        assertTrue(engine.check("manual", false));
        Map<String,Object> before = summary();
        String diagnostic = (String) before.get("diagnosticId");
        assertTrue(diagnostic.matches("n_[a-f0-9]{32}"));
        assertEquals("checking", UpdateIO.parse(preferences.summary).get("status"));
        assertEquals(0, requests);
        assertEquals(new HashSet<>(Arrays.asList("beta", "stable")), ((Map<?,?>) before.get("channels")).keySet());
        assertFalse(engine.check("duplicate", false));
        assertEquals(diagnostic, summary().get("diagnosticId"));
        queue.run();
        assertEquals("checking", before.get("status"));
        assertEquals("latest", summary().get("status"));
        assertEquals("empty", channel("beta").get("status"));
        assertEquals("empty", channel("stable").get("status"));
        assertEquals(diagnostic, summary().get("diagnosticId"));
        assertEquals(1000L, summary().get("startedAt"));
        assertEquals(1020L, summary().get("completedAt"));
        assertTrue(preferences.summary.getBytes(StandardCharsets.UTF_8).length <= 4096);
    }
    @Test public void partialFailureRetainsCandidateAndPerChannelHttpEvidence() throws Exception {
        beta = UpdateEngineTest.feed(2, "beta", new byte[]{1}); stableHttp = 404;
        start("beta"); engine.check("partial", false); queue.run();
        assertEquals("available", engine.state().get("status"));
        assertNotNull(engine.state().get("candidate"));
        assertEquals("partial-error", summary().get("status"));
        assertEquals(1001, summary().get("errorCode"));
        assertEquals("available", channel("beta").get("status"));
        assertEquals("error", channel("stable").get("status"));
        assertEquals(404, channel("stable").get("httpStatus"));
        assertEquals(10L, channel("stable").get("durationMs"));
        assertFalse(preferences.summary.contains("apkUrl"));
        assertFalse(preferences.summary.contains("notes"));
        assertFalse(preferences.summary.contains("https://"));
        assertTrue(engine.state().get("message").toString().contains("404"));
        assertTrue(engine.state().get("message").toString().contains((String) summary().get("diagnosticId")));
    }
    @Test public void allFailedChecksAndThrottledOrInvalidActionsKeepFeedbackIdentity() throws Exception {
        betaHttp = 403; stableHttp = 503;
        start("beta"); engine.check("first", true); queue.run();
        Map<String,Object> before = summary();
        assertEquals("error", before.get("status"));
        assertEquals(403, channel("beta").get("httpStatus"));
        assertEquals(503, channel("stable").get("httpStatus"));
        Object id = before.get("diagnosticId");
        assertEquals(id, engine.state().get("diagnostic_id"));
        assertFalse(engine.check("throttled", true));
        assertFalse(engine.download("invalid", "invalid-download"));
        assertFalse(engine.install("invalid", "invalid-install"));
        assertEquals(before, summary());
        assertEquals(id, engine.state().get("diagnostic_id"));
        engine.check("manual", false); queue.run();
        assertNotEquals(id, summary().get("diagnosticId"));
    }
    @Test public void stableChecksOnlyItsChannelAndDisabledAutoNeverCreatesAnOperation() throws Exception {
        preferences.enabled = false;
        start("stable");
        assertFalse(engine.check("disabled", true));
        assertEquals("unknown", summary().get("status"));
        assertTrue(engine.check("manual", false)); queue.run();
        assertEquals(1, requests);
        assertEquals(Collections.singleton("stable"), ((Map<?,?>) summary().get("channels")).keySet());
    }
    @Test public void restartRestoresInterruptedSummaryWithoutReplayingWork() throws Exception {
        start("beta"); engine.check("unfinished", false);
        String id = (String) summary().get("diagnosticId");
        queue = new UpdateEngineTest.Queue(); // Simulate a dead process; never run its queued work.
        start("beta");
        assertEquals(0, requests);
        assertEquals("interrupted", summary().get("status"));
        assertEquals("interrupted", channel("beta").get("status"));
        assertEquals(id, summary().get("diagnosticId"));
        assertTrue(engine.state().get("message").toString().contains("中断"));
    }
    @Test public void summaryWriteFailureNeverChangesSuccessfulOrFailedCheckOutcome() throws Exception {
        preferences.failSummary = true;
        start("beta"); engine.check("memory-only", false); queue.run();
        assertEquals("latest", summary().get("status"));
        assertEquals(false, engine.state().get("busy"));
        stableHttp = 429;
        engine.check("memory-error", false); queue.run();
        assertEquals("partial-error", summary().get("status"));
        assertEquals(429, channel("stable").get("httpStatus"));
        assertEquals(false, engine.state().get("busy"));
        assertFalse(engine.state().toString().contains("sk-test"));
    }
    @Test public void malformedOversizedOrPoisonedStoredSummaryIsNotExposed() throws Exception {
        for (String raw : Arrays.asList("broken sk-test", String.join("", Collections.nCopies(4097, "x")),
            "{\"status\":\"error\",\"diagnosticId\":\"/private/key\",\"url\":\"https://private/?token=sk-test\"}")) {
            preferences.summary = raw;
            start("beta");
            assertEquals("unknown", summary().get("status"));
            assertFalse(engine.state().toString().contains("sk-test"));
            assertFalse(engine.state().toString().contains("private"));
        }
    }

    @Test public void restartRejectsContradictorySuccessSummaryWithAFailedChannel() throws Exception {
        start("beta"); stableHttp = 404; engine.check("failed", false); queue.run();
        String valid = preferences.summary;
        Map<String,Object> wrong = new LinkedHashMap<>(UpdateIO.parse(valid));
        wrong.put("status", "latest"); wrong.put("errorCode", 0);
        preferences.summary = UpdateIO.json(wrong);
        start("beta");
        assertEquals("unknown", summary().get("status"));
        assertFalse(engine.state().get("message").toString().contains("当前没有兼容的新版本"));
    }
    @Test public void acceptedCheckWithUnavailableDeviceRestoresFailureInsteadOfUnknown() throws Exception {
        UpdateEngine.Device missingDevice = new UpdateEngine.Device() {
            public long installedCode() throws Exception { throw new IOException("sk-test private package"); }
            public int sdk() { return 29; }
            public List<String> abis() { return List.of("arm64-v8a"); }
            public void verify(File file, UpdatePolicy.Release release) {}
        };
        engine = new UpdateEngine("beta", temporary.newFolder(), preferences, missingDevice, new UpdateIO(url -> {
            fail("Device failure must not start network work"); return null;
        }), queue, () -> now[0], snapshots::add, (op, candidate) -> {});
        queue.run(); engine.check("device-error", false); queue.run();
        assertEquals("error", summary().get("status")); assertEquals(1204, summary().get("errorCode"));
        String diagnostic = (String) summary().get("diagnosticId");
        start("beta");
        assertEquals("error", summary().get("status")); assertEquals(diagnostic, summary().get("diagnosticId"));
    }
}
