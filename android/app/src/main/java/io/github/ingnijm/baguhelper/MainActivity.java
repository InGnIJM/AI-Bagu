package io.github.ingnijm.baguhelper;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import android.window.OnBackInvokedCallback;
import android.window.OnBackInvokedDispatcher;
import org.json.JSONException;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.ref.WeakReference;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.UUID;

/** Thin Android shell; quiz/session/model rules remain in the shared Python + HTML. */
public final class MainActivity extends Activity {
    private static final int DOCUMENT_REQUEST = 41;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private WebView web;
    private FrameLayout root;
    private LinearLayout progressPanel;
    private TextView progressText;
    private ProgressBar progress;
    private Button retry;
    private HostState state;
    private boolean pageReady;
    private boolean pageLoadFailed;
    private boolean backPending;
    private boolean imeVisible;
    private OnBackInvokedCallback backCallback;

    /** Retained across configuration recreation; never retains an Activity strongly. */
    private static final class HostState {
        WeakReference<MainActivity> owner = new WeakReference<>(null);
        JSONObject runtime;
        boolean starting;
        boolean working;
        String workingOperation;
        String operation;
        String template;
        ValueCallback<Uri[]> csvCallback;
        final ArrayList<JSONObject> results = new ArrayList<>();

        void result(String operation, String status, String message, JSONObject counts) {
            JSONObject detail = new JSONObject();
            try {
                detail.put("operation", operation).put("status", status).put("message", message);
                if (counts != null) {
                    detail.put("added", counts.getInt("added")).put("updated", counts.getInt("updated"));
                }
            } catch (JSONException impossible) { throw new IllegalStateException(impossible); }
            results.add(detail);
            MainActivity activity = owner.get();
            if (activity != null) activity.flushResults();
        }
    }

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        Object retained = getLastNonConfigurationInstance();
        state = retained instanceof HostState ? (HostState) retained : new HostState();
        state.owner = new WeakReference<>(this);
        if (retained == null && saved != null) {
            state.operation = saved.getString("documentOperation");
            state.template = saved.getString("documentTemplate");
            if (saved.getBoolean("documentWorking")) {
                state.result(saved.getString("workingOperation", "import"), "error", "文件操作被中断，请重试。", null);
            }
        }
        buildViews();
        if (Build.VERSION.SDK_INT >= 33) {
            backCallback = this::handleBack;
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, backCallback);
        }
        startRuntime();
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void buildViews() {
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(250, 245, 255));
        if (Build.VERSION.SDK_INT >= 30) getWindow().setDecorFitsSystemWindows(false);
        else getWindow().getDecorView().setSystemUiVisibility(getWindow().getDecorView().getSystemUiVisibility()
            | View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
            | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        if (Build.VERSION.SDK_INT < 30) {
            // API29 can resize back after IME dismissal without redispatching
            // insets to a child which consumed them. Read the fresh root state.
            root.getViewTreeObserver().addOnGlobalLayoutListener(() -> {
                WindowInsets current = root.getRootWindowInsets();
                if (current != null) updateImeVisibility(current);
            });
        }
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            // Focus can remain after Back/Done hides the keyboard. Report actual
            // IME visibility instead of making the page infer it from focus.
            updateImeVisibility(insets);
            if (Build.VERSION.SDK_INT >= 30) {
                Insets safe = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(safe.left, safe.top, safe.right, safe.bottom);
                return WindowInsets.CONSUMED;
            }
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets().consumeDisplayCutout();
        });
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(250, 245, 255));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true); // Only the SAF snapshot provider is used for file inputs.
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(true); // onCreateWindow never creates an untrusted WebView.
        web.addJavascriptInterface(new NativeBridge(getSharedPreferences("bagu-ui-state", MODE_PRIVATE), this), "BaguNative");
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                int port = state.runtime == null ? -1 : state.runtime.optInt("port", -1);
                if (HostPolicy.isLocalUrl(url, port)) return false;
                if (HostPolicy.isExplicitReference(url, request.isForMainFrame(), request.hasGesture())) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE)); }
                    catch (ActivityNotFoundException ignored) { toast("没有可打开参考链接的浏览器。"); }
                }
                return true;
            }

            @Override public void onPageFinished(WebView view, String url) {
                if (!pageLoadFailed && state.runtime != null && HostPolicy.isLocalUrl(url, state.runtime.optInt("port"))) {
                    pageReady = true;
                    progressPanel.setVisibility(View.GONE);
                    publishImeVisibility();
                    flushResults();
                }
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showStartupError();
            }
            // The default onReceivedSslError cancels; never bypass TLS validation.
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onCreateWindow(WebView view, boolean dialog, boolean userGesture, Message message) {
                // Reference anchors use target=_blank. Open only a real tapped anchor;
                // do not create a WebView or honor script-created windows.
                WebView.HitTestResult hit = view.getHitTestResult();
                if (hit != null && (hit.getType() == WebView.HitTestResult.SRC_ANCHOR_TYPE
                    || hit.getType() == WebView.HitTestResult.SRC_IMAGE_ANCHOR_TYPE)
                    && HostPolicy.isExplicitReference(hit.getExtra(), true, userGesture)) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(hit.getExtra())).addCategory(Intent.CATEGORY_BROWSABLE)); }
                    catch (ActivityNotFoundException ignored) { toast("没有可打开参考链接的浏览器。"); }
                }
                return false;
            }

            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams parameters) {
                if (state.csvCallback != null) state.csvCallback.onReceiveValue(null);
                state.csvCallback = callback;
                if (!pageReady || state.operation != null || state.working) {
                    callback.onReceiveValue(null);
                    state.csvCallback = null;
                    toast("请先完成当前文件操作。");
                    return true;
                }
                openDocument("csv", null);
                return true;
            }
        });
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        progressPanel = new LinearLayout(this);
        progressPanel.setOrientation(LinearLayout.VERTICAL);
        progressPanel.setGravity(Gravity.CENTER);
        progressPanel.setPadding(dp(24), dp(24), dp(24), dp(24));
        progressPanel.setBackgroundColor(Color.rgb(250, 245, 255));
        ImageView icon = new ImageView(this);
        icon.setImageResource(R.drawable.brand_icon);
        icon.setContentDescription(getString(R.string.app_name));
        progressPanel.addView(icon, new LinearLayout.LayoutParams(dp(96), dp(96)));
        TextView title = new TextView(this);
        title.setText(R.string.app_name);
        title.setTextColor(Color.rgb(124, 58, 237));
        title.setTextSize(24);
        title.setGravity(Gravity.CENTER);
        progressPanel.addView(title);
        progress = new ProgressBar(this);
        progressPanel.addView(progress, new LinearLayout.LayoutParams(dp(48), dp(48)));
        progressText = new TextView(this);
        progressText.setText(R.string.starting);
        progressText.setGravity(Gravity.CENTER);
        progressText.setPadding(0, dp(16), 0, dp(16));
        progressPanel.addView(progressText);
        retry = new Button(this);
        retry.setText(R.string.retry);
        retry.setMinHeight(dp(48));
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(view -> startRuntime());
        progressPanel.addView(retry);
        root.addView(progressPanel, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        root.requestApplyInsets();
    }

    private void startRuntime() {
        pageReady = false;
        pageLoadFailed = false;
        progressPanel.setVisibility(View.VISIBLE);
        progress.setVisibility(View.VISIBLE);
        progressText.setText(R.string.starting);
        retry.setVisibility(View.GONE);
        if (state.runtime != null) { web.loadUrl(state.runtime.optString("url")); return; }
        if (state.starting) return;
        state.starting = true;
        HostState target = state;
        Context app = getApplicationContext();
        RuntimeHost.WORKER.execute(() -> {
            JSONObject result = null;
            try { result = RuntimeHost.start(app); }
            catch (Exception ignored) { /* No exception text: no paths, tokens or config in UI/logs. */ }
            JSONObject ready = result;
            MAIN.post(() -> {
                target.starting = false;
                target.runtime = ready;
                MainActivity owner = target.owner.get();
                if (owner != null) {
                    if (ready == null) owner.showStartupError();
                    else owner.web.loadUrl(ready.optString("url"));
                }
            });
        });
    }

    private void showStartupError() {
        pageReady = false;
        pageLoadFailed = true;
        progressPanel.setVisibility(View.VISIBLE);
        progress.setVisibility(View.GONE);
        progressText.setText(R.string.startup_error);
        retry.setVisibility(View.VISIBLE);
    }

    void openDocument(String operation, String template) {
        if (!pageReady || state.operation != null || state.working) {
            if (!"csv".equals(operation)) state.result(operation, "error", "请先完成当前文件操作。", null);
            return;
        }
        if ("template".equals(operation) && (template == null || template.getBytes(StandardCharsets.UTF_8).length > 65536)) {
            state.result(operation, "error", "CSV 模板内容过大或无效。", null);
            return;
        }
        state.operation = operation;
        state.template = template;
        boolean read = "import".equals(operation) || "csv".equals(operation);
        Intent intent = new Intent(read ? Intent.ACTION_OPEN_DOCUMENT : Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType(read ? "*/*" : "template".equals(operation) ? "text/csv" : "application/octet-stream");
        if (!read) intent.putExtra(Intent.EXTRA_TITLE, "template".equals(operation) ? "questions-template.csv" : "bagu-backup.bagu-backup");
        try { startActivityForResult(intent, DOCUMENT_REQUEST); }
        catch (ActivityNotFoundException ignored) {
            state.operation = null;
            state.template = null;
            if ("csv".equals(operation)) finishCsv(null, "系统文件选择器不可用。");
            else state.result(operation, "error", "系统文件选择器不可用。", null);
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != DOCUMENT_REQUEST || state.operation == null) return;
        String operation = state.operation;
        String template = state.template;
        state.operation = null;
        state.template = null;
        Uri uri = data == null ? null : data.getData();
        if (result != RESULT_OK || uri == null) {
            if ("csv".equals(operation)) finishCsv(null, null);
            else state.result(operation, "cancelled", "已取消。", null);
            return;
        }
        if (!HostPolicy.isDocumentUri(uri.toString())) {
            if ("csv".equals(operation)) finishCsv(null, "请选择系统文件提供方中的文档。");
            else state.result(operation, "error", "请选择系统文件提供方中的文档。", null);
            return;
        }
        state.working = true;
        state.workingOperation = operation;
        HostState target = state;
        Context app = getApplicationContext();
        RuntimeHost.WORKER.execute(() -> {
            JSONObject counts = null;
            Uri csv = null;
            boolean success = false;
            try {
                // A process may have been recreated while the SAF picker was open.
                RuntimeHost.start(app);
                if ("import".equals(operation) || "csv".equals(operation)) {
                    byte[] bytes;
                    try (InputStream input = app.getContentResolver().openInputStream(uri)) {
                        bytes = HostPolicy.readBounded(input, "csv".equals(operation) ? 2 * 1024 * 1024 : 20 * 1024 * 1024);
                    }
                    if ("import".equals(operation)) counts = RuntimeHost.restoreArchive(bytes);
                    else {
                        File directory = new File(app.getCacheDir(), "csv-imports");
                        if (!directory.isDirectory() && !directory.mkdirs()) throw new java.io.IOException("Cannot create cache");
                        File snapshot = new File(directory, UUID.randomUUID() + ".csv");
                        try (OutputStream output = new FileOutputStream(snapshot)) { output.write(bytes); }
                        csv = new Uri.Builder().scheme("content").authority(BuildConfig.APPLICATION_ID + ".imports")
                            .appendPath(snapshot.getName()).build();
                    }
                } else {
                    byte[] bytes = "template".equals(operation) ? template.getBytes(StandardCharsets.UTF_8) : RuntimeHost.exportArchive();
                    try (OutputStream output = app.getContentResolver().openOutputStream(uri, "wt")) {
                        if (output == null) throw new java.io.IOException("Cannot open destination");
                        output.write(bytes);
                    }
                }
                success = true;
            } catch (Exception ignored) { /* User-facing messages are fixed and contain no private exception text. */ }
            boolean ok = success;
            JSONObject restored = counts;
            Uri chosenCsv = csv;
            MAIN.post(() -> {
                target.working = false;
                target.workingOperation = null;
                MainActivity owner = target.owner.get();
                if ("csv".equals(operation)) {
                    if (target.csvCallback != null) {
                        target.csvCallback.onReceiveValue(chosenCsv == null ? null : new Uri[]{chosenCsv});
                        target.csvCallback = null;
                    }
                    if (!ok && owner != null) owner.toast("CSV 读取失败，请选择不超过 2 MiB 的文件。");
                } else {
                    String message = ok ? "操作完成。" : "import".equals(operation)
                        ? "导入失败。请先结束当前练习，并选择有效且不超过 20 MiB 的备份。"
                        : "export".equals(operation)
                            ? "导出失败。请检查题目字段和题库大小（最多 10000 题、解压后 50 MiB、文件 20 MiB），并确认保存位置可写。原题库未改变。"
                            : "文件写入失败，请重试。";
                    target.result(operation, ok ? "ok" : "error", message, restored);
                }
            });
        });
    }

    private void finishCsv(Uri uri, String message) {
        if (state.csvCallback != null) {
            state.csvCallback.onReceiveValue(uri == null ? null : new Uri[]{uri});
            state.csvCallback = null;
        }
        if (message != null) toast(message);
    }

    private void flushResults() {
        if (!pageReady) return;
        for (JSONObject result : state.results) {
            // Encode the whole JSON value, never splice file text or exception messages into script.
            String json = JSONObject.quote(result.toString()).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029");
            web.evaluateJavascript("window.dispatchEvent(new CustomEvent('bagu-native-result',{detail:JSON.parse(" + json + ")}));", null);
        }
        state.results.clear();
    }

    private void publishImeVisibility() {
        if (pageReady) web.evaluateJavascript("window.dispatchEvent(new CustomEvent('bagu-ime',"
            + "{detail:{visible:" + imeVisible + "}}));", null);
    }

    private void updateImeVisibility(WindowInsets insets) {
        boolean visible = Build.VERSION.SDK_INT >= 30
            ? insets.isVisible(WindowInsets.Type.ime())
            : insets.getSystemWindowInsetBottom() > insets.getStableInsetBottom();
        if (imeVisible != visible) {
            imeVisible = visible;
            publishImeVisibility();
        }
    }

    // Legacy API 29-32 entry point only. API 33+ uses the registered
    // OnBackInvokedCallback above, including Android 16 gestures.
    @SuppressLint("GestureBackNavigation")
    @Override public void onBackPressed() { handleBack(); }

    private void handleBack() {
        if (backPending) return;
        if (!pageReady) { super.onBackPressed(); return; }
        backPending = true;
        web.evaluateJavascript("(function(){return typeof window.baguHandleBack==='function' && window.baguHandleBack()===true;})()", value -> {
            backPending = false;
            if (!"true".equals(value)) MainActivity.super.onBackPressed();
        });
    }

    @Override public void onConfigurationChanged(Configuration configuration) {
        super.onConfigurationChanged(configuration);
        root.requestApplyInsets();
    }

    @Override public Object onRetainNonConfigurationInstance() { return state; }

    @Override protected void onSaveInstanceState(Bundle saved) {
        super.onSaveInstanceState(saved);
        saved.putString("documentOperation", state.operation);
        saved.putString("documentTemplate", state.template);
        saved.putBoolean("documentWorking", state.working);
        saved.putString("workingOperation", state.workingOperation);
    }

    @Override protected void onDestroy() {
        finishCsv(null, null);
        if (state.owner.get() == this) state.owner.clear();
        if (Build.VERSION.SDK_INT >= 33 && backCallback != null) getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(backCallback);
        web.removeJavascriptInterface("BaguNative");
        root.removeView(web);
        web.destroy();
        super.onDestroy();
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void toast(String text) { Toast.makeText(this, text, Toast.LENGTH_LONG).show(); }
}
