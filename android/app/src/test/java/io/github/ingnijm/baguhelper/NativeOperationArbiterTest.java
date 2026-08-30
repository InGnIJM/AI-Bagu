package io.github.ingnijm.baguhelper;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import static org.junit.Assert.*;

public final class NativeOperationArbiterTest {
    @Test public void processArbiterIsSharedUntilItsExactLeaseCompletes() {
        NativeOperationArbiter first = NativeOperationArbiter.process();
        NativeOperationArbiter second = NativeOperationArbiter.process();
        assertSame(first, second);
        NativeOperationArbiter.Lease lease = first.tryAcquire("file", "process-worker");
        assertNotNull(lease);
        try {
            assertNull("another host cannot bypass an in-flight worker", second.tryAcquire("update", "new-host"));
        } finally {
            assertTrue(first.release(lease));
        }
        NativeOperationArbiter.Lease next = second.tryAcquire("update", "after-worker");
        assertNotNull(next);
        assertTrue(second.release(next));
    }

    @Test public void simultaneousFileAndUpdateClaimsHaveExactlyOneCompleteOwner() throws Exception {
        NativeOperationArbiter arbiter = new NativeOperationArbiter();
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch finished = new CountDownLatch(2);
        AtomicReference<NativeOperationArbiter.Lease> file = new AtomicReference<>();
        AtomicReference<NativeOperationArbiter.Lease> update = new AtomicReference<>();
        ExecutorService threads = Executors.newFixedThreadPool(2);
        try {
            threads.execute(() -> claim(arbiter, "file", "pack-import", file, ready, start, finished));
            threads.execute(() -> claim(arbiter, "update", "manual-check", update, ready, start, finished));
            assertTrue("both contenders reached the barrier", ready.await(5, TimeUnit.SECONDS));
            start.countDown();
            assertTrue("both contenders returned", finished.await(5, TimeUnit.SECONDS));

            NativeOperationArbiter.Lease winner = file.get() != null ? file.get() : update.get();
            assertTrue("exactly one operation atomically owns the boundary",
                (file.get() == null) != (update.get() == null));
            assertNotNull(winner);
            assertTrue(arbiter.owns(winner));
            assertFalse(arbiter.isIdle());
            assertEquals(file.get() != null ? "file" : "update", winner.kind());
            assertEquals(file.get() != null ? "pack-import" : "manual-check", winner.operation());

            assertTrue(arbiter.release(winner));
            assertTrue(arbiter.isIdle());
            NativeOperationArbiter.Lease next = arbiter.tryAcquire("file", "backup-import");
            assertNotNull("release permits a subsequent operation", next);
            assertFalse("a late callback cannot release the newer owner", arbiter.release(winner));
            assertTrue(arbiter.owns(next));
            assertTrue(arbiter.release(next));
        } finally {
            threads.shutdownNow();
        }
    }

    @Test public void bundledPackPreviewUsesTheSharedFileBoundaryUntilExactRelease() {
        NativeOperationArbiter arbiter = new NativeOperationArbiter();
        NativeOperationArbiter.Lease bundled = arbiter.tryAcquire("file", "pack-import");
        assertNotNull(bundled);
        assertNull(arbiter.tryAcquire("file", "backup-import"));
        assertNull(arbiter.tryAcquire("update", "update-install"));

        assertTrue(arbiter.release(bundled));
        NativeOperationArbiter.Lease next = arbiter.tryAcquire("file", "backup-import");
        assertNotNull(next);
        assertFalse(arbiter.release(bundled));
        assertTrue(arbiter.release(next));
    }

    private static void claim(NativeOperationArbiter arbiter, String kind, String operation,
            AtomicReference<NativeOperationArbiter.Lease> result, CountDownLatch ready,
            CountDownLatch start, CountDownLatch finished) {
        ready.countDown();
        try {
            if (!start.await(5, TimeUnit.SECONDS)) throw new AssertionError("start barrier timed out");
            result.set(arbiter.tryAcquire(kind, operation));
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new AssertionError(error);
        } finally {
            finished.countDown();
        }
    }
}
