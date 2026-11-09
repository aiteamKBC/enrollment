import type jsPDF from 'jspdf';
import type { PdfBounds } from './lavenderPdf';
import { REFERENCE_PURPLE as T } from './purpleReferenceTheme';

type Hours=number|string|null;
export interface LearningPlanModule {
  activity?:string; method?:string; deliveryLead?:string; plannedDate?:string; plannedOtjHours?:Hours;
  weeks?:{weekNumber?:number|null;weekTitle?:string;components?:{title?:string;method?:string;otjHours?:Hours}[]}[];
}
export interface PlanPrintRow { kind:'module'|'week'|'activity'|'empty';values:string[] }
export function learningPlanRows(modules:LearningPlanModule[]):PlanPrintRow[] {
  const rows:PlanPrintRow[]=[];
  for(const module of modules){
    rows.push({kind:'module',values:[module.activity||'',module.method||'',module.deliveryLead||'',module.plannedDate||'',String(module.plannedOtjHours??'')]});
    for(const week of module.weeks||[]){
      rows.push({kind:'week',values:[`Week ${week.weekNumber||''}${week.weekTitle?` - ${week.weekTitle}`:''}`,'','','','']});
      for(const component of week.components||[])rows.push({kind:'activity',values:[component.title||'',component.method||'','','',String(component.otjHours??'')]});
    }
    if(!module.weeks?.length)rows.push({kind:'empty',values:['No weeks have been authored for this module yet.','','','','']});
  }
  return rows;
}

export function learningPlanWidths(width:number,rows:PlanPrintRow[],measure:(value:string)=>number) {
  const padding=T.tablePadding*2;
  const hours=Math.max(22,...rows.map(r=>measure(r.values[4])+padding));
  const date=Math.max(26,...rows.map(r=>measure(r.values[3])+padding));
  const method=32,lead=30,activity=width-method-lead-date-hours;
  return {widths:[activity,method,lead,date,hours],stacked:activity<52};
}

/** Five measured columns, explicit non-wrapping dates/numbers, and row-level
 * pagination. No rounding or hour/date recalculation is performed here. */
