"""Execute the shared-page interview simulation and pack controls in Node."""

import json

import pytest

from test_android_project import WEB_DOM, run_web_js, web_section


def experience_source():
    return web_section("function setStudyMode", "function escapeHtml")


def answer_source():
    return web_section("function escapeHtml", "function setReviewButtonsDisabled")


def quiz_source():
    return web_section("function currentQuestion", "function applyPresetList")


def management_source():
    return web_section("function showQuestionMessage", "function openQuestionNew")


def transfer_source():
    return web_section("function setNativeMessage", "function readTextFile")


def test_practice_switch_filters_experiences_and_uses_recommended_section():
    result = run_web_js(WEB_DOM + """
let selectedMode='answer',practiceMode='daily';
let experienceState={items:[],filtered:[],detail:null,selectedId:null};
let session={session_id:null,items:[],pending:[]};
const nativeStore=null; const calls=[];
function rememberSessionMode(){} function showView(){} async function refresh(){}
function escapeHtml(value){return String(value==null?'':value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function api(method,path,body){calls.push([method,path,body]);
 if(path==='/api/experiences')return {experiences:[
  {id:7,direction:'后端',company:'甲公司',position:'服务端',stage:'技术面',kind:'interview',pack_name:'私有包',question_count:3,section_count:2},
  {id:8,direction:'前端',company:'乙公司',position:'Web',stage:'一面',kind:'interview',pack_name:'私有包',question_count:2,section_count:1}
 ]};
 if(path==='/api/experiences/7')return {experience:{id:7,direction:'后端',company:'甲公司',position:'服务端',stage:'技术面',kind:'interview',pack_name:'私有包',question_count:3,section_count:2,recommended_section_id:22},sections:[
  {id:21,title:'一面',position:1,recommended:false,question_count:2},
  {id:22,title:'二面',position:2,recommended:true,question_count:1}
 ]};
 if(path==='/api/experiences/8')return {experience:{id:8,direction:'前端',company:'乙公司',position:'Web',stage:'一面',kind:'interview',pack_name:'私有包',question_count:2,section_count:1,recommended_section_id:null},sections:[
  {id:31,title:'整套',position:1,recommended:false,question_count:2}
 ]};
 return {session_id:'s_experience',questions:[],session_type:'experience'};
}
""" + experience_source() + """
(async()=>{
 const supported=typeof setPracticeMode==='function' && typeof selectExperience==='function' && typeof startSelectedExperience==='function';
 if(!supported){process.stdout.write(JSON.stringify({supported:false}));return;}
 await setPracticeMode('experience');
 const firstScope=$('experience-scope').value;
 $('experience-direction').value='前端'; renderExperienceOptions();
 const filtered=$('experience-select').innerHTML;
 $('experience-direction').value=''; $('experience-company').value='甲公司'; renderExperienceOptions();
 const companyFiltered=$('experience-select').innerHTML;
 $('experience-company').value=''; $('experience-position').value='Web'; renderExperienceOptions();
 const positionFiltered=$('experience-select').innerHTML;
 $('experience-position').value=''; renderExperienceOptions();
 await selectExperience(7);
 $('experience-scope').value='whole'; await startSelectedExperience();
 $('experience-scope').value='section:22'; await startSelectedExperience();
 await selectExperience(8); const fallbackScope=$('experience-scope').value;
 await setPracticeMode('daily');
 process.stdout.write(JSON.stringify({supported,firstScope,filtered,companyFiltered,positionFiltered,fallbackScope,calls,
  dailyPressed:$('practice-daily')['aria-pressed'],experiencePressed:$('practice-experience')['aria-pressed'],
  dailyHidden:$('daily-practice-options').classList.contains('hidden'),experienceHidden:$('experience-practice-options').classList.contains('hidden')}));
})().catch(error=>{console.error(error);process.exit(1)});
""")
    assert result["supported"] is True
    assert result["firstScope"] == "section:22"
    assert "乙公司" in result["filtered"] and "甲公司" not in result["filtered"]
    assert "甲公司" in result["companyFiltered"] and "乙公司" not in result["companyFiltered"]
    assert "乙公司" in result["positionFiltered"] and "甲公司" not in result["positionFiltered"]
    assert result["fallbackScope"] == "whole"
    starts = [call for call in result["calls"] if call[0] == "POST"]
    assert starts == [
        ["POST", "/api/experiences/7/start", {}],
        ["POST", "/api/experiences/7/start", {"section_id": 22}],
    ]
    assert result["dailyPressed"] == "true" and result["experiencePressed"] == "false"
    assert result["dailyHidden"] is False and result["experienceHidden"] is True


