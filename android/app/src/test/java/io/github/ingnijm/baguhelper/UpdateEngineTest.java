package io.github.ingnijm.baguhelper;

import org.junit.Test;
import org.junit.Rule;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.Executor;

/** Real policy/state/cache/I/O; only network, package manager and scheduling are substituted. */
public class UpdateEngineTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();
    static final class Queue implements Executor {
        final ArrayDeque<Runnable> jobs = new ArrayDeque<>();
        public void execute(Runnable job) { jobs.add(job); }
        void run() { while (!jobs.isEmpty()) jobs.remove().run(); }
    }
    static final class Memory implements UpdateEngine.Preferences {
        final Map<String,Object> values = new HashMap<>();
        public long number(String key) { return ((Number) values.getOrDefault(key, 0L)).longValue(); }
        public boolean enabled() { return !Boolean.FALSE.equals(values.get("enabled")); }
        public void putNumber(String key, long value) { values.put(key, value); }
        public void enable(boolean value) { values.put("enabled", value); }
        public String lastStatus() { return (String) values.getOrDefault("lastStatus", "never"); }
        public void lastStatus(String value) { values.put("lastStatus", value); }
    }
    static final class Network implements UpdateIO.Transport {
        final Map<String, byte[]> bodies = new HashMap<>();
        final Map<String, String> redirects = new HashMap<>();
        int calls;
        Runnable duringRead;
        public UpdateIO.Response open(String url) throws IOException {
            calls++;
            if (redirects.containsKey(url)) return new UpdateIO.Response(302, redirects.get(url), -1, new ByteArrayInputStream(new byte[0]), () -> {});
            byte[] body = bodies.get(url);
            if (body == null) throw new IOException("private network detail");
            InputStream input = new ByteArrayInputStream(body) {
                @Override public synchronized int read(byte[] b, int off, int len) {
                    if (duringRead != null) { Runnable action = duringRead; duringRead = null; action.run(); }
                    return super.read(b, off, len);
                }
            };
            return new UpdateIO.Response(200, null, body.length, input, () -> {});
        }
    }
    static final class Device implements UpdateEngine.Device {
        long installed = 1; int verified;
        boolean reject;
        Runnable duringVerify;
        public long installedCode() { return installed; }
        public int sdk() { return 29; }
        public List<String> abis() { return Arrays.asList("arm64-v8a"); }
        public void verify(File file, UpdatePolicy.Release release) throws Exception {
            verified++;
            if (duringVerify != null) duringVerify.run();
            if (reject || release.versionCode <= installed) throw new IOException("archive invalid");
        }
    }
    static String feed(int code, String channel, byte[] apk) {
        Map<String,Object> r = UpdatePolicyTest.release(code, channel);
        r.put("size", apk.length); r.put("sha256", UpdateIO.sha256(apk));
        return UpdateIO.json(new LinkedHashMap<String,Object>() {{ put("schema_version",1);put("channel",channel);put("release",r); }});
    }
    final Queue queue = new Queue(); final Memory prefs = new Memory();
    final Network network = new Network(); final Device device = new Device();
    UpdateEngine engine; File cache; final List<String> installRequests = new ArrayList<>();
    void start() throws Exception {
        if (cache == null) cache = temporary.newFolder("updates");
        engine = new UpdateEngine("beta", cache, prefs, device, new UpdateIO(network), queue,
            () -> 1000L, state -> {}, (op, candidate) -> installRequests.add(op));
        queue.run();
    }
    void candidate() throws Exception {
        byte[] apk = "synthetic-apk".getBytes(StandardCharsets.UTF_8);
        network.bodies.put(UpdatePolicy.FEED_ROOT+"beta.json", feed(2,"beta",apk).getBytes(StandardCharsets.UTF_8));
        network.bodies.put(UpdatePolicy.FEED_ROOT+"stable.json", "{\"schema_version\":1,\"channel\":\"stable\",\"release\":null}".getBytes(StandardCharsets.UTF_8));
        network.bodies.put((String)UpdatePolicyTest.release(2,"beta").get("apkUrl"), apk);
        start(); engine.check("check", false); queue.run();
        assertEquals("available",engine.state().get("status"));
    }
    String id() { return (String)((Map<?,?>) engine.state().get("candidate")).get("id"); }

    @Test public void strictJsonRejectsDuplicatesFractionsExtraAndTrailingInput() throws Exception {
        for (String value : Arrays.asList("{\"a\":1,\"a\":2}", "{\"a\":1.0}", "{\"a\":1e0}", "{}{}", "{\"a\":01}", "{\"a\":\"\n\"}")) {
            assertThrows(IllegalArgumentException.class, () -> UpdateIO.parse(value));
        }
        assertEquals(1L, UpdateIO.parse("{\"a\":1}").get("a"));
        assertThrows(IllegalArgumentException.class, () -> UpdatePolicy.parseFeed(UpdateIO.parse("{\"schema_version\":1,\"channel\":\"beta\",\"release\":null,\"x\":0}"), "beta"));
    }
    @Test public void failedAutomaticAttemptIsPersistedBeforeNetworkAndThrottled() throws Exception {
        start(); engine.check("auto", true);
        assertEquals(1000L,prefs.number("lastAttempt")); assertEquals(0,network.calls);
        queue.run(); assertEquals("error",engine.state().get("status"));
        engine.check("auto2",true); queue.run(); assertEquals(2,network.calls);
        engine.check("manual",false); queue.run(); assertEquals(4,network.calls);
    }
    @Test public void failedFeedProducesStageEvidenceWithoutDiagnosticFailureChangingUpdateResult() throws Exception {
        List<String> stages = new ArrayList<>();
        engine = new UpdateEngine("beta", temporary.newFolder("diagnostic-updates"), prefs, device,
            new UpdateIO(network), queue, () -> 1000L, state -> {}, (op, candidate) -> {},
            (stage, failure) -> { stages.add(stage + ":" + failure.getClass().getSimpleName()); throw new IllegalStateException("logger unavailable"); });
        queue.run(); engine.check("diagnostic-check", false); queue.run();
        assertEquals(Arrays.asList("check:IOException", "check:IOException"), stages);
        assertEquals("error", engine.state().get("status"));
        assertEquals("error", prefs.lastStatus());
    }
    @Test public void partialFailureNeverClaimsLatestButCanOfferVerifiedCandidate() throws Exception {
        candidate(); network.bodies.remove(UpdatePolicy.FEED_ROOT+"stable.json");
        engine.check("partial",false); queue.run();
        assertEquals("partial-error",engine.state().get("lastStatus")); assertNotNull(engine.state().get("candidate"));
        network.bodies.put(UpdatePolicy.FEED_ROOT+"beta.json", "{\"schema_version\":1,\"channel\":\"beta\",\"release\":null}".getBytes(StandardCharsets.UTF_8));
        engine.check("partial-empty",false); queue.run(); assertNotEquals("latest",engine.state().get("status"));
    }
    @Test public void streamingDownloadPromotesOnlyVerifiedBytesAndRevalidatesOnRestart() throws Exception {
        candidate(); String candidateId=id(); engine.download(candidateId,"download"); queue.run();
        assertEquals("ready",engine.state().get("status")); assertTrue(new File(cache,"candidate.apk").isFile());
        assertFalse(new File(cache,"candidate.part").exists()); assertEquals(1,device.verified);
        start(); assertEquals("ready",engine.state().get("status")); assertEquals(2,device.verified);
        Files.write(new File(cache,"candidate.apk").toPath(),new byte[]{0}); start();
        assertEquals("available",engine.state().get("status")); assertFalse(new File(cache,"candidate.apk").exists());
    }
    @Test public void cancelledDownloadCannotPublishReadyOrLeavePartAndRejectsStaleCandidate() throws Exception {
        candidate(); String candidateId=id();
        assertFalse(engine.download("unknown","bad"));
        network.duringRead=()->engine.cancel("cancel");
        engine.download(candidateId,"download"); queue.run();
        assertEquals("available",engine.state().get("status")); assertEquals("cancel",engine.state().get("operationId"));
        assertFalse(new File(cache,"candidate.part").exists()); assertFalse(new File(cache,"candidate.apk").exists());
        assertEquals(0,device.verified);
    }
    @Test public void hashTruncationAndOversizeFailuresNeverPromote() throws Exception {
        candidate(); String url=(String)UpdatePolicyTest.release(2,"beta").get("apkUrl");
        for (byte[] bad : Arrays.asList(new byte[]{1}, new byte[50], "corrupted-apk".getBytes(StandardCharsets.UTF_8))) {
            network.bodies.put(url,bad); engine.download(id(),"badfile");queue.run();
            assertEquals("available",engine.state().get("status")); assertFalse(new File(cache,"candidate.apk").exists());
        }
    }
    @Test public void redirectAndFeedLimitsFailBeforeReadingUntrustedTarget() throws Exception {
        UpdateIO io=new UpdateIO(network); UpdateIO.Cancellation cancel=new UpdateIO.Cancellation();
        String url=(String)UpdatePolicyTest.release(2,"beta").get("apkUrl");
        network.redirects.put(url,"http://github.com/unsafe");
        assertThrows(Exception.class,()->io.download(UpdatePolicyTest.parse(UpdatePolicyTest.release(2,"beta"),"beta"),temporary.newFile(),cancel,n->{}));
        assertEquals(1,network.calls);
        network.bodies.put(UpdatePolicy.FEED_ROOT+"beta.json",new byte[65537]);
        assertThrows(Exception.class,()->io.feed("beta",cancel));
    }
    @Test public void installRequiresFreshVerificationAndOnlyActualVersionReportsSuccess() throws Exception {
        candidate(); engine.download(id(),"download"); queue.run();
        engine.install(id(),"install");queue.run();assertEquals(Arrays.asList("install"),installRequests);
        assertTrue(engine.installCurrent("install",id()));
        engine.installLaunched("install");
        start(); assertEquals("ready",engine.state().get("status"));
        device.installed=2;start();assertEquals("updated",engine.state().get("status"));
        assertFalse(engine.state().containsKey("candidate"));
    }
    @Test public void installTokenBecomesStaleAndSameVersionIsRejected() throws Exception {
        candidate(); engine.download(id(),"download");queue.run();String candidateId=id();
        engine.install(candidateId,"install");queue.run(); engine.installBlocked("install","busy");
        assertFalse(engine.installCurrent("install",candidateId));
        device.installed=2;engine.install(candidateId,"again");queue.run();
        assertEquals(1,installRequests.size());assertNotEquals("install-check",engine.state().get("status"));
    }
    @Test public void installGuardRequiresEveryNativeAndSharedBoundaryIdle() {
        assertTrue(UpdateEngine.canInstall(true,false,false,false,false,false,false,false,false));
        for(int i=0;i<8;i++) { boolean[] busy=new boolean[8];busy[i]=true;
            assertFalse(UpdateEngine.canInstall(true,busy[0],busy[1],busy[2],busy[3],busy[4],busy[5],busy[6],busy[7])); }
        assertFalse(UpdateEngine.canInstall(false,false,false,false,false,false,false,false,false));
    }
    @Test public void installerLeasePreventsReplacementUntilInstallerReturnsAndDoesNotClaimSuccess() throws Exception {
        candidate();engine.download(id(),"download");queue.run();String candidateId=id();
        engine.install(candidateId,"install");queue.run();engine.installLaunched("install");
        network.bodies.put(UpdatePolicy.FEED_ROOT+"beta.json",feed(3,"beta",new byte[]{1,2}).getBytes(StandardCharsets.UTF_8));
        engine.check("newer",false);queue.run();assertEquals(candidateId,id());
        assertFalse(engine.download(candidateId,"overwrite"));
        engine.installerReturned();assertEquals("ready",engine.state().get("status"));
        assertEquals(2,prefs.number("requestedVersion"));
        engine.check("newer-after-return",false);queue.run();assertNotEquals(candidateId,id());
    }
    @Test public void failedInstallerLaunchReleasesLeaseAndExplainsFailure() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        engine.installLaunched("install");engine.installLaunchFailed("install");
        assertEquals("ready",engine.state().get("status"));assertEquals(false,engine.state().get("installerLease"));
        assertTrue(((String)engine.state().get("message")).contains("无法"));
    }
    @Test public void clearedCacheAndResidualPartRecoverWithoutPretendingReady() throws Exception {
        candidate();engine.download(id(),"download");queue.run();
        Files.delete(new File(cache,"candidate.apk").toPath());Files.write(new File(cache,"candidate.part").toPath(),new byte[]{1});
        start();assertEquals("available",engine.state().get("status"));assertEquals(false,engine.state().get("ready"));
        assertFalse(new File(cache,"candidate.part").exists());
        assertTrue(engine.download(id(),"redownload"));queue.run();assertEquals("ready",engine.state().get("status"));
    }
    @Test public void invalidUnicodeEscapeAndDeepJsonAreRejected() {
        assertThrows(IllegalArgumentException.class,()->UpdateIO.parse("{\"x\":\"\\u-000\"}"));
        assertThrows(IllegalArgumentException.class,()->UpdateIO.parse("{\"x\":{\"x\":{\"x\":{\"x\":{\"x\":{\"x\":{\"x\":{\"x\":{\"x\":1}}}}}}}}}"));
    }
    @Test public void cacheWriteFailureCannotLeaveUiStuckChecking() throws Exception {
        candidate();Files.createDirectory(new File(cache,"candidate.json.tmp").toPath());
        network.bodies.put(UpdatePolicy.FEED_ROOT+"beta.json",feed(3,"beta",new byte[]{1,2}).getBytes(StandardCharsets.UTF_8));
        engine.check("write-error",false);queue.run();
        assertNotEquals("checking",engine.state().get("status"));assertEquals("error",engine.state().get("lastStatus"));
        assertEquals(false,engine.state().get("busy"));
    }
    @Test public void restorationPublishesOnlyCoherentSnapshotAfterVerification() throws Exception {
        candidate();engine.download(id(),"download");queue.run();
        device.duringVerify=()->{assertEquals("restoring",engine.state().get("status"));assertFalse(engine.state().containsKey("candidate"));};
        start();assertEquals("ready",engine.state().get("status"));
    }
    @Test public void reusedInstallOperationCannotMakeAnOldAsyncGuardCurrentAgain() throws Exception {
        candidate();engine.download(id(),"download");queue.run();String candidateId=id();
        engine.install(candidateId,"install");queue.run();engine.installBlocked("install","busy");
        assertFalse(engine.install(candidateId,"install"));
        assertFalse(engine.installCurrent("install",candidateId));
        assertTrue(engine.install(candidateId,"new-install"));queue.run();
        assertFalse(engine.installCurrent("install",candidateId));
    }
    @Test public void missingLeasedApkRequiresConfirmedRecoveryBeforeRedownload() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();engine.installLaunched("install");
        Files.delete(new File(cache,"candidate.apk").toPath());start();
        assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("installerLease"));
        assertEquals(true,engine.state().get("recovery"));assertFalse(engine.download(id(),"premature"));
        engine.recover("confirmed-recovery");assertEquals(false,engine.state().get("installerLease"));
        assertEquals(2,prefs.number("requestedVersion"));assertTrue(engine.download(id(),"retry"));
    }
    @Test public void validMetadataWithCorruptLeasedApkPreservesBytesAndOffersRecovery() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();engine.installLaunched("install");
        File apk=new File(cache,"candidate.apk"),metadata=new File(cache,"candidate.json");
        byte[] envelope=Files.readAllBytes(metadata.toPath()),damaged=new byte[]{7,8,9};
        Files.write(apk.toPath(),damaged);start();
        assertNotNull(engine.state().get("candidate"));assertEquals(false,engine.state().get("ready"));
        assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("recovery"));
        assertFalse(engine.download(id(),"blocked-download"));assertFalse(engine.install(id(),"blocked-install"));
        engine.check("still-leased",false);queue.run();assertEquals("recovery",engine.state().get("status"));
        engine.cancel("background");assertEquals(true,engine.state().get("installerLease"));
        assertArrayEquals(envelope,Files.readAllBytes(metadata.toPath()));assertArrayEquals(damaged,Files.readAllBytes(apk.toPath()));
        engine.recover("confirmed-recovery");assertEquals(false,engine.state().get("installerLease"));
        assertArrayEquals(damaged,Files.readAllBytes(apk.toPath()));
        assertTrue(engine.download(id(),"retry"));queue.run();assertEquals("ready",engine.state().get("status"));
    }
    @Test public void validLeasedApkRemainsReadyAndCannotBeResetByRecovery() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();engine.installLaunched("install");
        File apk=new File(cache,"candidate.apk");byte[] before=Files.readAllBytes(apk.toPath());start();
        assertEquals("ready",engine.state().get("status"));assertEquals(false,engine.state().get("recovery"));
        engine.recover("not-needed");assertEquals(true,engine.state().get("installerLease"));
        assertEquals("ready",engine.state().get("status"));assertArrayEquals(before,Files.readAllBytes(apk.toPath()));
    }
    @Test public void partialMetadataEvictionOffersConfirmedRecoveryWithoutTouchingLeasedBytes() throws Exception {
        for(boolean corrupt:Arrays.asList(false,true)) {
            cache=temporary.newFolder();
            candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();engine.installLaunched("install");
            File apk=new File(cache,"candidate.apk"),metadata=new File(cache,"candidate.json");
            byte[] before=Files.readAllBytes(apk.toPath());
            if(corrupt)Files.write(metadata.toPath(),"invalid".getBytes(StandardCharsets.UTF_8));else Files.delete(metadata.toPath());
            start();assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("recovery"));
            engine.check("cannot-replace",false);queue.run();assertArrayEquals(before,Files.readAllBytes(apk.toPath()));
            assertEquals("recovery",engine.state().get("status"));
            engine.cancel("background");assertEquals(true,engine.state().get("installerLease"));
            engine.recover("confirmed-recovery");assertEquals(false,engine.state().get("installerLease"));
            assertEquals(2,prefs.number("requestedVersion"));assertArrayEquals(before,Files.readAllBytes(apk.toPath()));
            engine.check("recover-check",false);queue.run();assertTrue(engine.download(id(),"retry"));queue.run();
        }
    }
    @Test public void noticeGenerationSurvivesWorkButChangesOnNextCheckAndNewProcess() throws Exception {
        candidate();Object first=engine.state().get("noticeId");assertNotNull(first);
        engine.download(id(),"download");queue.run();assertEquals(first,engine.state().get("noticeId"));
        engine.check("new-check",false);queue.run();Object second=engine.state().get("noticeId");assertNotEquals(first,second);
        start();assertNotEquals(second,engine.state().get("noticeId"));
    }
}
