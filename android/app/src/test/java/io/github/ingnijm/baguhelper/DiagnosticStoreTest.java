package io.github.ingnijm.baguhelper;

import org.junit.Test;
import static org.junit.Assert.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;

public class DiagnosticStoreTest {
    private final DiagnosticStore.Codec codec = new DiagnosticStore.Codec() {
        public Map<String, Object> parse(String text) { return UpdateIO.parse(text); }
        public String json(Object value) { return UpdateIO.json(value); }
    };
    private String event(String name, int count) {
        return "{\"time\":\"2026-08-28T00:00:00Z\",\"level\":\"ERROR\",\"event\":\"" + name + "\",\"count\":" + count + ",\"message\":\"sk-test secret answer\"}\n";
    }
    private void write(Path path, String text) throws IOException { Files.write(path, text.getBytes(StandardCharsets.UTF_8)); }
    private Map<String, String> unzip(byte[] archive) throws Exception {
        Map<String, String> files = new LinkedHashMap<>();
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(archive))) {
            ZipEntry entry; while ((entry = zip.getNextEntry()) != null) files.put(entry.getName(), new String(zip.readAllBytes(), StandardCharsets.UTF_8));
        }
        return files;
    }
    @Test public void emptyAndPartialExportsHaveFixedEntriesAndMissingDescription() throws Exception {
        Path root = Files.createTempDirectory("bagu-diagnostics-test");
        DiagnosticStore store = new DiagnosticStore(root.toFile(), codec);
        Map<String, String> empty = unzip(store.export(Collections.emptyMap()));
        assertEquals(new HashSet<>(Arrays.asList("manifest.json", "server.jsonl", "web.jsonl", "native.jsonl", "README.txt")), empty.keySet());
        assertTrue(empty.get("manifest.json").contains("\"missing\":true"));
        write(root.resolve("bagu-server.log"), event("request.error", 2) + "not json\n" + event("request.error", 3).trim());
        write(root.resolve("settings.json"), "must never be read");
        Map<String, String> partial = unzip(store.export(Collections.emptyMap()));
        assertTrue(partial.get("server.jsonl").contains("\"count\":2"));
        assertFalse(partial.get("server.jsonl").contains("\"count\":3"));
        assertFalse(partial.toString().contains("sk-test"));
        assertFalse(partial.toString().contains("must never"));
        assertTrue(partial.get("manifest.json").contains("\"dropped\":2"));
    }
    @Test public void rotationKeepsRecentRecordsAndNeverExportsNonAllowlistedFiles() throws Exception {
        Path root = Files.createTempDirectory("bagu-diagnostics-test");
        DiagnosticStore store = new DiagnosticStore(root.toFile(), codec);
        write(root.resolve("bagu-native.log.3"), event("native.file", 1));
        write(root.resolve("bagu-native.log.1"), event("native.file", 2));
        write(root.resolve("bagu-native.log"), event("native.file", 3));
        write(root.resolve("bagu-native.log.4"), event("native.file", 999));
        String result = unzip(store.export(Collections.emptyMap())).get("native.jsonl");
        assertTrue(result.indexOf("\"count\":1") < result.indexOf("\"count\":2"));
        assertTrue(result.indexOf("\"count\":2") < result.indexOf("\"count\":3"));
        assertFalse(result.contains("999"));
    }
    @Test public void limitsEachSourceAndDropsOversizedAndMalformedRecords() throws Exception {
        Path root = Files.createTempDirectory("bagu-diagnostics-test");
        DiagnosticStore store = new DiagnosticStore(root.toFile(), codec);
        try (BufferedWriter out = Files.newBufferedWriter(root.resolve("bagu-server.log"))) {
            out.write("x".repeat(9000) + "\n");
            for (int n = 0; n < 32000; n++) out.write(event("request.done", n));
        }
        byte[] archive = store.export(Collections.emptyMap());
        Map<String, String> files = unzip(archive);
        assertTrue(archive.length <= 8 * 1024 * 1024);
        assertTrue(files.get("server.jsonl").getBytes(StandardCharsets.UTF_8).length <= 2 * 1024 * 1024);
        assertTrue(files.get("server.jsonl").contains("\"count\":31999"));
        assertTrue(files.get("manifest.json").contains("\"truncated\":true"));
    }
    @Test public void rateLimiterCountsSuppressedEventsAndResetsWindow() {
        DiagnosticPolicy.RateLimit limit = new DiagnosticPolicy.RateLimit();
        for (int i = 0; i < 120; i++) assertTrue(limit.accept(1));
        assertFalse(limit.accept(1)); assertFalse(limit.accept(59999));
        assertEquals(2, limit.takeDropped()); assertEquals(0, limit.takeDropped());
        assertTrue(limit.accept(60001));
    }
    @Test public void appendRotatesBoundedFilesAndPreservesFreshRecordAfterIncompleteTail() throws Exception {
        Path root = Files.createTempDirectory("bagu-diagnostics-test");
        DiagnosticStore store = new DiagnosticStore(root.toFile(), codec);
        write(root.resolve("bagu-native.log"), "incomplete-private-text");
        store.append("native", codec.parse(event("native.file", 1)));
        assertTrue(unzip(store.export(Collections.emptyMap())).get("native.jsonl").contains("\"count\":1"));
        write(root.resolve("bagu-native.log"), "x".repeat(1024 * 1024));
        store.append("native", codec.parse(event("native.file", 2)));
        assertTrue(Files.exists(root.resolve("bagu-native.log.1")));
        assertTrue(Files.size(root.resolve("bagu-native.log")) < 1024);
        assertThrows(IOException.class, () -> store.append("server", codec.parse(event("request.error", 3))));
    }
    @Test public void invalidUtf8AndDirectoryInPlaceOfLogNeverAbortOtherSources() throws Exception {
        Path root = Files.createTempDirectory("bagu-diagnostics-test");
        DiagnosticStore store = new DiagnosticStore(root.toFile(), codec);
        Files.write(root.resolve("bagu-server.log"), new byte[]{(byte)0xc3, 0x28, 0x0a});
        Files.createDirectory(root.resolve("bagu-web.log"));
        write(root.resolve("bagu-native.log"), event("native.start", 8));
        Map<String, String> files = unzip(store.export(Collections.emptyMap()));
        assertTrue(files.get("native.jsonl").contains("\"count\":8"));
        assertEquals("", files.get("server.jsonl"));
        assertEquals("", files.get("web.jsonl"));
        assertTrue(files.get("manifest.json").contains("\"unreadable\":true"));
    }
}
