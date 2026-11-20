import {jobs,scheduling} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const parts=()=>(workspace.meta.parameters.find(p=>p.key==='part')?.options||[]).map(o=>({value:String(o.value),label:String(o.label)}));
const partLabel=v=>parts().find(p=>p.value===v)?.label||v;
const isAdmin=()=>session.user.role==='admin';
const canWrite=()=>session.user.role!=='viewer';
const entryStatus=e=>e.status==='scheduled'?'已排入':'未排入';

function materialLine(e){
 const m=e.material;
 return '用料日 '+esc(e.material_day)+' · 可发 '+esc(fmt(m.releasable))+' · 预测留料 '+esc(fmt(m.forecast_reserved))+' · 既有占料 '+esc(fmt(m.locked_material))+' · 本次占料 '+esc(fmt(m.scheduled_material))+' · 当日剩余 '+esc(fmt(m.remaining));
}

function entryTable(entries){
 if(!entries.length)return '<div class="empty">该备件暂无工单。请先维护工单后再排程。</div>';
 return '<div class="table-wrap"><table class="entry-table"><thead><tr><th>工单</th><th>类别</th><th>数量</th><th>期限</th><th>工位</th><th>开工日</th><th>完工日</th><th>状态 / 未排原因</th></tr></thead><tbody>'+
  entries.map(e=>'<tr class="'+(e.status==='scheduled'?'':'blocked')+'"><td><strong>'+esc(e.code)+'</strong><br><small>'+esc(e.name)+'</small></td>'+
   '<td><span class="kind '+(e.kind==='line_stop'?'stop':'')+'">'+esc(e.kind_label)+'</span></td>'+
   '<td>'+esc(fmt(e.quantity))+'</td><td>'+esc(e.deadline)+'</td>'+
   '<td>'+esc(e.workstation_code||'—')+'</td><td>'+esc(e.start_day||'—')+'</td><td>'+esc(e.end_day||'—')+'</td>'+
   '<td>'+(e.status==='scheduled'?'<span class="status completed">'+esc(entryStatus(e))+'</span>'+(e.locked?'<small class="tag">'+(e.started?'已开工 · 锁定':'已确认')+'</small>':'')+(e.reason?'<br><small>'+esc(e.reason)+'</small>':''):'<span class="status failed">'+esc(entryStatus(e))+'</span><br><small class="reason">'+esc(e.reason||'—')+'</small>')+
   (e.status==='scheduled'&&e.material?'<br><small class="basis">'+materialLine(e)+'</small>':'')+'</td></tr>').join('')+'</tbody></table></div>';
}

function dailyTable(daily){
 if(!daily?.length)return '<div class="empty">暂无每日用料依据。</div>';
 return '<div class="table-wrap"><table><thead><tr><th>日期</th><th>可发量</th><th>预测留料（向上取整）</th><th>既有占料</th><th>本次占料</th><th>当日剩余</th><th>说明</th></tr></thead><tbody>'+
  daily.map(d=>'<tr><td>'+esc(d.day)+'</td><td>'+esc(fmt(d.releasable))+'</td><td>'+esc(fmt(d.forecast_reserved))+'</td><td>'+esc(fmt(d.locked_material))+'</td><td>'+esc(fmt(d.scheduled_material))+'</td><td>'+esc(fmt(d.remaining))+'</td><td>'+(d.note?'<span class="reason">'+esc(d.note)+'</span>':'—')+'</td></tr>').join('')+'</tbody></table></div>';
}

