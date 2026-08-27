package io.github.ingnijm.baguhelper;

import org.junit.Test;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;
import static org.junit.Assert.*;

/** The engine and clock are external; request ownership and all transitions are real. */
public class SpeechInputTest {
    private static final class Clock implements SpeechInput.Scheduler {
        final List<Runnable> jobs = new ArrayList<>();
        final List<Long> delays = new ArrayList<>();
        @Override public SpeechInput.Cancellable after(long delay, Runnable job) {
            jobs.add(job); delays.add(delay);
            return () -> jobs.remove(job);
        }
        void expire() { assertFalse("Active recognition must have a deadline", jobs.isEmpty()); Runnable job = jobs.remove(0); job.run(); }
    }
    private static final class Engine implements SpeechInput.Engine {
        SpeechInput.Listener listener;
        int starts, stops, cancels, destroys;
        boolean failStart, failStop;
        @Override public void start() { starts++; if (failStart) throw new IllegalStateException("private engine data"); }
        @Override public void stop() { stops++; if (failStop) throw new IllegalStateException("private engine data"); }
        @Override public void cancel() { cancels++; }
        @Override public void destroy() { destroys++; }
    }
    private static final class Backend implements SpeechInput.Backend {
        boolean available = true, permission = true, failStart, failStop;
        int permissionRequests;
        Consumer<Boolean> permissionReply;
        final List<Engine> engines = new ArrayList<>();
        @Override public boolean available() { return available; }
        @Override public boolean hasPermission() { return permission; }
        @Override public void requestPermission(Consumer<Boolean> reply) { permissionRequests++; permissionReply = reply; }
        @Override public SpeechInput.Engine create(SpeechInput.Listener listener) {
            Engine value = new Engine(); value.listener = listener;
            value.failStart = failStart; value.failStop = failStop; engines.add(value); return value;
        }
        Engine engine() { assertFalse("Valid request must open an engine", engines.isEmpty()); return engines.get(engines.size() - 1); }
        void reply(boolean granted) { assertNotNull("Missing microphone permission request", permissionReply); permissionReply.accept(granted); }
    }
    private static final class Fixture {
        final Backend backend = new Backend();
        final Clock clock = new Clock();
        final List<SpeechInput.Event> events = new ArrayList<>();
        final SpeechInput input = new SpeechInput(backend, clock, events::add);
        SpeechInput.Event last() { assertFalse("Request must emit an event", events.isEmpty()); return events.get(events.size() - 1); }
    }

    @Test public void unavailableServiceFailsWithoutRequestingPermissionOrOpeningMicrophone() {
        Fixture f = new Fixture(); f.backend.available = false; f.backend.permission = false;
        f.input.start("r_1");
        assertEquals("error", f.last().type); assertTrue(f.last().message.contains("不可用"));
        assertEquals("r_1", f.last().requestId); assertEquals(0, f.backend.permissionRequests);
        assertTrue(f.backend.engines.isEmpty()); assertTrue(f.clock.jobs.isEmpty());
    }

    @Test public void permissionGrantStartsOnlyTheRequestThatAskedForIt() {
        Fixture f = new Fixture(); f.backend.permission = false; f.input.start("first");
        assertTrue(f.backend.engines.isEmpty());
        f.input.cancel("first"); f.backend.reply(true);
        assertEquals("cancelled", f.last().type); assertTrue(f.backend.engines.isEmpty());
        f.input.start("second"); f.backend.reply(true);
        assertEquals(1, f.backend.engine().starts);
        f.backend.engine().listener.ready(); assertEquals("second", f.last().requestId);
    }

    @Test public void permissionDenialIsExplicitAndDoesNotListen() {
        Fixture f = new Fixture(); f.backend.permission = false; f.input.start("r");
        f.backend.reply(false);
        assertEquals("error", f.last().type); assertTrue(f.last().message.contains("权限"));
        assertTrue(f.backend.engines.isEmpty());
    }

    @Test public void pauseWhileAwaitingPermissionStillReportsDenial() {
        Fixture f = new Fixture(); f.backend.permission = false; f.input.start("r");
        f.input.pause();
        assertTrue("Pause must await the permission decision", f.events.isEmpty());
        f.backend.reply(false);
        assertEquals("error", f.last().type); assertTrue(f.last().message.contains("权限"));
        assertEquals(1, f.events.size()); assertTrue(f.backend.engines.isEmpty());
        assertTrue(f.clock.jobs.isEmpty());
    }

    @Test public void permissionGrantAfterPauseCancelsWithoutAutomaticallyRecording() {
        Fixture f = new Fixture(); f.backend.permission = false; f.input.start("r");
        f.input.pause();
        assertTrue("Pause alone must not discard a pending decision", f.events.isEmpty());
        f.backend.reply(true);
        assertEquals("cancelled", f.last().type); assertEquals(1, f.events.size());
        assertTrue(f.backend.engines.isEmpty()); assertTrue(f.clock.jobs.isEmpty());
    }

    @Test public void explicitCancelAfterPauseInvalidatesLatePermissionGrant() {
        Fixture f = new Fixture(); f.backend.permission = false; f.input.start("r");
        f.input.pause(); f.input.cancel("r"); f.backend.reply(true); f.backend.reply(false);
        assertEquals("cancelled", f.last().type); assertEquals(1, f.events.size());
        assertTrue(f.backend.engines.isEmpty()); assertTrue(f.clock.jobs.isEmpty());
    }

