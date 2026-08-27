package io.github.ingnijm.baguhelper;

import org.junit.Test;
import static org.junit.Assert.*;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.HashMap;
import java.util.Map;

public class HostPolicyTest {
    @Test public void documentResultsCannotReadOrOverwritePrivateFileUrls() {
        assertTrue(HostPolicy.isDocumentUri("content://com.android.providers.downloads.documents/document/42"));
        assertFalse(HostPolicy.isDocumentUri("file:///data/user/0/example/files/config/.env"));
        assertFalse(HostPolicy.isDocumentUri("https://example.com/file"));
        assertFalse(HostPolicy.isDocumentUri("content:///missing-authority"));
        assertFalse(HostPolicy.isDocumentUri(null));
    }

    @Test public void navigationAllowsOnlyExactLoopbackOrigin() {
        assertTrue(HostPolicy.isLocalUrl("http://127.0.0.1:1234/?token=a", 1234));
        assertFalse(HostPolicy.isLocalUrl("http://127.0.0.1:4321/", 1234));
        assertFalse(HostPolicy.isLocalUrl("http://127.0.0.1.evil:1234/", 1234));
        assertFalse(HostPolicy.isLocalUrl("http://user@127.0.0.1:1234/", 1234));
        assertFalse(HostPolicy.isLocalUrl("file:///secret", 1234));
        assertFalse(HostPolicy.isLocalUrl("not a uri", 1234));
    }

    @Test public void externalReferenceRequiresMainFrameUserGestureAndWebScheme() {
        assertTrue(HostPolicy.isExplicitReference("https://example.com/answer", true, true));
        assertTrue(HostPolicy.isExplicitReference("http://example.com/answer", true, true));
        assertFalse(HostPolicy.isExplicitReference("https://example.com", false, true));
        assertFalse(HostPolicy.isExplicitReference("https://example.com", true, false));
        assertFalse(HostPolicy.isExplicitReference("intent://attack", true, true));
        assertFalse(HostPolicy.isExplicitReference("https://user:pass@example.com", true, true));
    }

    @Test public void storageAcceptsOnlyBoundedBaguKeysAndValues() {
        assertTrue(HostPolicy.validStorageKey("bagu-draft-1"));
        assertFalse(HostPolicy.validStorageKey("api_key"));
        assertFalse(HostPolicy.validStorageKey(null));
        assertFalse(HostPolicy.validStorageKey("bagu-" + "x".repeat(200)));
        assertTrue(HostPolicy.validStorageValue("中文草稿"));
        assertFalse(HostPolicy.validStorageValue(null));
        assertFalse(HostPolicy.validStorageValue("x".repeat(262145)));
        assertFalse(HostPolicy.validStorageValue("中".repeat(90000)));
    }

    @Test public void storageQuotaBoundsTotalBytesAndAllowsReplacingExistingKey() {
        Map<String, String> stored = new HashMap<>();
        for (int index = 0; index < 8; index++) stored.put("bagu-" + index, "x".repeat(250000));
        assertFalse(HostPolicy.canStore(stored, "bagu-extra", "x".repeat(250000)));
        assertTrue(HostPolicy.canStore(stored, "bagu-0", "small replacement"));
        assertFalse(HostPolicy.canStore(stored, "settings", "sk-test"));
        stored.clear();
        for (int index = 0; index < 512; index++) stored.put("bagu-" + index, "x");
        assertFalse(HostPolicy.canStore(stored, "bagu-extra", "x"));
        assertTrue(HostPolicy.canStore(stored, "bagu-0", "changed"));
    }

    @Test public void streamingFileReadEnforcesLimitWithoutTruncation() throws Exception {
        assertArrayEquals(new byte[]{1,2,3}, HostPolicy.readBounded(new ByteArrayInputStream(new byte[]{1,2,3}), 3));
        try {
            HostPolicy.readBounded(new ByteArrayInputStream(new byte[]{1,2,3,4}), 3);
            fail("Oversize content accepted");
        } catch (IOException expected) { assertNotNull(expected.getMessage()); }
    }
}
