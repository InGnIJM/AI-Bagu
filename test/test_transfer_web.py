"""Execute shared-page migration controls in Node with synthetic files/HTTP."""
import json

import pytest

from test_android_project import WEB_DOM, run_web_js, web_section


def transfer_source():
    return web_section("function setNativeMessage", "function readTextFile")


@pytest.mark.parametrize("confirmation", [True, False])
def test_desktop_import_preview_and_confirmation_use_one_snapshot(confirmation):
    result = run_web_js(WEB_DOM + f"""
const nativeStore=null; let nativeBusy='',session={{session_id:null}},currentView='settings';
let reads=0,refreshed=0,confirmed=''; const calls=[];
class FileReader {{ readAsArrayBuffer(file){{reads++;this.result=Uint8Array.from([0,128,255,10]).buffer;this.onload();}} }}
window.confirm=message=>{{confirmed=message;return {str(confirmation).lower()};}};
async function api(method,path,body){{calls.push([path,body.archive_base64]);
 if(path.endsWith('inspect'))return {{mode:'questions',schema_version:2,question_count:7,created_at:'2026-08-28T01:02:03Z',app_version:'synthetic-version'}};
 return {{added:2,updated:5,total:7}};}}
async function refresh(){{refreshed++;}} async function loadQuestions(){{}}
{transfer_source()}
(async()=>{{await importDesktopArchive({{size:4}});process.stdout.write(JSON.stringify({{reads,calls,refreshed,confirmed,busy:nativeBusy,message:$('native-message').textContent}}));}})();
""")
    assert result["reads"] == 1 and result["busy"] == ""
    assert result["calls"] == [["/api/backup/inspect", "AID/Cg=="]] + ([["/api/backup/restore", "AID/Cg=="]] if confirmation else [])
    assert result["refreshed"] == int(confirmation)
    assert all(text in result["confirmed"] for text in ("7", "2026-08-28", "synthetic-version", "保留", "进度"))
    assert ("完成" if confirmation else "取消") in result["message"]


@pytest.mark.parametrize("scenario", ["oversize", "open", "busy", "invalid", "network", "cancel-read"])
def test_desktop_import_failure_guards_do_not_write_or_auto_retry(scenario):
    result = run_web_js(WEB_DOM + f"""
const nativeStore=null; let nativeBusy={'"export"' if scenario == 'busy' else '""'},session={{session_id:{'"open"' if scenario == 'open' else 'null'}}},currentView='settings';
let reads=0,confirms=0; const calls=[];
class FileReader {{ readAsArrayBuffer(){{reads++;this.result=new Uint8Array([1]).buffer;{'this.onabort();' if scenario == 'cancel-read' else 'this.onload();'}}} }}
window.confirm=()=>{{confirms++;return true;}};
async function api(method,path){{calls.push(path);if(path.endsWith('restore') || {str(scenario == 'invalid').lower()})throw Error('private raw detail');
 return {{mode:'progress',question_count:1,created_at:'2026-08-28',app_version:'test',schema_version:2}};}}
async function refresh(){{}} async function loadQuestions(){{}}
{transfer_source()}
(async()=>{{await importDesktopArchive({{size:{20 * 1024 * 1024 + 1 if scenario == 'oversize' else 1}}});
process.stdout.write(JSON.stringify({{reads,confirms,calls,message:$('native-message').textContent}}));}})();
""")
    assert "private raw detail" not in result["message"]
    assert result["calls"].count("/api/backup/restore") == int(scenario == "network")
    if scenario in ("oversize", "open", "busy"):
        assert result["reads"] == 0 and result["confirms"] == 0
    if scenario == "network":
        assert "核对" in result["message"] and "重试" not in result["message"]
    if scenario == "cancel-read":
        assert "取消" in result["message"]


def test_archive_encoder_handles_large_binary_without_argument_spreads():
    result = run_web_js(WEB_DOM + f"""
{transfer_source()}
const bytes=new Uint8Array(1024*1024);for(let i=0;i<bytes.length;i++)bytes[i]=i%256;
const encoded=encodeArchiveBytes(bytes);
process.stdout.write(JSON.stringify([Buffer.from(encoded,'base64').equals(Buffer.from(bytes)),encoded.length]));
""")
    assert result == [True, 1398104]


