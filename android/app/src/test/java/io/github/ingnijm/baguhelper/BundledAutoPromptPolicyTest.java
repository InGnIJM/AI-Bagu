package io.github.ingnijm.baguhelper;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public final class BundledAutoPromptPolicyTest {
    @Test public void idleStartsExactlyOneAutomaticAttempt() {
        BundledAutoPromptPolicy policy = new BundledAutoPromptPolicy();

        assertEquals(BundledAutoPromptPolicy.Action.START, policy.evaluate(true, true));
        assertEquals(BundledAutoPromptPolicy.Action.NONE, policy.evaluate(true, true));
        policy.retryFired();
        assertEquals(BundledAutoPromptPolicy.Action.NONE, policy.evaluate(true, true));
    }

    @Test public void transientBusyStateSchedulesOneBoundedRetryAtATime() {
        BundledAutoPromptPolicy policy = new BundledAutoPromptPolicy();

        assertEquals(BundledAutoPromptPolicy.Action.SCHEDULE, policy.evaluate(false, true));
        assertEquals(BundledAutoPromptPolicy.Action.NONE, policy.evaluate(false, true));
        policy.retryFired();
        assertEquals(BundledAutoPromptPolicy.Action.SCHEDULE, policy.evaluate(false, true));

        for (int i = 2; i < BundledAutoPromptPolicy.MAX_IDLE_RETRIES; i++) {
            policy.retryFired();
            assertEquals(BundledAutoPromptPolicy.Action.SCHEDULE, policy.evaluate(false, true));
        }
        policy.retryFired();
        assertEquals(BundledAutoPromptPolicy.Action.NONE, policy.evaluate(false, true));
        assertEquals(BundledAutoPromptPolicy.Action.START, policy.evaluate(true, true));
    }

    @Test public void ineligibleActivityDoesNotScheduleButCanStartWhenIdleLater() {
        BundledAutoPromptPolicy policy = new BundledAutoPromptPolicy();

        assertEquals(BundledAutoPromptPolicy.Action.NONE, policy.evaluate(false, false));
        assertEquals(BundledAutoPromptPolicy.Action.START, policy.evaluate(true, false));
    }
}