def test_experience_detail_reverse_success_keeps_latest_selection_and_loading_lock():
    result = run_web_js(WEB_DOM + """
let selectedMode='answer',practiceMode='experience';
let experienceState={items:[
 {id:7,stable_experience_id:'old',pack_id:'pack',pack_name:'包',kind:'interview',direction:'后端',company:'旧公司',position:'旧岗位',stage:'一面',section_count:1,question_count:1,recommended_section_id:71},
 {id:8,stable_experience_id:'new',pack_id:'pack',pack_name:'包',kind:'interview',direction:'后端',company:'新公司',position:'新岗位',stage:'二面',section_count:1,question_count:1,recommended_section_id:81}
],filtered:[],detail:{experience:{id:7}},selectedId:7,requestGeneration:0};
function escapeHtml(value){return String(value==null?'':value);}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const pending={7:deferred(),8:deferred()};
async function api(method,path){return pending[Number(path.split('/').pop())].promise;}
""" + experience_source() + """
(async()=>{
 $('experience-scope').innerHTML='<option value="section:70">旧章节</option>';
 $('experience-scope').value='section:70'; $('experience-scope').disabled=false;
 $('experience-summary').classList.remove('hidden'); $('btn-start-experience').disabled=false;
 const oldRequest=selectExperience(7);
 const loading={detail:experienceState.detail,startDisabled:$('btn-start-experience').disabled,
  scopeDisabled:$('experience-scope').disabled,scope:$('experience-scope').value,
  summaryHidden:$('experience-summary').classList.contains('hidden'),message:$('experience-message').textContent};
 const newRequest=selectExperience(8);
 pending[8].resolve({experience:experienceState.items[1],sections:[
  {id:81,stable_section_id:'new-section',position:1,title:'新章节',recommended:true,question_count:1}
 ]});
 await newRequest;
 const newest={selectedId:experienceState.selectedId,detailId:experienceState.detail.experience.id,
  scope:$('experience-scope').value,startDisabled:$('btn-start-experience').disabled,
  message:$('experience-message').textContent,summary:$('experience-summary').textContent};
 pending[7].resolve({experience:experienceState.items[0],sections:[
  {id:71,stable_section_id:'old-section',position:1,title:'旧章节',recommended:true,question_count:1}
 ]});
 await oldRequest;
 const final={selectedId:experienceState.selectedId,detailId:experienceState.detail.experience.id,
  scope:$('experience-scope').value,startDisabled:$('btn-start-experience').disabled,
  message:$('experience-message').textContent,summary:$('experience-summary').textContent};
 process.stdout.write(JSON.stringify({loading,newest,final}));
})().catch(error=>{console.error(error);process.exit(1)});
""")
    assert result["loading"] == {
        "detail": None,
        "startDisabled": True,
        "scopeDisabled": True,
        "scope": "whole",
        "summaryHidden": True,
        "message": "正在加载专题章节…",
    }
    expected = {
        "selectedId": 8,
        "detailId": 8,
        "scope": "section:81",
        "startDisabled": False,
        "message": "",
        "summary": "新公司 · 新岗位 · 二面 · 1 题 / 1 章",
    }
    assert result["newest"] == expected
    assert result["final"] == expected


