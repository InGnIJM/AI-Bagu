package io.github.ingnijm.baguhelper;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.*;

/** Privacy boundary for native, web and historical server diagnostic records. */
final class DiagnosticPolicy {
    static final int EVENT_BYTES = 2048;
    static final Set<String> FILES = words("bagu.py android_runtime.py index.html MainActivity.java RuntimeHost.java NativeBridge.java AndroidSpeechBackend.java SpeechInput.java UpdateEngine.java UpdateController.java UpdateIO.java BaguApplication.java DiagnosticPolicy.java DiagnosticStore.java AndroidDiagnostics.java");
    private static final Set<String> WEB_EVENTS = words("web.error web.unhandledrejection web.api web.stream web.action web.dropped web.speech");
    private static final Set<String> NATIVE_EVENTS = words("native.start native.startup native.page native.speech native.file native.update native.crash");
    private static final Set<String> SERVER_EVENTS = words("server.start server.stop runtime.start runtime.ready runtime.error request.start request.error request.done model.request model.connected model.first_reasoning model.first_content model.done model.error reference.request reference.done reference.error judge.context_ready judge.graded db.repair_multiple_open_sessions diagnostic.ready runtime.test");
    private static final Set<String> ERRORS = words("Error TypeError SyntaxError RangeError ReferenceError URIError EvalError AbortError NetworkError TimeoutError ValueError LookupError RuntimeError OSError IOError PermissionError FileNotFoundError FileExistsError IsADirectoryError NotADirectoryError ConnectionError ConnectionResetError ConnectionRefusedError BrokenPipeError HTTPError URLError JSONDecodeError ResponseParseError JudgeError GradeRejected SkipRejected SessionOpenError DatabaseError OperationalError IntegrityError IOException RuntimeException IllegalStateException IllegalArgumentException SecurityException NullPointerException ActivityNotFoundException JSONException PyException Exception OutOfMemoryError");
    private static final Set<String> STAGES = words("start ready error done cancelled busy permission load initialize dispatch stream connect parse write read export import check download verify install timeout");
    private static final Set<String> ROUTES = words("/ /index.html /api/stats /api/session /api/draw /api/skip /api/answer /api/answer/stream /api/reveal /api/review /api/settings /api/models /api/models/test /api/questions /api/questions/import /api/backup/export /api/backup/inspect /api/backup/restore /api/diagnostics/export /api/diagnostics/events other");

    static Map<String, Object> sanitize(Map<String, Object> input, String source) {
        if (input == null || !("web".equals(source) ? WEB_EVENTS : "native".equals(source) ? NATIVE_EVENTS : SERVER_EVENTS).contains(input.get("event"))) return Collections.emptyMap();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("time", timestamp(input.get("time")));
        out.put("level", words("INFO WARNING ERROR").contains(input.get("level")) ? input.get("level") : "INFO");
        out.put("event", input.get("event"));
        copyEnum(input, out, "stage", STAGES);
        copyEnum(input, out, "file", FILES);
        if (input.containsKey("error_type")) out.put("error_type", errorType(input.get("error_type")));
        copyPattern(input, out, "request_id", "r_[a-f0-9]{8,32}");
        copyPattern(input, out, "operation_id", "[wn]_[a-f0-9]{32}");
        for (String field : words("status count dropped line column error_code")) copyInteger(input, out, field, field.equals("line") ? 1 : 0, field.equals("status") ? 599 : Integer.MAX_VALUE);
        Object duration = input.get("duration_ms");
        if (duration instanceof Number && Double.isFinite(((Number) duration).doubleValue()) && ((Number) duration).doubleValue() >= 0 && ((Number) duration).doubleValue() <= 9007199254740991L) out.put("duration_ms", duration);
        String route = route(input.get("route"));
        if (route != null) out.put("route", route);
        if ("server".equals(source)) {
            copyPattern(input, out, "session_id", "s_[0-9]{8}_[a-f0-9]{8}");
            copyPattern(input, out, "kept_session_id", "s_[0-9]{8}_[a-f0-9]{8}");
            route = route(input.get("path"));
            if (route != null) out.put("route", route);
            for (String field : words("question_id closed_count prompt_chars user_answer_chars content_chars reasoning_chars reasoning_chunks content_chunks limit port")) copyInteger(input, out, field, 0, Integer.MAX_VALUE);
            for (String field : words("stream saw_done replayed used_stored_answer")) if (input.get(field) instanceof Boolean) out.put(field, input.get(field));
            copyEnum(input, out, "method", words("GET POST PUT DELETE HEAD OPTIONS"));
            copyEnum(input, out, "provider", words("custom openai deepseek qwen moonshot zhipu siliconflow"));
            copyEnum(input, out, "finish_reason", words("stop length content_filter tool_calls function_call"));
            if (input.containsKey("finish_reason") && input.get("finish_reason") == null) out.put("finish_reason", null);
            copyEnum(input, out, "compat_profile", words("default"));
            copyEnum(input, out, "reference_source", words("stored remote missing"));
            copyEnum(input, out, "outcome", words("ok error client_disconnected"));
            copyEnum(input, out, "grade", words("again hard good easy"));
        }
        if (!"web".equals(source) && input.get("frames") instanceof List) {
            List<Map<String, Object>> frames = new ArrayList<>();
            for (Object frame : (List<?>) input.get("frames")) {
                if (frames.size() >= 16) break;
                if (!(frame instanceof Map)) continue;
                Map<?, ?> value = (Map<?, ?>) frame;
                if (FILES.contains(value.get("file")) && integer(value.get("line"), 1, 999999)) {
                    Map<String, Object> safe = new LinkedHashMap<>(); safe.put("file", value.get("file")); safe.put("line", value.get("line")); frames.add(safe);
                }
            }
            out.put("frames", frames);
        }
        return out;
    }

