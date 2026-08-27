"""Run native-update shared UI with actual functions and synthetic bridge events."""
from test_android_project import WEB_DOM, run_web_js, web_section, runtime
import bagu
import pytest


def update_source():
    return web_section("// Android updater:", "// Speech stays outside")


def test_update_events_ignore_stale_revision_and_render_notes_as_plain_text():
    result = run_web_js(WEB_DOM + f"""
const nativeStore={{}},isAndroidApp=true;let session={{session_id:null}},judgeTimer=null,nativeBusy='',speechInput=null;
{update_source()}
const candidate={{id:'2:hash',versionName:'0.1.0-beta.2',versionCode:2,size:1234,notes:'<script>inert</script>'}};
handleUpdateResult({{detail:{{revision:3,operationId:'new',status:'available',candidate,enabled:true,lastStatus:'partial-error',message:'检查未完整成功'}}}});
handleUpdateResult({{detail:{{revision:2,operationId:'old',status:'latest',enabled:true,message:'old'}}}});
const first=$('update-status').textContent;dismissUpdateNotice();
handleUpdateResult({{detail:{{revision:4,operationId:'new',status:'ready',candidate,enabled:true,ready:true}}}});
process.stdout.write(JSON.stringify({{first,notes:$('update-notes').textContent,hidden:$('update-notice').classList.contains('hidden'),ready:!$('btn-update-install').disabled}}));
""")
    assert result["first"] == "检查未完整成功"
    assert result["notes"] == "<script>inert</script>"
    assert result["hidden"] and result["ready"]


def test_update_dispatch_sends_only_candidate_id_and_blocks_duplicate_action():
    result = run_web_js(WEB_DOM + f"""
const calls=[];const nativeStore={{downloadUpdate:(id,op)=>{{calls.push([id,op]);return true;}}}},isAndroidApp=true;
let session={{session_id:null}},judgeTimer=null,nativeBusy='',speechInput=null;
{update_source()}
handleUpdateResult({{detail:{{revision:1,operationId:'check',status:'available',enabled:true,candidate:{{id:'2:hash',versionName:'test',size:1,notes:'notes'}}}}}});
dispatchUpdate('download');dispatchUpdate('download');
process.stdout.write(JSON.stringify({{calls,disabled:$('btn-update-download').disabled}}));
""")
    assert len(result["calls"]) == 1 and result["calls"][0][0] == "2:hash"
    assert result["calls"][0][1].startswith("update_") and result["disabled"]


@pytest.mark.parametrize("busy", ["session", "grade", "file", "speech"])
def test_update_shared_install_guard_refuses_each_busy_domain(busy):
    result = run_web_js(WEB_DOM + f"""
const nativeStore={{}},isAndroidApp=true;
let session={{session_id:{"'open'" if busy == "session" else "null"}}},judgeTimer={"1" if busy == "grade" else "null"},nativeBusy={"'import'" if busy == "file" else "''"},speechInput={"{}" if busy == "speech" else "null"};
{update_source()}
process.stdout.write(JSON.stringify(window.baguUpdateInstallState()));
""")
    assert result[busy] is True


def test_runtime_install_guard_is_read_only_and_never_creates_missing_database(runtime):
    module, private, static, _ = runtime
    paths = bagu.AppPaths(private / "data", private / "config", static, private / "logs")
    module._paths = paths
    with pytest.raises(Exception):
        module.has_open_session()
    assert not paths.db_path.exists()
    paths.data_dir.mkdir(parents=True)
    bagu.prepare_mobile_database(paths.db_path)
    connection = bagu.get_conn(paths.db_path)
    connection.execute("INSERT INTO questions(category,question) VALUES('test','synthetic')")
    connection.commit()
    assert module.has_open_session() is False
    sid, _ = bagu.draw(connection, 1)
    before = list(connection.iterdump())
    assert module.has_open_session() is True
    assert list(connection.iterdump()) == before
    assert bagu.get_open_session(connection)["id"] == sid
    connection.close()


def test_update_notice_dismissal_survives_rotation_but_not_next_check_or_process():
    result = run_web_js(WEB_DOM + f"""
const storage=new Map();const appStorage={{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}};
const nativeStore={{}},isAndroidApp=true;let session={{session_id:null}},judgeTimer=null,nativeBusy='',speechInput=null;
function page() {{ {update_source()} return {{event:handleUpdateResult,dismiss:dismissUpdateNotice}}; }}
const candidate={{id:'first',versionName:'v1',size:1}};
let p=page();p.event({{detail:{{revision:1,noticeId:'process-1:check-1',candidate,status:'available'}}}});p.dismiss();
p=page();p.event({{detail:{{revision:2,noticeId:'process-1:check-1',candidate,status:'ready',ready:true}}}});
const rotationHidden=$('update-notice').classList.contains('hidden');
p.event({{detail:{{revision:3,noticeId:'process-1:check-2',candidate,status:'available'}}}});
const nextCheckVisible=!$('update-notice').classList.contains('hidden');p.dismiss();
p=page();p.event({{detail:{{revision:1,noticeId:'process-2:check-0',candidate,status:'available'}}}});
process.stdout.write(JSON.stringify({{rotationHidden,nextCheckVisible,nextProcessVisible:!$('update-notice').classList.contains('hidden')}}));
""")
    assert result == {"rotationHidden": True, "nextCheckVisible": True, "nextProcessVisible": True}