def test_experience_stale_detail_error_cannot_clear_latest_success():
    result = run_web_js(WEB_DOM + """
let selectedMode='answer',practiceMode='experience';
let experienceState={items:[
 {id:7,stable_experience_id:'old',pack_id:'pack',pack_name:'包',kind:'interview',direction:'后端',company:'旧公司',position:'旧岗位',stage:'一面',section_count:1,question_count:1,recommended_section_id:71},
 {id:8,stable_experience_id:'new',pack_id:'pack',pack_name:'包',kind:'interview',direction:'后端',company:'新公司',position:'新岗位',stage:'二面',section_count:1,question_count:1,recommended_section_id:81}
],filtered:[],detail:null,selectedId:null,requestGeneration:0};
function escapeHtml(value){return String(value==null?'':value);}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const pending={7:deferred(),8:deferred()};
async function api(method,path){return pending[Number(path.split('/').pop())].promise;}
""" + experience_source() + """
(async()=>{
 const oldRequest=selectExperience(7); const newRequest=selectExperience(8);
 pending[8].resolve({experience:experienceState.items[1],sections:[
  {id:81,stable_section_id:'new-section',position:1,title:'新章节',recommended:true,question_count:1}
 ]});
 await newRequest; pending[7].reject(Error('旧请求失败')); await oldRequest;
 process.stdout.write(JSON.stringify({selectedId:experienceState.selectedId,
  detailId:experienceState.detail ? experienceState.detail.experience.id : null,scope:$('experience-scope').value,
  startDisabled:$('btn-start-experience').disabled,message:$('experience-message').textContent}));
})().catch(error=>{console.error(error);process.exit(1)});
""")
    assert result == {
        "selectedId": 8,
        "detailId": 8,
        "scope": "section:81",
        "startDisabled": False,
        "message": "",
    }


@pytest.mark.parametrize("stale_outcome", ["success", "error"])
def test_experience_filter_to_zero_replaces_loading_and_ignores_stale_detail(stale_outcome):
    result = run_web_js(WEB_DOM + """
let selectedMode='answer',practiceMode='experience';
let experienceState={items:[
 {id:7,stable_experience_id:'old',pack_id:'pack',pack_name:'包',kind:'interview',direction:'后端',company:'旧公司',position:'旧岗位',stage:'一面',section_count:1,question_count:1,recommended_section_id:71}
],filtered:[],detail:null,selectedId:null,requestGeneration:0};
function escapeHtml(value){return String(value==null?'':value);}
let resolveDetail,rejectDetail;
const delayedDetail=new Promise((resolve,reject)=>{resolveDetail=resolve;rejectDetail=reject;});
async function api(){return delayedDetail;}
""" + experience_source() + f"""
(async()=>{{
 const oldRequest=selectExperience(7);
 $('experience-direction').value='不存在';
 await applyExperienceFilters();
 const immediate={{selectedId:experienceState.selectedId,detail:experienceState.detail,
  scope:$('experience-scope').value,scopeDisabled:$('experience-scope').disabled,
  startDisabled:$('btn-start-experience').disabled,
  summaryHidden:$('experience-summary').classList.contains('hidden'),
  message:$('experience-message').textContent}};
 if({json.dumps(stale_outcome)}==='success') resolveDetail({{
  experience:experienceState.items[0],sections:[
   {{id:71,stable_section_id:'old-section',position:1,title:'旧章节',recommended:true,question_count:1}}
  ]
 }}); else rejectDetail(Error('旧请求失败'));
 await oldRequest;
 const final={{selectedId:experienceState.selectedId,detail:experienceState.detail,
  scope:$('experience-scope').value,scopeDisabled:$('experience-scope').disabled,
  startDisabled:$('btn-start-experience').disabled,
  summaryHidden:$('experience-summary').classList.contains('hidden'),
  message:$('experience-message').textContent}};
 process.stdout.write(JSON.stringify({{immediate,final}}));
}})().catch(error=>{{console.error(error);process.exit(1)}});
""")
    expected = {
        "selectedId": None,
        "detail": None,
        "scope": "whole",
        "scopeDisabled": True,
        "startDisabled": True,
        "summaryHidden": True,
        "message": "没有符合当前筛选条件的面经专题。",
    }
    assert result["immediate"] == expected
    assert result["final"] == expected


