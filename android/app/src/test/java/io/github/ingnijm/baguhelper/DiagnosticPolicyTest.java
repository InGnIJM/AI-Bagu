package io.github.ingnijm.baguhelper;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.*;

public class DiagnosticPolicyTest {
    private Map<String, Object> record(String event) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("event", event); value.put("time", "2026-08-28T01:02:03.000Z"); value.put("level", "ERROR");
        return value;
    }

    @Test public void keepsOnlyAllowlistedTypedFieldsFromLegacyServerLogs() {
        Map<String, Object> input = record("model.error");
        input.put("model", "private model"); input.put("provider", "sk-test");
        input.put("message", "secret answer"); input.put("error_type", "IOException");
        input.put("duration_ms", 2.5); input.put("stream", true);
        Map<String, Object> safe = DiagnosticPolicy.sanitize(input, "server");
        assertEquals("model.error", safe.get("event"));
        assertEquals("IOException", safe.get("error_type"));
        assertEquals(2.5, safe.get("duration_ms"));
        assertEquals(true, safe.get("stream"));
        assertFalse(safe.containsKey("model")); assertFalse(safe.containsKey("provider"));
        assertFalse(safe.containsKey("message"));
    }

    @Test public void dropsInvalidCorrelationLocationNumbersAndArbitraryErrorNames() {
        Map<String, Object> input = record("web.error");
        input.put("request_id", "r_secret"); input.put("operation_id", "w_" + "a".repeat(32));
        input.put("route", "https://secret.example/api/models/secret?key=sk-test");
        input.put("file", "/private/answer.js"); input.put("line", -1);
        input.put("status", 600); input.put("duration_ms", Double.NaN);
        input.put("error_type", "SecretErrorName"); input.put("count", true);
        Map<String, Object> safe = DiagnosticPolicy.sanitize(input, "web");
        assertEquals("Error", safe.get("error_type"));
        assertEquals("w_" + "a".repeat(32), safe.get("operation_id"));
        for (String key : List.of("request_id", "route", "file", "line", "status", "duration_ms", "count"))
            assertFalse(key, safe.containsKey(key));
    }

    @Test public void rejectsCrossSourceEventsAndNormalizesLegacyPathWithoutIdentifiers() {
        assertTrue(DiagnosticPolicy.sanitize(record("native.crash"), "web").isEmpty());
        assertTrue(DiagnosticPolicy.sanitize(record("private.event"), "server").isEmpty());
        Map<String, Object> input = record("request.error");
        input.put("path", "/api/models/private-name/activate");
        input.put("request_id", "r_0123abcd");
        Map<String, Object> safe = DiagnosticPolicy.sanitize(input, "server");
        assertEquals("/api/models/:id/activate", safe.get("route"));
        assertEquals("r_0123abcd", safe.get("request_id"));
        assertFalse(safe.containsKey("path"));
    }
}
