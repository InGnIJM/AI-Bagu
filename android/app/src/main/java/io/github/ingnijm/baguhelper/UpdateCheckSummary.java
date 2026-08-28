package io.github.ingnijm.baguhelper;

import java.nio.charset.StandardCharsets;
import java.util.*;

/** Bounded persisted history, separate from installer-critical state and candidate metadata. */
final class UpdateCheckSummary {
    static final int MAX_BYTES = 4096;
    private static final long MAX_INTEGER = 9007199254740991L;
    private static final Set<String> RESULTS = set("unknown", "checking", "latest", "available", "partial-error", "error", "interrupted");
    private static final Set<String> CHANNEL_RESULTS = set("pending", "checking", "empty", "available", "no-update", "incompatible", "error", "interrupted", "not-checked");
    final String diagnosticId, status;
    final long startedAt, completedAt;
    final int errorCode;
    private final Map<String,Map<String,Object>> channels;

    private UpdateCheckSummary(String id, long start, long end, String status, int code, Map<String,Map<String,Object>> channels) {
        diagnosticId = id; startedAt = start; completedAt = end; this.status = status; errorCode = code;
        this.channels = Collections.unmodifiableMap(new LinkedHashMap<>(channels));
    }
    static UpdateCheckSummary unknown() { return new UpdateCheckSummary(null, 0, 0, "unknown", 0, Collections.emptyMap()); }
    static UpdateCheckSummary start(String id, long now, List<String> channels) {
        Map<String,Map<String,Object>> pending = new LinkedHashMap<>();
        for (String channel : channels) pending.put(channel, result("pending", 0, null, 0));
        return new UpdateCheckSummary(id, now, 0, "checking", 0, pending);
    }
    UpdateCheckSummary channel(String name, String result, int code, Integer httpStatus, long duration) {
        Map<String,Map<String,Object>> next = new LinkedHashMap<>(channels);
        if (!next.containsKey(name)) throw new IllegalArgumentException("Unexpected update channel");
        next.put(name, result(result, code, httpStatus, Math.max(0, duration)));
        return new UpdateCheckSummary(diagnosticId, startedAt, completedAt, status, errorCode, next);
    }
    UpdateCheckSummary finish(String status, int code, long now) {
        Map<String,Map<String,Object>> next = new LinkedHashMap<>(channels);
        for (Map.Entry<String,Map<String,Object>> entry : channels.entrySet()) {
            if (set("pending", "checking").contains(entry.getValue().get("status")))
                next.put(entry.getKey(), result("not-checked", 0, null, 0));
        }
        return new UpdateCheckSummary(diagnosticId, startedAt, Math.max(startedAt, now), status, code, next);
    }
    Map<String,Object> toMap() {
        Map<String,Object> out = new LinkedHashMap<>();
        out.put("diagnosticId", diagnosticId); out.put("startedAt", startedAt); out.put("completedAt", completedAt);
        out.put("status", status); out.put("errorCode", errorCode); out.put("channels", channels);
        return Collections.unmodifiableMap(out);
    }
    String json() {
        String value = UpdateIO.json(toMap());
        if (value.getBytes(StandardCharsets.UTF_8).length > MAX_BYTES) throw new IllegalArgumentException("Update summary too large");
        return value;
    }
    private UpdateCheckSummary interrupted() {
        if (!"checking".equals(status)) return this;
        Map<String,Map<String,Object>> next = new LinkedHashMap<>(channels);
        for (Map.Entry<String,Map<String,Object>> entry : channels.entrySet()) {
            if (set("checking", "pending").contains(entry.getValue().get("status")))
                next.put(entry.getKey(), result("interrupted", 0, null, 0));
        }
        return new UpdateCheckSummary(diagnosticId, startedAt, 0, "interrupted", 0, next);
    }
    static UpdateCheckSummary restore(String json, String channel) {
        if (json == null || json.length() > MAX_BYTES || json.getBytes(StandardCharsets.UTF_8).length > MAX_BYTES) return unknown();
        try {
            Map<String,Object> input = UpdateIO.parse(json);
            if (!input.keySet().equals(set("diagnosticId", "startedAt", "completedAt", "status", "errorCode", "channels"))) return unknown();
            String status = text(input.get("status"), RESULTS);
            if ("unknown".equals(status)) return unknown();
            Object id = input.get("diagnosticId");
            if (!(id instanceof String) || !((String) id).matches("n_[a-f0-9]{32}")) return unknown();
            long start = number(input.get("startedAt")), end = number(input.get("completedAt"));
            int code = code(input.get("errorCode"));
            if (set("error", "partial-error").contains(status) != (code != 0)) return unknown();
            if (set("checking", "interrupted").contains(status) ? end != 0 : end < start) return unknown();
            if (!(input.get("channels") instanceof Map)) return unknown();
            Map<?,?> raw = (Map<?,?>) input.get("channels");
            if (!raw.keySet().equals(new HashSet<>(UpdatePolicy.channels(channel)))) return unknown();
            Map<String,Map<String,Object>> channels = new LinkedHashMap<>();
            int failures = 0, available = 0, unchecked = 0;
            for (String name : UpdatePolicy.channels(channel)) {
                if (!(raw.get(name) instanceof Map)) return unknown();
                Map<?,?> item = (Map<?,?>) raw.get(name);
                if (!item.keySet().equals(set("status", "errorCode", "httpStatus", "durationMs"))) return unknown();
                String outcome = text(item.get("status"), CHANNEL_RESULTS);
                int errorCode = code(item.get("errorCode"));
                if ("error".equals(outcome) != (errorCode != 0)) return unknown();
                if ("error".equals(outcome)) failures++;
                if ("available".equals(outcome)) available++;
                if ("not-checked".equals(outcome)) unchecked++;
                Integer http = item.get("httpStatus") == null ? null : Math.toIntExact(number(item.get("httpStatus")));
                if (http != null && (http < 100 || http > 599)) return unknown();
                if (!set("checking", "interrupted").contains(status) && set("pending", "checking", "interrupted").contains(outcome)) return unknown();
                channels.put(name, result(outcome, errorCode, http, number(item.get("durationMs"))));
            }
            if (set("latest", "available").contains(status) && (failures > 0 || unchecked > 0)) return unknown();
            if ("latest".equals(status) && available > 0 || "available".equals(status) && available == 0) return unknown();
            if ("partial-error".equals(status) && (failures == 0 || failures == channels.size() || unchecked > 0)) return unknown();
            return new UpdateCheckSummary((String) id, start, end, status, code, channels).interrupted();
        } catch (RuntimeException invalid) { return unknown(); }
    }
    String description() {
        if ("interrupted".equals(status)) return "上次检查中断，请重试。";
        if ("latest".equals(status)) return "当前没有兼容的新版本。";
        if ("available".equals(status)) return "发现可用版本；由你决定下载和安装。";
        if (errorCode == 0) return "checking".equals(status) ? "正在检查更新…" : "可手动检查更新。";
        StringBuilder out = new StringBuilder("partial-error".equals(status) ? "检查未完整成功。" : "检查失败。");
        boolean explained = false;
        for (Map.Entry<String,Map<String,Object>> entry : channels.entrySet()) {
            Map<String,Object> result = entry.getValue();
            int code = (Integer) result.get("errorCode");
            if (code == 0) continue;
            out.append(" ").append("beta".equals(entry.getKey()) ? "Beta" : "稳定").append("通道：")
                .append(UpdateFailure.reason(code, (Integer) result.get("httpStatus"))).append("。");
            explained |= code == errorCode;
        }
        if (!explained) out.append(" ").append(UpdateFailure.reason(errorCode, null)).append("。");
        return out.append(" 无法确认是否存在更高版本，可重试或导出诊断日志。").toString();
    }
    private static Map<String,Object> result(String status, int code, Integer http, long duration) {
        Map<String,Object> out = new LinkedHashMap<>(); out.put("status", status); out.put("errorCode", code);
        out.put("httpStatus", http); out.put("durationMs", duration); return Collections.unmodifiableMap(out);
    }
    private static Set<String> set(String... values) { return new HashSet<>(Arrays.asList(values)); }
    private static String text(Object value, Set<String> allowed) { if (!allowed.contains(value)) throw new IllegalArgumentException(); return (String) value; }
    private static long number(Object value) {
        if (!(value instanceof Long || value instanceof Integer)) throw new IllegalArgumentException();
        long result = ((Number) value).longValue();
        if (result < 0 || result > MAX_INTEGER) throw new IllegalArgumentException(); return result;
    }
    private static int code(Object value) {
        int code = Math.toIntExact(number(value));
        if (code != 0 && !UpdateFailure.validCode(code)) throw new IllegalArgumentException(); return code;
    }
}
