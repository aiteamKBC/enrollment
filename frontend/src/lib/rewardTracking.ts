import type { TimeTrackingSession,TrackingActivityKind } from '@/api/timeTracking';
import { synchronizeTimeTrackingClock } from '@/api/timeTracking';
import { trackingOffsets, clearTrackingClock } from './trackingClock';
import {newLmsObservations,readLmsObservations,appendLmsObservation,prepareLmsObservationEvent,confirmLmsObservationEvent} from './rewardObservations.mjs';
const memory=new Map<string,any>();
const flushing=new Map<string,Promise<void>>();
const key=(session:TimeTrackingSession)=>'kbc-reward-intervals:v1:'+session.sessionId;
function read(session:TimeTrackingSession){
 const saved=memory.get(key(session));
 if(saved)return saved;
 const raw=localStorage.getItem(key(session));
 const value=raw?readLmsObservations(JSON.parse(raw),session.sessionId):newLmsObservations(session.sessionId);
 memory.set(key(session),value);return value;
}
function save(session:TimeTrackingSession,value:any){
 memory.set(key(session),value);
 localStorage.setItem(key(session),JSON.stringify(value));
}
/** Called only by the activity clock while the page/player is actually active. */
export function observeRewardInterval(session:TimeTrackingSession|null,start:number,end:number){
 if(!session?.rewardTracking)return;
 if(!Number.isFinite(start)||!Number.isFinite(end))return;
 const offsets=trackingOffsets(session,start,end);
 let from=offsets.start,to=offsets.end;
 if(to<=from||(!session.persistent && to>86400000))return;
 let value=read(session);from=Math.max(from,value.lastEndOffsetMs);
 while(from<to){const next=Math.min(from+5000,to);value=appendLmsObservation(value,{startOffsetMs:from,endOffsetMs:next});from=next;}
 save(session,value);
}
export async function flushRewardObservations(session:TimeTrackingSession,activityKind:TrackingActivityKind,activityId:string,force=false){
 const previous=flushing.get(session.sessionId);
 const pending=(async()=>{if(previous)await previous.catch(()=>undefined);await flush(session,activityKind,activityId,force);})();
 flushing.set(session.sessionId,pending);
 try{await pending;}finally{if(flushing.get(session.sessionId)===pending)flushing.delete(session.sessionId);}
}
async function flush(session:TimeTrackingSession,activityKind:TrackingActivityKind,activityId:string,force:boolean){
 if(!session.rewardTracking)return;
 let value=read(session);
 while(value.pending.length||value.inFlight||value.nextSequence===1||force){
  value=prepareLmsObservationEvent(value,force);force=false;save(session,value);
  const request: RequestInit={method:'POST',credentials:'include',
   headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
   body:JSON.stringify({trackingToken:session.trackingToken,activityKind,activityId,...value.inFlight})};
  let response=await fetch('/learner_api/time-tracking/observations/',request);
  let data=await response.json();
  if(!response.ok && data.code==='observation_ahead_of_server'){
   const elapsed=await synchronizeTimeTrackingClock(session,activityKind,activityId);
   const ahead=(value.inFlight.intervals.at(-1)?.endOffsetMs ?? 0)-elapsed;
   // Wait at most one observation segment for real server time to catch up.
   // Never shift, trim, recreate or acknowledge the rejected observations.
   if(ahead>5000)throw new Error('The saved study timer is out of sync with the LMS. Your answers and timing draft are kept. Please contact support if retrying does not resolve this.');
   if(ahead>0)await new Promise(resolve=>setTimeout(resolve,ahead));
   response=await fetch('/learner_api/time-tracking/observations/',request);
   data=await response.json();
  }
  if(!response.ok)throw Object.assign(new Error(data.error||'Your study time could not be confirmed. Please retry.'),{code:data.code});
  // The timer may have appended more intervals while this request was in flight.
  value=confirmLmsObservationEvent(read(session),data);save(session,value);
 }
}
export function clearRewardObservations(session:TimeTrackingSession|null){
 if(!session)return;memory.delete(key(session));
 clearTrackingClock(session);
 try{localStorage.removeItem(key(session));}catch{/* Already saved to LMS; do not suggest resubmitting. */}
}