    static String errorType(Object name) { return ERRORS.contains(name) ? (String) name : "Error"; }
    static String newOperation() { return "n_" + UUID.randomUUID().toString().replace("-", ""); }
    private static Set<String> words(String value) { return new HashSet<>(Arrays.asList(value.split(" "))); }
    private static void copyEnum(Map<String, Object> in, Map<String, Object> out, String key, Set<String> values) { if (values.contains(in.get(key))) out.put(key, in.get(key)); }
    private static void copyPattern(Map<String, Object> in, Map<String, Object> out, String key, String regex) { Object value = in.get(key); if (value instanceof String && ((String) value).matches(regex)) out.put(key, value); }
    private static boolean integer(Object value, long low, long high) { return (value instanceof Byte || value instanceof Short || value instanceof Integer || value instanceof Long) && ((Number) value).longValue() >= low && ((Number) value).longValue() <= high; }
    private static void copyInteger(Map<String, Object> in, Map<String, Object> out, String key, long low, long high) { if (integer(in.get(key), low, high)) out.put(key, in.get(key)); }
    private static String timestamp(Object value) {
        if (value instanceof String && ((String) value).length() <= 40) try { return OffsetDateTime.parse((String) value).toInstant().toString(); } catch (RuntimeException ignored) {}
        return Instant.now().toString();
    }
    private static String route(Object value) {
        if (!(value instanceof String)) return null;
        String text = (String) value;
        if (text.length() > 4096 || text.contains("://") || text.contains("?") || text.contains("#")) return null;
        if (ROUTES.contains(text)) return text;
        if (text.matches("/api/(models|questions|submissions)/[^/]+(?:/(activate|copy))?")) {
            String[] parts = text.split("/"); return "/api/" + parts[2] + "/:id" + (parts.length == 5 ? "/" + parts[4] : "");
        }
        return "other";
    }

    /** A bridge caller never creates an unbounded queue or unlimited disk writes. */
    static final class RateLimit {
        private long window = Long.MIN_VALUE;
        private int accepted;
        private long dropped;
        synchronized boolean accept(long now) {
            if (window == Long.MIN_VALUE || now < window || now - window >= 60000) { window = now; accepted = 0; }
            if (accepted >= 120) { drop(); return false; }
            accepted++; return true;
        }
        synchronized void drop() { if (dropped < Integer.MAX_VALUE) dropped++; }
        synchronized long takeDropped() { long result = dropped; dropped = 0; return result; }
    }
}