def test_desktop_export_downloads_binary_blob_without_json_decoding():
    result = run_web_js(WEB_DOM + f"""
const nativeStore=null;let nativeBusy='',session={{session_id:'allowed'}},currentView='settings';
const calls=[];let clicked=false,removed=false,revoked=false,download='';
function requestHeaders(){{return {{}};}}
async function fetch(url){{calls.push(url);return {{ok:true,blob:async()=>new Blob([new Uint8Array([80,75,0,255])])}};}}
URL.createObjectURL=blob=>{{calls.push(blob.size);return 'blob:synthetic';}};URL.revokeObjectURL=()=>{{revoked=true;}};
document.createElement=()=>({{click(){{clicked=true;download=this.download;}},remove(){{removed=true;}}}});
document.body={{appendChild(){{}}}};
{transfer_source()}
(async()=>{{await exportDesktopArchive('questions');await new Promise(r=>setTimeout(r,1100));
process.stdout.write(JSON.stringify({{calls,clicked,removed,revoked,download,busy:nativeBusy}}));}})();
""")
    assert result["calls"] == ["/api/backup/export?mode=questions", 4]
    assert result["clicked"] and result["removed"] and result["revoked"] and result["busy"] == ""
    assert "questions" in result["download"] and result["download"].endswith(".bagu-backup")


def test_native_question_export_keeps_file_off_javascript():
    result = run_web_js(WEB_DOM + f"""
let nativeBusy='',session={{session_id:'open'}},currentView='settings';const calls=[];
const nativeStore={{exportQuestionBank:()=>calls.push('questions')}};
{transfer_source()}
startNativeOperation('export-questions');
handleNativeResult({{detail:{{operation:'export-questions',status:'cancelled'}}}});
process.stdout.write(JSON.stringify([calls,nativeBusy]));
""")
    assert result == [["questions"], ""]


def test_native_busy_result_rehydrates_controls_after_activity_recreation():
    result = run_web_js(WEB_DOM + f"""
let nativeBusy='',session={{session_id:null}},currentView='settings';const nativeStore={{}};
{transfer_source()}
handleNativeResult({{detail:{{operation:'import',status:'busy',message:'等待确认'}}}});
const during=[nativeBusy,$('btn-backup-import').disabled,$('btn-question-export').disabled];
handleNativeResult({{detail:{{operation:'import',status:'cancelled',message:'已取消'}}}});
process.stdout.write(JSON.stringify([during,nativeBusy,$('btn-backup-import').disabled]));
""")
    assert result == [["import", True, True], "", False]


@pytest.mark.parametrize("success", [True, False])
def test_diagnostic_export_downloads_without_touching_session_and_restores_button(success):
    result = run_web_js(WEB_DOM + f"""
const nativeStore=null;let nativeBusy='',session={{session_id:'open'}},currentView='settings';
let clicked=false,revoked=false; const calls=[];
window.baguDiagnostics={{flush:async()=>{{}},record:()=>{{}},id:()=> 'w_'+'a'.repeat(32)}};
async function fetch(url,options){{calls.push([url,options.headers]);return {{ok:{str(success).lower()},blob:async()=>new Blob(['zip'])}};}}
URL.createObjectURL=()=> 'blob:synthetic';URL.revokeObjectURL=()=>{{revoked=true;}};
document.createElement=()=>({{click(){{clicked=true;}},remove(){{}}}});document.body={{appendChild(){{}}}};
{transfer_source()}
(async()=>{{await exportDiagnostics();await new Promise(r=>setTimeout(r,1100));
process.stdout.write(JSON.stringify({{clicked,revoked,calls,busy:nativeBusy,disabled:$('btn-diagnostics-export').disabled,message:$('diagnostics-message').textContent,session}}));}})();
""")
    assert result["clicked"] == success and result["revoked"] == success
    assert result["busy"] == "" and not result["disabled"]
    assert result["session"]["session_id"] == "open"
    assert result["calls"] == [["/api/diagnostics/export", {"X-Bagu-Diagnostics": "1"}]]
    assert ("下载" if success else "失败") in result["message"]


@pytest.mark.parametrize("supported", [True, False])
def test_native_diagnostic_export_handles_old_host_duplicates_and_cancel(supported):
    result = run_web_js(WEB_DOM + f"""
let nativeBusy='',session={{session_id:'open'}},currentView='settings',calls=0;
const nativeStore={"{exportDiagnostics(){calls++;}}" if supported else "{}"};
{transfer_source()}
(async()=>{{await exportDiagnostics();await exportDiagnostics();
 const during=[$('btn-diagnostics-export').disabled,nativeBusy,$('diagnostics-message').textContent];
 handleNativeResult({{detail:{{operation:'diagnostics',status:'cancelled',message:'已取消'}}}});
 process.stdout.write(JSON.stringify({{calls,during,busy:nativeBusy,disabled:$('btn-diagnostics-export').disabled,session}}));}})();
""")
    assert result["calls"] == int(supported)
    assert result["during"][0] == supported
    if not supported:
        assert "不支持" in result["during"][2]
    assert result["busy"] == "" and not result["disabled"]
    assert result["session"]["session_id"] == "open"
