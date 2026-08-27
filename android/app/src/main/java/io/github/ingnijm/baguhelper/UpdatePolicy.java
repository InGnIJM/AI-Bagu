package io.github.ingnijm.baguhelper;

import java.net.URI;
import java.time.Instant;
import java.util.*;

/** Pure update policy. Remote data can never replace the local trust anchor. */
final class UpdatePolicy {
    static final String PACKAGE = "io.github.ingnijm.baguhelper";
    static final String TRUSTED_CERTIFICATE = "ac92a24f30a5e6c10c4ced0d0db89124f39f36e00778fef6ca3ba4973bdf0ee3";
    static final String FEED_ROOT = "https://ingnijm.github.io/AI-Bagu/updates/";
    static final long MAX_APK = 128L * 1024 * 1024;
    static final int MAX_FEED = 64 * 1024;
    static final long CHECK_INTERVAL = 24L * 60 * 60 * 1000;
    private static final Set<String> RELEASE_FIELDS = new HashSet<>(Arrays.asList(
        "versionName", "versionCode", "distribution", "packageName", "minSdk", "abi",
        "apkUrl", "size", "sha256", "releaseUrl", "publishedAt", "notes"));

    private UpdatePolicy() {}

    static boolean shouldAutomaticallyCheck(boolean enabled, long now, long lastAttempt) {
        return enabled && (lastAttempt <= 0 || now < lastAttempt || now - lastAttempt >= CHECK_INTERVAL);
    }

    static List<String> channels(String channel) {
        if ("stable".equals(channel)) return Collections.singletonList("stable");
        if ("beta".equals(channel)) return Arrays.asList("beta", "stable");
        throw invalid();
    }

    static void validateOperationId(String id) {
        if (id == null || !id.matches("[A-Za-z0-9_-]{1,80}")) throw invalid();
    }

    static final class Release {
        final String versionName, abi, apkUrl, sha256, releaseUrl, publishedAt, notes, channel;
        final long versionCode, size;
        final int minSdk;

        private Release(Map<?, ?> source, String channel) {
            this.channel = channel;
            versionName = string(source, "versionName", 64);
            if (!versionName.matches("\\d+\\.\\d+\\.\\d+(-beta\\.\\d+)?") ||
                versionName.contains("-beta.") != channel.equals("beta")) throw invalid();
            versionCode = integer(source, "versionCode", 1, 2100000000);
            size = integer(source, "size", 1, MAX_APK);
            minSdk = (int) integer(source, "minSdk", 29, 10000);
            abi = string(source, "abi", 32);
            if (!"arm64-v8a".equals(abi) || !"public".equals(source.get("distribution")) ||
                !PACKAGE.equals(source.get("packageName"))) throw invalid();
            sha256 = string(source, "sha256", 64);
            if (!sha256.matches("[0-9a-f]{64}")) throw invalid();
            apkUrl = string(source, "apkUrl", 2048);
            String name = "bagu-" + versionName + "-public-arm64-v8a.apk";
            if (!apkUrl.equals("https://github.com/InGnIJM/AI-Bagu/releases/download/v" + versionName + "/" + name)) throw invalid();
            releaseUrl = string(source, "releaseUrl", 2048);
            if (!releaseUrl.equals("https://github.com/InGnIJM/AI-Bagu/releases/tag/v" + versionName)) throw invalid();
            publishedAt = string(source, "publishedAt", 32);
            if (!publishedAt.matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z")) throw invalid();
            try { Instant.parse(publishedAt); } catch (RuntimeException error) { throw invalid(); }
            notes = string(source, "notes", 12000);
        }

        String id() { return versionCode + ":" + sha256; }

        Map<String, Object> toMap() {
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("versionName", versionName); result.put("versionCode", versionCode);
            result.put("distribution", "public"); result.put("packageName", PACKAGE);
            result.put("minSdk", minSdk); result.put("abi", abi); result.put("apkUrl", apkUrl);
            result.put("size", size); result.put("sha256", sha256); result.put("releaseUrl", releaseUrl);
            result.put("publishedAt", publishedAt); result.put("notes", notes);
            return result;
        }
    }

    static Release parseFeed(Map<String, Object> feed, String channel) {
        channels(channel);
        if (!feed.keySet().equals(new HashSet<>(Arrays.asList("schema_version", "channel", "release"))) ||
            integer(feed, "schema_version", 1, 1) != 1 || !channel.equals(feed.get("channel"))) throw invalid();
        Object raw = feed.get("release");
        if (raw == null) return null;
        if (!(raw instanceof Map)) throw invalid();
        Map<?, ?> release = (Map<?, ?>) raw;
        if (!release.keySet().equals(RELEASE_FIELDS)) throw invalid();
        return new Release(release, channel);
    }

    static Release choose(List<Release> releases, long installed, int sdk, List<String> abis) {
        Release selected = null;
        for (Release candidate : releases) {
            if (candidate == null || candidate.versionCode <= installed || candidate.minSdk > sdk ||
                !abis.contains(candidate.abi)) continue;
            if (selected != null && selected.versionCode == candidate.versionCode &&
                !selected.id().equals(candidate.id())) throw invalid();
            if (selected == null || candidate.versionCode > selected.versionCode) selected = candidate;
        }
        return selected;
    }

    static void validateRedirect(String url) {
        try {
            URI uri = new URI(url);
            if (!"https".equals(uri.getScheme()) || uri.getUserInfo() != null ||
                uri.getFragment() != null || (uri.getPort() != -1 && uri.getPort() != 443) ||
                !Arrays.asList("github.com", "release-assets.githubusercontent.com").contains(uri.getHost())) throw invalid();
        } catch (Exception error) { throw invalid(); }
    }

    static void validateArchive(Release candidate, String packageName, long versionCode,
            String versionName, int minSdk, String certificate, Set<String> abis,
            long installedCode, int deviceSdk) {
        if (!PACKAGE.equals(packageName) || candidate.versionCode != versionCode ||
            !candidate.versionName.equals(versionName) || candidate.minSdk != minSdk || minSdk > deviceSdk ||
            versionCode <= installedCode || !TRUSTED_CERTIFICATE.equals(certificate) ||
            !abis.equals(Collections.singleton(candidate.abi))) throw invalid();
    }

    private static String string(Map<?, ?> source, String field, int max) {
        Object value = source.get(field);
        if (!(value instanceof String) || ((String) value).trim().isEmpty() || ((String) value).length() > max) throw invalid();
        return (String) value;
    }

    private static long integer(Map<?, ?> source, String field, long min, long max) {
        Object value = source.get(field);
        if (!(value instanceof Integer) && !(value instanceof Long)) throw invalid();
        long number = ((Number) value).longValue();
        if (number < min || number > max) throw invalid();
        return number;
    }

    private static IllegalArgumentException invalid() {
        return new IllegalArgumentException("Invalid update metadata or policy");
    }
}
