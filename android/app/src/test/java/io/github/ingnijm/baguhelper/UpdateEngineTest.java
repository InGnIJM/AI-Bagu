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
        String failNumber;
        public long number(String key) { return ((Number) values.getOrDefault(key, 0L)).longValue(); }
        public boolean enabled() { return !Boolean.FALSE.equals(values.get("enabled")); }
        public void putNumber(String key, long value) { if(key.equals(failNumber))throw new IllegalStateException("sk-test private preferences"); values.put(key, value); }
        public void enable(boolean value) { values.put("enabled", value); }
        public String lastStatus() { return (String) values.getOrDefault("lastStatus", "never"); }
        public void lastStatus(String value) { values.put("lastStatus", value); }
        public String lastCheck() { return (String) values.getOrDefault("lastCheck", ""); }
        public void lastCheck(String value) { values.put("lastCheck", value); }
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
        boolean reject,failAbandon;
        final Set<Integer> sessions = new HashSet<>();
        final List<Integer> abandoned = new ArrayList<>();
        Runnable duringVerify,duringSessionExists;
        public long installedCode() { return installed; }
        public int sdk() { return 29; }
        public List<String> abis() { return Arrays.asList("arm64-v8a"); }
        public void verify(File file, UpdatePolicy.Release release) throws Exception {
            verified++;
            if (duringVerify != null) duringVerify.run();
            if (reject || release.versionCode <= installed) throw new IOException("archive invalid");
        }
        public boolean installSessionExists(int sessionId) { if(duringSessionExists!=null){Runnable action=duringSessionExists;duringSessionExists=null;action.run();}return sessions.contains(sessionId); }
        public void abandonInstallSession(int sessionId) throws Exception {
            if(failAbandon)throw new IOException("sk-test abandon unavailable");
            sessions.remove(sessionId); abandoned.add(sessionId);
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
    final List<UpdateDiagnostic> diagnostics = new ArrayList<>();
    int nextSession=40;
    void start() throws Exception {
        if (cache == null) cache = temporary.newFolder("updates");
        engine = new UpdateEngine("beta", cache, prefs, device, new UpdateIO(network), queue,
            () -> 1000L, state -> {}, (op, candidate) -> installRequests.add(op), diagnostics::add);
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
    int commit(String op) throws Exception {
        int sessionId=++nextSession;device.sessions.add(sessionId);engine.installSessionCommitted(op,sessionId);return sessionId;
    }

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
        List<UpdateDiagnostic> events = new ArrayList<>();
        engine = new UpdateEngine("beta", temporary.newFolder("diagnostic-updates"), prefs, device,
            new UpdateIO(network), queue, () -> 1000L, state -> {}, (op, candidate) -> {},
            event -> { events.add(event); throw new IllegalStateException("logger unavailable"); });
        queue.run(); engine.check("diagnostic-check", false); queue.run();
        List<String> channels = new ArrayList<>();
        for (UpdateDiagnostic event : events) if (event.channel != null && event.outcome.equals("error")) {
            channels.add(event.channel); assertEquals(1005, event.errorCode);
            assertEquals(engine.state().get("diagnostic_id"), event.diagnosticId);
        }
        assertEquals(Arrays.asList("beta", "stable"), channels);
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
    @Test public void restartCacheFailureOwnsFeedbackWhilePreservingLastCheck() throws Exception {
        for (boolean leased : Arrays.asList(false, true)) for (String kind : Arrays.asList("hash", "apk", "missing")) {
            cache=temporary.newFolder();prefs.values.clear();device.reject=false;
            candidate();network.bodies.remove(UpdatePolicy.FEED_ROOT+"stable.json");
            engine.check("partial-check",false);queue.run();
            Map<?,?> lastCheck=(Map<?,?>)engine.state().get("lastCheck");
            Object checkId=lastCheck.get("diagnosticId");assertEquals("partial-error",lastCheck.get("status"));
            engine.download(id(),"download");queue.run();
            if(leased){engine.install(id(),"install");queue.run();commit("install");}
            File apk=new File(cache,"candidate.apk");
            if("hash".equals(kind))Files.write(apk.toPath(),"corrupted-apk".getBytes(StandardCharsets.UTF_8));
            if("apk".equals(kind))device.reject=true;
            if("missing".equals(kind))Files.delete(apk.toPath());
            diagnostics.clear();start();
            int code="hash".equals(kind)?1203:"apk".equals(kind)?1204:1201;
            UpdateDiagnostic failure=null;
            for(UpdateDiagnostic event:diagnostics)if(event.errorCode==code)failure=event;
            assertNotNull(failure);assertEquals("verify",failure.stage);
            assertEquals("Current cache failure must own its feedback number",failure.diagnosticId,engine.state().get("diagnostic_id"));
            assertNotEquals(checkId,failure.diagnosticId);
            assertTrue(engine.state().get("message").toString().contains(failure.diagnosticId));
            assertFalse(engine.state().get("message").toString().contains(checkId.toString()));
            assertEquals(lastCheck,engine.state().get("lastCheck"));
            assertEquals(leased?"recovery":"available",engine.state().get("status"));
            assertFalse(engine.check("throttled-after-restart",true));
            assertEquals(failure.diagnosticId,engine.state().get("diagnostic_id"));
            assertEquals(leased&&!"missing".equals(kind),apk.exists());
        }
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
        commit("install");
        start(); assertEquals("recovery",engine.state().get("status"));
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
    @Test public void installerLeasePreventsReplacementUntilSystemCancellationAndDoesNotClaimSuccess() throws Exception {
        candidate();engine.download(id(),"download");queue.run();String candidateId=id();
        engine.install(candidateId,"install");queue.run();int sessionId=commit("install");
        network.bodies.put(UpdatePolicy.FEED_ROOT+"beta.json",feed(3,"beta",new byte[]{1,2}).getBytes(StandardCharsets.UTF_8));
        engine.check("newer",false);queue.run();assertEquals(candidateId,id());
        assertFalse(engine.download(candidateId,"overwrite"));
        engine.installResult(sessionId,UpdateInstallStatusPolicy.map(3,false));assertEquals("ready",engine.state().get("status"));
        assertEquals(0,prefs.number("requestedVersion"));
        engine.check("newer-after-return",false);queue.run();assertNotEquals(candidateId,id());
    }
    @Test public void failedInstallerPreparationReturnsReadyAndExplainsFailure() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        engine.installLaunchFailed("install");
        assertEquals("ready",engine.state().get("status"));assertEquals(false,engine.state().get("installerLease"));
        assertTrue(((String)engine.state().get("message")).contains("无法"));
    }
    @Test public void uncertainCommitKeepsLeaseUntilSessionCanBeRecovered() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        int sessionId=commit("install");engine.installCommitUncertain("install",new IOException("sk-test binder"));
        assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("installerLease"));
        assertEquals(false,engine.state().get("ready"));assertEquals(sessionId,prefs.number("installSessionId"));
        assertEquals(1302,diagnostics.get(diagnostics.size()-1).errorCode);
    }
    @Test public void stagedSessionAbandonFailureKeepsProtectionAndIdentity() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);assertTrue(engine.installSessionPrepared("install",73));
        engine.installAbandonFailed("install",73,new IOException("sk-test abandon"));
        assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("installerLease"));
        assertEquals(73,prefs.number("installSessionId"));assertEquals(false,engine.state().get("ready"));
    }
    @Test public void sessionCreatedAfterInstallGuardExpiresIsTrackedUntilDiscarded() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        engine.cancelInstallation("install","guard expired");device.sessions.add(73);
        assertFalse(engine.installSessionPrepared("install",73));
        assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("installerLease"));
        assertEquals(73,prefs.number("installSessionId"));assertEquals(false,engine.state().get("ready"));
        device.abandonInstallSession(73);engine.installRejectedSessionDiscarded(73);
        assertEquals(0,prefs.number("installSessionId"));assertEquals("ready",engine.state().get("status"));
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
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();commit("install");
        Files.delete(new File(cache,"candidate.apk").toPath());start();
        assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("installerLease"));
        assertEquals(true,engine.state().get("recovery"));assertFalse(engine.download(id(),"premature"));
        engine.recover("confirmed-recovery");assertEquals(false,engine.state().get("installerLease"));
        assertEquals(0,prefs.number("requestedVersion"));assertTrue(engine.download(id(),"retry"));
    }
    @Test public void validMetadataWithCorruptLeasedApkPreservesBytesAndOffersRecovery() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();commit("install");
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
    @Test public void validLeasedApkStillRequiresExplicitSessionRecovery() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();commit("install");
        File apk=new File(cache,"candidate.apk");byte[] before=Files.readAllBytes(apk.toPath());start();
        assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("recovery"));
        engine.recover("confirmed");assertEquals(false,engine.state().get("installerLease"));
        assertEquals("ready",engine.state().get("status"));assertArrayEquals(before,Files.readAllBytes(apk.toPath()));
    }
    @Test public void partialMetadataEvictionOffersConfirmedRecoveryWithoutTouchingLeasedBytes() throws Exception {
        for(boolean corrupt:Arrays.asList(false,true)) {
            cache=temporary.newFolder();
            candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();commit("install");
            File apk=new File(cache,"candidate.apk"),metadata=new File(cache,"candidate.json");
            byte[] before=Files.readAllBytes(apk.toPath());
            if(corrupt)Files.write(metadata.toPath(),"invalid".getBytes(StandardCharsets.UTF_8));else Files.delete(metadata.toPath());
            start();assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("recovery"));
            engine.check("cannot-replace",false);queue.run();assertArrayEquals(before,Files.readAllBytes(apk.toPath()));
            assertEquals("recovery",engine.state().get("status"));
            engine.cancel("background");assertEquals(true,engine.state().get("installerLease"));
            engine.recover("confirmed-recovery");assertEquals(false,engine.state().get("installerLease"));
            assertEquals(0,prefs.number("requestedVersion"));assertArrayEquals(before,Files.readAllBytes(apk.toPath()));
            engine.check("recover-check",false);queue.run();assertTrue(engine.download(id(),"retry"));queue.run();
        }
    }
    @Test public void noticeGenerationSurvivesWorkButChangesOnNextCheckAndNewProcess() throws Exception {
        candidate();Object first=engine.state().get("noticeId");assertNotNull(first);
        engine.download(id(),"download");queue.run();assertEquals(first,engine.state().get("noticeId"));
        engine.check("new-check",false);queue.run();Object second=engine.state().get("noticeId");assertNotEquals(first,second);
        start();assertNotEquals(second,engine.state().get("noticeId"));
    }

    @Test public void cancelledDownloadKeepsItsDiagnosticIdButNotTheCancelBridgeId() throws Exception {
        candidate(); Object checkId=engine.state().get("diagnostic_id");
        engine.download(id(),"download"); String downloadId=(String)engine.state().get("diagnostic_id");
        assertNotEquals(checkId,downloadId);
        assertFalse(engine.download(id(),"duplicate")); assertFalse(engine.check("busy",false));
        assertEquals(downloadId,engine.state().get("diagnostic_id"));
        engine.cancel("cancel");engine.cancel("duplicate-cancel");queue.run();
        assertEquals(downloadId,engine.state().get("diagnostic_id"));
        assertEquals("duplicate-cancel",engine.state().get("operationId"));
        List<UpdateDiagnostic> completed=new ArrayList<>();
        for(UpdateDiagnostic event:diagnostics)if(event.diagnosticId.equals(downloadId)&&!event.outcome.equals("started"))completed.add(event);
        assertEquals(1,completed.size());assertEquals("cancelled",completed.get(0).outcome);
        assertEquals(0,completed.get(0).errorCode);assertNull(completed.get(0).httpStatus);
    }
    @Test public void staleInstallerWorkerFailureCannotAttachToANewerCheck() throws Exception {
        candidate();engine.download(id(),"download");queue.run();
        String candidateId=id();engine.install(candidateId,"old-install");String previous=(String)engine.state().get("diagnostic_id");
        engine.cancelInstallation("old-install","已取消安装准备。");
        device.reject=true;engine.check("new-check",false);String current=(String)engine.state().get("diagnostic_id");
        assertNotEquals(previous,current);queue.run();
        assertEquals(current,engine.state().get("diagnostic_id"));assertEquals("available",engine.state().get("lastStatus"));
        assertTrue(installRequests.isEmpty());
        boolean found=false;
        for(UpdateDiagnostic event:diagnostics)if(event.errorCode==1204){assertEquals(previous,event.diagnosticId);found=true;}
        assertTrue(found);assertFalse(engine.state().get("message").toString().contains(previous));
    }
    @Test public void permissionAndInstallerFailureUseAcceptedOperationAndIgnoreStaleCallbacks() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        String installId=(String)engine.state().get("diagnostic_id");
        engine.installPermissionRequired("install",null);
        UpdateDiagnostic permission=diagnostics.get(diagnostics.size()-1);
        assertEquals(1301,permission.errorCode);assertEquals(installId,permission.diagnosticId);
        assertEquals("permission",permission.stage);
        engine.check("new-check",false);String current=(String)engine.state().get("diagnostic_id");
        engine.installPermissionRequired("install",new SecurityException("sk-test"));
        engine.installLaunchFailed("install",new IOException("sk-test"));queue.run();
        assertEquals(current,engine.state().get("diagnostic_id"));
        engine.install(id(),"install2");queue.run();
        engine.installLaunchFailed("install2",new IOException("sk-test C:/private/key"));
        UpdateDiagnostic failed=diagnostics.get(diagnostics.size()-1);
        assertEquals(1302,failed.errorCode);assertEquals(engine.state().get("diagnostic_id"),failed.diagnosticId);
        assertFalse(engine.state().get("message").toString().contains("sk-test"));
    }
    @Test public void devicePolicyBlockIsExplicitAndDoesNotOfferAnotherPermissionLoop() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        engine.installPolicyBlocked("install");
        assertEquals("ready",engine.state().get("status"));assertTrue(engine.state().get("message").toString().contains("系统策略"));
        assertFalse(engine.state().get("message").toString().contains("打开设置"));
        UpdateDiagnostic failure=diagnostics.get(diagnostics.size()-1);assertEquals(1301,failure.errorCode);assertEquals("permission",failure.stage);
    }
    @Test public void failedCriticalInstallerPreferenceStillPreventsHandoff() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        prefs.failNumber="installerLease";
        UpdateFailure failure=assertThrows(UpdateFailure.class,()->engine.installSessionCommitted("install",73));
        assertEquals(1201,failure.code);assertEquals(false,engine.state().get("installerLease"));
        assertEquals("install-check",engine.state().get("status"));
        assertFalse(engine.installSessionCurrent(73));
        assertFalse(engine.state().get("message").toString().contains("已打开系统安装器"));
    }
    @Test public void downloadDiagnosticsArePhaseBoundedNotPerChunk() throws Exception {
        candidate();byte[] large=new byte[200000];
        network.bodies.put(UpdatePolicy.FEED_ROOT+"beta.json",feed(3,"beta",large).getBytes(StandardCharsets.UTF_8));
        network.bodies.put((String)UpdatePolicyTest.release(3,"beta").get("apkUrl"),large);
        engine.check("larger",false);queue.run();engine.download(id(),"large-download");
        String downloadId=(String)engine.state().get("diagnostic_id");queue.run();
        int count=0;for(UpdateDiagnostic event:diagnostics)if(event.diagnosticId.equals(downloadId))count++;
        assertTrue("No diagnostic record per download chunk",count<=5);assertEquals("ready",engine.state().get("status"));
    }
    @Test public void recoveryStorageFailureKeepsLeaseAndReportsItsOwnOperation() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();commit("install");
        Files.delete(new File(cache,"candidate.apk").toPath());start();
        Object previous=engine.state().get("diagnostic_id");prefs.failNumber="installerLease";
        engine.recover("recovery-write");
        assertEquals(true,engine.state().get("installerLease"));assertEquals("recovery",engine.state().get("status"));
        assertFalse(engine.download(id(),"blocked"));
        UpdateDiagnostic failure=diagnostics.get(diagnostics.size()-1);
        assertEquals(1201,failure.errorCode);assertEquals("write",failure.stage);
        assertEquals(engine.state().get("diagnostic_id"),failure.diagnosticId);assertNotEquals(previous,failure.diagnosticId);
        prefs.failNumber=null;engine.recoveryFailed("revoke-error",new SecurityException("sk-test C:/private"));
        failure=diagnostics.get(diagnostics.size()-1);assertEquals(1302,failure.errorCode);
        assertTrue(engine.state().get("message").toString().contains("仍被保护"));
        engine.recover("recovery-ok");assertEquals(false,engine.state().get("installerLease"));
    }

    @Test public void committedSystemSessionPersistsIdentityAndCannotBeSubmittedTwice() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);
        assertEquals(73,prefs.number("installSessionId"));assertEquals(2,prefs.number("requestedVersion"));
        assertEquals(true,engine.state().get("installerLease"));assertEquals(false,engine.state().get("ready"));
        assertEquals(true,engine.state().get("recovery"));assertEquals("installing",engine.state().get("status"));
        assertFalse(engine.install(id(),"duplicate"));
        engine.installSessionCommitted("install",74);
        assertEquals(73,prefs.number("installSessionId"));
    }

    @Test public void pendingConfirmationRecordsArrivalAndLaunchWithoutSensitivePayload() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);
        engine.installConfirmationArrived(73);
        engine.installConfirmationLaunched(73);
        UpdateDiagnostic arrived=diagnostics.get(diagnostics.size()-2);
        UpdateDiagnostic launched=diagnostics.get(diagnostics.size()-1);
        assertEquals("confirm",arrived.stage);assertEquals("started",arrived.outcome);
        assertEquals("confirm",launched.stage);assertEquals("ok",launched.outcome);
        assertFalse(arrived.toRecord().containsKey("session_id"));
        assertFalse(launched.toRecord().toString().contains("73"));
    }

    @Test public void confirmationLaunchFailureUsesInstallerCodeAndKeepsSystemSessionProtected() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);
        engine.installConfirmationArrived(73);
        engine.installConfirmationFailed(73,new SecurityException("content://private/session/73 sk-test"));
        UpdateDiagnostic failed=diagnostics.get(diagnostics.size()-1);
        assertEquals("confirm",failed.stage);assertEquals("error",failed.outcome);assertEquals(1302,failed.errorCode);
        assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("installerLease"));
        assertFalse(failed.toRecord().toString().contains("private"));assertFalse(failed.toRecord().toString().contains("sk-test"));
    }

    @Test public void cancelledAndFailedSystemSessionsReleaseLeaseButKeepVerifiedApkRetryable() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);
        engine.installResult(73,UpdateInstallStatusPolicy.map(3,false));
        assertEquals("ready",engine.state().get("status"));assertEquals(true,engine.state().get("ready"));
        assertEquals(false,engine.state().get("installerLease"));assertEquals(0,prefs.number("installSessionId"));
        assertTrue(new File(cache,"candidate.apk").isFile());

        engine.install(id(),"retry");queue.run();device.sessions.add(74);engine.installSessionCommitted("retry",74);
        engine.installResult(74,UpdateInstallStatusPolicy.map(6,false));
        assertEquals("ready",engine.state().get("status"));assertEquals(true,engine.state().get("ready"));
        assertTrue(engine.state().get("message").toString().contains("无法读写"));
        assertEquals(1201,diagnostics.get(diagnostics.size()-1).errorCode);
    }

    @Test public void staleSessionCallbackCannotMutateCurrentInstall() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);
        long revision=((Number)engine.state().get("revision")).longValue();
        engine.installResult(72,UpdateInstallStatusPolicy.map(3,false));
        assertEquals(revision,((Number)engine.state().get("revision")).longValue());
        assertEquals(73,prefs.number("installSessionId"));assertEquals(true,engine.state().get("installerLease"));
    }

    @Test public void terminalResultCannotReleaseLeaseWhenSystemSessionAbandonFails() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);device.failAbandon=true;
        engine.installResult(73,UpdateInstallStatusPolicy.map(3,false));
        assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("installerLease"));
        assertEquals(73,prefs.number("installSessionId"));assertEquals(false,engine.state().get("ready"));
    }

    @Test public void restartRecoversExistingSessionAndReleasesMissingSessionWithoutClaimingSuccess() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);start();
        assertEquals("recovery",engine.state().get("status"));assertEquals(false,engine.state().get("ready"));
        device.sessions.clear();start();
        assertEquals("ready",engine.state().get("status"));assertEquals(true,engine.state().get("ready"));
        assertEquals(false,engine.state().get("installerLease"));assertEquals(0,prefs.number("installSessionId"));
        assertNotEquals("updated",engine.state().get("status"));
    }

    @Test public void confirmedRecoveryAbandonsExactSystemSessionBeforeReleasingLease() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);start();engine.recover("recover-session");
        assertEquals(Arrays.asList(73),device.abandoned);assertEquals(false,engine.state().get("installerLease"));
        assertEquals("ready",engine.state().get("status"));assertEquals(true,engine.state().get("ready"));
    }

    @Test public void stagedSessionIdentitySurvivesProcessDeathBeforeCommit() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);assertTrue(engine.installSessionPrepared("install",73));
        assertEquals(73,prefs.number("installSessionId"));assertEquals(0,prefs.number("installerLease"));
        start();assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("installerLease"));
        engine.recover("discard-staged");assertEquals(Arrays.asList(73),device.abandoned);
        assertEquals(0,prefs.number("installSessionId"));assertEquals("ready",engine.state().get("status"));
    }

    @Test public void terminalCallbackDuringRestoreCannotResurrectClearedLease() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);
        device.duringSessionExists=()->engine.installResult(73,UpdateInstallStatusPolicy.map(3,false));
        start();assertEquals(false,engine.state().get("installerLease"));assertEquals(false,engine.state().get("recovery"));
        assertEquals(0,prefs.number("installSessionId"));assertEquals("ready",engine.state().get("status"));
    }

    @Test public void terminalCallbackDuringCacheVerificationCannotResurrectClearedLease() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionCommitted("install",73);
        device.duringVerify=()->engine.installResult(73,UpdateInstallStatusPolicy.map(3,false));
        start();assertEquals(false,engine.state().get("installerLease"));assertEquals(false,engine.state().get("recovery"));
        assertEquals(0,prefs.number("installSessionId"));assertEquals("ready",engine.state().get("status"));
    }

    @Test public void corruptMetadataCannotHidePersistedStagedSessionRecovery() throws Exception {
        candidate();engine.download(id(),"download");queue.run();engine.install(id(),"install");queue.run();
        device.sessions.add(73);engine.installSessionPrepared("install",73);
        Files.write(new File(cache,"candidate.json").toPath(),"invalid".getBytes(StandardCharsets.UTF_8));
        start();assertEquals("recovery",engine.state().get("status"));assertEquals(true,engine.state().get("installerLease"));
        assertEquals(true,engine.state().get("recovery"));assertEquals(73,prefs.number("installSessionId"));
    }
}
