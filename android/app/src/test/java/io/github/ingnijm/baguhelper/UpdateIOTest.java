package io.github.ingnijm.baguhelper;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import javax.net.ssl.SSLHandshakeException;

/** Classification at the actual I/O boundary, without real network or APKs. */
public class UpdateIOTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();
    private static final String PRIVATE = "sk-test https://release-assets.githubusercontent.com/private?token=fake C:/private/key";
    private static final byte[] APK = "synthetic-apk".getBytes(StandardCharsets.UTF_8);
    interface Attempt { void run() throws Exception; }

    private static void failure(int code, Integer status, Attempt attempt) throws Exception {
        UpdateFailure error = assertThrows(UpdateFailure.class, attempt::run);
        assertEquals(code, error.code);
        assertEquals(status, error.httpStatus);
        assertFalse(error.getMessage().contains(PRIVATE));
    }
    private static UpdateIO response(int status, String location, long length, byte[] body) {
        return new UpdateIO(url -> new UpdateIO.Response(status, location, length, new ByteArrayInputStream(body), () -> {}));
    }
    private static UpdatePolicy.Release candidate() {
        return UpdatePolicy.parseFeed(UpdateIO.parse(UpdateEngineTest.feed(2, "beta", APK)), "beta");
    }
    private static void feed(UpdateIO io) throws Exception { io.feed("beta", new UpdateIO.Cancellation()); }

    @Test public void httpFailuresRetainRealStatusWithoutReadingErrorBody() throws Exception {
        for (int status : new int[]{401, 403, 404, 429, 500, 503}) {
            boolean[] closed = {false};
            UpdateIO io = new UpdateIO(url -> new UpdateIO.Response(status, null, -1, new InputStream() {
                public int read() { fail("An HTTP error body is never read"); return -1; }
            }, () -> closed[0] = true));
            failure(1001, status, () -> feed(io));
            assertTrue(closed[0]);
        }
    }
    @Test public void typedTransportFailuresDoNotDependOnExceptionMessages() throws Exception {
        IOException[] errors = {new UnknownHostException(PRIVATE), new SocketTimeoutException(PRIVATE),
            new SSLHandshakeException(PRIVATE), new ConnectException(PRIVATE), new IOException(PRIVATE)};
        int[] codes = {1002, 1003, 1004, 1005, 1005};
        for (int i = 0; i < errors.length; i++) {
            IOException original = errors[i];
            UpdateIO io = new UpdateIO(url -> { throw original; });
            failure(codes[i], null, () -> feed(io));
        }
        UpdateIO readFailure = new UpdateIO(url -> new UpdateIO.Response(200, null, -1, new InputStream() {
            public int read() throws IOException { throw new SocketTimeoutException(PRIVATE); }
        }, () -> {}));
        failure(1003, null, () -> feed(readFailure));
    }
    @Test public void malformedJsonUtf8AndInvalidManifestHaveDifferentCodes() throws Exception {
        failure(1101, null, () -> feed(response(200, null, 1, new byte[]{(byte) 0xff})));
        failure(1101, null, () -> feed(response(200, null, 1, new byte[]{'{'})));
        failure(1102, null, () -> feed(response(200, null, 2, "{}".getBytes(StandardCharsets.UTF_8))));
        byte[] empty = "{\"schema_version\":1,\"channel\":\"beta\",\"release\":null}".getBytes(StandardCharsets.UTF_8);
        assertNull(response(200, null, empty.length, empty).feed("beta", new UpdateIO.Cancellation()));
    }
    @Test public void feedLimitsAndRedirectRejectionAreNotReportedAsHttpOrJsonErrors() throws Exception {
        failure(1103, null, () -> feed(response(200, null, 65537, new byte[0])));
        failure(1103, null, () -> feed(response(200, null, -1, new byte[65537])));
        failure(1104, 302, () -> feed(response(302, "https://ingnijm.github.io/AI-Bagu/updates/beta.json", -1, new byte[0])));
        File part = temporary.newFile();
        failure(1104, 307, () -> response(307, "http://github.com/unsafe", -1, new byte[0])
            .download(candidate(), part, new UpdateIO.Cancellation(), n -> {}));
    }
    @Test public void downloadLengthHashLimitAndLocalWriteAreDistinguished() throws Exception {
        File part = temporary.newFile();
        failure(1202, null, () -> response(200, null, APK.length + 1, APK).download(candidate(), part, new UpdateIO.Cancellation(), n -> {}));
        failure(1202, null, () -> response(200, null, -1, new byte[1]).download(candidate(), part, new UpdateIO.Cancellation(), n -> {}));
        failure(1202, null, () -> response(200, null, -1, new byte[APK.length + 1]).download(candidate(), part, new UpdateIO.Cancellation(), n -> {}));
        failure(1203, null, () -> response(200, null, APK.length, new byte[APK.length]).download(candidate(), part, new UpdateIO.Cancellation(), n -> {}));
        failure(1103, null, () -> response(200, null, 128L * 1024 * 1024 + 1, new byte[0]).download(candidate(), part, new UpdateIO.Cancellation(), n -> {}));
        File directory = temporary.newFolder();
        failure(1201, null, () -> response(200, null, APK.length, APK).download(candidate(), directory, new UpdateIO.Cancellation(), n -> {}));
    }
    @Test public void cachedBytesAreRevalidatedWithUsefulFailureCodes() throws Exception {
        failure(1201, null, () -> UpdateIO.verifyBytes(new File(temporary.getRoot(), "missing.apk"), candidate()));
        File file = temporary.newFile();
        failure(1202, null, () -> UpdateIO.verifyBytes(file, candidate()));
        Files.write(file.toPath(), new byte[APK.length]);
        failure(1203, null, () -> UpdateIO.verifyBytes(file, candidate()));
        Files.write(file.toPath(), APK);
        UpdateIO.verifyBytes(file, candidate());
    }
    @Test public void cancellationIsNotAConnectionFailureAndAlwaysClosesTransport() throws Exception {
        UpdateIO.Cancellation cancelled = new UpdateIO.Cancellation();
        boolean[] closed = {false};
        UpdateIO io = new UpdateIO(url -> new UpdateIO.Response(200, null, -1, new InputStream() {
            public int read() { cancelled.cancel(); return 1; }
        }, () -> closed[0] = true));
        assertThrows(UpdateIO.Cancelled.class, () -> io.feed("beta", cancelled));
        assertTrue(closed[0]);
    }
}
