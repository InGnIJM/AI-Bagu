package io.github.ingnijm.baguhelper;

import org.junit.Test;
import static org.junit.Assert.*;

public class UpdateInstallStatusPolicyTest {
    @Test public void mapsEveryPublicPackageInstallerTerminalStatusWithoutRawMessages() {
        assertEquals(UpdateInstallStatusPolicy.Kind.PENDING, UpdateInstallStatusPolicy.map(-1, false).kind);
        assertEquals(UpdateInstallStatusPolicy.Kind.SUCCESS, UpdateInstallStatusPolicy.map(0, false).kind);
        assertEquals(UpdateInstallStatusPolicy.Kind.FAILURE, UpdateInstallStatusPolicy.map(1, false).kind);
        assertEquals(1302, UpdateInstallStatusPolicy.map(1, false).errorCode);
        assertEquals(1301, UpdateInstallStatusPolicy.map(2, false).errorCode);
        assertEquals(UpdateInstallStatusPolicy.Kind.CANCELLED, UpdateInstallStatusPolicy.map(3, false).kind);
        for (int status : new int[]{4, 5, 7}) assertEquals(1204, UpdateInstallStatusPolicy.map(status, false).errorCode);
        assertEquals(1201, UpdateInstallStatusPolicy.map(6, false).errorCode);
        assertEquals(1302, UpdateInstallStatusPolicy.map(8, false).errorCode);
        assertEquals(1302, UpdateInstallStatusPolicy.map(404, false).errorCode);
    }

    @Test public void developerVerificationExtraOverridesAbortedOrBlockedStatus() {
        for (int status : new int[]{2, 3}) {
            UpdateInstallStatusPolicy.Result result = UpdateInstallStatusPolicy.map(status, true);
            assertEquals(UpdateInstallStatusPolicy.Kind.FAILURE, result.kind);
            assertEquals(1303, result.errorCode);
        }
    }
}
