package io.github.ingnijm.baguhelper;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** Immutable native-owned import snapshot. Raw bytes never cross the WebView bridge. */
final class PendingImport {
    enum Source { EXTERNAL_FILE, BUNDLED_AUTO_PROMPT, BUNDLED_SETTINGS }

    private static final Set<String> BACKUP_PREVIEW = immutableSet(
        "mode", "schema_version", "question_count", "created_at", "app_version"
    );
    private static final Set<String> PACK_PREVIEW = immutableSet(
        "pack_id", "name", "revision", "display_version", "question_count",
        "experience_count", "installed_revision", "status"
    );

    private static Set<String> immutableSet(String... values) {
        return Collections.unmodifiableSet(new HashSet<>(Arrays.asList(values)));
    }

    private final String operation;
    private final byte[] snapshot;
    private final Map<String,Object> preview;
    private final Source source;

    private PendingImport(String operation, byte[] bytes, Map<String,Object> preview,
            Set<String> allowed, Source source) {
        if (bytes == null || bytes.length == 0 || preview == null || source == null) {
            throw new IllegalArgumentException("Invalid pending import");
        }
        this.operation = operation;
        this.snapshot = bytes.clone();
        this.source = source;
        LinkedHashMap<String,Object> safe = new LinkedHashMap<>();
        for (String key : allowed) if (preview.containsKey(key)) safe.put(key, preview.get(key));
        this.preview = Collections.unmodifiableMap(safe);
    }

    static PendingImport backup(byte[] bytes, Map<String,Object> preview) {
        return new PendingImport("import", bytes, preview, BACKUP_PREVIEW, Source.EXTERNAL_FILE);
    }

    static PendingImport interviewPack(byte[] bytes, Map<String,Object> preview) {
        return interviewPack(bytes, preview, Source.EXTERNAL_FILE);
    }

    static PendingImport interviewPack(byte[] bytes, Map<String,Object> preview, Source source) {
        return new PendingImport("pack-import", bytes, preview, PACK_PREVIEW, source);
    }

    String operation() { return operation; }
    byte[] snapshot() { return snapshot.clone(); }
    Map<String,Object> preview() { return preview; }
    Source source() { return source; }
}
