package io.github.ingnijm.baguhelper;

import android.app.ActivityOptions;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.os.Build;
import java.io.*;

/** Owns the system PackageInstaller session. No APK path or session ID crosses the JS bridge. */
final class PackageInstallDriver {
    static final String ACTION_RESULT = "io.github.ingnijm.baguhelper.UPDATE_INSTALL_RESULT";
    private static final String APK_NAME = "base.apk";
    private final Context app;
    private final PackageInstaller installer;

    PackageInstallDriver(Context context) {
        app=context.getApplicationContext();
        installer=app.getPackageManager().getPackageInstaller();
    }

    int prepare(File apk, long expectedSize)throws UpdateFailure {
        if(apk==null||!apk.isFile()||expectedSize<=0||apk.length()!=expectedSize)
            throw new UpdateFailure(UpdateFailure.STORAGE);
        PackageInstaller.SessionParams params=new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        params.setAppPackageName(app.getPackageName());
        params.setSize(expectedSize);
        params.setInstallReason(PackageManager.INSTALL_REASON_USER);
        if(Build.VERSION.SDK_INT>=31)params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_REQUIRED);
        if(Build.VERSION.SDK_INT>=33)params.setPackageSource(PackageInstaller.PACKAGE_SOURCE_DOWNLOADED_FILE);
        int sessionId=-1;
        try {
            sessionId=installer.createSession(params);
            try(PackageInstaller.Session session=installer.openSession(sessionId);
                    InputStream input=new BufferedInputStream(new FileInputStream(apk));
                    OutputStream output=session.openWrite(APK_NAME,0,expectedSize)) {
                byte[] buffer=new byte[64*1024];long copied=0;int count;
                while((count=input.read(buffer))!=-1){output.write(buffer,0,count);copied+=count;if(copied>expectedSize)throw new IOException("APK size changed");}
                if(copied!=expectedSize)throw new IOException("APK size changed");
                session.fsync(output);
            }
            return sessionId;
        } catch(IOException failure) {
            abandonQuietly(sessionId);throw UpdateFailure.at(UpdateFailure.STORAGE,failure);
        } catch(RuntimeException failure) {
            abandonQuietly(sessionId);throw UpdateFailure.at(UpdateFailure.INSTALLER,failure);
        }
    }

    void commit(int sessionId)throws UpdateFailure {
        if(sessionId<=0)throw new UpdateFailure(UpdateFailure.INSTALLER);
        PendingIntent receiver=callback(sessionId);
        try(PackageInstaller.Session session=installer.openSession(sessionId)) {
            session.commit(receiver.getIntentSender());
        } catch(IOException | RuntimeException failure) {
            // Binder may have accepted commit before the caller observes a failure. Keep the
            // persisted lease so startup recovery can query or explicitly abandon the session.
            throw UpdateFailure.at(UpdateFailure.INSTALLER,failure);
        }
    }

    PendingIntent callback(int sessionId) {
        Intent callback=new Intent(app,UpdateInstallActivity.class).setAction(ACTION_RESULT)
            .putExtra(PackageInstaller.EXTRA_SESSION_ID,sessionId);
        int flags=PendingIntent.FLAG_UPDATE_CURRENT;
        if(Build.VERSION.SDK_INT>=31)flags|=PendingIntent.FLAG_MUTABLE;
        ActivityOptions options=callbackActivityOptions(Build.VERSION.SDK_INT);
        return options==null?PendingIntent.getActivity(app,sessionId,callback,flags)
            :PendingIntent.getActivity(app,sessionId,callback,flags,options.toBundle());
    }

    @android.annotation.TargetApi(35)
    static ActivityOptions callbackActivityOptions(int sdk) {
        if(sdk<35)return null;
        return ActivityOptions.makeBasic().setPendingIntentCreatorBackgroundActivityStartMode(
            ActivityOptions.MODE_BACKGROUND_ACTIVITY_START_ALLOWED);
    }

    boolean exists(int sessionId)throws UpdateFailure {
        if(sessionId<=0)return false;
        try{return installer.getSessionInfo(sessionId)!=null;}
        catch(RuntimeException failure){throw UpdateFailure.at(UpdateFailure.INSTALLER,failure);}
    }

    void abandon(int sessionId)throws UpdateFailure {
        if(sessionId<=0)return;
        try{if(installer.getSessionInfo(sessionId)!=null)installer.abandonSession(sessionId);}
        catch(RuntimeException failure){throw UpdateFailure.at(UpdateFailure.INSTALLER,failure);}
    }

    private void abandonQuietly(int sessionId) {
        if(sessionId<=0)return;
        try{installer.abandonSession(sessionId);}catch(RuntimeException ignored){}
    }
}
