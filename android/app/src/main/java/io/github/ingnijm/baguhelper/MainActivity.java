package io.github.ingnijm.baguhelper;

import android.annotation.SuppressLint;
import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Bitmap;
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
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.function.Consumer;

/** Thin Android shell; quiz/session/model rules remain in the shared Python + HTML. */
public final class MainActivity extends Activity {
    private static final int DOCUMENT_REQUEST = 41;
    private static final int DOCUMENT_REQUEST_SEQUENCE = 4100;
    private static final int DOCUMENT_REQUEST_LIMIT = 4299;
    private static final int PACK_DOCUMENT_REQUEST = 4300;
    private static final int PACK_DOCUMENT_REQUEST_LIMIT = 4399;
    private static final int MICROPHONE_REQUEST = 42;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private WebView web;
    private FrameLayout root;
    private LinearLayout progressPanel;
    private TextView progressText;
    private ProgressBar progress;
    private Button retry;
    private Button diagnostics;
    private TextView diagnosticsResult;
    private HostState state;
    private boolean pageReady;
    private boolean pageLoadFailed;
    private boolean backPending;
    private boolean imeVisible;
    private OnBackInvokedCallback backCallback;
    private SpeechInput speechInput;
    private Consumer<Boolean> microphoneReply;
    private boolean resumed;
    private AlertDialog importDialog;
    private UpdateController updater;
    private final UpdateInstallGate updateInstallGate = new UpdateInstallGate();
    private Runnable updatePrepareTimeout;
    private String speechOperationId;

    /** Retained across configuration recreation; never retains an Activity strongly. */
    private static final class HostState {
        WeakReference<MainActivity> owner = new WeakReference<>(null);
        JSONObject runtime;
        boolean starting;
        boolean working;
        String workingOperation;
        String operation;
        int documentRequestCode;
        int nextDocumentRequestCode = DOCUMENT_REQUEST;
        int nextPackRequestCode = PACK_DOCUMENT_REQUEST;
        String template;
        String diagnosticsId;
        String startupOperationId = DiagnosticPolicy.newOperation();
        String diagnosticsMessage;
        PendingImport pendingImport;
        final NativeOperationArbiter arbiter = NativeOperationArbiter.process();
        NativeOperationArbiter.Lease documentLease;
        NativeOperationArbiter.Lease updateLease;
        ValueCallback<Uri[]> csvCallback;
        final ArrayList<JSONObject> results = new ArrayList<>();

        synchronized NativeOperationArbiter.Lease claimDocument(String operation) {
            NativeOperationArbiter.Lease lease = arbiter.tryAcquire("file", operation);
            if (lease != null) documentLease = lease;
            return lease;
        }

        synchronized NativeOperationArbiter.Lease claimUpdate(String operation) {
            NativeOperationArbiter.Lease lease = arbiter.tryAcquire("update", operation);
            if (lease != null) updateLease = lease;
            return lease;
        }

        synchronized boolean ownsDocument(NativeOperationArbiter.Lease lease) {
            return documentLease == lease && arbiter.owns(lease);
        }

        synchronized boolean releaseDocument(NativeOperationArbiter.Lease lease) {
            if (documentLease != lease || !arbiter.release(lease)) return false;
            documentLease = null;
            return true;
        }

        synchronized boolean releaseUpdate(NativeOperationArbiter.Lease lease) {
            if (updateLease != lease || !arbiter.release(lease)) return false;
            updateLease = null;
            return true;
        }

        synchronized boolean nativeIdle() { return arbiter.isIdle(); }

        synchronized String documentOperation() {
            return documentLease == null ? null : documentLease.operation();
        }

