import type jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import { lavenderPainter, type PdfBounds } from './lavenderPdf';
import { REFERENCE_PURPLE as T, REFERENCE_WAVES } from './purpleReferenceTheme';
import { substitute } from './fields';
import type { IssuedTemplate } from './types';
import { drawLearningPlan, type LearningPlanModule } from './learningPlanLayout';

type Colour = readonly [number,number,number];
const rgb = (c:Colour):[number,number,number]=>[...c];

export function pathCommands(path:string) {
  const tokens=path.match(/[MLCZ]|-?\d+(?:\.\d+)?/g)!; const result:{op:string;c:number[]}[]=[];
  const counts:Record<string,number>={M:2,L:2,C:6,Z:0};
  for(let i=0;i<tokens.length;){const op=tokens[i++];const count=counts[op];result.push({op:op==='Z'?'h':op.toLowerCase(),c:tokens.slice(i,i+count).map(Number)});i+=count;}
  return result;
}

export function drawReferenceDecorations(doc:jsPDF) {
  doc.saveGraphicsState();
  for(const wave of REFERENCE_WAVES)doc.setFillColor(...rgb(wave.colour)).path(pathCommands(wave.path)).fill();
  doc.restoreGraphicsState();
}

/** Round the outer frame only; all text/cell geometry stays in the PDF engine. */
export function roundedTableFrame(doc:jsPDF,x:number,y:number,width:number,height:number,border:Colour=T.border) {
  const r=Math.min(2,height/3);
  doc.saveGraphicsState();doc.setFillColor(255,255,255);
  // White wedges remove only square corner pixels, inside cell padding.
  const c=.552285;
  doc.path([{op:'m',c:[x,y]},{op:'l',c:[x+r,y]},{op:'c',c:[x+r-r*c,y,x,y+r-r*c,x,y+r]},{op:'h',c:[]}]).fill();
  doc.path([{op:'m',c:[x+width,y]},{op:'l',c:[x+width,y+r]},{op:'c',c:[x+width,y+r-r*c,x+width-r+r*c,y,x+width-r,y]},{op:'h',c:[]}]).fill();
  doc.path([{op:'m',c:[x,y+height]},{op:'l',c:[x,y+height-r]},{op:'c',c:[x,y+height-r+r*c,x+r-r*c,y+height,x+r,y+height]},{op:'h',c:[]}]).fill();
  doc.path([{op:'m',c:[x+width,y+height]},{op:'l',c:[x+width-r,y+height]},{op:'c',c:[x+width-r+r*c,y+height,x+width,y+height-r+r*c,x+width,y+height-r]},{op:'h',c:[]}]).fill();
  doc.setDrawColor(...rgb(border)).setLineWidth(.22).roundedRect(x,y,width,height,r,r,'S');doc.restoreGraphicsState();
}

