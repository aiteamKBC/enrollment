import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { keys, contentFor, payloadFor } from './fixtures';
import type { DocumentKey } from '../types';
import type { DocumentRevision } from '../revisionApi';

const api=vi.hoisted(()=>({revisionHistory:vi.fn(),readRevision:vi.fn(),startRevision:vi.fn(),createDocumentFromTemplate:vi.fn(),discardRevision:vi.fn()}));
vi.mock('../revisionApi',()=>api);
vi.mock('@/hooks/useAuth.helpers',()=>({useAuth:()=>({auth:{account:{role:'staff'},user:{fullName:'Synthetic Admin'}}})}));
vi.mock('../LearnerRevisionEditor',()=>({LearnerRevisionEditor:({initial}:any)=><div role="dialog" aria-label="Existing editor">{initial.revision.id}</div>}));
vi.mock('../DocumentPreview',()=>({DocumentPreview:()=> <div role="dialog" aria-label="Existing PDF viewer"/>}));
vi.mock('../previewPdf',()=>({previewPdf:vi.fn(async()=>new Blob(['synthetic pdf']))}));
import { DocumentHistory } from '../DocumentHistory';
import { LearnerDocumentRevisions } from '../LearnerDocumentRevisions';

function row(key:DocumentKey,number:number):DocumentRevision{
 return {id:`${key}-r${number}`,issueId:key,documentKey:key,sourceId:key,number,title:contentFor(key).title,
  baseId:number>1?`${key}-r1`:null,state:number===1?'original':'draft',status:number===1?'Signed':'Draft',fullySigned:number===1,
  signatureMethod:number===1?'original':'',editVersion:1,canDiscard:number>1,templateVersionId:'v1',templateVersionNumber:1,
  createdBy:'Admin',createdAt:'2030-01-01',finalizedAt:number===1?'2030-01-01':null,reason:'Correction',changes:{},checksum:'hash',provenance:{},signatures:{}};
}
let histories:Map<DocumentKey,DocumentRevision[]>;
beforeEach(()=>{
 vi.clearAllMocks();histories=new Map(keys.map(key=>[key,[row(key,1),row(key,2)]]));
 api.revisionHistory.mockImplementation(async(_id,key:DocumentKey)=>({results:histories.get(key)||[],setupRequired:false}));
 api.readRevision.mockImplementation(async(_id,id:string)=>({revision:keys.flatMap(k=>histories.get(k)||[]).find(r=>r.id===id),preview:payloadFor('ilr')}));
 api.startRevision.mockImplementation(async(_id,key:DocumentKey)=>({revision:row(key,2),preview:payloadFor(key)}));
 api.discardRevision.mockImplementation(async(_id,r:DocumentRevision)=>{histories.set(r.documentKey,(histories.get(r.documentKey)||[]).filter(v=>v.id!==r.id));return {discarded:true};});
 vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:synthetic');vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{});
 vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
});
function Cards({order=keys,learner='20',sourceSuffix=''}:{order?:DocumentKey[];learner?:string;sourceSuffix?:string}){
 return <MemoryRouter>{order.map(key=><DocumentHistory key={`${learner}:${key}:${key+sourceSuffix}`} title={contentFor(key).title}>{({chevron,count,historyControl})=><section aria-label={`${key} profile document`}>
  <header>{chevron}<span>{contentFor(key).title}</span>{count}<span>Learner signed</span><span>Employer pending</span><span>Provider signed</span><time>01/01/2030</time><button>Original download</button></header>
  <LearnerDocumentRevisions learnerId={learner} documentKey={key} sourceId={key+sourceSuffix} historyControl={historyControl}/>
 </section>}</DocumentHistory>)}</MemoryRouter>;
}
const expand=(key:DocumentKey)=>screen.findByRole('button',{name:`Expand version history for ${contentFor(key).title}`});
const region=(key:DocumentKey)=>screen.getByRole('region',{name:`${key} profile document`});

