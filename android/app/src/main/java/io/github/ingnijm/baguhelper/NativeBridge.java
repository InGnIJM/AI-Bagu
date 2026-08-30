package io.github.ingnijm.baguhelper;

import android.content.SharedPreferences;
import android.webkit.JavascriptInterface;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Map;

/** Only origin-independent bagu UI state is stored here; model credentials never pass through. */
public final class NativeBridge {
    private final SharedPreferences preferences;
    private final WeakReference<MainActivity> activity;
    private final String appName;
    private final UpdateController updater;

    NativeBridge(SharedPreferences preferences, MainActivity activity) {
        this.preferences = preferences;
        this.activity = new WeakReference<>(activity);
        this.appName = activity.getString(R.string.app_name);
        this.updater = UpdateController.get(activity);
    }

    @JavascriptInterface public String getAppInfo() {
        try {
            return new JSONObject().put("name", appName).put("packageName", BuildConfig.APPLICATION_ID)
                .put("versionName", BuildConfig.VERSION_NAME).put("versionCode", BuildConfig.VERSION_CODE)
                .put("flavor", BuildConfig.FLAVOR).toString();
        } catch (JSONException impossible) { throw new IllegalStateException(impossible); }
    }

    @JavascriptInterface public String getItem(String key) {
        return HostPolicy.validStorageKey(key) ? preferences.getString(key, null) : null;
    }

    @JavascriptInterface public synchronized void setItem(String key, String value) {
        if (!HostPolicy.canStore(preferences.getAll(), key, value)) throw new IllegalArgumentException("UI storage limit exceeded");
        if (!preferences.edit().putString(key, value).commit()) throw new IllegalStateException("UI storage write failed");
    }

    @JavascriptInterface public synchronized void removeItem(String key) {
        if (HostPolicy.validStorageKey(key) && !preferences.edit().remove(key).commit()) {
            throw new IllegalStateException("UI storage write failed");
        }
    }

    @JavascriptInterface public String keys() {
        ArrayList<String> keys = new ArrayList<>();
        for (Map.Entry<String, ?> entry : preferences.getAll().entrySet()) {
            if (HostPolicy.validStorageKey(entry.getKey())) keys.add(entry.getKey());
        }
        Collections.sort(keys);
        return new JSONArray(keys).toString();
    }

    @JavascriptInterface public void exportBackup() { open("export", null); }
    @JavascriptInterface public void exportDiagnostics() { open("diagnostics", null); }
    @JavascriptInterface public void reportDiagnostic(String json) { AndroidDiagnostics.reportWeb(json); }
    @JavascriptInterface public void exportQuestionBank() { open("export-questions", null); }
    @JavascriptInterface public void importBackup() { open("import", null); }
    @JavascriptInterface public void importInterviewPack() { open("pack-import", null); }
    @JavascriptInterface public void saveCsvTemplate(String csv) { open("template", csv); }
    @JavascriptInterface public void startSpeech(String requestId) { speech("start", requestId); }
    @JavascriptInterface public void stopSpeech(String requestId) { speech("stop", requestId); }
    @JavascriptInterface public void cancelSpeech(String requestId) { speech("cancel", requestId); }
    @JavascriptInterface public String getUpdateState() { return updater.state(); }
    @JavascriptInterface public void setAutomaticUpdates(boolean enabled, String operationId) { updater.automatic(enabled, operationId); }
    @JavascriptInterface public boolean checkForUpdate(String operationId) {
        MainActivity owner = activity.get();
        return owner != null && owner.checkForUpdate(operationId);
    }
    @JavascriptInterface public boolean downloadUpdate(String candidateId, String operationId) {
        MainActivity owner = activity.get();
        return owner != null && owner.downloadUpdate(candidateId, operationId);
    }
    @JavascriptInterface public void cancelUpdate(String operationId) { updater.cancel(operationId); }
    @JavascriptInterface public boolean installUpdate(String candidateId, String operationId) {
        MainActivity owner = activity.get();
        return owner != null && owner.installUpdate(candidateId, operationId);
    }

    private void speech(String operation, String requestId) {
        SpeechInput.validateRequestId(requestId);
        MainActivity owner = activity.get();
        if (owner != null) owner.runOnUiThread(() -> owner.speech(operation, requestId));
    }

    private void open(String operation, String content) {
        MainActivity owner = activity.get();
        if (owner != null) owner.requestDocument(operation, content);
    }
}
