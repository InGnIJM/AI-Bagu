package io.github.ingnijm.baguhelper;

import java.util.function.Consumer;

/** Main-thread request controller. The platform supplies permission, engine and timer boundaries. */
final class SpeechInput {
    interface Cancellable { void cancel(); }
    interface Scheduler { Cancellable after(long delay, Runnable job); }
    interface Listener {
        void ready();
        void partial(String text);
        void result(String text);
        void error(String message);
        void ended();
    }
    interface Engine { void start(); void stop(); void cancel(); void destroy(); }
    interface Backend {
        boolean available();
        boolean hasPermission();
        void requestPermission(Consumer<Boolean> reply);
        Engine create(Listener listener);
    }
    static final class Event {
        final String requestId, type, text, message;
        Event(String requestId, String type, String text, String message) {
            this.requestId = requestId; this.type = type; this.text = text; this.message = message;
        }
    }

    private final Backend backend;
    private final Scheduler scheduler;
    private final Consumer<Event> events;
    private Request active;

    private static final class Request {
        final String id;
        Engine engine;
        Cancellable timeout;
        int timerVersion;
        boolean ready, stopping, paused;
        Request(String id) { this.id = id; }
    }

    SpeechInput(Backend backend, Scheduler scheduler, Consumer<Event> events) {
        this.backend = backend; this.scheduler = scheduler; this.events = events;
    }

    static void validateRequestId(String requestId) {
        if (requestId == null || !requestId.matches("[A-Za-z0-9_-]{1,80}")) {
            throw new IllegalArgumentException("语音请求标识无效。");
        }
    }

    void start(String requestId) {
        validateRequestId(requestId);
        cancelActive();
        Request request = new Request(requestId);
        active = request;
        try {
            if (!backend.available()) { fail(request, "系统语音识别服务不可用，请使用文字输入。"); return; }
            if (backend.hasPermission()) { listen(request); return; }
            deadline(request, 60000);
            backend.requestPermission(granted -> {
                if (active != request) return;
                if (!Boolean.TRUE.equals(granted)) fail(request, "未获得麦克风权限，请允许权限后重试。");
                else if (request.paused) finish(request, "cancelled", null, null);
                else listen(request);
            });
        } catch (RuntimeException ignored) { fail(request, "无法启动语音识别，请稍后重试。"); }
    }

    private void listen(Request request) {
        if (active != request) return;
        try {
            request.engine = backend.create(new Listener() {
                @Override public void ready() {
                    if (active != request || request.ready || request.stopping) return;
                    request.ready = true;
                    deadline(request, 60000);
                    emit(request, "ready", null, null);
                }
                @Override public void partial(String text) {
                    if (active != request || text == null || text.trim().isEmpty()) return;
                    if (text.length() > 20000) { fail(request, "语音内容过长，请分段输入。"); return; }
                    emit(request, "partial", text, null);
                }
                @Override public void result(String text) {
                    if (active != request) return;
                    if (text == null || text.trim().isEmpty()) { fail(request, "未识别到语音，请重试。"); return; }
                    if (text.length() > 20000) { fail(request, "语音内容过长，请分段输入。"); return; }
                    finish(request, "result", text, null);
                }
                @Override public void error(String message) { fail(request, message); }
                @Override public void ended() {
                    if (active != request || request.stopping) return;
                    request.stopping = true;
                    deadline(request, 10000);
                }
            });
            deadline(request, 15000);
            request.engine.start();
        } catch (RuntimeException ignored) { fail(request, "无法启动语音识别，请稍后重试。"); }
    }

    void stop(String requestId) {
        validateRequestId(requestId);
        Request request = active;
        if (request == null || !request.id.equals(requestId) || request.stopping) return;
        if (request.engine == null) { cancelActive(); return; }
        request.stopping = true;
        deadline(request, 10000);
        try { request.engine.stop(); }
        catch (RuntimeException ignored) { fail(request, "无法结束语音识别，请重试。"); }
    }

    void cancel(String requestId) {
        validateRequestId(requestId);
        if (active != null && active.id.equals(requestId)) cancelActive();
    }

    void cancelActive() {
        if (active != null) finish(active, "cancelled", null, null);
    }

    boolean isActive() { return active != null; }

    void pause() {
        if (active == null) return;
        // The Android permission dialog pauses the Activity. Preserve its decision
        // for an explicit denial message, but never auto-listen after this pause.
        if (active.engine == null) active.paused = true;
        else cancelActive();
    }

    private void deadline(Request request, long millis) {
        if (request.timeout != null) request.timeout.cancel();
        int version = ++request.timerVersion;
        request.timeout = scheduler.after(millis, () -> {
            if (active == request && request.timerVersion == version) fail(request, "语音识别超时，请重试。");
        });
    }

    private void fail(Request request, String message) { finish(request, "error", null, message); }

    private void finish(Request request, String type, String text, String message) {
        if (active != request) return;
        // Invalidate before cancelling: engines may synchronously report an error from cancel().
        active = null;
        if (request.timeout != null) request.timeout.cancel();
        if (request.engine != null) {
            try { request.engine.cancel(); } catch (RuntimeException ignored) { /* Still destroy. */ }
            try { request.engine.destroy(); } catch (RuntimeException ignored) { /* No private engine details. */ }
        }
        emit(request, type, text, message);
    }

    private void emit(Request request, String type, String text, String message) {
        events.accept(new Event(request.id, type, text, message));
    }
}
