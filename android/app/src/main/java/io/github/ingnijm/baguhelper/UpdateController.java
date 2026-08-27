package io.github.ingnijm.baguhelper;

import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import java.io.File;
import java.io.IOException;
import java.lang.ref.WeakReference;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.zip.ZipFile;

/** Android boundaries only. Context and worker are process-owned; Activity is always weak. */
final class UpdateController {
    static final int INSTALL_REQUEST = 43;
    private static UpdateController instance;
    private final Context app;
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final Handler main=new Handler(Looper.getMainLooper());
    private final UpdateEngine engine;
    private WeakReference<MainActivity> owner=new WeakReference<>(null);
    private boolean recoveryPrompt;
    private volatile String diagnosticOperationId = DiagnosticPolicy.newOperation();
    private volatile String diagnosticFailureId;
    private WeakReference<AlertDialog> recoveryDialog=new WeakReference<>(null);
    static synchronized UpdateController get(Context context) {
        if(instance==null)instance=new UpdateController(context.getApplicationContext());
        return instance;
    }
    private UpdateController(Context app) {
        this.app=app;
        SharedPreferences preferences=app.getSharedPreferences("bagu-native-updates",Context.MODE_PRIVATE);
        UpdateEngine.Preferences store=new UpdateEngine.Preferences() {
            public long number(String key){return preferences.getLong(key,0);}
            public boolean enabled(){return preferences.getBoolean("automatic",true);}
            public void putNumber(String key,long value){write(preferences.edit().putLong(key,value));}
            public void enable(boolean value){write(preferences.edit().putBoolean("automatic",value));}
            public String lastStatus(){return preferences.getString("lastStatus","never");}
            public void lastStatus(String value){write(preferences.edit().putString("lastStatus",value));}
            private void write(SharedPreferences.Editor edit){if(!edit.commit())throw new IllegalStateException("Update preferences unavailable");}
        };
        UpdateEngine.Device device=new UpdateEngine.Device() {
            public long installedCode()throws Exception{return currentPackage().getLongVersionCode();}
            public int sdk(){return Build.VERSION.SDK_INT;}
            public List<String> abis(){return Arrays.asList(Build.SUPPORTED_ABIS);}
            public void verify(File file,UpdatePolicy.Release candidate)throws Exception{verifyArchive(file,candidate);}
        };
        engine=new UpdateEngine(BuildConfig.UPDATE_CHANNEL,new File(app.getCacheDir(),"updates"),store,device,
            UpdateIO.https(),worker,System::currentTimeMillis,
            state->main.post(()->publish(state)),
            (op,candidate)->main.post(()->requestInstallation(op,candidate)),
            (stage,failure)->{diagnosticFailureId=diagnosticOperationId;AndroidDiagnostics.event("native.update",stage,failure,diagnosticOperationId,null);});
    }
    private void requestInstallation(String op,UpdatePolicy.Release candidate) {
        MainActivity activity=owner.get();
        if(activity!=null&&activity.updateForeground())activity.validateUpdateInstallation(op,candidate);
        else engine.installBlocked(op,"请返回应用后再次点击安装。");
    }
    void attach(MainActivity activity){owner=new WeakReference<>(activity);foreground(activity);}
    void detach(MainActivity activity){if(owner.get()==activity){
        AlertDialog dialog=recoveryDialog.get();if(dialog!=null)dialog.dismiss();
        recoveryDialog.clear();recoveryPrompt=false;owner.clear();
    }}
    void foreground(MainActivity activity){
        if(owner.get()!=activity||!activity.updateForeground())return;
        activity.publishUpdate(state());
        checkOperation("auto_"+UUID.randomUUID().toString(),true);
    }
    void background(MainActivity activity){
        if(owner.get()!=activity)return;
        engine.cancel("background_"+UUID.randomUUID().toString());
        Map<String,Object> state=engine.state();
        engine.installBlocked((String)state.get("operationId"),"应用已离开前台，请返回后再次点击安装。");
    }
    private void publish(Map<String,Object> state){
        if ("error".equals(state.get("status"))) AndroidDiagnostics.event("native.update", "error", null, diagnosticOperationId, null);
        MainActivity activity=owner.get();
        if(activity!=null&&activity.updateForeground()) {
            activity.publishUpdate(diagnosticState(state));
            // Cache restoration can complete after the first foreground/page-ready callback.
            if("startup".equals(state.get("operationId")))checkOperation("auto_"+UUID.randomUUID().toString(),true);
        }
    }
    private String diagnosticState(Map<String,Object> state){
        Map<String,Object> detail=new LinkedHashMap<>(state);
        if(diagnosticOperationId.equals(diagnosticFailureId)){
            detail.put("message",state.get("message")+"\n反馈编号："+diagnosticOperationId);
            detail.put("diagnostic_id",diagnosticOperationId);
        }
        return UpdateIO.json(detail);
    }
    private void beginDiagnostic(){if(!Boolean.TRUE.equals(engine.state().get("busy"))){diagnosticOperationId=DiagnosticPolicy.newOperation();diagnosticFailureId=null;}}
    private synchronized boolean checkOperation(String op,boolean automatic){beginDiagnostic();return engine.check(op,automatic);}
    String state(){return diagnosticState(engine.state());}
    void automatic(boolean value,String op){engine.automatic(value,op);}
    boolean check(String op){return checkOperation(op,false);}
    synchronized boolean download(String id,String op){beginDiagnostic();return engine.download(id,op);}
    void cancel(String op){
        UpdatePolicy.validateOperationId(op);
        if(Boolean.TRUE.equals(engine.state().get("recovery"))) {
            main.post(()->{
                MainActivity activity=owner.get();
                if(activity==null||!activity.updateForeground()||recoveryPrompt)return;
                recoveryPrompt=true;
                AlertDialog prompt=new AlertDialog.Builder(activity).setTitle("恢复更新操作")
                    .setMessage("请先关闭旧的系统安装器。确认后将撤销旧安装器的文件读取授权，保留当前安装文件；你可以重新检查并下载更新。不会自动安装。")
                    .setNegativeButton("取消",(dialog,which)->activity.publishUpdate(state()))
                    .setPositiveButton("已关闭，恢复更新",(dialog,which)->{
                        if(!activity.updateForeground()||!Boolean.TRUE.equals(engine.state().get("recovery")))return;
                        try {
                            app.revokeUriPermission(Uri.parse("content://"+app.getPackageName()+".updates/candidate.apk"),Intent.FLAG_GRANT_READ_URI_PERMISSION);
                            engine.recover(op);
                        } catch(RuntimeException failure) { AndroidDiagnostics.event("native.update", "verify", failure, null, null); activity.publishUpdate(state()); }
                    }).setOnCancelListener(ignored->activity.publishUpdate(state())).setOnDismissListener(ignored->recoveryPrompt=false).create();
                recoveryDialog=new WeakReference<>(prompt);
                prompt.show();
            });
        } else {
            engine.cancel(op);
            engine.installBlocked((String)engine.state().get("operationId"),"已取消安装准备。");
            main.post(()->{MainActivity activity=owner.get();if(activity!=null)activity.cancelUpdatePreparation("已取消安装准备。");});
        }
    }
    synchronized boolean install(String id,String op){beginDiagnostic();return engine.install(id,op);}
    boolean installCurrent(String op,String id){return engine.installCurrent(op,id);}
    void blocked(String op,String reason){engine.installBlocked(op,reason);}
    void installerReturned(){engine.installerReturned();}
    boolean launchInstaller(MainActivity activity,String op,UpdatePolicy.Release candidate) {
        if(owner.get()!=activity||!activity.updateForeground()||!engine.installCurrent(op,candidate.id()))return false;
        try {
            PackageInfo installed=currentPackage();
            if(installed.getLongVersionCode()>=candidate.versionCode||!UpdatePolicy.TRUSTED_CERTIFICATE.equals(signer(installed)))
                throw new IOException("Installed package changed");
            if(!app.getPackageManager().canRequestPackageInstalls()) {
                engine.installBlocked(op,"需要允许本应用安装更新。授权返回后请再次点击安装。");
                new AlertDialog.Builder(activity).setTitle("允许安装应用更新")
                    .setMessage("系统需要“安装未知应用”权限。只在你确认后打开设置；返回后不会自动安装。")
                    .setNegativeButton("取消",null).setPositiveButton("打开设置",(dialog,which)->{
                        if(!activity.updateForeground())return;
                        try{activity.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+app.getPackageName())));}
                        catch(RuntimeException failure){AndroidDiagnostics.event("native.update", "permission", failure, null, null);engine.installBlocked(op,"系统设置不可用，请稍后再试。");}
                    }).show();
                return false;
            }
            Uri uri=Uri.parse("content://"+app.getPackageName()+".updates/candidate.apk");
            Intent intent=new Intent(Intent.ACTION_VIEW).setDataAndType(uri,UpdateApkProvider.MIME)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.setClipData(ClipData.newRawUri("Bagu update",uri));
            // Persist before handoff. No Activity result is interpreted as installation success.
            engine.installLaunched(op);
            activity.startActivityForResult(intent,INSTALL_REQUEST);
            return true;
        } catch(Exception failure){AndroidDiagnostics.event("native.update", "install", failure, null, null);engine.installLaunchFailed(op);return false;}
    }
    private PackageInfo currentPackage()throws PackageManager.NameNotFoundException {
        return app.getPackageManager().getPackageInfo(app.getPackageName(),PackageManager.GET_SIGNING_CERTIFICATES);
    }
    private static String signer(PackageInfo info)throws IOException {
        if(info==null||info.signingInfo==null||info.signingInfo.hasMultipleSigners())throw new IOException("Signer unavailable");
        Signature[] signers=info.signingInfo.getApkContentsSigners();
        if(signers==null||signers.length!=1)throw new IOException("Exactly one signer required");
        return UpdateIO.sha256(signers[0].toByteArray());
    }
    private void verifyArchive(File file,UpdatePolicy.Release candidate)throws Exception {
        PackageInfo archive=app.getPackageManager().getPackageArchiveInfo(file.getAbsolutePath(),PackageManager.GET_SIGNING_CERTIFICATES);
        if(archive==null||archive.applicationInfo==null)throw new IOException("Invalid APK archive");
        PackageInfo installed=currentPackage();
        if(!UpdatePolicy.TRUSTED_CERTIFICATE.equals(signer(installed)))throw new IOException("Installed signer mismatch");
        Set<String> abis=new HashSet<>();
        try(ZipFile zip=new ZipFile(file)) {
            Enumeration<? extends java.util.zip.ZipEntry> entries=zip.entries();
            while(entries.hasMoreElements()){String name=entries.nextElement().getName();if(name.startsWith("lib/")){String[] parts=name.split("/");if(parts.length>=2)abis.add(parts[1]);}}
        }
        // Archive metadata parsing is not a proof of the entire APK signature. Android's
        // package installer performs final cryptographic verification of the actual APK.
        UpdatePolicy.validateArchive(candidate,archive.packageName,archive.getLongVersionCode(),archive.versionName,
            archive.applicationInfo.minSdkVersion,signer(archive),abis,installed.getLongVersionCode(),Build.VERSION.SDK_INT);
    }
}