export function purpleReferencePainter(doc:jsPDF,template:IssuedTemplate,b:PdfBounds,latin:(v:unknown)=>string,ensureDecoration:()=>void) {
  const base=lavenderPainter(doc,template,{...b,width:b.width-1.5},latin,T);
  const font=(size:number,bold=false,italic=false)=>doc.setFont('helvetica',bold&&italic?'bolditalic':bold?'bold':italic?'italic':'normal').setFontSize(size);
  const lineHeight=(size:number)=>size*.352778*T.lineHeight;
  function textHeight(value:string,size:number,bold:boolean,after:number){font(size,bold);return doc.splitTextToSize(latin(substitute(value,template)),b.width-5).length*lineHeight(size)+after;}

  const text:typeof base.text=(runs,style={},kind='paragraph',prefix='',interpolate=true)=>{
    if(!['title','heading','subheading'].includes(kind)) {base.text(runs,style,'paragraph',prefix,interpolate);return;}
    const size=style.fontSize??(kind==='title'?T.font.title:kind==='heading'?T.font.section:T.font.subheading);
    const value=runs.map(r=>interpolate?substitute(r.text,template):r.text).join('');
    font(size,style.bold??true,!!style.italic);
    const inset=kind==='title'?2:4.5;
    const titleWidth=kind==='title'?Math.min(b.width-inset,158):b.width-inset;
    const lines=doc.splitTextToSize(latin(value),titleWidth) as string[];
    const height=Math.max(1,lines.length)*lineHeight(size);
    b.room(Math.min(height+(kind==='title'?9:13),b.bottom-b.top));
    const top=b.getY();
    if(kind!=='title')doc.setFillColor(...rgb(T.accent)).roundedRect(b.x,top+.3,1.9,Math.min(6,height),.8,.8,'F');
    // Reuse the rich-text writer so supported per-run overrides remain intact.
    const adjusted={...b,x:b.x+inset,width:titleWidth};
    const headingInk={...T,text:T.heading};
    lavenderPainter(doc,template,adjusted,latin,headingInk).text(runs,{...style,fontSize:size,bold:style.bold??true,spaceAfter:0},'paragraph','',interpolate);
    if(kind==='title'){
      b.setY(b.getY()+3);
      const width=Math.min(113,b.width);
      doc.setFillColor(...rgb(T.section)).roundedRect(b.x+2,b.getY(),width,1.35,.65,.65,'F');
      doc.setFillColor(...rgb(T.accent)).roundedRect(b.x+2,b.getY(),Math.min(33,width),1.35,.65,.65,'F');
      b.setY(b.getY()+2);
    }
    b.setY(b.getY()+(style.spaceAfter??(kind==='title'?5:3)));
  };

  function columnWidth(columns:string[],body:string[][]) {
    if(columns.length!==2)return undefined;
    font(T.font.table);
    const labels=Math.max(0,...body.map(row=>doc.getTextWidth(row[0]||'')+T.tablePadding*2));
    const values=Math.max(0,...body.map(row=>doc.getTextWidth(row[1]||'')+T.tablePadding*2));
    return Math.max(b.width*.5,Math.min(b.width*.82,labels,b.width-Math.min(values,b.width*.5)));
  }
  function tableStartHeight(columns:string[],rows:unknown[][]) {
    const body=rows.map(row=>row.map(v=>latin(String(v??'')))), first=columnWidth(columns,body);
    const widths=columns.map((_,i)=>first?(i===0?first:b.width-first):b.width/columns.length);
    const height=(row:string[],size:number,padding:number,bold:boolean)=>{
      font(size,bold);
      return Math.max(1,...row.map((v,i)=>doc.splitTextToSize(v,Math.max(5,widths[i]-padding*2)).length))*size*.352778*1.15+padding*2;
    };
    // Exceptionally tall rows still flow across pages; reserve a readable
    // first fragment rather than pushing their heading onto an empty sheet.
    return Math.min(80,height(columns,10,1.8,true)+height(body[0]||[],T.font.table,T.tablePadding,false));
  }
  function table(columns:string[],rows:unknown[][],interpolate=true) {
    const head=columns.map(v=>latin(substitute(v,template)));
    const body=rows.map(row=>row.map(v=>latin(interpolate?substitute(String(v??''),template):v)));
    b.room(Math.min(tableStartHeight(head,body),b.bottom-b.top));
    const segments=new Map<number,{top:number;bottom:number}>();
    const firstWidth=columnWidth(columns,body);
    autoTable(doc,{
      startY:b.getY(),head:[head],body,tableWidth:b.width,theme:'grid',rowPageBreak:'avoid',
      margin:{left:b.x,right:210-b.x-b.width,top:b.top,bottom:297-b.bottom},
      styles:{font:'helvetica',fontSize:T.font.table,textColor:rgb(T.text),fillColor:rgb(T.white),cellPadding:T.tablePadding,overflow:'linebreak',valign:'top',lineColor:rgb(T.border),lineWidth:.16},
      headStyles:{fillColor:rgb(T.section),textColor:rgb(T.secondary),fontStyle:'bold',fontSize:10,cellPadding:1.8},
      ...(firstWidth?{columnStyles:{0:{cellWidth:firstWidth}}}:{}),
      willDrawPage:()=>ensureDecoration(),
      didDrawCell:data=>{const n=doc.getCurrentPageInfo().pageNumber;const segment=segments.get(n)||{top:data.cell.y,bottom:data.cell.y};segment.top=Math.min(segment.top,data.cell.y);segment.bottom=Math.max(segment.bottom,data.cell.y+data.cell.height);segments.set(n,segment);},
    });
    const end=doc.getCurrentPageInfo().pageNumber;
    for(const [page,segment] of segments){doc.setPage(page);roundedTableFrame(doc,b.x,segment.top,b.width,segment.bottom-segment.top);}
    doc.setPage(end);b.setY((doc as jsPDF&{lastAutoTable:{finalY:number}}).lastAutoTable.finalY+4);
  }

  function signatureHeight(label:string,state:Record<string,any>,provenance?:Record<string,any>) {
    const name=`Name: ${state.name||''}${state.position?` | Position: ${state.position}`:''}`;
    const date=`Date: ${state.signedAt?String(state.signedAt).slice(0,10).split('-').reverse().join('/'):''}`;
    const history=provenance?`Historical signature reproduced from Revision R${provenance.sourceRevision}. Signed by ${provenance.name||state.name||''} on ${String(provenance.signedAt||'').slice(0,10)}. This signature records approval of that earlier revision; it does not record approval of this revised wording.`:'';
    font(T.font.section,true);const labelLines=doc.splitTextToSize(latin(substitute(label,template)),b.width-5).length;
    font(9);const detailLines=doc.splitTextToSize(latin(name),b.width-5).length+1+(history?doc.splitTextToSize(latin(history),b.width-5).length:0);
    return {name,date,history,labelHeight:labelLines*lineHeight(T.font.section),height:labelLines*lineHeight(T.font.section)+36+detailLines*lineHeight(9)};
  }
  function signature(label:string,state:Record<string,any>,mark:unknown,provenance?:Record<string,any>) {
    const measured=signatureHeight(label,state,provenance);
    b.room(Math.min(measured.height,b.bottom-b.top));
    text([{text:label}],{fontSize:T.font.section,bold:true,spaceAfter:3},'heading');
    b.room(22);
    if(state.signed&&typeof mark==='string'&&mark.startsWith('data:image/')){
      try{const image=doc.getImageProperties(mark),scale=Math.min(72/image.width,18/image.height);doc.addImage(mark,image.fileType,b.x+4,b.getY(),image.width*scale,image.height*scale);}catch{/* Existing invalid-image fallback: never invent a mark. */}
    }
    b.setY(b.getY()+19);
    doc.setDrawColor(...rgb(T.border)).setLineWidth(.28).line(b.x+4,b.getY(),b.x+Math.min(80,b.width),b.getY());b.setY(b.getY()+2);
    base.text([{text:measured.name}],{fontSize:9,spaceAfter:2},'signature-detail','',false);
    base.text([{text:measured.date}],{fontSize:9,spaceAfter:3},'signature-detail','',false);
    if(measured.history)base.text([{text:measured.history}],{fontSize:8.5,italic:true,spaceAfter:4},'signature-detail','',false);
    b.setY(b.getY()+3);
  }
  function learningPlan(columns:string[],modules:LearningPlanModule[]) {
    drawLearningPlan(doc,b,columns,modules,latin,{table,frame:roundedTableFrame});
  }
  function finish() {
    const layout=template.content.pageLayout,total=doc.getNumberOfPages();
    for(let page=1;page<=total;page++){
      doc.setPage(page);font(T.font.caption);doc.setTextColor(...rgb(T.muted));
      if(layout?.header){const lines=doc.splitTextToSize(latin(substitute(layout.header,template)),b.width);doc.text(lines,b.x,Math.max(5,b.top-3-(lines.length-1)*lineHeight(T.font.caption)),{lineHeightFactor:T.lineHeight});}
      const y=Math.min(276,b.bottom+5);
      if(layout?.footer)doc.text(doc.splitTextToSize(latin(substitute(layout.footer,template)),b.width-22),b.x,y,{lineHeightFactor:T.lineHeight});
      if(layout?.pageNumbers)doc.text(`${page} / ${total}`,b.x+b.width,y,{align:'right'});
    }
  }
  return {text,table,textHeight,tableStartHeight,signatureHeight,signature,finish,learningPlan};
}