    @Test public void pauseWhileRecordingImmediatelyReleasesAndIgnoresLateResult() {
        Fixture f = new Fixture(); f.input.start("r"); Engine engine = f.backend.engine();
        engine.listener.ready(); f.input.pause(); engine.listener.result("后台答案");
        assertEquals("cancelled", f.last().type); assertEquals(2, f.events.size());
        assertEquals(1, engine.cancels); assertEquals(1, engine.destroys);
        assertTrue(f.clock.jobs.isEmpty());
    }

    @Test public void readyPartialAndFinalAreScopedAndFinalReleasesResources() {
        Fixture f = new Fixture(); f.input.start("r"); Engine engine = f.backend.engine();
        engine.listener.ready(); engine.listener.partial("中间答案"); engine.listener.result("完整答案");
        assertEquals(List.of("ready", "partial", "result"), f.events.stream().map(e -> e.type).toList());
        assertEquals("完整答案", f.last().text); assertEquals(1, engine.destroys);
        assertTrue(f.clock.jobs.isEmpty());
        engine.listener.result("迟到答案"); engine.listener.error("迟到错误");
        assertEquals(3, f.events.size());
    }

    @Test public void stopWaitsForFinalButNeverWaitsForever() {
        Fixture f = new Fixture(); f.input.start("r"); Engine engine = f.backend.engine(); engine.listener.ready();
        f.input.stop("r"); f.input.stop("r");
        assertEquals(1, engine.stops); assertEquals("ready", f.last().type);
        assertEquals(Long.valueOf(10000), f.clock.delays.get(f.clock.delays.size() - 1));
        f.clock.expire(); assertEquals("error", f.last().type); assertTrue(f.last().message.contains("超时"));
        assertEquals(1, engine.destroys); engine.listener.result("迟到"); assertEquals("error", f.last().type);
    }

    @Test public void stopStillAcceptsFinalResult() {
        Fixture f = new Fixture(); f.input.start("r"); f.input.stop("r");
        f.backend.engine().listener.result("停止后的最终答案");
        assertEquals("result", f.last().type); assertEquals("停止后的最终答案", f.last().text);
    }

    @Test public void replacementAndLifecycleCancelCannotLeakOldResultsIntoNewRequest() {
        Fixture f = new Fixture(); f.input.start("same"); Engine old = f.backend.engine();
        f.input.start("same"); Engine current = f.backend.engine();
        old.listener.result("旧答案"); assertEquals("cancelled", f.last().type);
        f.input.cancel("different"); assertEquals(0, current.destroys);
        f.input.cancelActive(); current.listener.ready(); current.listener.result("后台答案");
        assertEquals(2, f.events.size()); assertEquals("cancelled", f.last().type);
        assertEquals(1, old.cancels); assertEquals(1, old.destroys); assertEquals(1, current.destroys);
    }

    @Test public void startupAndNaturalEndHaveBoundedDeadlines() {
        Fixture f = new Fixture(); f.input.start("start"); f.clock.expire();
        assertEquals("error", f.last().type);
        f.input.start("end"); f.backend.engine().listener.ready(); f.backend.engine().listener.ended();
        f.clock.expire(); assertEquals("error", f.last().type); assertTrue(f.last().message.contains("超时"));
    }

    @Test public void stopBeforePermissionArrivesCancelsInsteadOfStartingLater() {
        Fixture f = new Fixture(); f.backend.permission = false; f.input.start("r"); f.input.stop("r");
        f.backend.reply(true);
        assertEquals("cancelled", f.last().type); assertTrue(f.backend.engines.isEmpty());
    }

    @Test public void emptyAndOversizedResultsAreErrorsNotSuccessfulAnswers() {
        for (String text : new String[]{null, "  ", "中".repeat(20001)}) {
            Fixture f = new Fixture(); f.input.start("r"); f.backend.engine().listener.result(text);
            assertEquals("error", f.last().type); assertNull(f.last().text);
            assertEquals(1, f.backend.engine().destroys);
        }
    }

    @Test public void startupAndStopExceptionsNeverExposeEngineDetails() {
        Fixture f = new Fixture(); f.backend.failStart = true; f.input.start("r");
        assertEquals("error", f.last().type); assertFalse(f.last().message.contains("private"));
        assertEquals(1, f.backend.engine().destroys);
        f.backend.failStart = false; f.backend.failStop = true; f.input.start("s"); f.input.stop("s");
        assertEquals("error", f.last().type); assertFalse(f.last().message.contains("private"));
        assertEquals(1, f.backend.engine().destroys);
    }

    @Test public void invalidRequestIdsNeverReachMicrophoneOrEventPayload() {
        Fixture f = new Fixture();
        for (String id : new String[]{null, "", "a b", "\"</script>", "a".repeat(81)}) {
            assertThrows(IllegalArgumentException.class, () -> f.input.start(id));
            assertThrows(IllegalArgumentException.class, () -> f.input.stop(id));
            assertThrows(IllegalArgumentException.class, () -> f.input.cancel(id));
        }
        assertTrue(f.backend.engines.isEmpty()); assertTrue(f.events.isEmpty());
    }
}
