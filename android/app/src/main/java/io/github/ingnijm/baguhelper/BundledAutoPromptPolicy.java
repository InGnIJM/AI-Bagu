package io.github.ingnijm.baguhelper;

/** Bounded single-flight policy for waiting until the first automatic preview can start. */
final class BundledAutoPromptPolicy {
    static final int MAX_IDLE_RETRIES = 40;

    enum Action { NONE, SCHEDULE, START }

    private boolean attemptStarted;
    private boolean retryPending;
    private int retryCount;

    synchronized Action evaluate(boolean idle, boolean eligibleToRetry) {
        if (attemptStarted) return Action.NONE;
        if (idle) {
            attemptStarted = true;
            retryPending = false;
            return Action.START;
        }
        if (!eligibleToRetry || retryPending || retryCount >= MAX_IDLE_RETRIES) {
            return Action.NONE;
        }
        retryPending = true;
        retryCount++;
        return Action.SCHEDULE;
    }

    synchronized void retryFired() {
        retryPending = false;
    }
}