def test_prepare_render_and_completion_never_touch_draft_reveal_or_model():
    result = run_web_js(WEB_DOM + """
let drafts=0,reveals=0,streams=0,progress=0;
let session={session_id:'s_exp',session_type:'experience',items:[
 {id:5,position:4,question:'自我介绍准备',category:'沟通',question_type:'prepare',preparation_prompt:'按项目、职责、结果组织两分钟介绍。',pack_id:'private-pack',pack_name:'私有面经包',completion_type:null},
 {id:6,position:5,question:'什么是事务？',category:'MySQL',question_type:'review',pack_id:'private-pack',pack_name:'私有面经包',completion_type:null}
],pending:[]};
session.pending=session.items.slice();
let statsState={review_due:0},selectedMode='answer',currentView='quiz',lastVerdict=null,revealGeneration=0,speechInput=null;
function cancelSpeechInput(){} function stopJudgeProgress(){} function updateSpeechControls(){}
function loadDraft(){drafts++;return '不应读取';} function revealCurrentQuestion(){reveals++;}
function clearDraft(){drafts++;} function clearActiveSubmission(){} function readActiveSubmission(){return null;}
function renderRoundProgress(){progress++;} async function refreshQuestionStats(){}
function forgetSessionMode(){} function currentSessionMode(){return 'memorize';}
function showContextError(){} function beginStudyWork(){return ()=>{};} function showView(){}
async function streamAnswer(){streams++;}
async function api(method,path,body){
 if(path==='/api/session/complete')return {session_id:'s_exp',question_id:5,completion_type:body.completion_type,replayed:false,status:'open'};
 if(path==='/api/stats')return {total:2,review_due:0,new_count:2,mastered:0,by_cat:[]};
 if(path==='/api/session')return session;
 throw Error('unexpected '+path);
}
""" + quiz_source() + """
(async()=>{
 const supported=typeof completePrepareQuestion==='function';
 if(!supported){process.stdout.write(JSON.stringify({supported:false}));return;}
 renderQuiz(); const rendered={drafts,reveals,streams,
  prepareVisible:!$('prepare-flow').classList.contains('hidden'),
  answerHidden:$('answer-flow').classList.contains('hidden'),
  memorizeHidden:$('memorize-flow').classList.contains('hidden'),
  prompt:$('prepare-prompt').textContent,meta:$('q-meta').textContent};
 await completePrepareQuestion('prepared');
 process.stdout.write(JSON.stringify({supported,rendered,drafts,reveals,streams,progress,
  pending:session.pending.map(item=>item.id),current:currentQuestion().id}));
})().catch(error=>{console.error(error);process.exit(1)});
""")
    assert result["supported"] is True
    assert result["rendered"] == {
        "drafts": 0,
        "reveals": 0,
        "streams": 0,
        "prepareVisible": True,
        "answerHidden": True,
        "memorizeHidden": True,
        "prompt": "按项目、职责、结果组织两分钟介绍。",
        "meta": "私有面经包 · #5 · 第 4 / 2 题 · 准备题",
    }
    assert result["drafts"] == 1  # only the following review question loads its draft
    assert result["reveals"] == 1  # only the following review question reveals in memorize mode
    assert result["streams"] == 0
    assert result["pending"] == [6] and result["current"] == 6 and result["progress"] == 1


def test_experience_resume_keeps_backend_order_and_saved_positions():
    result = run_web_js(WEB_DOM + """
let session={session_id:'s_exp',session_type:'experience',items:[
 {id:91,position:12,question:'先恢复这一题',category:'网络',question_type:'review',pack_id:'pack',pack_name:'面经包'},
 {id:2,position:13,question:'再恢复这一题',category:'数据库',question_type:'review',pack_id:'pack',pack_name:'面经包'}
],pending:[]}; session.pending=session.items.slice();
let statsState={review_due:0},selectedMode='answer',currentView='quiz',lastVerdict=null,revealGeneration=0,speechInput=null;
function cancelSpeechInput(){} function stopJudgeProgress(){} function updateSpeechControls(){}
function loadDraft(){return '';} function clearActiveSubmission(){} function readActiveSubmission(){return null;}
function renderRoundProgress(){} function showContextError(){} function currentSessionMode(){return 'answer';}
""" + quiz_source() + """
renderQuiz(); const first={id:currentQuestion().id,title:$('q-title').textContent,meta:$('q-meta').textContent};
session.pending.shift(); renderQuiz();
process.stdout.write(JSON.stringify({first,second:{id:currentQuestion().id,title:$('q-title').textContent,meta:$('q-meta').textContent}}));
""")
    assert result == {
        "first": {"id": 91, "title": "先恢复这一题", "meta": "面经包 · #91 · 第 12 / 2 题"},
        "second": {"id": 2, "title": "再恢复这一题", "meta": "面经包 · #2 · 第 13 / 2 题"},
    }