export function drawLearningPlan(doc:jsPDF,b:PdfBounds,columns:string[],modules:LearningPlanModule[],latin:(v:unknown)=>string,helpers:{table:(columns:string[],rows:unknown[][],interpolate?:boolean)=>void;frame:(doc:jsPDF,x:number,y:number,width:number,height:number)=>void;palette?:import('./documentTheme').DocumentPalette}) {
  const palette=helpers.palette;
  const rows=learningPlanRows(modules).map(row=>({...row,values:row.values.map(latin)}));
  const colour=(rgb:readonly[number,number,number]):[number,number,number]=>[...rgb];
  const font=(bold=false,size:number=T.font.table)=>doc.setFont('helvetica',bold?'bold':'normal').setFontSize(size);
  const measure=(value:string)=>{font();const normal=doc.getTextWidth(value);font(true);return Math.max(normal,doc.getTextWidth(value));};
  const layout=learningPlanWidths(b.width,rows,measure);
  const labels=Array.from({length:5},(_,i)=>latin(columns[i]||['Activity/Unit','Method','Delivery Lead','Planned Date','OTJ (hours)'][i]));
  if(layout.stacked){
    // Explicit wide-margin configurations use readable, related field/value
    // rows instead of shrinking fonts or losing a column.
    for(const row of rows){
      if(row.kind==='week'||row.kind==='empty'){helpers.table([labels[0]],[[row.values[0]]],false);continue;}
      // A long activity belongs in the body, never the repeating header: an
      // oversized header cannot be paginated safely by AutoTable.
      helpers.table(['Field','Value'],labels.map((label,i)=>[label,row.values[i]]),false);
    }
    return;
  }
  const widths=layout.widths,padding=T.tablePadding,lineHeight=T.font.table*.352778*1.2;
  const segments=new Map<number,{top:number;bottom:number}>();
  const noteSegment=(top:number,bottom:number)=>{const page=doc.getCurrentPageInfo().pageNumber;const segment=segments.get(page)||{top,bottom};segment.top=Math.min(segment.top,top);segment.bottom=Math.max(segment.bottom,bottom);segments.set(page,segment);};
  function lines(values:string[],kind:PlanPrintRow['kind']|'header') {
    font(kind==='header'||kind==='module'||kind==='week',kind==='header'?10:T.font.table);
    if(kind==='week'||kind==='empty')return [doc.splitTextToSize(values[0],b.width-padding*2) as string[]];
    return values.map((value,i)=>kind!=='header'&&(i===3||i===4)?[value]:doc.splitTextToSize(value,widths[i]-padding*2-(i===0&&kind==='activity'?2.5:0)) as string[]);
  }
  const headerLines=lines(labels,'header');
  const headerLineHeight=10*.352778*1.2;
  const headerHeight=Math.max(...headerLines.map(v=>v.length))*headerLineHeight+padding*2;
  function paintRow(cellLines:string[][],kind:PlanPrintRow['kind']|'header',height:number) {
    const top=b.getY();const grouped=kind==='week'||kind==='empty';
    const fill=kind==='header'&&palette?palette.secondary:kind==='header'||kind==='module'?(palette?.section??T.section):kind==='week'?[246,238,255] as const:T.white;
    doc.setFillColor(...colour(fill)).setDrawColor(...colour(T.border)).setLineWidth(.16);
    let x=b.x;
    cellLines.forEach((values,i)=>{
      const width=grouped?b.width:widths[i];
      // jsPDF text painting changes the active fill colour; restore it for
      // every cell so later columns cannot be filled with the text colour.
      doc.setFillColor(...colour(fill)).rect(x,top,width,height,'FD');
      font(kind==='header'||kind==='module'||kind==='week',kind==='header'?10:T.font.table);
      doc.setTextColor(...colour(kind==='header'&&palette?palette.white:kind==='header'||kind==='module'||kind==='week'?(palette?.heading??T.heading):(palette?.text??T.text)));
      const numeric=i===4&&!grouped,date=i===3&&!grouped;
      const left=x+padding+(i===0&&kind==='activity'?2.5:0);
      const textX=numeric?x+width-padding:date?x+width/2:left;
      doc.text(values,textX,top+padding+(kind==='header'?10:T.font.table)*.352778*.85,{align:numeric?'right':date?'center':'left',lineHeightFactor:1.2});
      x+=width;
    });
    b.setY(top+height);noteSegment(top,b.getY());
  }
  const header=()=>paintRow(headerLines,'header',headerHeight);
  const newPage=()=>{b.page();header();};
  let firstGroup=0;
  for(const row of rows){firstGroup+=Math.max(1,...lines(row.values,row.kind).map(v=>v.length))*lineHeight+padding*2;if(row.kind==='activity'||row.kind==='empty')break;}
  b.room(headerHeight+Math.min(firstGroup,b.bottom-b.top-headerHeight));header();
  for(let index=0;index<rows.length;index++){
    const row=rows[index],measured=lines(row.values,row.kind);
    let remaining=measured.map(v=>[...v]);
    const height=Math.max(1,...remaining.map(v=>v.length))*lineHeight+padding*2;
    let keep=height;
    if(row.kind==='module'||row.kind==='week'){
      for(let next=index+1;next<rows.length;next++){
        const following=rows[next],nextHeight=Math.max(1,...lines(following.values,following.kind).map(v=>v.length))*lineHeight+padding*2;
        keep+=Math.min(nextHeight,b.bottom-b.top-headerHeight);
        if(following.kind==='activity'||following.kind==='module')break;
      }
    }
    if(b.getY()+Math.min(keep,b.bottom-b.top-headerHeight)>b.bottom)newPage();
    while(remaining.some(v=>v.length)){
      const available=b.bottom-b.getY();
      const required=Math.max(1,...remaining.map(v=>v.length))*lineHeight+padding*2;
      if(required<=available){paintRow(remaining,row.kind,required);break;}
      const wholePage=b.bottom-b.top-headerHeight;
      if(required<=wholePage){newPage();continue;}
      const count=Math.floor((available-padding*2)/lineHeight);
      if(count<1){newPage();continue;}
      // Only a row taller than a full page is split; short date/hour strings
      // remain intact in its first fragment, never character-wrapped.
      const fragment=remaining.map(v=>v.slice(0,count));
      paintRow(fragment,row.kind,Math.max(...fragment.map(v=>v.length))*lineHeight+padding*2);
      remaining=remaining.map(v=>v.slice(count));
      if(remaining.some(v=>v.length))newPage();
    }
  }
  const end=doc.getCurrentPageInfo().pageNumber;
  for(const [page,segment] of segments){doc.setPage(page);helpers.frame(doc,b.x,segment.top,b.width,segment.bottom-segment.top);}
  doc.setPage(end);b.setY(b.getY()+4);
}
