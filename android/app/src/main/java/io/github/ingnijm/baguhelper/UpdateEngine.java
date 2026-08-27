package io.github.ingnijm.baguhelper;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.*;
import java.util.concurrent.Executor;
import java.util.function.*;

/** Process lifetime state machine; expensive work stays on its dedicated single executor. */
final class UpdateEngine {
    interface Preferences {
        long number(String key);boolean enabled();void putNumber(String key,long value);void enable(boolean value);
        String lastStatus();void lastStatus(String value);
    }
    interface Device { long installedCode() throws Exception;int sdk();List<String> abis();void verify(File file,UpdatePolicy.Release release)throws Exception; }
    private final String channel;private final File cache;private final Preferences prefs;private final Device device;
    private final UpdateIO io;private final Executor worker;private final LongSupplier clock;
    private final Consumer<Map<String,Object>> listener;private final BiConsumer<String,UpdatePolicy.Release> installer;
    private final BiConsumer<String,Throwable> diagnostic;
    private String status="restoring",message="正在检查更新缓存…",operationId="startup";
    private long revision,received;private UpdatePolicy.Release candidate;
    private boolean busy=true,ready,installerLease;private UpdateIO.Cancellation running;
    private final Set<String> installOperations = new HashSet<>();
    private final String noticeProcess = UUID.randomUUID().toString();
    private long checkGeneration;
    UpdateEngine(String channel,File cache,Preferences prefs,Device device,UpdateIO io,Executor worker,
            LongSupplier clock,Consumer<Map<String,Object>> listener,BiConsumer<String,UpdatePolicy.Release> installer) {
        this(channel,cache,prefs,device,io,worker,clock,listener,installer,(stage,failure)->{});
    }
    UpdateEngine(String channel,File cache,Preferences prefs,Device device,UpdateIO io,Executor worker,
            LongSupplier clock,Consumer<Map<String,Object>> listener,BiConsumer<String,UpdatePolicy.Release> installer,
            BiConsumer<String,Throwable> diagnostic) {
        UpdatePolicy.channels(channel);this.channel=channel;this.cache=cache;this.prefs=prefs;this.device=device;
        this.io=io;this.worker=worker;this.clock=clock;this.listener=listener;this.installer=installer;
        this.diagnostic=diagnostic;
        worker.execute(this::restore);
    }
    synchronized Map<String,Object> state() {
        Map<String,Object> out=new LinkedHashMap<>();out.put("operationId",operationId);out.put("revision",revision);
        out.put("status",status);out.put("message",message);out.put("busy",busy);out.put("enabled",prefs.enabled());
        out.put("lastAttempt",prefs.number("lastAttempt"));out.put("lastStatus",prefs.lastStatus());out.put("received",received);
        out.put("ready",ready);out.put("installerLease",installerLease);
        out.put("recovery",recoveryRequired());
        out.put("noticeId",noticeProcess+":"+checkGeneration);
        if(candidate!=null){Map<String,Object> c=new LinkedHashMap<>();c.put("id",candidate.id());c.put("versionName",candidate.versionName);c.put("versionCode",candidate.versionCode);c.put("size",candidate.size);c.put("notes",candidate.notes);out.put("candidate",c);}
        return out;
    }
    private synchronized void emit(String next,String text) {status=next;message=text;revision++;listener.accept(state());}
    private boolean recoveryRequired() {return installerLease&&!ready;}
    private void diagnostic(String stage,Throwable failure) {try{diagnostic.accept(stage,failure);}catch(Throwable ignored){/* Logging must not change update behavior. */}}
    synchronized void automatic(boolean enabled,String op){UpdatePolicy.validateOperationId(op);prefs.enable(enabled);if(!busy)operationId=op;emit(status,enabled?"已开启自动检查（仅检查，不自动下载）。":"已关闭自动检查。");}
    synchronized boolean check(String op,boolean automatic) {
        UpdatePolicy.validateOperationId(op);if(busy)return false;
        if(automatic&&!UpdatePolicy.shouldAutomaticallyCheck(prefs.enabled(),clock.getAsLong(),prefs.number("lastAttempt")))return false;
        operationId=op;busy=true;prefs.putNumber("lastAttempt",clock.getAsLong());
        UpdateIO.Cancellation token=new UpdateIO.Cancellation();running=token;emit("checking","正在检查更新…");
        worker.execute(()->checkWork(token));return true;
    }
    private void checkWork(UpdateIO.Cancellation token) {
        List<UpdatePolicy.Release> releases=new ArrayList<>();int errors=0;
        for(String feed:UpdatePolicy.channels(channel))try{releases.add(io.feed(feed,token));}catch(Exception failure){diagnostic("check",failure);errors++;}
        try {
            UpdatePolicy.Release found=UpdatePolicy.choose(releases,device.installedCode(),device.sdk(),device.abis());
            synchronized(this) {
                if(running!=token)return;
                checkGeneration++;
                String result=errors==0?(found==null?"latest":"available"):(errors==UpdatePolicy.channels(channel).size()?"error":"partial-error");
                prefs.lastStatus(result);
                // A handed-off APK is immutable until the installer returns, or a later
                // process observes the actual installed version.
                if(found!=null && !installerLease && (candidate==null||!candidate.id().equals(found.id()))) {
                    candidate=found;ready=false;received=0;discardApk();saveCandidate();
                }
                busy=false;running=null;
                if(recoveryRequired()){emit("recovery","安装缓存不可用。请先关闭系统安装器，再点击恢复更新。");return;}
                if(errors>0)emit(candidate!=null?(ready?"ready":"available"):result,"检查未完整成功，请稍后重试。"+(candidate!=null?"保留已验证的候选版本。":"无法确认是否为最新版本。"));
                else emit(candidate!=null?(ready?"ready":"available"):"latest",candidate!=null?"发现可用版本；由你决定下载和安装。":"当前没有兼容的新版本。");
            }
        } catch(Exception failure){diagnostic("check",failure);synchronized(this){if(running==token){busy=false;running=null;checkGeneration++;prefs.lastStatus("error");emit(recoveryRequired()?"recovery":candidate!=null?(ready?"ready":"available"):"error","检查失败，无法确认是否为最新版本。");}}}
    }
    synchronized boolean download(String id,String op) {
        UpdatePolicy.validateOperationId(op);if(busy||installerLease||candidate==null||!candidate.id().equals(id)||ready)return false;
        operationId=op;busy=true;received=0;UpdatePolicy.Release target=candidate;
        UpdateIO.Cancellation token=new UpdateIO.Cancellation();running=token;emit("downloading","正在下载，切到后台将取消；不会自动安装。");
        worker.execute(()->downloadWork(target,token));return true;
    }
    private void downloadWork(UpdatePolicy.Release target,UpdateIO.Cancellation token) {
        File part=new File(cache,"candidate.part");
        try {
            ensureCache();io.download(target,part,token,n->{synchronized(this){if(running==token&&!token.cancelled()){received=n;emit("downloading","正在下载…");}}});
            token.check();device.verify(part,target);token.check();
            synchronized(this) {
                if(running!=token||token.cancelled())throw new IOException("Cancelled");
                Files.move(part.toPath(),apk().toPath(),StandardCopyOption.REPLACE_EXISTING);
                ready=true;saveCandidate();busy=false;running=null;emit("ready","下载及完整性检查通过。系统安装器将进行最终签名验证。");
            }
        } catch(Exception failure) {
            diagnostic(token.cancelled()?"cancelled":"download",failure);
            synchronized(this){if(running==token){busy=false;running=null;ready=false;emit("available",token.cancelled()?"下载已取消，可重新下载。":"下载或校验失败，未进入安装；可重新下载。");}}
        } finally { try{Files.deleteIfExists(part.toPath());}catch(IOException failure){diagnostic("write",failure);} }
    }
    synchronized void cancel(String op) {
        UpdatePolicy.validateOperationId(op);
        if(running==null||!(status.equals("downloading")||status.equals("cancelling")))return;
        operationId=op;running.cancel();emit("cancelling","正在取消下载…");
    }
    synchronized void recover(String op) {
        UpdatePolicy.validateOperationId(op);
        if(!busy&&recoveryRequired()){
            // Android boundary has confirmed the user closed the installer and revoked
            // the exact URI grant. Keep the existing APK bytes untouched during recovery.
            prefs.putNumber("installerLease",0);installerLease=false;operationId=op;
            emit(candidate==null?"idle":"available","已恢复更新操作，原安装文件保持不变。可重新检查更新或下载。");return;
        }
    }
    synchronized boolean install(String id,String op) {
        UpdatePolicy.validateOperationId(op);if(busy||!ready||candidate==null||!candidate.id().equals(id))return false;
        // A timed-out guard may still deliver a callback. Never reuse its identity in this process.
        if (!installOperations.add(op)) return false;
        operationId=op;busy=true;UpdatePolicy.Release target=candidate;emit("install-check","正在重新校验安装文件和使用状态…");
        worker.execute(()->{
            try{UpdateIO.verifyBytes(apk(),target);device.verify(apk(),target);
                synchronized(this){if(installCurrent(op,id))installer.accept(op,target);}
            }catch(Exception failure){diagnostic("verify",failure);synchronized(this){if(installCurrent(op,id)){busy=false;ready=false;emit(recoveryRequired()?"recovery":"available",recoveryRequired()?"安装缓存不可用。请先关闭系统安装器，再点击恢复更新。":"安装文件不可用或版本已改变，请重新检查更新。");}}}
        });return true;
    }
    synchronized boolean installCurrent(String op,String id){return busy&&"install-check".equals(status)&&operationId.equals(op)&&candidate!=null&&candidate.id().equals(id);}
    synchronized void installBlocked(String op,String reason){if(operationId.equals(op)&&"install-check".equals(status)){busy=false;emit("ready",reason);}}
    synchronized void installLaunched(String op){if(operationId.equals(op)&&"install-check".equals(status)){prefs.putNumber("requestedVersion",candidate.versionCode);prefs.putNumber("installerLease",candidate.versionCode);installerLease=true;busy=false;emit("ready","已打开系统安装器；取消后可再次点击安装。更新成功将在下次启动核实。");}}
    synchronized void installerReturned(){
        prefs.putNumber("installerLease",0);installerLease=false;
        if(!busy)emit(ready?"ready":candidate!=null?"available":"idle","已从安装器返回；如未安装，可再次点击安装。成功状态将在下次启动核实。");
    }
    synchronized void installLaunchFailed(String op){
        if(!operationId.equals(op))return;
        prefs.putNumber("installerLease",0);installerLease=false;busy=false;
        emit(ready?"ready":"available","无法打开系统安装器，请核对安装权限或稍后再试。");
    }
    private void restore() {
        try {
            ensureCache();Files.deleteIfExists(new File(cache,"candidate.part").toPath());
            long requested=prefs.number("requestedVersion");
            if(requested>0&&device.installedCode()>=requested){synchronized(this){prefs.putNumber("requestedVersion",0);prefs.putNumber("installerLease",0);discardApk();Files.deleteIfExists(new File(cache,"candidate.json").toPath());busy=false;emit("updated","已核实本机安装版本，更新成功。");}return;}
            boolean restoredLease=prefs.number("installerLease")>0;
            // Cache eviction does not revoke an installer's URI grant. Keep even a
            // missing-file lease until confirmed recovery revokes that exact grant.
            UpdatePolicy.Release restoredCandidate=null;
            boolean restoredReady=false;
            File metadata=new File(cache,"candidate.json");
            if(metadata.isFile()) {
                byte[] bytes;try(InputStream input=new FileInputStream(metadata)){bytes=UpdateIO.readBounded(input,UpdatePolicy.MAX_FEED,new UpdateIO.Cancellation());}
                Map<String,Object> envelope=UpdateIO.parse(new String(bytes,StandardCharsets.UTF_8));
                Object cachedChannel=envelope.get("channel");
                if(!(cachedChannel instanceof String)||!UpdatePolicy.channels(channel).contains(cachedChannel))throw new IOException("Invalid cache channel");
                UpdatePolicy.Release restored=UpdatePolicy.parseFeed(envelope,(String)cachedChannel);
                restoredCandidate=UpdatePolicy.choose(Collections.singletonList(restored),device.installedCode(),device.sdk(),device.abis());
                if(restoredCandidate!=null){try{UpdateIO.verifyBytes(apk(),restoredCandidate);device.verify(apk(),restoredCandidate);restoredReady=true;}catch(Exception failure){diagnostic("verify",failure);if(!restoredLease)discardApk();}}
            }
            synchronized(this){candidate=restoredCandidate;ready=restoredReady;installerLease=restoredLease;busy=false;emit(recoveryRequired()?"recovery":candidate==null?"idle":ready?"ready":"available",recoveryRequired()?"安装缓存不可用。请先关闭系统安装器，再点击恢复更新。":candidate==null?"可手动检查更新。":ready?"已重新校验缓存安装包，可点击安装。":"安装缓存不可用，可重新下载。");}
        }catch(Exception failure){diagnostic("initialize",failure);synchronized(this){candidate=null;ready=false;installerLease=prefs.number("installerLease")>0;busy=false;emit(installerLease?"recovery":"error",installerLease?"更新缓存说明损坏。请先关闭系统安装器，再点击恢复更新。":"更新缓存不可用，请重新检查更新。");}}
    }
    private void ensureCache()throws IOException{if(!cache.isDirectory()&&!cache.mkdirs())throw new IOException("Cannot create update cache");}
    private File apk(){return new File(cache,"candidate.apk");}
    private void discardApk()throws IOException{Files.deleteIfExists(apk().toPath());}
    private void saveCandidate()throws IOException{
        ensureCache();Map<String,Object> envelope=new LinkedHashMap<>();envelope.put("schema_version",1);envelope.put("channel",candidate.channel);envelope.put("release",candidate.toMap());
        File temp=new File(cache,"candidate.json.tmp");
        try(FileOutputStream output=new FileOutputStream(temp)){output.write(UpdateIO.json(envelope).getBytes(StandardCharsets.UTF_8));output.getFD().sync();}
        Files.move(temp.toPath(),new File(cache,"candidate.json").toPath(),StandardCopyOption.REPLACE_EXISTING);
    }
    static boolean canInstall(boolean foreground,boolean documentOperation,boolean documentWorking,boolean pendingArchive,
            boolean nativeSpeech,boolean openSession,boolean grading,boolean webFile,boolean webSpeech) {
        return foreground&&!documentOperation&&!documentWorking&&!pendingArchive&&!nativeSpeech&&!openSession&&!grading&&!webFile&&!webSpeech;
    }
}