function scheduleDetail(s,workstations){
 if(!s)return '<section class="panel empty">还没有排程版本。填写左侧参数后生成草稿，再确认占位。</section>';
 const sum=s.summary||{},draft=s.status==='draft';
 const versions=(workspace.schedules||[]).map(v=>'<button class="batch-item '+(v.id===s.id?'selected':'')+'" data-schedule="'+v.id+'"><strong>#'+v.id+' · '+esc(v.name)+'</strong><span>'+esc(partLabel(v.part))+' · '+(v.status==='confirmed'?'已确认':'草稿')+' · '+date(v.created_at)+'</span><small>开始 '+esc(v.start_date)+(v.replaces_id?' · 替换 #'+v.replaces_id:'')+'</small></button>').join('');
 return '<section class="panel"><div class="section-title"><h2>排程 #'+s.id+' · '+esc(s.name)+'</h2><span>'+(s.status==='confirmed'?'已确认 '+date(s.confirmed_at):'草稿 · 未占料')+'</span></div>'+
  '<div class="sched-summary"><div><span>停线单</span><strong>'+sum.line_stop_scheduled+' / '+(sum.line_stop_scheduled+sum.line_stop_unscheduled)+'</strong></div><div><span>普通单</span><strong>'+sum.normal_scheduled+' / '+(sum.normal_scheduled+sum.normal_unscheduled)+'</strong></div><div><span>合计数量</span><strong>'+esc(fmt(sum.quantity_scheduled))+'</strong></div><div><span>启用工位</span><strong>'+(sum.workstations??'—')+'</strong></div></div>'+
  '<p class="hint">备件 '+esc(partLabel(s.part))+' · 排程开始 '+esc(s.start_date)+' · 预测覆盖 '+esc(sum.forecast_first_day||'—')+' 至 '+esc(sum.forecast_last_day||'—')+(s.replaces_id?' · 替换已确认版本 #'+s.replaces_id:'')+'</p>'+
  (draft?'<div class="schedule-actions"><button class="primary" id="confirm-schedule" '+(isAdmin()?'':'disabled')+'>'+(isAdmin()?'确认排程并占料':'仅业务负责人可确认')+'</button><span class="hint">确认时若工位日历或领料余量已更新，将退回重算；替换失败保留旧版。</span></div>':'<p class="hint">该版本已确认。工位停用或余量变化后请重新生成草稿，已开工工单会保留原位。</p>')+
  '</section>'+
  '<section class="panel"><div class="section-title"><h2>工单排入与未排原因</h2><span>'+sum.scheduled+' 已排 · '+sum.unscheduled+' 未排</span></div>'+entryTable(s.entries||[])+'</section>'+
  '<section class="panel"><div class="section-title"><h2>每日用料依据</h2><span>可发量 − 预测留料 − 占料 = 剩余</span></div>'+dailyTable(s.daily)+'</section>'+
  '<section class="panel"><div class="section-title"><h2>历史版本</h2><span>'+((workspace.schedules||[]).length)+' 版</span></div><div class="batch-list long">'+versions+'</div></section>';
}

