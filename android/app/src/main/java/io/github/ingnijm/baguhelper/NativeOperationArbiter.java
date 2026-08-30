package io.github.ingnijm.baguhelper;

/** Process-local atomic boundary shared by native file and update operations. */
final class NativeOperationArbiter {
    private static final NativeOperationArbiter PROCESS = new NativeOperationArbiter();

    static final class Lease {
        private final NativeOperationArbiter owner;
        private final long id;
        private final String kind;
        private final String operation;

        private Lease(NativeOperationArbiter owner, long id, String kind, String operation) {
            this.owner = owner;
            this.id = id;
            this.kind = kind;
            this.operation = operation;
        }

        String kind() { return kind; }
        String operation() { return operation; }
    }

    private long sequence;
    private Lease current;

    static NativeOperationArbiter process() { return PROCESS; }

    synchronized Lease tryAcquire(String kind, String operation) {
        if (!("file".equals(kind) || "update".equals(kind))
                || operation == null || operation.trim().isEmpty()) {
            throw new IllegalArgumentException("Invalid native operation");
        }
        if (current != null) return null;
        current = new Lease(this, ++sequence, kind, operation);
        return current;
    }

    synchronized boolean owns(Lease lease) {
        return lease != null && lease.owner == this && current == lease;
    }

    synchronized boolean release(Lease lease) {
        if (!owns(lease)) return false;
        current = null;
        return true;
    }

    synchronized boolean isIdle() { return current == null; }
}
