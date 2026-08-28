package io.github.ingnijm.baguhelper;

import java.io.*;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.charset.*;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

final class DiagnosticStore {
    static final int SOURCE_LIMIT = 2 * 1024 * 1024;
    static final int ROTATE_LIMIT = 1024 * 1024;
    static final int ZIP_LIMIT = 8 * 1024 * 1024;
    private static final int LINE_LIMIT = 8192;
    private final Path directory;
    private final Codec codec;
    interface Codec { Map<String, Object> parse(String text); String json(Object value); }
    DiagnosticStore(File directory, Codec codec) { this.directory = directory.toPath().toAbsolutePath().normalize(); this.codec = codec; }

    /** The trusted private files parent may have Android aliases; logs itself may not be a link. */
    private Path root(boolean create) throws IOException {
        if (Files.isSymbolicLink(directory)) throw new IOException("Unsafe diagnostic directory");
        if (create && !Files.exists(directory, LinkOption.NOFOLLOW_LINKS)) Files.createDirectory(directory);
        if (!Files.isDirectory(directory, LinkOption.NOFOLLOW_LINKS)) throw new IOException("Diagnostic directory unavailable");
        return directory.toRealPath();
    }
    private Path file(Path root, String name) throws IOException {
        if (!name.matches("bagu-(server|web|native)\\.log(?:\\.[1-3])?")) throw new IOException("Unknown diagnostic source");
        Path result = root.resolve(name);
        if (Files.isSymbolicLink(result) || !result.getParent().equals(root)) throw new IOException("Unsafe diagnostic source");
        if (Files.exists(result, LinkOption.NOFOLLOW_LINKS) && !Files.isRegularFile(result, LinkOption.NOFOLLOW_LINKS)) throw new IOException("Invalid diagnostic source");
        return result;
    }
    synchronized void append(String source, Map<String, Object> event) throws IOException {
        if (!source.equals("native") && !source.equals("web")) throw new IOException("Unknown diagnostic source");
        Map<String, Object> safe = DiagnosticPolicy.sanitize(event, source);
        if (safe.isEmpty()) return;
        byte[] bytes = (codec.json(safe) + "\n").getBytes(StandardCharsets.UTF_8);
        if (bytes.length > LINE_LIMIT) throw new IOException("Diagnostic event too large");
        Path root = root(true);
        Path current = file(root, "bagu-" + source + ".log");
        boolean separator = false;
        if (Files.exists(current, LinkOption.NOFOLLOW_LINKS)) {
            try (SeekableByteChannel previous = Files.newByteChannel(current, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
                if (previous.size() > 0) {
                    ByteBuffer last = ByteBuffer.allocate(1); previous.position(previous.size() - 1);
                    separator = previous.read(last) == 1 && last.array()[0] != '\n';
                }
            }
        }
        if (Files.exists(current, LinkOption.NOFOLLOW_LINKS) && Files.size(current) + bytes.length + (separator ? 1 : 0) > ROTATE_LIMIT) {
            // Validate every destination before any rotation mutation.
            for (int n = 1; n <= 3; n++) file(root, current.getFileName() + "." + n);
            Files.deleteIfExists(file(root, current.getFileName() + ".3"));
            for (int n = 2; n >= 0; n--) {
                Path previous = file(root, current.getFileName() + (n == 0 ? "" : "." + n));
                if (Files.exists(previous, LinkOption.NOFOLLOW_LINKS)) Files.move(previous, file(root, current.getFileName() + "." + (n + 1)), StandardCopyOption.REPLACE_EXISTING);
            }
            separator = false;
        }
        try (SeekableByteChannel output = Files.newByteChannel(current, StandardOpenOption.CREATE, StandardOpenOption.WRITE, StandardOpenOption.APPEND, LinkOption.NOFOLLOW_LINKS)) {
            if (separator) output.write(ByteBuffer.wrap(new byte[]{'\n'}));
            ByteBuffer data = ByteBuffer.wrap(bytes); while (data.hasRemaining()) output.write(data);
        }
    }

    byte[] export(Map<String, Object> metadata) throws IOException {
        Map<String, Snapshot> snapshots = new LinkedHashMap<>();
        for (String source : Arrays.asList("server", "web", "native")) snapshots.put(source, snapshot(source));
        Map<String, Object> manifest = new LinkedHashMap<>();
        manifest.put("format_version", 1); manifest.put("generated_at", Instant.now().toString());
        manifest.put("metadata", metadata); manifest.put("source_limit_bytes", SOURCE_LIMIT);
        Map<String, Object> sources = new LinkedHashMap<>();
        for (Map.Entry<String, Snapshot> entry : snapshots.entrySet()) sources.put(entry.getKey(), entry.getValue().description());
        manifest.put("sources", sources);
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(new FilterOutputStream(buffer) {
            private int written;
            @Override public void write(int value) throws IOException { if (++written > ZIP_LIMIT) throw new IOException("Diagnostic archive too large"); out.write(value); }
            @Override public void write(byte[] bytes, int offset, int length) throws IOException { if (length > ZIP_LIMIT - written) throw new IOException("Diagnostic archive too large"); written += length; out.write(bytes, offset, length); }
        })) {
            entry(zip, "manifest.json", codec.json(manifest).getBytes(StandardCharsets.UTF_8));
            for (Map.Entry<String, Snapshot> item : snapshots.entrySet()) {
                zip.putNextEntry(new ZipEntry(item.getKey() + ".jsonl"));
                for (byte[] line : item.getValue().lines) zip.write(line);
                zip.closeEntry();
            }
            entry(zip, "README.txt", ("八股助手本地诊断日志\n仅包含经过白名单过滤的结构化事件；不包含题目、答案、配置、密钥、数据库、logcat、ANR 或原生崩溃转储。\n每个来源最多保留最近 2 MiB 的完整记录；缺失、损坏、截断、不可读及记录时间范围见 manifest.json。\n反馈时请同时提供发生时间、复现步骤和界面中的反馈编号。请自行检查附件后再主动分享；应用不会自动上传。\n保存中断不代表导出成功，请检查或删除不完整文件后重试。\n").getBytes(StandardCharsets.UTF_8));
        }
        return buffer.toByteArray();
    }
    private static void entry(ZipOutputStream zip, String name, byte[] bytes) throws IOException { zip.putNextEntry(new ZipEntry(name)); zip.write(bytes); zip.closeEntry(); }

