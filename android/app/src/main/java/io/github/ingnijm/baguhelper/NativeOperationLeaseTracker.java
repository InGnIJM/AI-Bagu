package io.github.ingnijm.baguhelper;

import java.util.Map;
import java.util.Objects;
import java.util.function.BooleanSupplier;
import java.util.function.Supplier;

/** Tracks one accepted update lease from observed active state through terminal state. */
final class NativeOperationLeaseTracker {
    private static final class TrackedLease {
        final String operationId;
        final NativeOperationArbiter.Lease token;
        final Runnable release;
        final long baselineRevision;
        boolean seenActive;

        TrackedLease(String operationId, NativeOperationArbiter.Lease token, Runnable release,
                long baselineRevision) {
            this.operationId = operationId;
            this.token = token;
            this.release = release;
            this.baselineRevision = baselineRevision;
        }
    }

    private TrackedLease current;

    boolean start(String operationId, NativeOperationArbiter.Lease token, Runnable release,
            long baselineRevision, BooleanSupplier action,
            Supplier<Map<String,Object>> currentState) {
        Objects.requireNonNull(operationId, "operationId");
        Objects.requireNonNull(token, "token");
        Objects.requireNonNull(release, "release");
        Objects.requireNonNull(action, "action");
        Objects.requireNonNull(currentState, "currentState");
        TrackedLease tracked;
        synchronized (this) {
            if (current != null) tracked = null;
            else {
                tracked = new TrackedLease(operationId, token, release, baselineRevision);
                current = tracked;
            }
        }
        if (tracked == null) {
            release.run();
            return false;
        }
        boolean started = false;
        try {
            started = action.getAsBoolean();
            if (started) run(transition(tracked, currentState.get(), true));
            return started;
        } finally {
            // Engine rejection and exceptions never strand the token waiting for state.
            if (!started) release(tracked);
        }
    }

    void observe(Map<String,Object> state) {
        run(transition(null, state, false));
    }

    private synchronized Runnable transition(TrackedLease expected, Map<String,Object> state,
            boolean acceptedActionWindow) {
        TrackedLease tracked = current;
        if (tracked == null || (expected != null && tracked != expected)) return null;
        long revision = ((Number)state.get("revision")).longValue();
        if (revision <= tracked.baselineRevision) return null;
        boolean matchingOperation = tracked.operationId.equals(state.get("operationId"));
        if (Boolean.TRUE.equals(state.get("busy"))
                || Boolean.TRUE.equals(state.get("installerLease"))) {
            if (matchingOperation) tracked.seenActive = true;
            return null;
        }
        // A queued, unrelated idle revision can arrive after reservation but before
        // the engine's active emission. Only post-action reconciliation may accept a
        // no-active terminal, and only for this exact operation and a newer revision.
        if (!tracked.seenActive && !(acceptedActionWindow && matchingOperation)) return null;
        current = null;
        return tracked.release;
    }

    private void release(TrackedLease expected) {
        Runnable rejected = null;
        synchronized (this) {
            if (current != expected) return;
            current = null;
            rejected = expected.release;
        }
        rejected.run();
    }

    private static void run(Runnable completed) {
        if (completed != null) completed.run();
    }
}