def test_pack_answers_have_reviewed_provenance_without_relabelling_local_answers():
    result = run_web_js(WEB_DOM + """
""" + answer_source() + """
const result={
 packAnswer:answerMarkup({answer:'A',answer_html:'<p>A</p>',url:'https://example.test',pack_id:'pack'}),
 localAnswer:answerMarkup({answer:'A',answer_html:'<p>A</p>',url:'',pack_id:null}),
 packJudge:judgeResultMarkup({grade:'good',comment:'ok',full_answer:'A',full_answer_html:'<p>A</p>',answer_source:'stored'},{pack_id:'pack'}),
 localJudge:judgeResultMarkup({grade:'good',comment:'ok',full_answer:'A',full_answer_html:'<p>A</p>',answer_source:'stored'},{pack_id:null})
};process.stdout.write(JSON.stringify(result));
""")
    assert "题包参考答案 · 已复核" in result["packAnswer"]
    assert "题包参考答案 · 已复核" not in result["localAnswer"]
    assert "题包参考答案 · 已复核" in result["packJudge"]
    assert "标准答案 · 题库" in result["localJudge"]


def test_question_manager_renders_pack_ownership_sources_and_no_write_controls():
    result = run_web_js(WEB_DOM + """
let questionState={items:[
 {id:1,category:'MySQL',question:'本地题',answer:'本地答',answer_html:'本地答',url:'',level:0,times_seen:0,times_right:0,next_due:null,pack_id:null,sources:[]},
 {id:2,category:'项目',question:'题包题',answer:'题包答',answer_html:'题包答',url:'https://example.test/source',level:1,times_seen:2,times_right:1,next_due:null,pack_id:'pack-one',pack_name:'个人面经包',stable_question_id:'q-2',question_type:'review',answer_review_status:'reviewed',retired:false,sources:[{path:'interviews/acme.md',url:'https://example.test/source'}]}
],total:2,page:1,page_size:20,pages:1,categories:['MySQL','项目']};
function escapeHtml(value){return String(value==null?'':value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function safeHttpUrl(value){try{const parsed=new URL(value);return ['http:','https:'].includes(parsed.protocol)?parsed.href:'';}catch(_){return '';}}
function bindAnswerImageFallbacks(){} function showQuestionMessage(){} async function api(){} async function loadQuestions(){} async function refreshQuestionStats(){}
""" + management_source() + """
if(typeof renderQuestionRows!=='function'){process.stdout.write(JSON.stringify({supported:false}));}
else {renderQuestionRows();const html=$('question-list').innerHTML;
 const local=html.split('data-id="1"')[1].split('</article>')[0];
 const packed=html.split('data-id="2"')[1].split('</article>')[0];
 process.stdout.write(JSON.stringify({supported:true,local,packed}));}
""")
    assert result["supported"] is True
    assert "修改" in result["local"] and "删除" in result["local"]
    assert "个人面经包" in result["packed"] and "q-2" in result["packed"]
    assert "interviews/acme.md" in result["packed"] and "题包参考答案 · 已复核" in result["packed"]
    assert "修改" not in result["packed"] and "删除" not in result["packed"]


