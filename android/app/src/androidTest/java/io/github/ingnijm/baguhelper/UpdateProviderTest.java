package io.github.ingnijm.baguhelper;

import android.content.Context;
import android.net.Uri;
import android.content.pm.ProviderInfo;
import android.os.ParcelFileDescriptor;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;
import java.io.File;
import java.io.FileNotFoundException;
import java.nio.file.Files;

/** Run only in an isolated test application; never launches installer or uses network. */
@RunWith(AndroidJUnit4.class)
public class UpdateProviderTest {
    @Test public void providerOnlyOffersFixedApkReadOnlyAndDoesNotBroadenCsvProvider() throws Exception {
        Context app=InstrumentationRegistry.getInstrumentation().getTargetContext();
        ProviderInfo info=app.getPackageManager().resolveContentProvider(app.getPackageName()+".updates",0);
        assertNotNull(info);assertFalse(info.exported);assertTrue(info.grantUriPermissions);
        File folder=new File(app.getCacheDir(),"updates");assertTrue(folder.isDirectory()||folder.mkdirs());
        File target=new File(folder,"candidate.apk");
        if(target.exists())throw new IllegalStateException("Run provider test with isolated empty update cache");
        Files.write(target.toPath(),new byte[]{1,2,3});
        try {
            Uri uri=Uri.parse("content://"+app.getPackageName()+".updates/candidate.apk");
            try(ParcelFileDescriptor descriptor=app.getContentResolver().openFileDescriptor(uri,"r")){assertEquals(3,descriptor.getStatSize());}
            for(String mode:new String[]{"w","rw","wa","rwt"})assertThrows(FileNotFoundException.class,()->app.getContentResolver().openFileDescriptor(uri,mode));
            for(String path:new String[]{"/candidate.part","/../candidate.apk","/candidate.apk?x=1","/other.apk"})
                assertThrows(FileNotFoundException.class,()->app.getContentResolver().openFileDescriptor(Uri.parse("content://"+app.getPackageName()+".updates"+path),"r"));
        } finally { Files.deleteIfExists(target.toPath()); }
    }
}
