/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,code:string,name:string,active:boolean,blocked_days:string[],calendar_version:number}} Workstation */
/** @typedef {{id:number,code:string,name:string,part:string,kind:'line_stop'|'normal',quantity:number,duration_days:number,deadline:string,earliest_start:string|null,eligible_workstations:string[],started:boolean}} WorkOrder */
/** @typedef {{work_order_id:number,code:string,name:string,kind:string,kind_label:string,quantity:number,duration_days:number,deadline:string,started:boolean,locked:boolean,status:'scheduled'|'unscheduled',workstation_id:number|null,workstation_code:string|null,start_day:string|null,end_day:string|null,material_day:string|null,reason:string|null,material:Object|null}} ScheduleEntry */
/** @typedef {{day:string,releasable:number|null,forecast_reserved:number|null,locked_material:number,scheduled_material:number,remaining:number|null,note:string}} DailyUsage */
/** @typedef {{id:number,name:string,part:string,forecast_job_id:number,start_date:string,status:'draft'|'confirmed',entries:ScheduleEntry[],daily:DailyUsage[],summary:Object,replaces_id:number|null}} MaintenanceSchedule */
export {};
