import type jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import assets from './brandingAssets.json';
import { BRANDED_PURPLE as T, BRAND_WAVES, BRAND_ID, CURRENT_BRAND_ID } from './brandedPurpleTheme';
import { headerLogoGeometry } from './documentHeader';
import { lavenderPainter, type PdfBounds } from './lavenderPdf';
import { purpleReferencePainter, pathCommands, roundedTableFrame } from './purpleReferencePdf';
import { substitute } from './fields';
import type { IssuedTemplate } from './types';
import { drawLearningPlan, type LearningPlanModule } from './learningPlanLayout';
const rgb = (value: readonly [number, number, number]): [number, number, number] => [...value];

export function brandedPage(doc: jsPDF, presentationTheme:string=BRAND_ID) {
  doc.saveGraphicsState();
  for (const wave of BRAND_WAVES) doc.setFillColor(...rgb(wave.colour)).path(pathCommands(wave.path)).fill();
  if(presentationTheme===CURRENT_BRAND_ID) {
    for(const name of ['kbcDeepPurple','dfe'] as const) {
      const position=headerLogoGeometry(name);
      doc.addImage(assets[name].data,'PNG',position.x,position.y,position.width,position.height,`official-${name}`,'FAST');
    }
    doc.restoreGraphicsState();return;
  }
  // Frozen v3 snapshots/signature continuations keep their original presentation.
  const first = doc.getCurrentPageInfo().pageNumber === 1;
  for (const [name, x, width] of [['kbc', 10, first ? 47 : 30], ['dfe', first ? 164 : 176, first ? 37 : 24]] as const) {
    const image = assets[name].data, properties = doc.getImageProperties(image);
    doc.addImage(image, 'PNG', x, name === 'kbc' ? first ? 2 : 0 : first ? -5 : -4, width, width * properties.height / properties.width, `official-${name}`, 'FAST');
  }
  doc.restoreGraphicsState();
}

function sectionIcon(doc: jsPDF, title: string, x: number, y: number) {
  doc.saveGraphicsState();
  doc.setDrawColor(255, 255, 255).setLineWidth(.42);
  const line = (a: number, b: number, c: number, d: number) => doc.line(x+a, y+b, x+c, y+d);
  if (/health|safety|disab/i.test(title)) {
    doc.path(pathCommands(`M ${x+4} ${y+6.8} C ${x+1} ${y+4.8} ${x+.3} ${y+2.8} ${x+1.9} ${y+1.8} C ${x+2.8} ${y+1.2} ${x+3.7} ${y+1.8} ${x+4} ${y+2.5} C ${x+4.7} ${y+.8} ${x+6.7} ${y+1.3} ${x+7} ${y+2.7} C ${x+7.5} ${y+4.3} ${x+5.4} ${y+6} ${x+4} ${y+6.8}`)).stroke();
    line(.8,4,2.5,4);line(2.5,4,3.2,3);line(3.2,3,4.1,5.3);line(4.1,5.3,4.8,4);line(4.8,4,7.3,4);
  } else if (/education|training|qualification|learning|rpl|prior/i.test(title)) {
    doc.path(pathCommands(`M ${x+.2} ${y+2.5} L ${x+4} ${y+.8} L ${x+7.8} ${y+2.5} L ${x+4} ${y+4.3} Z`)).stroke();
    line(1.4,3.2,1.4,5.8);line(1.4,5.8,4,6.8);line(4,6.8,6.6,5.8);line(6.6,5.8,6.6,3.2);line(7.8,2.5,7.8,5.2);
  } else if (/employ|work|delivery|responsibil|provider/i.test(title)) {
    doc.roundedRect(x+.6,y+2.5,6.8,4.8,.6,.6,'S');doc.roundedRect(x+2.5,y+1,3,2,.4,.4,'S');line(.6,4.5,7.4,4.5);line(4,4,4,5.3);
  } else if (/learner|personal|apprentice|contact|signature/i.test(title)) {
    doc.circle(x+4,y+2.5,1.45,'S');doc.roundedRect(x+1.4,y+4.6,5.2,2.6,.8,.8,'S');
  } else {
    doc.roundedRect(x+1.5,y+.7,5.1,6.6,.4,.4,'S');line(2.5,2.5,5.5,2.5);line(2.5,4,5.5,4);line(2.5,5.5,4.7,5.5);
  }
  doc.restoreGraphicsState();
}

