package io.github.ingnijm.baguhelper;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

/** Non-exported trampoline for PackageInstaller commit callbacks. */
public final class UpdateInstallActivity extends Activity {
    @Override protected void onCreate(Bundle state){super.onCreate(state);handle(getIntent());}
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);handle(intent);}
    private void handle(Intent intent){UpdateController.get(this).handleInstallResult(this,intent);}
}