def test_pack_list_and_daily_review_toggle_use_exact_api_payload():
    result = run_web_js(WEB_DOM + """
let packState={packs:[]};const calls=[];
async function api(method,path,body){calls.push([method,path,body]);
 if(method==='GET')return {packs:[{pack_id:'pack.one',name:'个人面经包',revision:3,display_version:'2026.08',question_count:42,experience_count:5,include_in_review:true}]};
 return {pack_id:'pack.one',name:'个人面经包',revision:3,display_version:'2026.08',question_count:42,experience_count:5,include_in_review:body.include_in_review};}
function escapeHtml(v){return String(v);} function showContextError(){} async function refreshQuestionStats(){}
""" + management_source() + """
(async()=>{
 const supported=typeof loadPacks==='function' && typeof setPackReviewEnabled==='function';
 if(!supported){process.stdout.write(JSON.stringify({supported:false}));return;}
 await loadPacks();const html=$('pack-list').innerHTML;
 await setPackReviewEnabled('pack.one',false);
 process.stdout.write(JSON.stringify({supported,html,calls,enabled:packState.packs[0].include_in_review}));
})().catch(error=>{console.error(error);process.exit(1)});
""")
    assert result["supported"] is True
    assert all(text in result["html"] for text in ("个人面经包", "revision 3", "42", "5", "2026.08"))
    assert result["calls"] == [
        ["GET", "/api/packs", None],
        ["PUT", "/api/packs/pack.one", {"include_in_review": False}],
    ]
    assert result["enabled"] is False


def test_pack_toggle_failure_restores_control_without_unhandled_rejection():
    result = run_web_js(WEB_DOM + """
let packState={packs:[{pack_id:'pack.one',include_in_review:true}]};let errors=0,stats=0;
async function api(){throw Error('toggle failed');}
function escapeHtml(v){return String(v);} function showContextError(){errors++;}
async function refreshQuestionStats(){stats++;}
""" + management_source() + """
(async()=>{const control={checked:false,disabled:false};
await setPackReviewEnabled('pack.one',false,control);
process.stdout.write(JSON.stringify({checked:control.checked,disabled:control.disabled,errors,stats}));
})().catch(error=>{console.error(error);process.exit(1)});
""")
    assert result == {"checked": True, "disabled": False, "errors": 1, "stats": 0}


def test_desktop_pack_inspect_and_install_reuse_one_exact_snapshot_then_clear_it():
    result = run_web_js(WEB_DOM + """
const nativeStore=null;let nativeBusy='',session={session_id:null},currentView='settings';
let pendingPackBase64='',pendingPackPreview=null,reads=0;const calls=[];
class FileReader {readAsArrayBuffer(){reads++;this.result=Uint8Array.from([0,128,255,10]).buffer;this.onload();}}
async function api(method,path,body){calls.push([path,body.archive_base64]);
 if(path.endsWith('inspect'))return {pack_id:'pack-one',name:'个人面经包',revision:3,display_version:'2026.08',question_count:42,experience_count:5,installed_revision:2,status:'upgrade'};
 return {pack_id:'pack-one',status:'upgraded'};}
async function loadPacks(){} async function loadExperiences(){} async function refreshQuestionStats(){}
""" + transfer_source() + """
(async()=>{
 const supported=typeof inspectDesktopInterviewPack==='function' && typeof confirmInterviewPackInstall==='function';
 if(!supported){process.stdout.write(JSON.stringify({supported:false}));return;}
 await inspectDesktopInterviewPack({size:4});const before={cached:pendingPackBase64,preview:$('pack-preview').classList.contains('hidden'),name:$('pack-preview-name').textContent};
 await confirmInterviewPackInstall();
 process.stdout.write(JSON.stringify({supported,reads,calls,before,after:pendingPackBase64,message:$('pack-message').textContent}));
})().catch(error=>{console.error(error);process.exit(1)});
""")
    assert result["supported"] is True
    assert result["reads"] == 1
    assert result["calls"] == [
        ["/api/packs/inspect", "AID/Cg=="],
        ["/api/packs/install", "AID/Cg=="],
    ]
    assert result["before"] == {"cached": "AID/Cg==", "preview": False, "name": "个人面经包"}
    assert result["after"] == "" and "完成" in result["message"]


