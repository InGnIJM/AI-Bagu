package io.github.ingnijm.baguhelper;

import org.junit.Test;
import java.util.LinkedHashMap;
import java.util.Map;
import static org.junit.Assert.*;

public final class PendingImportTest {
    @Test public void packSnapshotIsImmutableAndPreviewIsStrictlyAllowlisted() {
        byte[] source = new byte[]{1, 2, 3, 4};
        Map<String,Object> preview = new LinkedHashMap<>();
        preview.put("pack_id", "private-pack"); preview.put("name", "Private pack");
        preview.put("revision", 3); preview.put("display_version", "2026.08");
        preview.put("question_count", 42); preview.put("experience_count", 5);
        preview.put("installed_revision", 2); preview.put("status", "upgrade");
        preview.put("answer", "PRIVATE_PACK_ANSWER_SENTINEL");
        preview.put("source", "content://private/provider/file");

        PendingImport pending = PendingImport.interviewPack(source, preview);
        source[0] = 99;
        preview.put("name", "mutated");
        byte[] first = pending.snapshot();
        first[1] = 88;

        assertArrayEquals(new byte[]{1, 2, 3, 4}, pending.snapshot());
        assertEquals("pack-import", pending.operation());
        assertEquals("Private pack", pending.preview().get("name"));
        assertEquals(8, pending.preview().size());
        assertFalse(pending.preview().containsKey("answer"));
        assertFalse(pending.preview().containsKey("source"));
        try { pending.preview().put("answer", "bad"); fail("preview must be immutable"); }
        catch (UnsupportedOperationException expected) { /* immutable safe metadata */ }
    }

    @Test public void backupAndPackKindsCannotBeConfused() {
        Map<String,Object> backup = new LinkedHashMap<>();
        backup.put("mode", "questions"); backup.put("schema_version", 3);
        backup.put("question_count", 7); backup.put("created_at", "2026-08-30T00:00:00Z");
        backup.put("app_version", "test"); backup.put("answer", "PRIVATE");

        PendingImport pending = PendingImport.backup(new byte[]{7}, backup);

        assertEquals("import", pending.operation());
        assertEquals(5, pending.preview().size());
        assertFalse(pending.preview().containsKey("answer"));
    }

    @Test public void nativeSourceSurvivesCloningWithoutEnteringPreview() {
        Map<String,Object> preview = new LinkedHashMap<>();
        preview.put("pack_id", "safe-pack"); preview.put("status", "upgrade");
        preview.put("source", "content://PRIVATE_PATH");

        PendingImport pending = PendingImport.interviewPack(new byte[]{5, 6}, preview,
            PendingImport.Source.BUNDLED_AUTO_PROMPT);

        assertEquals(PendingImport.Source.BUNDLED_AUTO_PROMPT, pending.source());
        assertFalse(pending.preview().containsKey("source"));
        assertFalse(pending.preview().toString().contains("PRIVATE_PATH"));
    }
}
