package io.github.ingnijm.baguhelper;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import static org.junit.Assert.*;

public final class NativeOperationLeaseTrackerTest {
    @Test public void unrelatedIdleCannotReleaseUntilActiveThenTerminalRevision() {
        NativeOperationArbiter arbiter = new NativeOperationArbiter();
        NativeOperationLeaseTracker tracker = new NativeOperationLeaseTracker();
        AtomicInteger firstReleases = new AtomicInteger();
        NativeOperationArbiter.Lease first = arbiter.tryAcquire("update", "first");
        Map<String,Object> firstBaseline = state(10, false, false, "before-first");
        assertTrue(tracker.start("first", first, () -> {
            assertTrue(arbiter.release(first));
            firstReleases.incrementAndGet();
        }, 10, () -> true, () -> firstBaseline));

        tracker.observe(state(11, false, false, "unrelated-idle"));
        assertEquals("an unrelated idle revision is not activation proof", 0, firstReleases.get());
        assertTrue(arbiter.owns(first));
        tracker.observe(state(12, true, false, "first"));
        assertEquals(0, firstReleases.get());
        tracker.observe(state(13, false, false, "terminal-alias"));
        assertEquals(1, firstReleases.get());
        tracker.observe(state(13, false, false, "terminal-alias"));
        assertEquals("a duplicate terminal state releases exactly once", 1, firstReleases.get());

        AtomicInteger secondReleases = new AtomicInteger();
        NativeOperationArbiter.Lease second = arbiter.tryAcquire("update", "second");
        Map<String,Object> secondBaseline = state(13, false, false, "before-second");
        assertTrue(tracker.start("second", second, () -> {
            assertTrue(arbiter.release(second));
            secondReleases.incrementAndGet();
        }, 13, () -> true, () -> secondBaseline));
        tracker.observe(state(13, false, false, "terminal-alias"));
        tracker.observe(state(14, false, false, "unrelated-idle"));
        assertEquals("old and unrelated idle states cannot release a newer lease", 0, secondReleases.get());
        tracker.observe(state(15, true, false, "other-operation"));
        tracker.observe(state(16, false, false, "other-operation"));
        assertEquals("another active operation is not activation proof", 0, secondReleases.get());
        tracker.observe(state(17, false, true, "second"));
        tracker.observe(state(13, false, false, "terminal-alias"));
        assertEquals("an old terminal cannot release after the newer lease becomes active", 0, secondReleases.get());
        tracker.observe(state(18, false, false, "terminal-alias"));
        assertEquals(1, secondReleases.get());
    }

    @Test public void rejectedOrThrowingStartReleasesReservedToken() {
        NativeOperationArbiter arbiter = new NativeOperationArbiter();
        NativeOperationLeaseTracker tracker = new NativeOperationLeaseTracker();
        AtomicInteger releases = new AtomicInteger();
        NativeOperationArbiter.Lease rejected = arbiter.tryAcquire("update", "rejected");
        AtomicInteger stateReads = new AtomicInteger();
        assertFalse(tracker.start("rejected", rejected, () -> {
            assertTrue(arbiter.release(rejected));
            releases.incrementAndGet();
        }, 20, () -> false, () -> { stateReads.incrementAndGet(); return null; }));
        assertEquals(1, releases.get());

        NativeOperationArbiter.Lease throwing = arbiter.tryAcquire("update", "throwing");
        try {
            tracker.start("throwing", throwing, () -> {
                assertTrue(arbiter.release(throwing));
                releases.incrementAndGet();
            }, 21, () -> { throw new IllegalStateException("start failed"); },
                () -> { stateReads.incrementAndGet(); return null; });
            fail("start exception must propagate");
        } catch (IllegalStateException expected) {
            assertEquals("start failed", expected.getMessage());
        }
        assertEquals(2, releases.get());
        assertEquals("rejected and throwing actions do not reconcile state", 0, stateReads.get());
        assertTrue(arbiter.isIdle());
    }