    private Snapshot snapshot(String source) {
        Snapshot result = new Snapshot();
        Path root;
        try { root = root(false); } catch (IOException | SecurityException failure) { result.unreadable = Files.exists(directory, LinkOption.NOFOLLOW_LINKS); return result; }
        for (int n = 3; n >= 0; n--) {
            try {
                Path path = file(root, "bagu-" + source + ".log" + (n == 0 ? "" : "." + n));
                if (!Files.exists(path, LinkOption.NOFOLLOW_LINKS)) continue;
                result.missing = false;
                try (SeekableByteChannel input = Files.newByteChannel(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
                    long end = input.size(); // A bounded snapshot: concurrent appends are not followed.
                    int scanLimit = (source.equals("server") ? 5 : 1) * 1024 * 1024 + LINE_LIMIT;
                    long begin = Math.max(0, end - scanLimit);
                    input.position(begin);
                    if (begin > 0) result.truncated = true;
                    ByteBuffer chunk = ByteBuffer.allocate(16384);
                    ByteArrayOutputStream line = new ByteArrayOutputStream();
                    boolean discard = begin > 0;
                    boolean partial = false;
                    while (input.position() < end) {
                        chunk.clear(); chunk.limit((int) Math.min(chunk.capacity(), end - input.position()));
                        int read = input.read(chunk); if (read <= 0) break;
                        chunk.flip();
                        while (chunk.hasRemaining()) {
                            byte b = chunk.get(); partial = true;
                            if (b == '\n') {
                                if (discard) result.dropped++;
                                else consume(result, line.toByteArray(), source);
                                line.reset(); discard = false; partial = false;
                            } else if (!discard) {
                                if (line.size() >= LINE_LIMIT) { discard = true; line.reset(); }
                                else line.write(b);
                            }
                        }
                    }
                    if (partial) result.dropped++; // Incomplete writes never enter the archive.
                }
            } catch (IOException | RuntimeException failure) { result.unreadable = true; }
        }
        return result;
    }
    private void consume(Snapshot result, byte[] raw, String source) {
        try {
            String line = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(raw)).toString();
            Map<String, Object> safe = DiagnosticPolicy.sanitize(codec.parse(line), source);
            if (safe.isEmpty()) { result.dropped++; return; }
            byte[] encoded = (codec.json(safe) + "\n").getBytes(StandardCharsets.UTF_8);
            if (encoded.length > SOURCE_LIMIT) { result.dropped++; return; }
            while (result.bytes + encoded.length > SOURCE_LIMIT) { result.bytes -= result.lines.removeFirst().length; result.times.removeFirst(); result.truncated = true; }
            result.lines.add(encoded); result.times.add((String) safe.get("time")); result.bytes += encoded.length;
        } catch (Exception | StackOverflowError failure) { result.dropped++; }
    }
    private static final class Snapshot {
        final Deque<byte[]> lines = new ArrayDeque<>();
        final Deque<String> times = new ArrayDeque<>();
        boolean missing = true;
        boolean unreadable;
        boolean truncated;
        int dropped;
        int bytes;
        Map<String, Object> description() {
            Map<String, Object> value = new LinkedHashMap<>();
            value.put("missing", missing); value.put("unreadable", unreadable); value.put("dropped", dropped);
            value.put("truncated", truncated); value.put("records", lines.size()); value.put("bytes", bytes);
            value.put("first_time", times.peekFirst()); value.put("last_time", times.peekLast());
            return value;
        }
    }
}
