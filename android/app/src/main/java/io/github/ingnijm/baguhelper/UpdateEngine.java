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
        long number(String key); boolean enabled(); void putNumber(String key,long value); void enable(boolean value);
        String lastStatus(); void lastStatus(String value);
        String lastCheck(); void lastCheck(String value);
    }
    interface Device {
        long installedCode() throws Exception; int sdk(); List<String> abis();
        void verify(File file,UpdatePolicy.Release release)throws Exception;
        default boolean installSessionExists(int sessionId)throws Exception{return false;}
        default void abandonInstallSession(int sessionId)throws Exception{}
    }
    /** Captured by every worker; cancel bridge IDs never change diagnostic ownership. */
    private static final class Operation {
        final String bridgeId, diagnosticId = "n_" + UUID.randomUUID().toString().replace("-", "");
        final long startedAt;
        final UpdateIO.Cancellation cancellation = new UpdateIO.Cancellation();
        Operation(String bridgeId,long now) { this.bridgeId=bridgeId; startedAt=now; }
    }
    private final String channel; private final File cache; private final Preferences prefs; private final Device device;
    private final UpdateIO io; private final Executor worker; private final LongSupplier clock;
    private final Consumer<Map<String,Object>> listener; private final BiConsumer<String,UpdatePolicy.Release> installer;
    private final Consumer<UpdateDiagnostic> diagnostic;
    private String status="restoring",message="正在检查更新缓存…",operationId="startup",diagnosticId;
    private long revision,received; private UpdatePolicy.Release candidate;
    private boolean busy=true,ready,installerLease,summaryWriteReported;
    private Operation active, running;
    private UpdateFailure lastFailure;
    private UpdateCheckSummary lastCheck=UpdateCheckSummary.unknown();
    private final Set<String> installOperations = new HashSet<>();
    private final String noticeProcess = UUID.randomUUID().toString();
    private long checkGeneration;

    UpdateEngine(String channel,File cache,Preferences prefs,Device device,UpdateIO io,Executor worker,
            LongSupplier clock,Consumer<Map<String,Object>> listener,BiConsumer<String,UpdatePolicy.Release> installer) {
        this(channel,cache,prefs,device,io,worker,clock,listener,installer,event->{});
    }
    UpdateEngine(String channel,File cache,Preferences prefs,Device device,UpdateIO io,Executor worker,
            LongSupplier clock,Consumer<Map<String,Object>> listener,BiConsumer<String,UpdatePolicy.Release> installer,
            Consumer<UpdateDiagnostic> diagnostic) {
        UpdatePolicy.channels(channel); this.channel=channel; this.cache=cache; this.prefs=prefs; this.device=device;
        this.io=io; this.worker=worker; this.clock=clock; this.listener=listener; this.installer=installer; this.diagnostic=diagnostic;
        worker.execute(this::restore);
    }
    synchronized Map<String,Object> state() {
        Map<String,Object> out=new LinkedHashMap<>(); out.put("operationId",operationId); out.put("revision",revision);
        out.put("status",status); out.put("message",message+(lastFailure!=null&&diagnosticId!=null?"\n反馈编号："+diagnosticId:""));
        out.put("busy",busy); out.put("enabled",prefs.enabled());
        out.put("lastAttempt",prefs.number("lastAttempt")); out.put("lastStatus",prefs.lastStatus()); out.put("received",received);
        out.put("lastCheck",lastCheck.toMap());
        if(diagnosticId!=null)out.put("diagnostic_id",diagnosticId);
        out.put("ready",ready); out.put("installerLease",installerLease); out.put("recovery",recoveryRequired());
        out.put("noticeId",noticeProcess+":"+checkGeneration);
        if(candidate!=null){Map<String,Object> c=new LinkedHashMap<>();c.put("id",candidate.id());c.put("versionName",candidate.versionName);c.put("versionCode",candidate.versionCode);c.put("size",candidate.size);c.put("notes",candidate.notes);out.put("candidate",c);}
        return out;
    }
    private synchronized void emit(String next,String text) { status=next; message=text; revision++; listener.accept(state()); }
    private boolean recoveryRequired() { return installerLease; }
    private Operation accept(String op) {
        Operation context=new Operation(op,clock.getAsLong()); active=context; operationId=op;
        diagnosticId=context.diagnosticId; lastFailure=null; summaryWriteReported=false; return context;
    }
    private void diagnostic(Operation context,String stage,String outcome,String feed,long start,UpdateFailure failure) {
        try { long now=clock.getAsLong(); diagnostic.accept(new UpdateDiagnostic(stage,outcome,feed,context.diagnosticId,now,Math.max(0,now-start),failure)); }
        catch(Throwable ignored) { /* Logging must not change update behavior. */ }
    }
    private synchronized void rememberFailure(Operation context,UpdateFailure failure) {
        if(active==context){lastFailure=failure;diagnosticId=context.diagnosticId;}
    }
    private void persistSummary(Operation context) {
        try { prefs.lastCheck(lastCheck.json()); }
        catch(Throwable failure) {
            // Only this non-critical new preference is best effort. Installer state is not.
            if(!summaryWriteReported){summaryWriteReported=true;diagnostic(context,"write","error",null,context.startedAt,UpdateFailure.at(UpdateFailure.STORAGE,failure));}
        }
    }
    synchronized void automatic(boolean enabled,String op) {
        UpdatePolicy.validateOperationId(op); prefs.enable(enabled); if(!busy)operationId=op;
        emit(status,enabled?"已开启自动检查（仅检查，不自动下载）。":"已关闭自动检查。");
    }
    synchronized boolean check(String op,boolean automatic) {
        UpdatePolicy.validateOperationId(op); if(busy)return false;
        if(automatic&&!UpdatePolicy.shouldAutomaticallyCheck(prefs.enabled(),clock.getAsLong(),prefs.number("lastAttempt")))return false;
        Operation context=accept(op); busy=true; running=context;
        lastCheck=UpdateCheckSummary.start(context.diagnosticId,context.startedAt,UpdatePolicy.channels(channel)); persistSummary(context);
        diagnostic(context,"check","started",null,context.startedAt,null);
        try { prefs.putNumber("lastAttempt",context.startedAt); }
        catch(RuntimeException failure){checkFailed(context,UpdateFailure.at(UpdateFailure.STORAGE,failure));return true;}
        emit("checking","正在检查更新…");
        try { worker.execute(()->checkWork(context)); }
        catch(RuntimeException failure){checkFailed(context,UpdateFailure.at(UpdateFailure.UNKNOWN,failure));}
        return true;
    }
    private void checkWork(Operation context) {
        try {
            long installed=installedCode(); int sdk=device.sdk(); List<String> abis=device.abis();
            List<UpdatePolicy.Release> releases=new ArrayList<>(); int errors=0; UpdateFailure firstFailure=null;
            for(String feed:UpdatePolicy.channels(channel)) {
                long start=clock.getAsLong();
                synchronized(this){if(running!=context)return;lastCheck=lastCheck.channel(feed,"checking",0,null,0);}
                diagnostic(context,"check","started",feed,start,null);
                try {
                    UpdatePolicy.Release release=io.feed(feed,context.cancellation); releases.add(release);
                    String outcome=release==null?"empty":release.versionCode<=installed?"no-update":
                        release.minSdk>sdk||!abis.contains(release.abi)?"incompatible":"available";
                    synchronized(this){if(running!=context)return;lastCheck=lastCheck.channel(feed,outcome,0,200,clock.getAsLong()-start);persistSummary(context);}
                    diagnostic(context,"check","ok",feed,start,null);
                } catch(Exception failure) {
                    UpdateFailure error=UpdateFailure.at(UpdateFailure.UNKNOWN,failure); errors++; if(firstFailure==null)firstFailure=error;
                    synchronized(this){if(running!=context)return;lastCheck=lastCheck.channel(feed,"error",error.code,error.httpStatus,clock.getAsLong()-start);persistSummary(context);}
                    diagnostic(context,"check","error",feed,start,error);
                }
            }
            UpdatePolicy.Release found;
            try { found=UpdatePolicy.choose(releases,installed,sdk,abis); }
            catch(IllegalArgumentException failure){throw UpdateFailure.at(UpdateFailure.MANIFEST,failure);}
            synchronized(this) {
                if(running!=context)return;
                String result=errors==0?(found==null?"latest":"available"):(errors==UpdatePolicy.channels(channel).size()?"error":"partial-error");
                // A handed-off APK is immutable until installer return or verified upgrade.
                if(found!=null&&!installerLease&&(candidate==null||!candidate.id().equals(found.id()))) {
                    candidate=found;ready=false;received=0;discardApk();saveCandidate();
                }
                try{prefs.lastStatus(result);}catch(RuntimeException failure){throw UpdateFailure.at(UpdateFailure.STORAGE,failure);}
                lastCheck=lastCheck.finish(result,firstFailure==null?0:firstFailure.code,clock.getAsLong());persistSummary(context);
                rememberFailure(context,firstFailure); checkGeneration++; busy=false; running=null;
                diagnostic(context,"check",errors==0?"ok":"error",null,context.startedAt,firstFailure);
                String text=lastCheck.description()+(errors>0&&candidate!=null?" 保留已验证的候选版本。":"");
                if(recoveryRequired())emit("recovery","存在尚未结束的系统安装会话，请先处理系统确认界面或点击恢复更新。 "+text);
                else emit(candidate!=null?(ready?"ready":"available"):result,text);
            }
        } catch(Exception failure) { checkFailed(context,UpdateFailure.at(UpdateFailure.UNKNOWN,failure)); }
    }
    private synchronized void checkFailed(Operation context,UpdateFailure failure) {
        if(running!=context)return;
        rememberFailure(context,failure);lastCheck=lastCheck.finish("error",failure.code,clock.getAsLong());persistSummary(context);
        // Saving the old status cannot strand the in-memory error state; no install handoff occurs here.
        try{prefs.lastStatus("error");}catch(RuntimeException ignored){}
        checkGeneration++;busy=false;running=null;diagnostic(context,"check","error",null,context.startedAt,failure);
        emit(recoveryRequired()?"recovery":candidate!=null?(ready?"ready":"available"):"error",lastCheck.description());
    }
    synchronized boolean download(String id,String op) {
        UpdatePolicy.validateOperationId(op);if(busy||installerLease||candidate==null||!candidate.id().equals(id)||ready)return false;
        Operation context=accept(op);busy=true;received=0;UpdatePolicy.Release target=candidate;running=context;
        diagnostic(context,"download","started",target.channel,context.startedAt,null);
        emit("downloading","正在下载，切到后台将取消；不会自动安装。");
        try{worker.execute(()->downloadWork(target,context));}
        catch(RuntimeException failure){downloadFailed(target,context,UpdateFailure.at(UpdateFailure.UNKNOWN,failure));}
        return true;
    }
    private void downloadWork(UpdatePolicy.Release target,Operation context) {
        File part=new File(cache,"candidate.part");
        try {
            ensureCache();io.download(target,part,context.cancellation,n->{synchronized(this){if(running==context&&!context.cancellation.cancelled()){received=n;emit("downloading","正在下载…");}}});
            context.cancellation.check();
            diagnostic(context,"verify","started",target.channel,context.startedAt,null);
            verifyDevice(part,target);context.cancellation.check();
            synchronized(this) {
                if(running!=context||context.cancellation.cancelled())throw new UpdateIO.Cancelled();
                move(part,apk());ready=true;saveCandidate();busy=false;running=null;
                diagnostic(context,"verify","ok",target.channel,context.startedAt,null);
                diagnostic(context,"download","ok",target.channel,context.startedAt,null);
                emit("ready","下载及完整性检查通过。系统安装器将进行最终签名验证。");
            }
        } catch(Exception failure) { downloadFailed(target,context,failure);
        } finally { try{Files.deleteIfExists(part.toPath());}catch(IOException | SecurityException failure){diagnostic(context,"write","error",target.channel,context.startedAt,UpdateFailure.at(UpdateFailure.STORAGE,failure));} }
    }
    private synchronized void downloadFailed(UpdatePolicy.Release target,Operation context,Throwable failure) {
        boolean cancelled=context.cancellation.cancelled()||failure instanceof UpdateIO.Cancelled;
        UpdateFailure error=cancelled?null:UpdateFailure.at(UpdateFailure.UNKNOWN,failure);
        diagnostic(context,"download",cancelled?"cancelled":"error",target.channel,context.startedAt,error);
        if(running==context){rememberFailure(context,error);busy=false;running=null;ready=false;
            emit("available",cancelled?"下载已取消，可重新下载。":UpdateFailure.reason(error.code,error.httpStatus)+"；未进入安装，可重新下载。");}
    }
    synchronized void cancel(String op) {
        UpdatePolicy.validateOperationId(op);
        if(running==null||!(status.equals("downloading")||status.equals("cancelling")))return;
        operationId=op;running.cancellation.cancel();emit("cancelling","正在取消下载…");
    }
    synchronized void recover(String op) {
        UpdatePolicy.validateOperationId(op);
        if(!busy&&recoveryRequired()){
            Operation context=accept(op);diagnostic(context,"verify","started",null,context.startedAt,null);
            int sessionId=(int)prefs.number("installSessionId");
            try{
                if(sessionId>0)device.abandonInstallSession(sessionId);
                clearInstallPersistence();
            } catch(Exception failure){
                UpdateFailure error=UpdateFailure.at(failure instanceof RuntimeException?UpdateFailure.STORAGE:UpdateFailure.INSTALLER,failure);
                rememberFailure(context,error);diagnostic(context,error.code==UpdateFailure.STORAGE?"write":"install","error",null,context.startedAt,error);
                emit("recovery","无法废弃遗留的系统安装会话，更新操作仍被保护；请稍后重试。");return;
            }
            installerLease=false;ready=false;
            if(candidate!=null&&apk().isFile())try{UpdateIO.verifyBytes(apk(),candidate);verifyDevice(apk(),candidate);ready=true;}
            catch(Exception failure){UpdateFailure error=UpdateFailure.at(UpdateFailure.UNKNOWN,failure);rememberFailure(context,error);diagnostic(context,"verify","error",candidate.channel,context.startedAt,error);}
            if(ready)diagnostic(context,"verify","ok",candidate.channel,context.startedAt,null);
            emit(candidate==null?"idle":ready?"ready":"available",ready?"已废弃遗留安装会话并重新校验安装包，可再次安装。":"已废弃遗留安装会话；安装文件需重新下载或校验。");
        }
    }
    synchronized void recoveryFailed(String op,Throwable failure) {
        UpdatePolicy.validateOperationId(op);
        if(busy||!recoveryRequired())return;
        Operation context=accept(op);UpdateFailure error=UpdateFailure.at(UpdateFailure.INSTALLER,failure);
        rememberFailure(context,error);diagnostic(context,"install","error",null,context.startedAt,error);
        emit("recovery","无法废弃遗留的系统安装会话，更新操作仍被保护；请稍后重试。");
    }
    synchronized boolean install(String id,String op) {
        UpdatePolicy.validateOperationId(op);if(busy||!ready||candidate==null||!candidate.id().equals(id))return false;
        // A timed-out guard may still deliver a callback. Never reuse its identity.
        if(!installOperations.add(op))return false;
        Operation context=accept(op);busy=true;UpdatePolicy.Release target=candidate;
        diagnostic(context,"install","started",target.channel,context.startedAt,null);
        emit("install-check","正在重新校验安装文件和使用状态…");
        try{worker.execute(()->{
            try{diagnostic(context,"verify","started",target.channel,context.startedAt,null);UpdateIO.verifyBytes(apk(),target);verifyDevice(apk(),target);
                boolean dispatch;
                synchronized(this){dispatch=installCurrent(op,id);if(dispatch)diagnostic(context,"verify","ok",target.channel,context.startedAt,null);}
                if(dispatch)installer.accept(op,target);
            }catch(Exception failure){installationVerifyFailed(context,target,failure);}
        });}catch(RuntimeException failure){installationVerifyFailed(context,target,failure);}
        return true;
    }
    private synchronized void installationVerifyFailed(Operation context,UpdatePolicy.Release target,Throwable failure) {
        UpdateFailure error=UpdateFailure.at(UpdateFailure.UNKNOWN,failure);
        diagnostic(context,"verify","error",target.channel,context.startedAt,error);
        if(installCurrent(context.bridgeId,target.id())){rememberFailure(context,error);busy=false;ready=false;
            emit(recoveryRequired()?"recovery":"available",UpdateFailure.reason(error.code,error.httpStatus)+"。"+
                (recoveryRequired()?"请先处理系统确认界面或点击恢复更新。":"请重新检查更新。"));}
    }
    synchronized boolean installCurrent(String op,String id){return busy&&"install-check".equals(status)&&operationId.equals(op)&&candidate!=null&&candidate.id().equals(id);}
    synchronized void installBlocked(String op,String reason){finishInstallGuard(op,reason,"blocked");}
    synchronized void cancelInstallation(String op,String reason){finishInstallGuard(op,reason,"cancelled");}
    private void finishInstallGuard(String op,String reason,String outcome){
        if(active!=null&&active.bridgeId.equals(op)&&operationId.equals(op)&&"install-check".equals(status)){
            diagnostic(active,"install",outcome,candidate.channel,active.startedAt,null);busy=false;emit("ready",reason);
        }
    }
    synchronized void installPermissionRequired(String op,Throwable failure) {
        installPermission(op,failure,failure==null?"需要允许本应用安装更新。授权返回后请再次点击安装。":"系统授权设置不可用，请稍后再试。");
    }
    synchronized void installPolicyBlocked(String op) {
        installPermission(op,new SecurityException("Install source disabled by policy"),"系统策略禁止此来源安装应用；请联系设备管理员或调整系统安全策略。");
    }
    private void installPermission(String op,Throwable failure,String text) {
        if(active==null||!active.bridgeId.equals(op)||!operationId.equals(op)||!("install-check".equals(status)||"ready".equals(status)))return;
        UpdateFailure error=UpdateFailure.at(UpdateFailure.PERMISSION,failure);rememberFailure(active,error);
        diagnostic(active,"permission","error",candidate.channel,active.startedAt,error);busy=false;
        emit("ready",text);
    }
    synchronized boolean installPermissionCurrent(String op) {
        return active!=null&&active.bridgeId.equals(op)&&operationId.equals(op)&&"ready".equals(status)&&lastFailure!=null&&lastFailure.code==UpdateFailure.PERMISSION;
    }
    synchronized boolean installSessionPrepared(String op,int sessionId)throws UpdateFailure {
        if(sessionId<=0)throw new IllegalArgumentException("Invalid install session");
        boolean current=operationId.equals(op)&&"install-check".equals(status);
        long existing;
        try{existing=prefs.number("installSessionId");}
        catch(RuntimeException failure){throw UpdateFailure.at(UpdateFailure.STORAGE,failure);}
        if(existing>0&&existing!=sessionId)throw new UpdateFailure(UpdateFailure.INSTALLER);
        try{
            long version=candidate==null?0:candidate.versionCode;
            prefs.putNumber("requestedVersion",version);
            prefs.putNumber("installSessionId",sessionId);
            prefs.putNumber("installerLease",current?0:version>0?version:1);
        } catch(RuntimeException failure){
            try{clearInstallPersistence();}catch(RuntimeException ignored){}
            throw UpdateFailure.at(UpdateFailure.STORAGE,failure);
        }
        if(!current){installerLease=true;ready=false;busy=false;
            emit("recovery","安装准备期间应用状态已变化；系统安装会话已被保护，正在等待安全废弃。");}
        return current;
    }
    synchronized boolean installSessionCommitted(String op,int sessionId)throws UpdateFailure {
        if(sessionId<=0)throw new IllegalArgumentException("Invalid install session");
        if(operationId.equals(op)&&"install-check".equals(status)){
            try{
                if(prefs.number("installSessionId")!=sessionId&&!installSessionPrepared(op,sessionId))return false;
                prefs.putNumber("installerLease",candidate.versionCode);
            } catch(RuntimeException failure){
                try{clearInstallPersistence();}catch(RuntimeException ignored){}
                throw UpdateFailure.at(UpdateFailure.STORAGE,failure);
            }
            installerLease=true;ready=false;busy=false;diagnostic(active,"install","ok",candidate.channel,active.startedAt,null);
            emit("installing","已提交系统安装会话；更新成功将在下次启动核实。");
            return true;
        }
        return false;
    }
    synchronized boolean installSessionCurrent(int sessionId){
        return sessionId>0&&prefs.number("installSessionId")==sessionId&&prefs.number("installerLease")>0;
    }
    synchronized void installConfirmationArrived(int sessionId) {
        if(!installSessionCurrent(sessionId))return;
        Operation context=installCallbackContext();
        diagnostic(context,"confirm","started",candidate==null?null:candidate.channel,context.startedAt,null);
    }
    synchronized void installConfirmationLaunched(int sessionId) {
        if(!installSessionCurrent(sessionId))return;
        Operation context=installCallbackContext();
        diagnostic(context,"confirm","ok",candidate==null?null:candidate.channel,context.startedAt,null);
    }
    synchronized void installConfirmationFailed(int sessionId,Throwable failure) {
        if(!installSessionCurrent(sessionId))return;
        Operation context=installCallbackContext();
        UpdateFailure error=UpdateFailure.at(UpdateFailure.INSTALLER,failure);rememberFailure(context,error);
        diagnostic(context,"confirm","error",candidate==null?null:candidate.channel,context.startedAt,error);
        installerLease=true;ready=false;busy=false;
        emit("recovery","无法打开系统安装确认界面；安装会话仍受保护，请恢复更新后重试。");
    }
    private Operation installCallbackContext() {
        if(active==null){active=new Operation("install_callback",clock.getAsLong());diagnosticId=active.diagnosticId;operationId=active.bridgeId;}
        return active;
    }
    synchronized void installSessionDiscarded(String op,int sessionId) {
        if(sessionId>0&&operationId.equals(op)&&prefs.number("installSessionId")==sessionId&&prefs.number("installerLease")==0)
            clearInstallPersistence();
    }
    synchronized void installRejectedSessionDiscarded(int sessionId) {
        if(sessionId<=0||prefs.number("installSessionId")!=sessionId)return;
        clearInstallPersistence();installerLease=false;busy=false;ready=candidate!=null&&apk().isFile();
        emit(candidate==null?"idle":ready?"ready":"available",ready?"已安全废弃过期的系统安装会话，可再次安装。":"已安全废弃过期的系统安装会话。");
    }
    synchronized void installCommitUncertain(String op,Throwable failure) {
        int sessionId=(int)prefs.number("installSessionId");
        if(sessionId<=0||!operationId.equals(op))return;
        retainInstallRecovery(active,failure,"系统安装会话的提交结果不确定；更新操作仍被保护，请稍后恢复。");
    }
    synchronized void installAbandonFailed(String op,int sessionId,Throwable failure) {
        if(sessionId<=0||prefs.number("installSessionId")!=sessionId)return;
        retainInstallRecovery(active,failure,"无法废弃系统安装会话，更新操作仍被保护；请稍后恢复。");
    }
    private void retainInstallRecovery(Operation context,Throwable failure,String text) {
        if(context==null)context=new Operation("install_recovery",clock.getAsLong());
        UpdateFailure error=UpdateFailure.at(UpdateFailure.INSTALLER,failure);
        try {
            long lease=prefs.number("installerLease");
            if(lease<=0){lease=prefs.number("requestedVersion");prefs.putNumber("installerLease",lease>0?lease:1);}
        } catch(RuntimeException storage){error=UpdateFailure.at(UpdateFailure.STORAGE,storage);}
        rememberFailure(context,error);diagnostic(context,"install","error",candidate==null?null:candidate.channel,context.startedAt,error);
        installerLease=true;ready=false;busy=false;emit("recovery",text);
    }
    synchronized void installResult(int sessionId,UpdateInstallStatusPolicy.Result result){
        if(result==null||result.kind==UpdateInstallStatusPolicy.Kind.PENDING||!installSessionCurrent(sessionId))return;
        Operation context=installCallbackContext();
        if(result.kind==UpdateInstallStatusPolicy.Kind.SUCCESS){
            busy=false;ready=false;diagnostic(context,"install","ok",candidate==null?null:candidate.channel,context.startedAt,null);
            emit("installing","系统已完成安装；将在应用下次启动时核实实际版本。");return;
        }
        try{device.abandonInstallSession(sessionId);}
        catch(Exception failure){retainInstallRecovery(context,failure,"无法废弃已结束的系统安装会话，更新操作仍被保护；请稍后恢复。");return;}
        try{clearInstallPersistence();}
        catch(RuntimeException failure){
            UpdateFailure error=UpdateFailure.at(UpdateFailure.STORAGE,failure);rememberFailure(context,error);
            diagnostic(context,"write","error",null,context.startedAt,error);installerLease=true;busy=false;ready=false;
            emit("recovery","无法保存安装会话结果，更新操作仍被保护；请稍后恢复。");return;
        }
        installerLease=false;busy=false;ready=candidate!=null&&apk().isFile();
        if(result.kind==UpdateInstallStatusPolicy.Kind.CANCELLED){
            diagnostic(context,"install","cancelled",candidate==null?null:candidate.channel,context.startedAt,null);
            emit(ready?"ready":candidate==null?"idle":"available","已取消系统安装，可再次点击安装。");return;
        }
        UpdateFailure error=new UpdateFailure(result.errorCode);rememberFailure(context,error);
        diagnostic(context,"install","error",candidate==null?null:candidate.channel,context.startedAt,error);
        emit(ready?"ready":candidate==null?"error":"available",UpdateFailure.reason(error.code,null)+"；已保留校验过的安装包，可重试。");
    }
    synchronized void installLaunchFailed(String op){installLaunchFailed(op,new UpdateFailure(UpdateFailure.INSTALLER));}
    synchronized void installLaunchFailed(String op,Throwable failure){
        if(active==null||!active.bridgeId.equals(op)||!operationId.equals(op))return;
        if(prefs.number("installerLease")>0){retainInstallRecovery(active,failure,"系统安装会话状态不确定；更新操作仍被保护，请稍后恢复。");return;}
        UpdateFailure error=UpdateFailure.at(UpdateFailure.INSTALLER,failure);rememberFailure(active,error);
        diagnostic(active,"install","error",candidate.channel,active.startedAt,error);
        try{clearInstallPersistence();}catch(RuntimeException storage){error=UpdateFailure.at(UpdateFailure.STORAGE,storage);rememberFailure(active,error);}
        installerLease=false;ready=candidate!=null&&apk().isFile();busy=false;
        emit(ready?"ready":"available",UpdateFailure.reason(error.code,error.httpStatus)+"，请核对安装权限或稍后再试。");
    }
    private void restore() {
        Operation context=new Operation("startup",clock.getAsLong());
        synchronized(this){active=context;
            try{lastCheck=UpdateCheckSummary.restore(prefs.lastCheck(),channel);}catch(Throwable ignored){lastCheck=UpdateCheckSummary.unknown();}
            if(lastCheck.diagnosticId!=null){diagnosticId=lastCheck.diagnosticId;if(lastCheck.errorCode!=0)lastFailure=new UpdateFailure(lastCheck.errorCode);}
            if("interrupted".equals(lastCheck.status))persistSummary(context);
        }
        diagnostic(context,"initialize","started",null,context.startedAt,null);
        try {
            ensureCache();delete(new File(cache,"candidate.part"));
            long requested=prefs.number("requestedVersion");
            int storedSession=(int)prefs.number("installSessionId");
            if(requested>0&&installedCode()>=requested){synchronized(this){
                try{if(storedSession>0)device.abandonInstallSession(storedSession);}catch(Exception ignored){}
                clearInstallPersistence();discardApk();delete(new File(cache,"candidate.json"));busy=false;diagnostic(context,"initialize","ok",null,context.startedAt,null);emit("updated","已核实本机安装版本，更新成功。");}return;}
            boolean restoredLease=storedSession>0;
            if(restoredLease&&!device.installSessionExists(storedSession)){clearInstallPersistence();restoredLease=false;}
            UpdatePolicy.Release restoredCandidate=null;boolean restoredReady=false;
            File metadata=new File(cache,"candidate.json");
            if(metadata.isFile()) {
                UpdatePolicy.Release restored=readCandidate(metadata);
                restoredCandidate=UpdatePolicy.choose(Collections.singletonList(restored),installedCode(),device.sdk(),device.abis());
                if(restoredCandidate!=null){try{UpdateIO.verifyBytes(apk(),restoredCandidate);verifyDevice(apk(),restoredCandidate);restoredReady=true;}
                    catch(Exception failure){UpdateFailure error=UpdateFailure.at(UpdateFailure.UNKNOWN,failure);rememberFailure(context,error);
                        diagnostic(context,"verify","error",restoredCandidate.channel,context.startedAt,error);if(!restoredLease)discardApk();}}
            }
            synchronized(this){
                boolean currentLease=restoredLease&&prefs.number("installSessionId")==storedSession;
                candidate=restoredCandidate;ready=restoredReady&&!currentLease;installerLease=currentLease;busy=false;
                diagnostic(context,"initialize","ok",null,context.startedAt,null);
                String text=recoveryRequired()?"发现尚未结束的系统安装会话。请先处理系统确认界面，或点击恢复更新废弃该会话。":candidate==null?lastCheck.description():ready?"已重新校验缓存安装包，可点击安装。":"安装缓存不可用，可重新下载。";
                if(candidate!=null&&"interrupted".equals(lastCheck.status))text+=" "+lastCheck.description();
                emit(recoveryRequired()?"recovery":candidate==null?"idle":ready?"ready":"available",text);
            }
        }catch(Exception failure){UpdateFailure error=UpdateFailure.at(UpdateFailure.UNKNOWN,failure);diagnostic(context,"initialize","error",null,context.startedAt,error);
            synchronized(this){rememberFailure(context,error);candidate=null;ready=false;installerLease=prefs.number("installSessionId")>0;busy=false;
                emit(installerLease?"recovery":"error",installerLease?"安装缓存说明损坏，但系统安装会话仍存在；请点击恢复更新废弃该会话。":UpdateFailure.reason(error.code,error.httpStatus)+"，请重新检查更新。");}}
    }
    private long installedCode()throws UpdateFailure {try{return device.installedCode();}catch(Exception failure){throw UpdateFailure.at(UpdateFailure.APK,failure);}}
    private void verifyDevice(File file,UpdatePolicy.Release target)throws UpdateFailure {try{device.verify(file,target);}catch(Exception failure){throw UpdateFailure.at(UpdateFailure.APK,failure);}}
    private UpdatePolicy.Release readCandidate(File metadata)throws IOException {
        byte[] bytes;try(InputStream input=new FileInputStream(metadata)){bytes=UpdateIO.readBounded(input,UpdatePolicy.MAX_FEED,new UpdateIO.Cancellation());}
        catch(IOException | SecurityException failure){throw UpdateFailure.at(UpdateFailure.STORAGE,failure);}
        Map<String,Object> envelope;try{envelope=UpdateIO.parse(new String(bytes,StandardCharsets.UTF_8));}
        catch(IllegalArgumentException failure){throw UpdateFailure.at(UpdateFailure.JSON,failure);}
        try{Object cachedChannel=envelope.get("channel");if(!(cachedChannel instanceof String)||!UpdatePolicy.channels(channel).contains(cachedChannel))throw new IllegalArgumentException();
            return UpdatePolicy.parseFeed(envelope,(String)cachedChannel);
        }catch(IllegalArgumentException failure){throw UpdateFailure.at(UpdateFailure.MANIFEST,failure);}
    }
    private void ensureCache()throws UpdateFailure {
        try{if(!cache.isDirectory()&&!cache.mkdirs())throw new UpdateFailure(UpdateFailure.STORAGE);}
        catch(SecurityException failure){throw UpdateFailure.at(UpdateFailure.STORAGE,failure);}
    }
    private File apk(){return new File(cache,"candidate.apk");}
    private void clearInstallPersistence(){prefs.putNumber("installSessionId",0);prefs.putNumber("installerLease",0);prefs.putNumber("requestedVersion",0);}
    private void discardApk()throws UpdateFailure{delete(apk());}
    private static void delete(File file)throws UpdateFailure{try{Files.deleteIfExists(file.toPath());}catch(IOException | SecurityException failure){throw UpdateFailure.at(UpdateFailure.STORAGE,failure);}}
    private static void move(File from,File to)throws UpdateFailure{try{Files.move(from.toPath(),to.toPath(),StandardCopyOption.REPLACE_EXISTING);}catch(IOException | SecurityException failure){throw UpdateFailure.at(UpdateFailure.STORAGE,failure);}}
    private void saveCandidate()throws UpdateFailure{
        ensureCache();Map<String,Object> envelope=new LinkedHashMap<>();envelope.put("schema_version",1);envelope.put("channel",candidate.channel);envelope.put("release",candidate.toMap());
        File temp=new File(cache,"candidate.json.tmp");
        try(FileOutputStream output=new FileOutputStream(temp)){output.write(UpdateIO.json(envelope).getBytes(StandardCharsets.UTF_8));output.getFD().sync();}
        catch(IOException | SecurityException failure){throw UpdateFailure.at(UpdateFailure.STORAGE,failure);}
        move(temp,new File(cache,"candidate.json"));
    }
    static boolean canInstall(boolean foreground,boolean documentOperation,boolean documentWorking,boolean pendingArchive,
            boolean nativeSpeech,boolean openSession,boolean grading,boolean webFile,boolean webSpeech) {
        return foreground&&!documentOperation&&!documentWorking&&!pendingArchive&&!nativeSpeech&&!openSession&&!grading&&!webFile&&!webSpeech;
    }
}
