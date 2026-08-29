package io.github.ingnijm.baguhelper;

import java.time.Instant;
import java.util.*;

/** One immutable, operation-owned diagnostic event; no remote or arbitrary text fields. */
final class UpdateDiagnostic {
    static final Set<String> STAGES = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(
        "initialize", "check", "download", "verify", "write", "permission", "install", "confirm")));
    static final Set<String> OUTCOMES = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(
        "started", "ok", "error", "cancelled", "blocked", "interrupted")));
    final String stage, outcome, channel, diagnosticId;
    final int errorCode;
    final Integer httpStatus;
    final long time, durationMs;
    final Throwable failure;

    UpdateDiagnostic(String stage, String outcome, String channel, String diagnosticId,
            long time, long durationMs, UpdateFailure failure) {
        if (!STAGES.contains(stage) || !OUTCOMES.contains(outcome) ||
            (channel != null && !Arrays.asList("beta", "stable").contains(channel)) ||
            diagnosticId == null || !diagnosticId.matches("n_[a-f0-9]{32}"))
            throw new IllegalArgumentException("Invalid update diagnostic");
        this.stage = stage; this.outcome = outcome; this.channel = channel; this.diagnosticId = diagnosticId;
        this.time = time; this.durationMs = Math.max(0, durationMs);
        this.errorCode = failure == null ? 0 : failure.code;
        this.httpStatus = failure == null ? null : failure.httpStatus;
        this.failure = failure;
    }

    Map<String,Object> toRecord() {
        Map<String,Object> value = new LinkedHashMap<>();
        value.put("time", Instant.ofEpochMilli(time).toString());
        value.put("event", "native.update");
        value.put("level", "error".equals(outcome) ? "ERROR" : "INFO");
        value.put("stage", stage); value.put("outcome", outcome);
        if (channel != null) value.put("channel", channel);
        value.put("operation_id", diagnosticId); value.put("error_code", errorCode);
        if (httpStatus != null) value.put("status", httpStatus);
        value.put("duration_ms", durationMs);
        return value;
    }
}
