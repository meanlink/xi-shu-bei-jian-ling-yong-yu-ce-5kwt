import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const scheduling={
 list:()=>api('/schedules'),
 get:id=>api('/schedules/'+id),
 create:data=>api('/schedules',{method:'POST',body:JSON.stringify(data)}),
 confirm:id=>api('/schedules/'+id+'/confirm',{method:'POST'}),
 workstations:()=>api('/workstations'),
 createWorkstation:data=>api('/workstations',{method:'POST',body:JSON.stringify(data)}),
 toggleWorkstation:id=>api('/workstations/'+id+'/toggle',{method:'POST'}),
 setCalendar:(id,blocked_days)=>api('/workstations/'+id+'/calendar',{method:'POST',body:JSON.stringify({blocked_days})}),
 material:part=>api('/material-days?part='+encodeURIComponent(part)),
 setMaterial:data=>api('/material-days',{method:'POST',body:JSON.stringify(data)}),
 workOrders:part=>api('/work-orders'+(part?'?part='+encodeURIComponent(part):'')),
 createWorkOrder:data=>api('/work-orders',{method:'POST',body:JSON.stringify(data)}),
 startWorkOrder:id=>api('/work-orders/'+id+'/start',{method:'POST'}),
};