describe('profile document history presentation',()=>{
 it('collapses all eight histories by default while headers, statuses and actions remain visible',async()=>{
  render(<Cards/>);
  for(const key of keys){
   const toggle=await expand(key);expect(toggle).toHaveAttribute('aria-expanded','false');
   expect(document.getElementById(toggle.getAttribute('aria-controls')!)).not.toBeVisible();
   const card=region(key);expect(within(card).getByText('2 versions')).toBeVisible();
   for(const label of ['Learner signed','Employer pending','Provider signed','01/01/2030'])expect(within(card).getByText(label)).toBeVisible();
   for(const name of ['Original download','Create','History'])expect(within(card).getByRole('button',{name})).toBeVisible();
   expect(within(card).queryByRole('article')).not.toBeInTheDocument();
  }
  expect(api.startRevision).not.toHaveBeenCalled();expect(api.discardRevision).not.toHaveBeenCalled();
 });
 it('keeps chevron and History synchronized, permits multiple open documents, and performs no writes',async()=>{
  render(<Cards/>);fireEvent.click(await expand('ilr'));
  expect(within(region('ilr')).getAllByRole('article')).toHaveLength(2);
  expect(within(region('ilr')).getByRole('button',{name:'History'})).toHaveAttribute('aria-expanded','true');
  expect(within(region('training-plan')).queryByRole('article')).not.toBeInTheDocument();
  fireEvent.click(within(region('training-plan')).getByRole('button',{name:'History'}));
  expect(within(region('training-plan')).getAllByRole('article')).toHaveLength(2);
  fireEvent.click(within(region('ilr')).getByRole('button',{name:'History'}));
  expect(await expand('ilr')).toHaveAttribute('aria-expanded','false');expect(within(region('ilr')).queryByRole('article')).not.toBeInTheDocument();
  expect(within(region('training-plan')).getAllByRole('article')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button',{name:'Collapse version history for Training Plan'}));
  expect(within(region('training-plan')).queryByRole('article')).not.toBeInTheDocument();
  expect(api.startRevision).not.toHaveBeenCalled();expect(api.discardRevision).not.toHaveBeenCalled();expect(api.readRevision).not.toHaveBeenCalled();
 });
 it('opens with Enter and Space, keeps state with stable identities on reorder, and resets for another learner',async()=>{
  const user=userEvent.setup(),view=render(<Cards order={['ilr','training-plan']}/>);
  const toggle=await expand('ilr');toggle.focus();await user.keyboard('{Enter}');expect(toggle).toHaveAttribute('aria-expanded','true');
  await user.keyboard(' ');expect(toggle).toHaveAttribute('aria-expanded','false');await user.keyboard('{Enter}');
  view.rerender(<Cards order={['training-plan','ilr']}/>);
  expect(screen.getByRole('button',{name:'Collapse version history for Individual Learner Record'})).toHaveAttribute('aria-expanded','true');
  expect(await expand('training-plan')).toHaveAttribute('aria-expanded','false');
  view.rerender(<Cards order={['ilr']} learner="21"/>);
  expect(await expand('ilr')).toHaveAttribute('aria-expanded','false');
 });
 it('does not offer a chevron or empty version cards when there is no history or a source changes',async()=>{
  histories.set('ilr',[]);const view=render(<Cards order={['ilr']}/>);
  await waitFor(()=>expect(api.revisionHistory).toHaveBeenCalled());
  expect(screen.queryByRole('button',{name:/version history for/})).not.toBeInTheDocument();expect(screen.queryByRole('article')).not.toBeInTheDocument();
  histories.set('ilr',[row('ilr',1)]);view.rerender(<Cards order={['ilr']} sourceSuffix="-new"/>);
  await waitFor(()=>expect(api.revisionHistory).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole('button',{name:/version history for/})).not.toBeInTheDocument();
 });
 it('preserves audit details, Compare, View, Download, Edit, Continue editing and Delete Draft rules',async()=>{
  render(<Cards order={['ilr']}/>);const toggle=await expand('ilr');fireEvent.click(toggle);
  const cards=within(region('ilr')).getAllByRole('article');
  expect(within(cards[0]).queryByRole('button',{name:'Delete'})).not.toBeInTheDocument();
  fireEvent.click(within(cards[1]).getByRole('button',{name:'Compare'}));expect(await screen.findByRole('dialog',{name:'Revision R2 changes'})).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'Close comparison'}));
  fireEvent.click(within(cards[1]).getByRole('button',{name:'Continue editing'}));expect(await screen.findByRole('dialog',{name:'Existing editor'})).toHaveTextContent('ilr-r2');
  fireEvent.click(within(cards[1]).getByRole('button',{name:'View'}));expect(await screen.findByRole('dialog',{name:'Existing PDF viewer'})).toBeVisible();
  fireEvent.click(within(cards[1]).getByRole('button',{name:'Download'}));await waitFor(()=>expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled());
  fireEvent.click(within(cards[0]).getByRole('button',{name:'Edit'}));await waitFor(()=>expect(api.startRevision).toHaveBeenCalledWith('20','ilr','ilr','ilr-r1'));
  fireEvent.click(within(region('ilr')).getByRole('button',{name:'View history details'}));
  const details=await screen.findByRole('dialog',{name:'Document revision history'});expect(details).toHaveTextContent('Checksum: hash');expect(details).toHaveTextContent('Correction');
  fireEvent.click(screen.getByRole('button',{name:'Close history'}));
  fireEvent.click(within(cards[1]).getByRole('button',{name:'Delete'}));
  const confirmation=await screen.findByRole('dialog',{name:'Delete Draft Revision?'});
  fireEvent.click(within(confirmation).getByRole('button',{name:'Delete Draft'}));
  await waitFor(()=>expect(within(region('ilr')).queryByText('Version 2 — Draft')).not.toBeInTheDocument());
  expect(await within(region('ilr')).findByText('1 version')).toBeVisible();expect(within(region('ilr')).getByText('Version 1 — Original')).toBeVisible();
 });
});

