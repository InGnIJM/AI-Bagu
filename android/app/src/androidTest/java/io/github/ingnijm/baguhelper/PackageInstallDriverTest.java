package io.github.ingnijm.baguhelper;

import android.app.PendingIntent;
import android.app.ActivityOptions;
import android.content.Context;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;
import java.io.File;
import java.nio.file.Files;

/** Creates and abandons an uncommitted session only; it never opens the installer UI. */
@RunWith(AndroidJUnit4.class)
public class PackageInstallDriverTest {
    @Test public void stagesExactBytesAndAbandonsOwnedSession() throws Exception {
        Context app=InstrumentationRegistry.getInstrumentation().getTargetContext();
        PackageInstallDriver driver=new PackageInstallDriver(app);
        File apk=new File(app.getCacheDir(),"driver-test.apk");Files.write(apk.toPath(),new byte[]{1,2,3});
        int sessionId=-1;
        try {
            sessionId=driver.prepare(apk,3);assertTrue(driver.exists(sessionId));
            assertEquals(app.getPackageName(),app.getPackageManager().getPackageInstaller().getSessionInfo(sessionId).getAppPackageName());
        } finally { if(sessionId>0)driver.abandon(sessionId);Files.deleteIfExists(apk.toPath()); }
        assertFalse(driver.exists(sessionId));
    }

    @Test public void callbackPendingIntentIsExplicitMutableActivityOnModernAndroid() throws Exception {
        Context app=InstrumentationRegistry.getInstrumentation().getTargetContext();
        ActivityInfo info=app.getPackageManager().getActivityInfo(new android.content.ComponentName(app,UpdateInstallActivity.class),0);
        assertFalse(info.exported);
        PendingIntent callback=new PackageInstallDriver(app).callback(37);
        assertEquals(app.getPackageName(),callback.getCreatorPackage());
        if(Build.VERSION.SDK_INT>=31){assertTrue(callback.isActivity());assertFalse(callback.isImmutable());}
    }

    @Test public void callbackOptsCreatorIntoBackgroundActivityLaunchOnlyFromApi35() {
        assertNull(PackageInstallDriver.callbackActivityOptions(34));
        ActivityOptions options=PackageInstallDriver.callbackActivityOptions(35);
        assertNotNull(options);
        assertEquals(ActivityOptions.MODE_BACKGROUND_ACTIVITY_START_ALLOWED,
            options.getPendingIntentCreatorBackgroundActivityStartMode());
    }
}