export async function schedulePage(id){
 const [schedules,workstations,orders,allJobs]=await Promise.all([scheduling.list(),scheduling.workstations(),scheduling.workOrders(),jobs.list()]);
 workspace.schedules=schedules;
 const chosen=schedules.find(s=>String(s.id)===String(id))||schedules.find(s=>s.id===workspace.selectedSchedule)||schedules[0]||null;
 workspace.selectedSchedule=chosen?.id||null;
 const part=chosen?.part||workspace.schedulePart||parts()[0]?.value||'R17';
 workspace.schedulePart=part;
 const material=await scheduling.material(part);
 const approved=allJobs.filter(j=>j.approved&&j.status==='completed'&&String(j.params.part)===part);
 const defaultJob=approved.find(j=>j.id===workspace.scheduleJob)||approved[0];
 const firstDay=defaultJob?.result?.table?.rows?.[0]?.day||'';
 const lastDay=defaultJob?.result?.table?.rows?.slice(-1)[0]?.day||'';
 const materialByDay=Object.fromEntries(material.days.map(d=>[d.day,d.releasable]));
 const list=orders.filter(o=>o.part===part);
 return heading('05 / SCHEDULING','检修排程与日常留料','每天从可发量中扣掉向上取整的已批准预测，工位与余料都够才整单排入；停线单优先，普通单其次，完工日不越过工单期限。','<button class="button" id="refresh-schedule">刷新排程 ↻</button>')+
  '<div class="sched-grid"><section class="panel"><div class="section-title"><h2>生成排程草稿</h2><span>草稿不占料</span></div>'+
   '<form id="schedule-form"><label>排程名称<input name="name" required minlength="2" value="'+esc(partLabel(part)+' 检修排程草稿')+'"></label>'+
   '<label>备件<select name="part" id="schedule-part">'+parts().map(p=>'<option value="'+esc(p.value)+'" '+(p.value===part?'selected':'')+'>'+esc(p.label)+'</option>').join('')+'</select></label>'+
   '<label>已批准预测<select name="forecast_job_id" id="schedule-job" required>'+approved.map(j=>'<option value="'+j.id+'" data-part="'+esc(j.params.part)+'" data-first="'+esc(j.result?.table?.rows?.[0]?.day||'')+'" data-last="'+esc(j.result?.table?.rows?.slice(-1)[0]?.day||'')+'" '+(defaultJob&&j.id===defaultJob.id?'selected':'')+'>'+esc(j.name)+' · '+(j.result?.table?.rows?.length||0)+' 天</option>').join('')+'</select></label>'+
   '<label>排程开始日<input type="date" name="start_date" id="schedule-start" required value="'+esc(firstDay)+'" min="'+esc(firstDay)+'" max="'+esc(lastDay)+'"></label>'+
   '<button type="submit" class="primary" '+(canWrite()&&approved.length?'':'disabled')+'>生成草稿并试排</button>'+
   '<p class="hint">'+(approved.length?'基于所选已批准预测的逐日期望值留料。':'该备件没有已批准的预测结果，请先在工作台运行并批准。')+'</p></form>'+
   '</section>'+
   '<section class="panel"><div class="section-title"><h2>'+esc(partLabel(part))+' 工单</h2><span>'+list.length+' 单</span></div>'+
   '<form id="order-form"><div class="parameter-grid"><label>工单编号<input name="code" required minlength="2" placeholder="LS-2620"></label><label>类别<select name="kind"><option value="normal">普通单</option><option value="line_stop">停线单</option></select></label></div>'+
   '<label>工单名称<input name="name" required minlength="2" placeholder="例如：R17 主轴密封环更换"></label>'+
   '<div class="parameter-grid"><label>整单数量<input type="number" name="quantity" step="any" min="0.0001" value="5" required></label><label>工期（天）<input type="number" name="duration_days" min="1" max="60" step="1" value="1" required></label></div>'+
   '<div class="parameter-grid"><label>工单期限<input type="date" name="deadline" required value="'+esc(lastDay)+'"></label><label>最早开工<input type="date" name="earliest_start" value="'+esc(firstDay)+'"></label></div>'+
   '<label>可用工位<select name="eligible" multiple size="'+Math.min(4,Math.max(1,workstations.length))+'">'+workstations.map(w=>'<option value="'+esc(w.code)+'">'+esc(w.code+' · '+w.name)+'</option>').join('')+'</select></label>'+
   '<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>新增工单</button><p class="hint">不选可用工位表示全部启用工位。整单数量必须一次备足。</p></form>'+
   '<div class="order-list">'+list.map(o=>'<article><div><strong>'+esc(o.code)+' · '+esc(o.name)+'</strong><p>'+esc(o.kind_label)+' · '+esc(fmt(o.quantity))+' 件 · 工期 '+o.duration_days+' 天 · 期限 '+esc(o.deadline)+'</p><small>'+(o.started?'已开工 · 占位锁定':'未开工')+(o.eligible_workstations.length?' · 限定工位 '+esc(o.eligible_workstations.join(' / ')):'')+'</small></div>'+(o.started?'':'<button class="button small" data-start-order="'+o.id+'" '+(canWrite()?'':'disabled')+'>标记开工</button>')+'</article>').join('')+(!list.length?'<p class="empty">该备件暂无工单。</p>':'')+'</div></section></div>'+
  scheduleDetail(chosen,workstations)+
  '<div class="sched-grid"><section class="panel"><div class="section-title"><h2>工位与日历</h2><span>停用或改日历会使草稿失效</span></div>'+
   '<div class="station-list">'+workstations.map(w=>'<article><div><strong>'+esc(w.code)+' · '+esc(w.name)+'</strong><small>'+(w.active?'启用':'已停用')+' · 日历版本 '+w.calendar_version+' · 停用日 '+(w.blocked_days.length?esc(w.blocked_days.join('、')):'无')+'</small></div><button class="button small" data-toggle-station="'+w.id+'" '+(isAdmin()?'':'disabled')+'>'+(w.active?'停用':'启用')+'</button></article>').join('')+(!workstations.length?'<p class="empty">尚无工位。</p>':'')+'</div>'+
   (workstations.length?'<form id="calendar-form" class="inline-form"><label>工位<select name="workstation_id">'+workstations.map(w=>'<option value="'+w.id+'">'+esc(w.code)+'</option>').join('')+'</select></label><label>停用日期（逗号分隔）<input name="days" placeholder="2026-10-02, 2026-10-03"></label><button type="submit" '+(isAdmin()?'':'disabled')+'>保存日历</button></form>':'')+
   (isAdmin()?'<form id="station-form" class="inline-form"><div class="parameter-grid"><label>工位编号<input name="code" required minlength="1" maxlength="40" placeholder="W4"></label><label>工位名称<input name="name" required minlength="2" placeholder="四号检修工位"></label></div><button type="submit">新增工位</button></form>':'')+
   '</section>'+
   '<section class="panel"><div class="section-title"><h2>'+esc(partLabel(part))+' 可发量</h2><span>版本 '+material.version+' · '+material.days.length+' 天</span></div>'+
   '<form id="material-form" class="inline-form"><div class="parameter-grid"><label>用料日<input type="date" name="day" required value="'+esc(firstDay)+'"></label><label>可发量<input type="number" name="releasable" step="any" min="0" value="'+esc(materialByDay[firstDay]??9)+'" required></label></div><button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>更新可发量</button></form>'+
   '<p class="hint">可发量是当天能发出的总量；系统每天再扣掉该备件已批准预测的向上取整值作为日常领用留料。</p>'+
   '<div class="table-wrap material-table"><table><thead><tr><th>日期</th><th>可发量</th><th>预测留料</th></tr></thead><tbody>'+material.days.map(d=>'<tr><td>'+esc(d.day)+'</td><td>'+esc(fmt(d.releasable))+'</td><td>'+esc(fmt(chosen?.daily?.find(x=>x.day===d.day)?.forecast_reserved??'—'))+'</td></tr>').join('')+'</tbody></table></div>'+
   (!material.days.length?'<p class="empty">该备件还没有可发量记录。</p>':'')+'</section></div>';
}

