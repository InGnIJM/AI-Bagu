package io.github.ingnijm.baguhelper;

import android.app.Application;

/** Installed before Python startup, WebView construction and Activity callbacks. */
public final class BaguApplication extends Application {
    @Override public void onCreate() {
        super.onCreate();
        AndroidDiagnostics.initialize(this);
        try {
            Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
            Thread.setDefaultUncaughtExceptionHandler((thread, failure) -> {
                try { AndroidDiagnostics.event("native.crash", "error", failure, null, null); }
                finally {
                    if (previous != null) previous.uncaughtException(thread, failure);
                    else { android.os.Process.killProcess(android.os.Process.myPid()); System.exit(10); }
                }
            });
        } catch (Throwable ignored) { /* The existing platform handler remains authoritative. */ }
    }
}
