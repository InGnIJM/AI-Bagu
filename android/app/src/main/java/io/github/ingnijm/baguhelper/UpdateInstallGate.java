package io.github.ingnijm.baguhelper;

import java.util.function.Consumer;

/** Main-thread short reservation. Reserve UI first, then read DB, then validate reservation. */
final class UpdateInstallGate {
    interface Boundary {
        boolean idle();
        void reserve(String operation, Consumer<Boolean> reply);
        void noSession(Consumer<Boolean> reply);
        void stillReserved(String operation, Consumer<Boolean> reply);
        boolean launch();
        void release(String operation);
        void abort(String operation, String reason);
    }
    private static final class Attempt {
        final String operation; final Boundary boundary; int phase;
        Attempt(String operation, Boundary boundary) { this.operation=operation;this.boundary=boundary; }
    }
    private Attempt active;
    boolean isActive() { return active!=null; }
    void begin(String operation, Boundary boundary) {
        UpdatePolicy.validateOperationId(operation);
        if(active!=null || !boundary.idle()) { boundary.abort(operation,"请先结束当前操作，再安装更新。");return; }
        Attempt attempt=new Attempt(operation,boundary);active=attempt;
        try { boundary.reserve(operation,reply->reserved(attempt,reply)); }
        catch(RuntimeException ignored) { fail(attempt,"无法预留安装状态，请重试。"); }
    }
    private void reserved(Attempt attempt,boolean ok) {
        if(active!=attempt || attempt.phase!=0)return;
        if(!ok || !attempt.boundary.idle()){fail(attempt,"请先结束抽题、练习、评分、语音或文件操作，再安装更新。");return;}
        attempt.phase=1;
        try { attempt.boundary.noSession(reply->databaseChecked(attempt,reply)); }
        catch(RuntimeException ignored){fail(attempt,"无法检查练习状态，请重试。");}
    }
    private void databaseChecked(Attempt attempt,boolean ok) {
        if(active!=attempt || attempt.phase!=1)return;
        if(!ok || !attempt.boundary.idle()){fail(attempt,"请先结束本轮练习，再安装更新。");return;}
        attempt.phase=2;
        try { attempt.boundary.stillReserved(attempt.operation,reply->validated(attempt,reply)); }
        catch(RuntimeException ignored){fail(attempt,"安装准备状态已改变，请重试。");}
    }
    private void validated(Attempt attempt,boolean ok) {
        if(active!=attempt || attempt.phase!=2)return;
        if(!ok || !attempt.boundary.idle()){fail(attempt,"安装准备状态已改变，请重试。");return;}
        attempt.phase=3;
        try { if(!attempt.boundary.launch())release(attempt); }
        catch(RuntimeException ignored){fail(attempt,"无法打开安装器，请重试。");}
    }
    void cancel(String reason) { if(active!=null)fail(active,reason); }
    private void fail(Attempt attempt,String reason) {
        if(active!=attempt)return;
        try { attempt.boundary.abort(attempt.operation,reason); }
        finally { release(attempt); }
    }
    private void release(Attempt attempt) {
        if(active!=attempt)return;
        active=null;
        attempt.boundary.release(attempt.operation);
    }
}
