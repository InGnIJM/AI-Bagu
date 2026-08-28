package io.github.ingnijm.baguhelper;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.*;

public class UpdatePolicyTest {
    static Map<String, Object> release(int code, String channel) {
        String name = channel.equals("beta") ? "0.1.0-beta." + code : "0.1." + code;
        Map<String, Object> r = new HashMap<>();
        r.put("versionCode", code); r.put("versionName", name); r.put("distribution", "public");
        r.put("packageName", "io.github.ingnijm.baguhelper"); r.put("minSdk", 29);
        r.put("abi", "arm64-v8a"); r.put("size", 1234); r.put("sha256", "a".repeat(64));
        r.put("apkUrl", "https://github.com/InGnIJM/AI-Bagu/releases/download/v" + name + "/bagu-" + name + "-public-arm64-v8a.apk");
        r.put("releaseUrl", "https://github.com/InGnIJM/AI-Bagu/releases/tag/v" + name);
        r.put("publishedAt", "2026-08-28T00:00:00Z"); r.put("notes", "<script>plain text</script>");
        return r;
    }

    static UpdatePolicy.Release parse(Map<String, Object> release, String channel) {
        Map<String, Object> feed = new HashMap<>();
        feed.put("schema_version", 1); feed.put("channel", channel); feed.put("release", release);
        return UpdatePolicy.parseFeed(feed, channel);
    }

    @Test public void automaticThrottleUsesAttemptsAndManualIsSeparate() {
        assertTrue(UpdatePolicy.shouldAutomaticallyCheck(true, 100, 0));
        assertFalse(UpdatePolicy.shouldAutomaticallyCheck(false, 100000000, 0));
        assertFalse(UpdatePolicy.shouldAutomaticallyCheck(true, 86400099, 100));
        assertTrue(UpdatePolicy.shouldAutomaticallyCheck(true, 86400100, 100));
        assertTrue(UpdatePolicy.shouldAutomaticallyCheck(true, 50, 100));
    }

    @Test public void betaSelectsHighestCompatibleCodeAndStableIgnoBeta() {
        UpdatePolicy.Release beta = parse(release(3, "beta"), "beta");
        UpdatePolicy.Release stable = parse(release(4, "stable"), "stable");
        assertEquals(4, UpdatePolicy.choose(Arrays.asList(beta, stable), 2, 29, Arrays.asList("arm64-v8a")).versionCode);
        assertNull(UpdatePolicy.choose(Arrays.asList(beta, stable), 4, 29, Arrays.asList("arm64-v8a")));
        assertNull(UpdatePolicy.choose(Arrays.asList(beta), 2, 28, Arrays.asList("arm64-v8a")));
        assertNull(UpdatePolicy.choose(Arrays.asList(beta), 2, 29, Arrays.asList("x86_64")));
        assertEquals(Arrays.asList("stable"), UpdatePolicy.channels("stable"));
        assertEquals(Arrays.asList("beta", "stable"), UpdatePolicy.channels("beta"));
    }

    @Test public void nullReleaseIsAValidEmptyChannel() {
        assertNull(parse(null, "beta"));
    }

    @Test public void malformedOrPrivateMetadataIsRejected() {
        for (Map.Entry<String, Object> bad : Map.<String, Object>of(
                "versionCode", 2.0, "distribution", "internal", "size", 134217729,
                "sha256", "BAD", "packageName", "other.package", "abi", "x86_64",
                "apkUrl", "https://evil.test/app.apk", "releaseUrl", "https://github.com/other/repo",
                "minSdk", true).entrySet()) {
            Map<String, Object> r = release(2, "beta");
            r.put(bad.getKey(), bad.getValue());
            assertThrows(IllegalArgumentException.class, () -> parse(r, "beta"));
        }
        Map<String, Object> r = release(2, "beta");
        r.put("trustedCertificate", "remote-must-not-replace-pin");
        assertThrows(IllegalArgumentException.class, () -> parse(r, "beta"));
        assertThrows(IllegalArgumentException.class, () -> parse(release(2, "beta"), "stable"));
    }

    @Test public void redirectsNeverAllowCredentialsDowngradeOrForeignHosts() {
        for (String url : Arrays.asList("http://github.com/a", "https://evil.test/a",
                "https://github.com.evil.test/a", "https://user@github.com/a", "https://github.com:444/a",
                "https://github.com/a#fragment", "file:///private")) {
            assertThrows(IllegalArgumentException.class, () -> UpdatePolicy.validateRedirect(url));
        }
        UpdatePolicy.validateRedirect("https://release-assets.githubusercontent.com/asset?signature=temporary");
    }

    @Test public void archiveChecksPinVersionAndMinimumSdkInsteadOfTrustingFeed() {
        UpdatePolicy.Release r = parse(release(2, "beta"), "beta");
        UpdatePolicy.validateArchive(r, "io.github.ingnijm.baguhelper", 2, "0.1.0-beta.2", 29,
            UpdatePolicy.TRUSTED_CERTIFICATE, new HashSet<>(Arrays.asList("arm64-v8a")), 1, 29);
        assertThrows(IllegalArgumentException.class, () -> UpdatePolicy.validateArchive(r,
            "io.github.ingnijm.baguhelper", 2, "0.1.0-beta.2", 29, "b".repeat(64),
            new HashSet<>(Arrays.asList("arm64-v8a")), 1, 29));
        assertThrows(IllegalArgumentException.class, () -> UpdatePolicy.validateArchive(r,
            "io.github.ingnijm.baguhelper", 2, "0.1.0-beta.2", 29, UpdatePolicy.TRUSTED_CERTIFICATE,
            new HashSet<>(Arrays.asList("arm64-v8a")), 2, 29));
        assertThrows(IllegalArgumentException.class, () -> UpdatePolicy.validateArchive(r,
            "io.github.ingnijm.baguhelper", 3, "0.1.0-beta.3", 29, UpdatePolicy.TRUSTED_CERTIFICATE,
            new HashSet<>(Arrays.asList("arm64-v8a")), 1, 29));
    }
}