    @Test public void acceptedSynchronousTerminalReconcilesExactlyOnceAndAllowsNextLease() {
        NativeOperationArbiter arbiter = new NativeOperationArbiter();
        NativeOperationLeaseTracker tracker = new NativeOperationLeaseTracker();
        AtomicReference<Map<String,Object>> current = new AtomicReference<>(
            state(30, false, false, "before-sync"));
        AtomicInteger releases = new AtomicInteger();
        NativeOperationArbiter.Lease lease = arbiter.tryAcquire("update", "sync-terminal");

        assertTrue(tracker.start("sync-terminal", lease, () -> {
            assertTrue(arbiter.release(lease));
            releases.incrementAndGet();
        }, 30, () -> {
            Map<String,Object> terminal = state(31, false, false, "sync-terminal");
            current.set(terminal);
            tracker.observe(terminal); // Synchronous publish before start returns true.
            return true;
        }, current::get));

        assertEquals(1, releases.get());
        tracker.observe(current.get());
        assertEquals("duplicate terminal cannot double release", 1, releases.get());
        NativeOperationArbiter.Lease next = arbiter.tryAcquire("file", "after-sync-terminal");
        assertNotNull("the reconciled lease no longer blocks the next operation", next);
        assertTrue(arbiter.release(next));
    }

    @Test public void mismatchedSynchronousTerminalDoesNotBypassActivationProof() {
        NativeOperationArbiter arbiter = new NativeOperationArbiter();
        NativeOperationLeaseTracker tracker = new NativeOperationLeaseTracker();
        AtomicReference<Map<String,Object>> current = new AtomicReference<>(
            state(40, false, false, "before-expected"));
        AtomicInteger releases = new AtomicInteger();
        NativeOperationArbiter.Lease lease = arbiter.tryAcquire("update", "expected");

        assertTrue(tracker.start("expected", lease, () -> {
            assertTrue(arbiter.release(lease));
            releases.incrementAndGet();
        }, 40, () -> {
            Map<String,Object> unrelated = state(41, false, false, "other-operation");
            current.set(unrelated);
            tracker.observe(unrelated);
            return true;
        }, current::get));
        assertEquals(0, releases.get());
        assertTrue(arbiter.owns(lease));

        tracker.observe(state(42, true, false, "expected"));
        tracker.observe(state(43, false, false, "terminal-alias"));
        assertEquals(1, releases.get());
    }

    @Test public void synchronousActiveAndTerminalBeforeReturnRemainIdempotent() {
        NativeOperationArbiter arbiter = new NativeOperationArbiter();
        NativeOperationLeaseTracker tracker = new NativeOperationLeaseTracker();
        AtomicReference<Map<String,Object>> current = new AtomicReference<>(
            state(50, false, false, "before-race"));
        AtomicInteger releases = new AtomicInteger();
        AtomicInteger nextReleases = new AtomicInteger();
        AtomicReference<NativeOperationArbiter.Lease> nextLease = new AtomicReference<>();
        NativeOperationArbiter.Lease lease = arbiter.tryAcquire("update", "race");

        assertTrue(tracker.start("race", lease, () -> {
            assertTrue(arbiter.release(lease));
            releases.incrementAndGet();
        }, 50, () -> {
            tracker.observe(state(51, true, false, "race"));
            Map<String,Object> terminal = state(52, false, false, "race");
            current.set(terminal);
            tracker.observe(terminal);
            NativeOperationArbiter.Lease next = arbiter.tryAcquire("update", "next-race");
            assertNotNull(next);
            nextLease.set(next);
            assertTrue(tracker.start("next-race", next, () -> {
                assertTrue(arbiter.release(next));
                nextReleases.incrementAndGet();
            }, 52, () -> true, current::get));
            return true;
        }, current::get));
        assertEquals("callback completion and post-return reconcile release once", 1, releases.get());
        assertEquals("old reconciliation cannot release the newer tracker", 0, nextReleases.get());
        assertTrue(arbiter.owns(nextLease.get()));
        tracker.observe(state(53, true, false, "next-race"));
        tracker.observe(state(54, false, false, "next-race"));
        assertEquals(1, nextReleases.get());
        assertTrue(arbiter.isIdle());
    }

    private static Map<String,Object> state(long revision, boolean busy, boolean installerLease,
            String operationId) {
        Map<String,Object> state = new LinkedHashMap<>();
        state.put("revision", revision);
        state.put("busy", busy);
        state.put("installerLease", installerLease);
        state.put("operationId", operationId);
        return state;
    }
}
