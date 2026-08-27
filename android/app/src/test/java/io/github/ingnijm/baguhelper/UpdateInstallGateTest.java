package io.github.ingnijm.baguhelper;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.*;
import java.util.function.Consumer;

public class UpdateInstallGateTest {
    static final class Boundary implements UpdateInstallGate.Boundary {
        boolean idle=true,launched=true;int launches,releases,aborts;
        final List<String> calls=new ArrayList<>();
        Consumer<Boolean> reserveReply,databaseReply,currentReply;
        public boolean idle(){return idle;}
        public void reserve(String op,Consumer<Boolean> reply){calls.add("reserve");reserveReply=reply;}
        public void noSession(Consumer<Boolean> reply){calls.add("database");databaseReply=reply;}
        public void stillReserved(String op,Consumer<Boolean> reply){calls.add("current");currentReply=reply;}
        public boolean launch(){launches++;return launched;}
        public void release(String op){releases++;}
        public void abort(String op,String message){aborts++;}
    }
    @Test public void reservationPrecedesDatabaseAndBlocksDelayedDrawInterleaving() {
        UpdateInstallGate gate=new UpdateInstallGate();Boundary boundary=new Boundary();
        gate.begin("install",boundary);assertTrue(gate.isActive());assertEquals(Arrays.asList("reserve"),boundary.calls);
        boundary.reserveReply.accept(false); // Page reports an in-flight draw, before its UI refresh.
        assertFalse(gate.isActive());assertEquals(0,boundary.launches);assertNull(boundary.databaseReply);assertEquals(1,boundary.releases);
    }
    @Test public void databaseFailureAndLateCallbacksCannotLaunchAndReleaseReservation() {
        UpdateInstallGate gate=new UpdateInstallGate();Boundary first=new Boundary();
        gate.begin("first",first);first.reserveReply.accept(true);first.databaseReply.accept(false);
        assertEquals(0,first.launches);assertEquals(1,first.releases);assertFalse(gate.isActive());
        Boundary next=new Boundary();gate.begin("next",next);next.reserveReply.accept(true);
        first.databaseReply.accept(true);assertNull(first.currentReply);assertTrue(gate.isActive());
        gate.cancel("timeout");next.databaseReply.accept(true);assertEquals(0,next.launches);assertEquals(1,next.releases);
    }
    @Test public void nativeRecheckAndLaunchFailureBothReleaseWithoutLeavingBusyState() {
        UpdateInstallGate gate=new UpdateInstallGate();Boundary b=new Boundary();
        gate.begin("native-busy",b);b.reserveReply.accept(true);b.databaseReply.accept(true);b.idle=false;b.currentReply.accept(true);
        assertEquals(0,b.launches);assertEquals(1,b.releases);assertFalse(gate.isActive());
        Boundary failing=new Boundary();failing.launched=false;gate.begin("launch-failed",failing);
        failing.reserveReply.accept(true);failing.databaseReply.accept(true);failing.currentReply.accept(true);
        assertEquals(1,failing.launches);assertEquals(1,failing.releases);assertFalse(gate.isActive());
    }
    @Test public void successfulLaunchRetainsReservationUntilLifecycleCancellation() {
        UpdateInstallGate gate=new UpdateInstallGate();Boundary b=new Boundary();gate.begin("install",b);
        b.reserveReply.accept(true);b.databaseReply.accept(true);b.currentReply.accept(true);
        assertEquals(1,b.launches);assertTrue(gate.isActive());assertEquals(0,b.releases);
        gate.cancel("pause");assertFalse(gate.isActive());assertEquals(1,b.releases);
        b.currentReply.accept(true);assertEquals(1,b.launches);
    }
}