def test_install_reservation_rejects_draw_until_response_and_refresh_finish():
    result = run_web_js(WEB_DOM + f"""
const nativeStore={{}},isAndroidApp=true;let session={{session_id:null}},judgeTimer=null,nativeBusy='',speechInput=null,selectedMode='answer';
{update_source()}
function requestHeaders(){{return {{}};}}let resolveResponse,resolveRefresh,dbOpened=false;
async function fetch(){{await new Promise(r=>resolveResponse=r);dbOpened=true;return {{ok:true,headers:{{get:()=> 'application/json'}},json:async()=>({{session_id:'opened'}})}};}}
function rememberSessionMode(){{}}function showView(){{}}function alert(){{}}
async function refresh(){{await new Promise(r=>resolveRefresh=r);session={{session_id:'opened'}};}}
{web_section('async function api(', 'async function streamAnswer')}
{web_section('function diagnosticTrace(', 'function startJudgeProgress')}
{web_section('async function draw(', 'async function advanceQuestion')}
(async()=>{{const drawing=draw(1);const duringNetwork=window.baguReserveUpdateInstallation('first');
resolveResponse();await new Promise(r=>setImmediate(r));
const duringRefresh=window.baguReserveUpdateInstallation('second');resolveRefresh();await drawing;
const afterOpen=window.baguReserveUpdateInstallation('third');
process.stdout.write(JSON.stringify({{duringNetwork,duringRefresh,afterOpen,dbOpened}}));}})();
""")
    assert result == {"duringNetwork": False, "duringRefresh": False, "afterOpen": False, "dbOpened": True}


def test_install_reservation_blocks_new_writes_but_allows_reads_and_releases_after_failure():
    result = run_web_js(WEB_DOM + f"""
const nativeStore={{}},isAndroidApp=true;let session={{session_id:null}},judgeTimer=null,nativeBusy='',speechInput=null;
{update_source()}
const calls=[];function requestHeaders(){{return {{}};}}
async function fetch(path){{calls.push(path);return {{ok:true,headers:{{get:()=> 'application/json'}},json:async()=>({{}})}};}}
{web_section('async function api(', 'async function streamAnswer')}
{web_section('function diagnosticTrace(', 'function startJudgeProgress')}
(async()=>{{const acquired=window.baguReserveUpdateInstallation('install');let blocked=0;
for(const path of ['/api/draw','/api/review','/api/models/test','/api/questions/import']){{try{{await api('POST',path,{{}});}}catch(_){{blocked++;}}}}
await api('GET','/api/stats');window.baguReleaseUpdateInstallation('install');await api('POST','/api/draw',{{}});
const lateRejected=!window.baguReserveUpdateInstallation('install');
process.stdout.write(JSON.stringify({{acquired,blocked,calls,lateRejected}}));}})();
""")
    assert result == {"acquired": True, "blocked": 4, "calls": ["/api/stats", "/api/draw"], "lateRejected": True}


@pytest.mark.parametrize("kind", ["model", "review", "stream", "file"])
def test_install_reservation_tracks_pending_work_and_releases_on_error(kind):
    action = {"model": "api('POST','/api/models/test',{})", "review": "api('POST','/api/review',{})", "stream": "streamAnswer({},()=>{})", "file": "readTextFile({text:()=>pending})"}[kind]
    result = run_web_js(WEB_DOM + f"""
const nativeStore={{}},isAndroidApp=true;let session={{session_id:null}},judgeTimer=null,nativeBusy='',speechInput=null;
{update_source()}
let rejectWork;const pending=new Promise((_,reject)=>rejectWork=reject);
function requestHeaders(){{return {{}};}}async function fetch(){{await pending;}}
{web_section('function readTextFile(', 'function startJudgeProgress')}
(async()=>{{const work={action}.catch(()=>{{}});const during=window.baguReserveUpdateInstallation('during');
rejectWork(Error('synthetic transport failure'));await work;
const after=window.baguReserveUpdateInstallation('after');window.baguReleaseUpdateInstallation('after');
process.stdout.write(JSON.stringify({{during,after}}));}})();
""")
    assert result == {"during": False, "after": True}


def test_install_reservation_blocks_speech_and_native_file_dispatch():
    result = run_web_js(WEB_DOM + f"""
const calls=[];const nativeStore={{startSpeech:()=>calls.push('speech'),exportBackup:()=>calls.push('file')}},isAndroidApp=true;
let session={{session_id:null}},judgeTimer=null,nativeBusy='';
{update_source()}
{web_section('let speechInput = null', 'function readTextFile')}
const acquired=window.baguReserveUpdateInstallation('install');startSpeechInput();startNativeOperation('export');
window.baguReleaseUpdateInstallation('install');
process.stdout.write(JSON.stringify({{acquired,calls,speech:$('speech-error').textContent,file:$('native-message').textContent}}));
""")
    assert result["acquired"] and result["calls"] == []
    assert "安装" in result["speech"] and "安装" in result["file"]