        void result(String operation, String status, String message, JSONObject counts) {
            AndroidDiagnostics.event("native.file", "ok".equals(status) ? "done" : status, null, diagnosticsId, null);
            JSONObject detail = new JSONObject();
            if (diagnosticsId != null && ("error".equals(status) || "ok".equals(status))) message += "\n反馈编号：" + diagnosticsId;
            try {
                detail.put("operation", operation).put("status", status).put("message", message);
                if (diagnosticsId != null) detail.put("operation_id", diagnosticsId);
                if (counts != null) {
                    if ("pack-import".equals(operation)) {
                        for (String key : new String[]{"pack_id", "name", "revision", "question_count", "experience_count"}) {
                            if (counts.has(key) && !counts.isNull(key)) detail.put(key, counts.get(key));
                        }
                    } else {
                        detail.put("added", counts.getInt("added")).put("updated", counts.getInt("updated"));
                    }
                }
            } catch (JSONException impossible) { throw new IllegalStateException(impossible); }
            results.add(detail);
            if ("diagnostics".equals(operation)) diagnosticsMessage = message;
            MainActivity activity = owner.get();
            if (activity != null) { activity.updateDiagnosticsUi(); activity.flushResults(); }
        }
    }

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        Object retained = getLastNonConfigurationInstance();
        state = retained instanceof HostState ? (HostState) retained : new HostState();
        state.owner = new WeakReference<>(this);
        if (retained == null && saved != null) {
            String savedId = saved.getString("documentOperationId");
            if (savedId != null && savedId.matches("n_[a-f0-9]{32}")) state.diagnosticsId = savedId;
            if (saved.getBoolean("documentWorking")) {
                String operation = saved.getString("workingOperation", "import");
                state.result(operation, "error", "pack-import".equals(operation)
                    ? "题包安装被中断，是否完成未知。请先核对题包列表，不要重复安装。"
                    : "import".equals(operation)
                        ? "导入操作被中断，是否完成未知。请先核对题库与进度，不要重复导入。"
                        : "文件操作被中断，请检查保存位置。", null);
            } else {
                String pendingKind = saved.getString("documentPendingImportKind");
                if (pendingKind == null && saved.getBoolean("documentPendingImport")) pendingKind = "import";
                if ("pack-import".equals(pendingKind) || "import".equals(pendingKind)) {
                    state.result(pendingKind, "cancelled", "待确认的导入已取消，原数据未改变。请重新选择文件。", null);
                } else {
                    String operation = saved.getString("documentOperation");
                    if (operation != null) state.result(operation, "cancelled",
                        "应用已重新启动，文件操作已取消。请重新选择文件。", null);
                }
            }
        }
        try { buildViews(); }
        catch (Throwable failure) {
            AndroidDiagnostics.event("native.startup", "initialize", failure, state.startupOperationId, null);
            discardWebView(); showStartupError();
        }
        if (updater != null) updater.attach(this);
        if (Build.VERSION.SDK_INT >= 33) {
            backCallback = this::handleBack;
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, backCallback);
        }
        if (web != null) startRuntime();
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
        buildStartupPanel();
        updater = UpdateController.get(this);
        if (speechInput == null) speechInput = new SpeechInput(new AndroidSpeechBackend(this, this::requestMicrophone),
            (delay, job) -> { MAIN.postDelayed(job, delay); return () -> MAIN.removeCallbacks(job); }, this::publishSpeech);
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
            @Override public void onPageStarted(WebView view, String url, Bitmap favicon) {
                cancelUpdatePreparation("页面已重载，请重新点击安装。");
                speechInput.cancelActive();
                pageReady = false;
            }
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
                    AndroidDiagnostics.event("native.page", "ready", null, state.startupOperationId, null);
                    progressPanel.setVisibility(View.GONE);
                    publishImeVisibility();
                    flushResults();
                    publishDocumentState();
                    showImportConfirmation();
                    updater.foreground(MainActivity.this);
                }
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    AndroidDiagnostics.event("native.page", "error", null, state.startupOperationId, Math.abs(error.getErrorCode()));
                    showStartupError();
                }
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, android.webkit.WebResourceResponse response) {
                if (request.isForMainFrame()) {
                    AndroidDiagnostics.event("native.page", "error", null, state.startupOperationId, response.getStatusCode());
                    showStartupError();
                }
            }
            @Override public boolean onRenderProcessGone(WebView view, android.webkit.RenderProcessGoneDetail detail) {
                AndroidDiagnostics.event("native.page", "error", null, state.startupOperationId, detail.didCrash() ? 1 : 0);
                showStartupError();
                discardWebView();
                return true;
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
                if (!pageReady || state.operation != null || state.working || state.pendingImport != null
                        || updateInstallGate.isActive()) {
                    callback.onReceiveValue(null);
                    state.csvCallback = null;
                    toast("请先完成当前文件操作。");
                    return true;
                }
                openDocument("csv", null);
                return true;
            }
        });
        root.addView(web, 0, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private void buildStartupPanel() {
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
        diagnostics = new Button(this);
        diagnostics.setText("导出诊断日志");
        diagnostics.setMinHeight(dp(48));
        diagnostics.setOnClickListener(view -> openDocument("diagnostics", null));
        progressPanel.addView(diagnostics);
        diagnosticsResult = new TextView(this);
        diagnosticsResult.setGravity(Gravity.CENTER);
        progressPanel.addView(diagnosticsResult);
        root.addView(progressPanel, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        root.requestApplyInsets();
        updateDiagnosticsUi();
    }

    private void startRuntime() {
        if (!state.starting) state.startupOperationId = DiagnosticPolicy.newOperation();
        if (web == null) {
            try { buildViews(); if (updater != null) updater.attach(this); }
            catch (Throwable failure) {
                AndroidDiagnostics.event("native.startup", "initialize", failure, state.startupOperationId, null);
                discardWebView(); showStartupError(); return;
            }
        }
        pageReady = false;
        pageLoadFailed = false;
        progressPanel.setVisibility(View.VISIBLE);
        progress.setVisibility(View.VISIBLE);
        progressText.setText(R.string.starting);
        retry.setVisibility(View.GONE);
        if (state.runtime != null) { loadRuntimePage(state.runtime); return; }
        if (state.starting) return;
        state.starting = true;
        HostState target = state;
        Context app = getApplicationContext();
        String startupId = state.startupOperationId;
        RuntimeHost.WORKER.execute(() -> {
            JSONObject result = null;
            try { result = RuntimeHost.start(app); }
            catch (Throwable failure) { AndroidDiagnostics.event("native.startup", "error", failure, startupId, null); }
            JSONObject ready = result;
            MAIN.post(() -> {
                target.starting = false;
                target.runtime = ready;
                MainActivity owner = target.owner.get();
                if (owner != null) {
                    if (ready == null) owner.showStartupError();
                    else if (owner.web != null) owner.loadRuntimePage(ready);
                }
            });
        });
    }

    private void showStartupError() {
        if (speechInput != null) speechInput.cancelActive();
        pageReady = false;
        pageLoadFailed = true;
        progressPanel.setVisibility(View.VISIBLE);
        progress.setVisibility(View.GONE);
        progressText.setText(getString(R.string.startup_error_with_diagnostic, state.startupOperationId));
        retry.setVisibility(View.VISIBLE);
    }

    private void loadRuntimePage(JSONObject runtime) {
        try { web.loadUrl(runtime.optString("url")); }
        catch (Throwable failure) {
            AndroidDiagnostics.event("native.page", "load", failure, state.startupOperationId, null);
            showStartupError();
        }
    }

    void requestDocument(String operation, String template) {
        HostState target = state;
        NativeOperationArbiter.Lease lease = target.claimDocument(operation);
        if (lease == null) {
            MAIN.post(() -> {
                MainActivity owner = target.owner.get();
                if (owner != null) owner.documentBusy(operation);
            });
            return;
        }
        MAIN.post(() -> {
            MainActivity owner = target.owner.get();
            if (owner == null) target.releaseDocument(lease);
            else owner.openDocument(operation, template, lease);
        });
    }

    boolean openDocument(String operation, String template) {
        NativeOperationArbiter.Lease lease = state.claimDocument(operation);
        if (lease == null) {
            documentBusy(operation);
            return false;
        }
        openDocument(operation, template, lease);
        return true;
    }

    private void documentBusy(String operation) {
        if ("csv".equals(operation)) finishCsv(null, "请先完成当前文件操作。");
        else state.result(operation, "diagnostics".equals(operation) ? "busy" : "error",
            "请先完成当前文件操作。", null);
    }

    private void openDocument(String operation, String template, NativeOperationArbiter.Lease lease) {
        if (!state.ownsDocument(lease)) return;
        if ((!pageReady && !"diagnostics".equals(operation)) || state.operation != null || state.working
                || state.pendingImport != null || updateInstallGate.isActive()
                || (updater != null && !updater.fileOperationIdle())) {
            state.releaseDocument(lease);
            documentBusy(operation);
            return;
        }
        if ("template".equals(operation) && (template == null || template.getBytes(StandardCharsets.UTF_8).length > 65536)) {
            state.releaseDocument(lease);
            state.result(operation, "error", "CSV 模板内容过大或无效。", null);
            return;
        }
        state.operation = operation;
        state.template = template;
        state.diagnosticsId = DiagnosticPolicy.newOperation();
        AndroidDiagnostics.event("native.file", "start", null, state.diagnosticsId, null);
        if ("diagnostics".equals(operation)) {
            state.diagnosticsMessage = "请选择诊断 ZIP 的保存位置。";
            AndroidDiagnostics.event("native.file", "export", null, state.diagnosticsId, null);
        }
        updateDiagnosticsUi();
        boolean read = "import".equals(operation) || "pack-import".equals(operation) || "csv".equals(operation);
        Intent intent = new Intent(read ? Intent.ACTION_OPEN_DOCUMENT : Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType(read ? "*/*" : "diagnostics".equals(operation) ? "application/zip" : "template".equals(operation) ? "text/csv" : "application/octet-stream");
        if ("pack-import".equals(operation)) intent.putExtra(Intent.EXTRA_MIME_TYPES,
            new String[]{"application/zip", "application/octet-stream"});
        if (!read) intent.putExtra(Intent.EXTRA_TITLE, "template".equals(operation) ? "questions-template.csv"
            : "diagnostics".equals(operation) ? "bagu-diagnostics-" + java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss", java.util.Locale.ROOT).withZone(java.time.ZoneOffset.UTC).format(java.time.Instant.now()) + ".zip"
            : "export-questions".equals(operation) ? "bagu-questions.bagu-backup" : "bagu-progress.bagu-backup");
        int requestCode = DOCUMENT_REQUEST;
        if ("pack-import".equals(operation)) {
            requestCode = state.nextPackRequestCode;
            state.nextPackRequestCode = requestCode >= PACK_DOCUMENT_REQUEST_LIMIT
                ? PACK_DOCUMENT_REQUEST : requestCode + 1;
        } else {
            requestCode = state.nextDocumentRequestCode;
            state.nextDocumentRequestCode = requestCode == DOCUMENT_REQUEST
                ? DOCUMENT_REQUEST_SEQUENCE
                : requestCode >= DOCUMENT_REQUEST_LIMIT ? DOCUMENT_REQUEST_SEQUENCE : requestCode + 1;
        }
        state.documentRequestCode = requestCode;
        try { startActivityForResult(intent, requestCode); }
        catch (RuntimeException failure) {
            AndroidDiagnostics.event("native.file", "error", failure, state.diagnosticsId, null);
            state.operation = null;
            state.documentRequestCode = 0;
            state.template = null;
            state.releaseDocument(lease);
            if ("csv".equals(operation)) finishCsv(null, "系统文件选择器不可用。");
            else state.result(operation, "error", "系统文件选择器不可用。", null);
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        NativeOperationArbiter.Lease lease = state.documentLease;
        if (request != state.documentRequestCode || state.operation == null
                || !state.ownsDocument(lease)) return;
        String operation = state.operation;
        String template = state.template;
        state.operation = null;
        state.documentRequestCode = 0;
        state.template = null;
        Uri uri = data == null ? null : data.getData();
        if (result != RESULT_OK || uri == null) {
            state.releaseDocument(lease);
            if ("csv".equals(operation)) finishCsv(null, null);
            else state.result(operation, "cancelled", "已取消。", null);
            return;
        }
        if (!HostPolicy.isDocumentUri(uri.toString())) {
            state.releaseDocument(lease);
            if ("csv".equals(operation)) finishCsv(null, "请选择系统文件提供方中的文档。");
            else state.result(operation, "error", "请选择系统文件提供方中的文档。", null);
            return;
        }
        if ("diagnostics".equals(operation)) { exportDiagnostics(uri, lease); return; }
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
                if ("import".equals(operation) || "pack-import".equals(operation) || "csv".equals(operation)) {
                    byte[] bytes;
                    try (InputStream input = app.getContentResolver().openInputStream(uri)) {
                        bytes = HostPolicy.readBounded(input, "csv".equals(operation) ? 2 * 1024 * 1024 : 20 * 1024 * 1024);
                    }
                    if ("import".equals(operation) || "pack-import".equals(operation)) {
                        JSONObject preview = "pack-import".equals(operation)
                            ? RuntimeHost.inspectInterviewPack(bytes) : RuntimeHost.inspectArchive(bytes);
                        PendingImport pending = "pack-import".equals(operation)
                            ? PendingImport.interviewPack(bytes, previewMap(preview,
                                "pack_id", "name", "revision", "display_version", "question_count",
                                "experience_count", "installed_revision", "status"))
                            : PendingImport.backup(bytes, previewMap(preview,
                                "mode", "schema_version", "question_count", "created_at", "app_version"));
                        MAIN.post(() -> {
                            if (!target.ownsDocument(lease)) return;
                            target.working = false;
                            target.workingOperation = null;
                            target.pendingImport = pending;
                            MainActivity owner = target.owner.get();
                            if (owner != null) owner.showImportConfirmation();
                        });
                        return;
                    } else {
                        File directory = new File(app.getCacheDir(), "csv-imports");
                        if (!directory.isDirectory() && !directory.mkdirs()) throw new java.io.IOException("Cannot create cache");
                        File snapshot = new File(directory, UUID.randomUUID() + ".csv");
                        try (OutputStream output = new FileOutputStream(snapshot)) { output.write(bytes); }
                        csv = new Uri.Builder().scheme("content").authority(BuildConfig.APPLICATION_ID + ".imports")
                            .appendPath(snapshot.getName()).build();
                    }
                } else {
                    byte[] bytes = "template".equals(operation) ? template.getBytes(StandardCharsets.UTF_8)
                        : RuntimeHost.exportArchive("export-questions".equals(operation) ? "questions" : "progress");
                    try (OutputStream output = app.getContentResolver().openOutputStream(uri, "wt")) {
                        if (output == null) throw new java.io.IOException("Cannot open destination");
                        output.write(bytes);
                    }
                }
                success = true;
            } catch (Exception failure) { AndroidDiagnostics.event("native.file", "error", failure, target.diagnosticsId, null); }
            boolean ok = success;
            JSONObject restored = counts;
            Uri chosenCsv = csv;
            MAIN.post(() -> {
                if (!target.ownsDocument(lease)) return;
                target.working = false;
                target.workingOperation = null;
                MainActivity owner = target.owner.get();
                if ("csv".equals(operation)) {
                    ValueCallback<Uri[]> callback = target.csvCallback;
                    target.csvCallback = null;
                    target.releaseDocument(lease);
                    if (callback != null) callback.onReceiveValue(chosenCsv == null ? null : new Uri[]{chosenCsv});
                    if (!ok && owner != null) owner.toast("CSV 读取失败，请选择不超过 2 MiB 的文件。");
                } else {
                    String message = ok ? "操作完成。" : "pack-import".equals(operation)
                        ? "题包校验失败。请先结束当前练习，并选择有效且不超过 20 MiB 的 .bagu-pack。"
                        : "import".equals(operation)
                            ? "导入失败。请先结束当前练习，并选择有效且不超过 20 MiB 的备份。"
                        : ("export".equals(operation) || "export-questions".equals(operation))
                            ? "导出失败。请检查题目字段和题库大小（最多 10000 题、解压后 50 MiB、文件 20 MiB），并确认保存位置可写。原题库未改变。"
                            : "文件写入失败，请重试。";
                    target.releaseDocument(lease);
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
        updateDiagnosticsUi();
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

    /** SAF diagnostics never initializes Python, reads the database, or enters its worker. */
    private void exportDiagnostics(Uri uri, NativeOperationArbiter.Lease lease) {
        state.working = true;
        state.workingOperation = "diagnostics";
        state.diagnosticsMessage = "正在保存诊断日志…";
        updateDiagnosticsUi();
        HostState target = state;
        Context app = getApplicationContext();
        String operationId = state.diagnosticsId;
        AndroidDiagnostics.EXPORTER.execute(() -> {
            boolean success = false;
            try {
                byte[] archive = AndroidDiagnostics.export(app);
                try (OutputStream output = app.getContentResolver().openOutputStream(uri, "wt")) {
                    if (output == null) throw new java.io.IOException("Destination unavailable");
                    output.write(archive);
                }
                success = true; // Closing a provider stream is part of a successful save.
                AndroidDiagnostics.event("native.file", "done", null, operationId, null);
            } catch (Throwable failure) {
                AndroidDiagnostics.event("native.file", "error", failure, operationId, null);
            }
            boolean ok = success;
            MAIN.post(() -> {
                if (!target.ownsDocument(lease)) return;
                target.working = false;
                target.workingOperation = null;
                target.releaseDocument(lease);
                target.result("diagnostics", ok ? "ok" : "error", ok ? "诊断日志已保存。"
                    : "诊断日志保存失败。请检查或删除保存位置中的不完整文件后重试。", null);
            });
        });
    }

    private void updateDiagnosticsUi() {
        if (state == null || diagnostics == null) return;
        diagnostics.setEnabled(state.operation == null && !state.working && state.pendingImport == null && !updateInstallGate.isActive());
        if (diagnosticsResult != null) diagnosticsResult.setText(state.diagnosticsMessage == null ? "" : state.diagnosticsMessage);
    }

    private void discardWebView() {
        if (web == null) return;
        WebView previous = web;
        web = null;
        try { previous.removeJavascriptInterface("BaguNative"); if (root != null) root.removeView(previous); previous.destroy(); }
        catch (Throwable failure) { AndroidDiagnostics.event("native.page", "error", failure, null, null); }
    }

    private static Map<String,Object> previewMap(JSONObject source, String... keys) {
        LinkedHashMap<String,Object> result = new LinkedHashMap<>();
        for (String key : keys) {
            if (!source.has(key)) continue;
            Object value = source.opt(key);
            result.put(key, value == JSONObject.NULL ? null : value);
        }
        return result;
    }

    private void showImportConfirmation() {
        if (!resumed || !pageReady || isFinishing() || isDestroyed() || importDialog != null
            || state.pendingImport == null) return;
        final PendingImport pending = state.pendingImport;
        Map<String,Object> preview = pending.preview();
        boolean pack = "pack-import".equals(pending.operation());
        String message;
        if (pack) {
            Integer installed = preview.get("installed_revision") instanceof Number
                ? ((Number)preview.get("installed_revision")).intValue() : null;
            String status = String.valueOf(preview.get("status"));
            String change = "upgrade".equals(status) ? "将从 revision " + installed + " 升级。"
                : "installed".equals(status) ? "相同内容已安装，可幂等确认。"
                : "downgrade".equals(status) ? "本机 revision 更新，禁止降级。"
                : "conflict".equals(status) ? "同 revision 内容不同，禁止覆盖。" : "将安装为新题包。";
            message = "题包：" + preview.get("name")
                + "\n版本：" + preview.get("display_version") + " · revision " + preview.get("revision")
                + "\n内容：" + preview.get("question_count") + " 道题 · " + preview.get("experience_count") + " 个专题"
                + "\n状态：" + change
                + "\n\n题包内容只读；安装不会自动公开或上传任何文件。";
        } else {
            boolean pure = "questions".equals(preview.get("mode"));
            message = "类型：" + (pure ? "纯题库（保留本机复习进度）" : "含进度备份（覆盖同名题的复习进度）")
                + "\n题目：" + preview.get("question_count") + " 道"
                + "\n创建时间：" + preview.get("created_at")
                + "\n来源版本：" + preview.get("app_version")
                + "\n\n同名题答案和链接将被覆盖，包括空内容。本机其他题目及历史记录保留。建议先导出备份。";
        }
        AlertDialog.Builder builder = new AlertDialog.Builder(this)
            .setTitle(pack ? "题包导入预览" : "导入预览").setMessage(message)
            .setNegativeButton("取消", (dialog, which) -> cancelImport(pending))
            .setOnCancelListener(dialog -> cancelImport(pending));
        if (!pack || !("downgrade".equals(preview.get("status")) || "conflict".equals(preview.get("status")))) {
            builder.setPositiveButton(pack ? "确认安装" : "确认导入", (dialog, which) -> confirmImport(pending));
        }
        importDialog = builder.create();
        importDialog.setOnDismissListener(dialog -> importDialog = null);
        importDialog.show();
    }

    private void publishDocumentState() {
        String operation = state.operation != null ? state.operation
            : state.working ? state.workingOperation : state.pendingImport != null ? state.pendingImport.operation() : null;
        if (operation != null && !"csv".equals(operation)) {
            state.result(operation, "busy", "文件操作进行中，请完成选择或确认。", null);
        }
    }

    private void cancelImport(PendingImport expected) {
        NativeOperationArbiter.Lease lease = state.documentLease;
        if (state.pendingImport != expected || !state.ownsDocument(lease)) return;
        state.pendingImport = null;
        state.releaseDocument(lease);
        state.result(expected.operation(), "cancelled", "已取消导入，原数据未改变。", null);
    }

    private void confirmImport(PendingImport expected) {
        NativeOperationArbiter.Lease lease = state.documentLease;
        if (state.pendingImport != expected || !state.ownsDocument(lease)
                || !resumed || !pageReady || isDestroyed()) return;
        // Only this explicit button consumes the validated snapshot. No URI reread,
        // Bundle serialization, JS transfer, or automatic restore on recreation.
        state.pendingImport = null;
        state.working = true;
        state.workingOperation = expected.operation();
        byte[] snapshot = expected.snapshot();
        HostState target = state;
        RuntimeHost.WORKER.execute(() -> {
            JSONObject counts = null;
            boolean success = false;
            try {
                counts = "pack-import".equals(expected.operation())
                    ? RuntimeHost.installInterviewPack(snapshot) : RuntimeHost.restoreArchive(snapshot);
                success = true;
            } catch (Exception failure) { AndroidDiagnostics.event("native.file", "import", failure, target.diagnosticsId, null); }
            JSONObject result = counts;
            boolean ok = success;
            MAIN.post(() -> {
                if (!target.ownsDocument(lease)) return;
                target.working = false;
                target.workingOperation = null;
                boolean pack = "pack-import".equals(expected.operation());
                target.releaseDocument(lease);
                target.result(expected.operation(), ok ? "ok" : "error",
                    ok ? pack ? "题包安装完成。" : "导入完成。"
                        : pack ? "题包安装失败。请核对题包列表，并确认本轮练习已结束。"
                            : "导入失败。请核对题库与进度，并确认本轮练习已结束。", result);
            });
        });
    }

    void speech(String operation, String requestId) {
        if ("start".equals(operation)) speechOperationId = DiagnosticPolicy.newOperation();
        if (speechInput == null) { publishSpeech(new SpeechInput.Event(requestId, "error", null, "语音服务尚未就绪。")); return; }
        if ("cancel".equals(operation)) { speechInput.cancel(requestId); return; }
        if ("stop".equals(operation)) { speechInput.stop(requestId); return; }
        if (!resumed || !pageReady || isFinishing() || isDestroyed() || updateInstallGate.isActive()) {
            publishSpeech(new SpeechInput.Event(requestId, "error", null, "当前页面无法使用语音输入，请返回练习页重试。"));
            return;
        }
        speechInput.start(requestId);
    }

    private void requestMicrophone(Consumer<Boolean> reply) {
        if (microphoneReply != null) throw new IllegalStateException("Permission request pending");
        microphoneReply = reply;
        try { requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, MICROPHONE_REQUEST); }
        catch (RuntimeException failure) { microphoneReply = null; AndroidDiagnostics.event("native.speech", "permission", failure, null, null); throw failure; }
    }

    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(request, permissions, grants);
        if (request != MICROPHONE_REQUEST) return;
        Consumer<Boolean> reply = microphoneReply;
        microphoneReply = null;
        if (reply != null) reply.accept(grants.length == 1
            && grants[0] == PackageManager.PERMISSION_GRANTED);
    }

    private void publishSpeech(SpeechInput.Event event) {
        if ("error".equals(event.type)) AndroidDiagnostics.event("native.speech", "error", null, speechOperationId, null);
        if (!pageReady || web == null || isDestroyed()) return;
        try {
            JSONObject detail = new JSONObject().put("requestId", event.requestId).put("type", event.type);
            if (event.text != null) detail.put("text", event.text);
            if (event.message != null) detail.put("message", event.message + (speechOperationId == null ? "" : "\n反馈编号：" + speechOperationId));
            String json = JSONObject.quote(detail.toString()).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029");
            web.evaluateJavascript("window.dispatchEvent(new CustomEvent('bagu-speech',{detail:JSON.parse(" + json + ")}));", null);
        } catch (JSONException impossible) { throw new IllegalStateException(impossible); }
    }

    boolean updateForeground() { return resumed && pageReady && !isFinishing() && !isDestroyed(); }

    void publishUpdate(String detail) {
        if (!updateForeground() || web == null) return;
        String quoted = JSONObject.quote(detail).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029");
        web.evaluateJavascript("window.dispatchEvent(new CustomEvent('bagu-update',{detail:JSON.parse(" + quoted + ")}));", null);
    }

    private boolean nativeUpdateIdle() {
        return UpdateEngine.canInstall(updateForeground(), state.operation != null, state.working,
            state.pendingImport != null, speechInput != null && speechInput.isActive(), false, false, false, false);
    }

    boolean checkForUpdate(String operationId) {
        HostState target=state;
        NativeOperationArbiter.Lease lease=target.claimUpdate(operationId);
        return lease!=null&&updater.check(operationId,lease,()->target.releaseUpdate(lease));
    }

    boolean checkForAutomaticUpdate(String operationId) {
        HostState target=state;
        NativeOperationArbiter.Lease lease=target.claimUpdate(operationId);
        return lease!=null&&updater.checkAutomatic(operationId,lease,()->target.releaseUpdate(lease));
    }

    boolean downloadUpdate(String candidateId,String operationId) {
        HostState target=state;
        NativeOperationArbiter.Lease lease=target.claimUpdate(operationId);
        return lease!=null&&updater.download(candidateId,operationId,lease,()->target.releaseUpdate(lease));
    }

    boolean installUpdate(String candidateId,String operationId) {
        HostState target=state;
        NativeOperationArbiter.Lease lease=target.claimUpdate(operationId);
        return lease!=null&&updater.install(candidateId,operationId,lease,()->target.releaseUpdate(lease));
    }

    boolean nativeFileIdle() {
        return state != null && state.nativeIdle() && state.operation == null && !state.working && state.pendingImport == null
            && !updateInstallGate.isActive();
    }

    void validateUpdateInstallation(String operationId, UpdatePolicy.Release candidate) {
        final WebView expectedPage = web;
        updateInstallGate.begin(operationId, new UpdateInstallGate.Boundary() {
            public boolean idle() { return nativeUpdateIdle() && web == expectedPage && updater.installCurrent(operationId, candidate.id()); }
            public void reserve(String op, Consumer<Boolean> reply) {
                updatePrepareTimeout = () -> cancelUpdatePreparation("安装前检查超时，请重试。");
                MAIN.postDelayed(updatePrepareTimeout, 15000);
                callPage("baguReserveUpdateInstallation", op, reply);
            }
            public void noSession(Consumer<Boolean> reply) {
                RuntimeHost.WORKER.execute(() -> {
                    boolean idle;
                    try { idle = !RuntimeHost.hasOpenSession(); } catch (RuntimeException ignored) { idle = false; }
                    final boolean noSession = idle;
                    MAIN.post(() -> reply.accept(noSession));
                });
            }
            public void stillReserved(String op, Consumer<Boolean> reply) { callPage("baguUpdateReservationCurrent", op, reply); }
            public boolean launch() { return updater.launchInstaller(MainActivity.this, operationId, candidate); }
            public void abort(String op, String reason) { updater.blocked(op, reason); }
            public void release(String op) {
                if (updatePrepareTimeout != null) MAIN.removeCallbacks(updatePrepareTimeout);
                updatePrepareTimeout = null;
                if (!isDestroyed() && web == expectedPage) callPage("baguReleaseUpdateInstallation", op, ignored -> {});
            }
            private void callPage(String function, String op, Consumer<Boolean> reply) {
                String quoted = JSONObject.quote(op);
                expectedPage.evaluateJavascript("(function(){return typeof window." + function + "==='function'&&window." + function + "(" + quoted + ")===true;})()", value -> reply.accept("true".equals(value)));
            }
        });
    }

    void cancelUpdatePreparation(String reason) { updateInstallGate.cancel(reason); }

    @Override protected void onResume() { super.onResume(); resumed = true; showImportConfirmation(); if (updater != null) updater.foreground(this); }

    @Override protected void onPause() {
        cancelUpdatePreparation("应用已离开前台，请重新点击安装。");
        resumed = false;
        if (updater != null) updater.background(this);
        if (speechInput != null) speechInput.pause();
        super.onPause();
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
        if (state.pendingImport != null) {
            PendingImport pending = state.pendingImport;
            if (importDialog != null) {
                importDialog.setOnCancelListener(null);
                importDialog.setOnDismissListener(null);
                importDialog.dismiss();
                importDialog = null;
            }
            cancelImport(pending);
            return;
        }
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
        saved.putString("documentOperation", state.operation == null
            ? state.documentOperation() : state.operation);
        saved.putString("documentOperationId", state.diagnosticsId);
        saved.putBoolean("documentWorking", state.working);
        saved.putString("workingOperation", state.workingOperation);
        saved.putString("documentPendingImportKind",
            state.pendingImport == null ? null : state.pendingImport.operation());
    }

    @Override protected void onDestroy() {
        cancelUpdatePreparation("页面已重建，请重新点击安装。");
        if (updater != null) updater.detach(this);
        if (importDialog != null) {
            // Dismissing the old Activity is not user cancellation or confirmation.
            importDialog.setOnCancelListener(null);
            importDialog.setOnDismissListener(null);
            importDialog.dismiss();
            importDialog = null;
        }
        if (speechInput != null) speechInput.cancelActive();
        microphoneReply = null;
        finishCsv(null, null);
        if (!isChangingConfigurations()) {
            state.operation = null;
            state.documentRequestCode = 0;
            state.template = null;
            state.pendingImport = null;
            // A file worker or update/install handoff is process-owned and may outlive
            // this Activity. Its exact callback releases the shared process lease.
            if (!state.working) state.releaseDocument(state.documentLease);
        }
        if (state.owner.get() == this) state.owner.clear();
        if (Build.VERSION.SDK_INT >= 33 && backCallback != null) getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(backCallback);
        discardWebView();
        super.onDestroy();
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void toast(String text) { Toast.makeText(this, text, Toast.LENGTH_LONG).show(); }
}