def test_pack_downgrade_preview_cannot_be_confirmed():
    result = run_web_js(WEB_DOM + """
const nativeStore=null;let nativeBusy='',session={session_id:null},currentView='settings';
let pendingPackBase64='',pendingPackPreview=null;const calls=[];
class FileReader {readAsArrayBuffer(){this.result=Uint8Array.from([1]).buffer;this.onload();}}
async function api(method,path,body){calls.push(path);return {pack_id:'pack-one',name:'个人面经包',revision:2,display_version:'2',question_count:4,experience_count:1,installed_revision:3,status:'downgrade'};}
async function loadPacks(){} async function loadExperiences(){} async function refreshQuestionStats(){}
""" + transfer_source() + """
(async()=>{await inspectDesktopInterviewPack({size:1});const disabled=$('btn-pack-confirm').disabled;
await confirmInterviewPackInstall();process.stdout.write(JSON.stringify({disabled,calls,cached:pendingPackBase64}));})();
""")
    assert result == {"disabled": True, "calls": ["/api/packs/inspect"], "cached": ""}


def test_pack_import_cancel_clears_snapshot_and_old_android_never_reads_file():
    desktop = run_web_js(WEB_DOM + """
const nativeStore=null;let nativeBusy='',session={session_id:null},currentView='settings';
let pendingPackBase64='',pendingPackPreview=null,reads=0;const calls=[];
class FileReader {readAsArrayBuffer(){reads++;this.result=Uint8Array.from([1,2,3]).buffer;this.onload();}}
async function api(method,path,body){calls.push(path);return {pack_id:'p',name:'P',revision:1,display_version:'1',question_count:1,experience_count:1,installed_revision:null,status:'new'};}
""" + transfer_source() + """
(async()=>{if(typeof inspectDesktopInterviewPack!=='function'){process.stdout.write(JSON.stringify({supported:false}));return;}
await inspectDesktopInterviewPack({size:3});cancelInterviewPackInstall();
process.stdout.write(JSON.stringify({reads,calls,cached:pendingPackBase64,hidden:$('pack-preview').classList.contains('hidden'),message:$('pack-message').textContent}));})();
""")
    assert desktop.get("supported", True) is True
    assert desktop == {"reads": 1, "calls": ["/api/packs/inspect"], "cached": "", "hidden": True, "message": "已取消安装，题包未改变。"}

    android = run_web_js(WEB_DOM + """
let nativeBusy='',session={session_id:null},currentView='settings',clicked=0,reads=0;
const nativeStore={};$('pack-file').click=()=>{clicked++;};
""" + transfer_source() + """
if(typeof chooseInterviewPack!=='function')process.stdout.write(JSON.stringify({supported:false}));
else {chooseInterviewPack();process.stdout.write(JSON.stringify({supported:true,clicked,reads,busy:nativeBusy,message:$('pack-message').textContent}));}
""")
    assert android["supported"] is True
    assert android["clicked"] == 0 and android["reads"] == 0 and android["busy"] == ""
    assert "更新" in android["message"] and "不支持" in android["message"]


def test_modern_android_pack_import_calls_only_native_bridge():
    result = run_web_js(WEB_DOM + """
let nativeBusy='',session={session_id:null},currentView='settings',nativeCalls=0,clicked=0;
const nativeStore={importInterviewPack:()=>{nativeCalls++;}};$('pack-file').click=()=>{clicked++;};
""" + transfer_source() + """
if(typeof chooseInterviewPack!=='function')process.stdout.write(JSON.stringify({supported:false}));
else {chooseInterviewPack();process.stdout.write(JSON.stringify({supported:true,nativeCalls,clicked,busy:nativeBusy}));}
""")
    assert result == {"supported": True, "nativeCalls": 1, "clicked": 0, "busy": "pack-import"}


def test_backup_preview_accepts_schema_v3_pack_aware_exports():
    result = run_web_js(WEB_DOM + """
""" + transfer_source() + """
try {process.stdout.write(JSON.stringify(archivePreviewMessage({mode:'progress',schema_version:3,question_count:42,created_at:'2026-08-30T01:02:03Z',app_version:'test'})));}
catch(error){process.stdout.write(JSON.stringify({error:error.message}));}
""")
    assert isinstance(result, str)
    assert "42" in result and "进度" in result
