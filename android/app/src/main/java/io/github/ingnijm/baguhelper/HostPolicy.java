package io.github.ingnijm.baguhelper;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/** Pure policies shared by the Activity and native bridge; no Android framework dependencies. */
final class HostPolicy {
    private HostPolicy() {}

    static boolean isDocumentUri(String url) {
        try {
            URI uri = new URI(url);
            return "content".equalsIgnoreCase(uri.getScheme())
                && uri.getRawAuthority() != null && !uri.getRawAuthority().isEmpty();
        } catch (URISyntaxException | NullPointerException ignored) { return false; }
    }

    static boolean isLocalUrl(String url, int port) {
        try {
            URI uri = new URI(url);
            return port > 0 && "http".equalsIgnoreCase(uri.getScheme())
                && "127.0.0.1".equals(uri.getHost()) && uri.getPort() == port && uri.getRawUserInfo() == null;
        } catch (URISyntaxException | NullPointerException ignored) { return false; }
    }

    static boolean isExplicitReference(String url, boolean mainFrame, boolean userGesture) {
        if (!mainFrame || !userGesture) return false;
        try {
            URI uri = new URI(url);
            return ("http".equalsIgnoreCase(uri.getScheme()) || "https".equalsIgnoreCase(uri.getScheme()))
                && uri.getHost() != null && uri.getRawUserInfo() == null;
        } catch (URISyntaxException | NullPointerException ignored) { return false; }
    }

    static boolean validStorageKey(String key) {
        if (key == null || !key.startsWith("bagu-") || key.length() <= 5 || key.length() > 128) return false;
        for (int index = 0; index < key.length(); index++) if (Character.isISOControl(key.charAt(index))) return false;
        return true;
    }

    static boolean validStorageValue(String value) {
        return value != null && value.getBytes(StandardCharsets.UTF_8).length <= 262144;
    }

    static boolean canStore(Map<String, ?> stored, String key, String value) {
        if (!validStorageKey(key) || !validStorageValue(value)) return false;
        int count = 1;
        long total = key.getBytes(StandardCharsets.UTF_8).length + value.getBytes(StandardCharsets.UTF_8).length;
        for (Map.Entry<String, ?> entry : stored.entrySet()) {
            if (entry.getKey().equals(key) || !validStorageKey(entry.getKey())) continue;
            count++;
            total += entry.getKey().getBytes(StandardCharsets.UTF_8).length;
            if (entry.getValue() instanceof String) total += ((String) entry.getValue()).getBytes(StandardCharsets.UTF_8).length;
        }
        return count <= 512 && total <= 2 * 1024 * 1024;
    }

    static byte[] readBounded(InputStream input, int maximum) throws IOException {
        if (input == null || maximum < 0) throw new IOException("Invalid input stream");
        ByteArrayOutputStream output = new ByteArrayOutputStream(Math.min(maximum, 8192));
        byte[] buffer = new byte[8192];
        int count;
        while ((count = input.read(buffer)) != -1) {
            if (count > maximum - output.size()) throw new IOException("File exceeds allowed size");
            output.write(buffer, 0, count);
        }
        return output.toByteArray();
    }
}