export function bindScheduling(render){
 document.querySelector('#refresh-schedule').onclick=render;
 document.querySelectorAll('[data-schedule]').forEach(b=>b.onclick=()=>{workspace.selectedSchedule=Number(b.dataset.schedule);location.hash='#/scheduling/'+b.dataset.schedule;});
 const partSelect=document.querySelector('#schedule-part'),jobSelect=document.querySelector('#schedule-job'),startInput=document.querySelector('#schedule-start');
 if(partSelect)partSelect.onchange=()=>{workspace.schedulePart=partSelect.value;workspace.selectedSchedule=null;location.hash='#/scheduling';render();};
 if(jobSelect)jobSelect.onchange=()=>{const o=jobSelect.selectedOptions[0];if(o){startInput.value=o.dataset.first;startInput.min=o.dataset.first;startInput.max=o.dataset.last;}};
 const createForm=document.querySelector('#schedule-form');
 if(createForm)submit(createForm,async f=>{const j=await scheduling.create({name:f.get('name'),part:f.get('part'),forecast_job_id:Number(f.get('forecast_job_id')),start_date:f.get('start_date')});workspace.selectedSchedule=j.id;workspace.schedulePart=j.part;notify('排程草稿已生成，请核对未排原因后确认');location.hash='#/scheduling/'+j.id;});
 const confirm=document.querySelector('#confirm-schedule');
 if(confirm)confirm.onclick=async()=>{confirm.disabled=true;try{await scheduling.confirm(workspace.selectedSchedule);notify('排程已确认并占料');await render();}catch(e){notify(e.message,true);confirm.disabled=false;}};
 document.querySelectorAll('[data-toggle-station]').forEach(b=>b.onclick=async()=>{try{await scheduling.toggleWorkstation(b.dataset.toggleStation);notify('工位状态已更新，已有草稿需重新生成');await render();}catch(e){notify(e.message,true);}});
 const calendar=document.querySelector('#calendar-form');
 if(calendar)submit(calendar,async f=>{const days=String(f.get('days')||'').split(/[,，\s]+/).map(s=>s.trim()).filter(Boolean);await scheduling.setCalendar(Number(f.get('workstation_id')),days);notify('工位日历已保存，相关草稿需重新生成');await render();});
 const stationForm=document.querySelector('#station-form');
 if(stationForm)submit(stationForm,async f=>{await scheduling.createWorkstation({code:f.get('code'),name:f.get('name')});notify('工位已新增');await render();});
 const material=document.querySelector('#material-form');
 if(material)submit(material,async f=>{await scheduling.setMaterial({part:workspace.schedulePart,day:f.get('day'),releasable:Number(f.get('releasable'))});notify('可发量已更新，相关草稿需重新生成');await render();});
 const orderForm=document.querySelector('#order-form');
 if(orderForm)submit(orderForm,async f=>{const eligible=[...document.querySelector('[name=eligible]').selectedOptions].map(o=>o.value);await scheduling.createWorkOrder({code:f.get('code'),name:f.get('name'),part:workspace.schedulePart,kind:f.get('kind'),quantity:Number(f.get('quantity')),duration_days:Number(f.get('duration_days')),deadline:f.get('deadline'),earliest_start:f.get('earliest_start')||null,eligible_workstations:eligible});notify('工单已新增，重新生成排程即可排入');await render();});
 document.querySelectorAll('[data-start-order]').forEach(b=>b.onclick=async()=>{try{await scheduling.startWorkOrder(b.dataset.startOrder);notify('工单已标记开工，后续重排保留原位');await render();}catch(e){notify(e.message,true);}});
}