export function brandedPurplePainter(doc: jsPDF, template: IssuedTemplate, b: PdfBounds, latin: (value: unknown) => string, decorate: () => void) {
  const base = lavenderPainter(doc, template, b, latin, T);
  const historical = purpleReferencePainter(doc, template, b, latin, decorate);
  const font = (size: number, bold = false) => doc.setFont('helvetica', bold ? 'bold' : 'normal').setFontSize(size);
  const text: typeof base.text = (runs, style = {}, kind = 'paragraph', prefix = '', interpolate = true) => {
    if (!['title', 'heading', 'subheading'].includes(kind)) { base.text(runs, style, kind, prefix, interpolate); return; }
    const title = kind === 'title', value = runs.map(run => interpolate ? substitute(run.text, template) : run.text).join('');
    const size = style.fontSize ?? (title ? T.font.title : kind === 'subheading' ? T.font.subheading : T.font.section);
    const inset = title ? 0 : 16, width = b.width-inset-2;
    font(size, style.bold ?? true);
    const lines = doc.splitTextToSize(latin(value), width) as string[];
    const lineHeight = size*.352778*T.lineHeight;
    const height = Math.max(title ? 8 : 9.6, lines.length*lineHeight+2.5);
    b.room(Math.min(height+(title ? 9 : 9), b.bottom-b.top));
    const top = b.getY();
    if (!title) {
      // Axial-looking lavender bands remain selectable-vector PDF decoration.
      doc.setFillColor(...rgb(T.section)).roundedRect(b.x,top,b.width,height,1.8,1.8,'F');
      for(let i=0;i<60;i++) {
        const f=i/59;doc.setFillColor(Math.round(245-19*f),Math.round(233-28*f),Math.round(252-10*f));
        doc.rect(b.x+1.8+i*(b.width-3.6)/60,top,(b.width-3.6)/60+.03,height,'F');
      }
      doc.setFillColor(...rgb(T.accent)).roundedRect(b.x+1.5,top+.5,11.3,height-1,1.4,1.4,'F');
      sectionIcon(doc, value, b.x+3.2, top+(height-8)/2);
    }
    const adjusted = {...b,x:b.x+inset,width};
    b.setY(top+(title ? 0 : 1.6));
    lavenderPainter(doc,template,adjusted,latin,{...T,text:T.heading}).text(runs,{...style,fontSize:size,bold:style.bold??true,spaceAfter:0},'paragraph','',interpolate);
    b.setY(Math.max(b.getY(),top+height));
    if(title) { doc.setFillColor(...rgb(T.accent)).roundedRect(b.x,b.getY()+1,34,1.7,.85,.85,'F'); b.setY(b.getY()+8); }
    else b.setY(b.getY()+2.7);
  };
  const tableStartHeight = (columns:string[],rows:unknown[][]) => {
    const widths=columns.map(()=>b.width/Math.max(1,columns.length));
    const height=(row:unknown[],bold:boolean,padding:number)=>{
      font(T.font.table,bold);
      return Math.max(1,...row.map((value,i)=>doc.splitTextToSize(latin(String(value??'')),Math.max(5,widths[i]-padding*2)).length))*T.font.table*.352778*1.15+padding*2;
    };
    return Math.min(b.bottom-b.top,height(columns,true,1.65)+height(rows[0]||[],false,T.tablePadding));
  };
  const table = (columns: string[], rows: unknown[][], interpolate = true) => {
    b.room(tableStartHeight(columns,rows));
    const segments=new Map<number,{top:number;bottom:number}>();
    autoTable(doc, {startY:b.getY(),head:[columns.map(v=>latin(substitute(v,template)))],
      body:rows.map(row=>row.map(v=>latin(interpolate?substitute(String(v??''),template):v))),
      tableWidth:b.width,theme:'grid',rowPageBreak:'avoid',
      margin:{left:b.x,right:210-b.x-b.width,top:b.top,bottom:297-b.bottom},
      styles:{font:'helvetica',fontSize:T.font.table,cellPadding:T.tablePadding,textColor:rgb(T.text),fillColor:rgb(T.white),lineColor:rgb(T.border),lineWidth:.16,overflow:'linebreak',valign:'top'},
      headStyles:{fillColor:rgb(T.secondary),textColor:rgb(T.white),fontStyle:'bold',fontSize:10,cellPadding:1.65},
      ...(columns.length===2?{columnStyles:{0:{cellWidth:b.width*.5}}}:{}),
      willDrawPage:()=>decorate(),
      didDrawCell:value=>{const page=doc.getCurrentPageInfo().pageNumber;const old=segments.get(page)||{top:value.cell.y,bottom:value.cell.y};old.top=Math.min(old.top,value.cell.y);old.bottom=Math.max(old.bottom,value.cell.y+value.cell.height);segments.set(page,old);},
    });
    const last=doc.getCurrentPageInfo().pageNumber;
    for(const [page,segment] of segments){doc.setPage(page);roundedTableFrame(doc,b.x,segment.top,b.width,segment.bottom-segment.top,T.border);}
    doc.setPage(last);
    b.setY((doc as jsPDF&{lastAutoTable:{finalY:number}}).lastAutoTable.finalY+5);
  };
  function finish() {
    const total=doc.getNumberOfPages();
    for(let page=1;page<=total;page++) {
      doc.setPage(page);doc.saveGraphicsState();
      doc.setDrawColor(...rgb(T.accent)).setLineWidth(.3).line(61,287,194,287);
      doc.setFillColor(...rgb(T.secondary)).roundedRect(175,282.7,28,8.2,3.5,3.5,'F');
      font(9);doc.setTextColor(255,255,255).text(`Page ${page} of ${total}`,189,287.9,{align:'center'});
      const layout=template.content.pageLayout;
      if(layout?.footer){font(T.font.caption);doc.setTextColor(...rgb(T.muted)).text(doc.splitTextToSize(latin(substitute(layout.footer,template)),b.width-32),b.x,Math.min(278,b.bottom+5),{lineHeightFactor:T.lineHeight});}
      if(layout?.header){font(T.font.caption);const lines=doc.splitTextToSize(latin(substitute(layout.header,template)),b.width);doc.setTextColor(...rgb(T.muted)).text(lines,b.x,b.top-3-(lines.length-1)*T.font.caption*.352778*T.lineHeight,{lineHeightFactor:T.lineHeight});}
      doc.restoreGraphicsState();
    }
  }
  const signatureHeight = (label:string,state:Record<string,any>,provenance?:Record<string,any>) => {
    const panel=base.signatureHeight('',state,provenance);font(T.font.section,true);
    const lines=doc.splitTextToSize(latin(substitute(label,template)),b.width-18).length;
    return {...panel,height:panel.height+Math.max(9.6,lines*T.font.section*.352778*T.lineHeight+2.5)+6.7};
  };
  return {...historical,text,table,tableStartHeight,signatureHeight,finish,
    signature: (label:string,state:Record<string,any>,mark:unknown,provenance?:Record<string,any>) => {
      // Reuse the existing exact mark/provenance writer, with the branded heading.
      b.room(Math.min(signatureHeight(label,state,provenance).height,b.bottom-b.top));
      text([{text:label}],{fontSize:T.font.section,bold:true},'heading');
      base.signature('',state,mark,provenance);
    },
    learningPlan:(columns:string[],modules:LearningPlanModule[]) => drawLearningPlan(doc,b,columns,modules,latin,{table,frame:()=>{},palette:T}),
  };
}
