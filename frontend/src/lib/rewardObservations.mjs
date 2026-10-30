// A retryable queue of measured short intervals. Only the LMS can accept them
// or award hours: this queue never creates points or formal OTJ records.
const safeId = value => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,150}$/.test(value);
const interval = value => value && Number.isSafeInteger(value.startOffsetMs) && Number.isSafeInteger(value.endOffsetMs) && value.startOffsetMs >= 0 && value.endOffsetMs > value.startOffsetMs && value.endOffsetMs-value.startOffsetMs <= 5000;
const ordered = list => Array.isArray(list) && list.length <= 90000 && list.every((item,i) => interval(item) && (!i || item.startOffsetMs >= list[i-1].endOffsetMs));
export function newLmsObservations(sessionId) {
 if (!safeId(sessionId)) throw new Error('This timing session cannot be matched to the LMS.');
 return {version:1,sessionId,nextSequence:1,acknowledgedMilliseconds:0,lastEndOffsetMs:0,pending:[],inFlight:null};
}
export function readLmsObservations(value, sessionId) {
 if (value === undefined || value === null) return newLmsObservations(sessionId);
 if (!value || value.version!==1 || value.sessionId!==sessionId || !safeId(sessionId) || !Number.isSafeInteger(value.nextSequence) || value.nextSequence<1 || value.nextSequence>2147483647 || !Number.isSafeInteger(value.acknowledgedMilliseconds) || value.acknowledgedMilliseconds<0 || !Number.isSafeInteger(value.lastEndOffsetMs) || value.lastEndOffsetMs<0 || !ordered(value.pending)) throw new Error('The measured-time draft needs attention. Your answers are kept; retry syncing your time.');
 const flight=value.inFlight;
 if (flight && (flight.sequence!==value.nextSequence || flight.eventId!==sessionId+':'+value.nextSequence || !ordered(flight.intervals) || flight.intervals.length>120)) throw new Error('The saved timing request could not be confirmed. Your draft is kept.');
 const all=[...(flight?.intervals||[]),...value.pending];
 if(!ordered(all) || (all.length && all.at(-1).endOffsetMs>value.lastEndOffsetMs)) throw new Error('The saved timing intervals could not be confirmed. Your draft is kept.');
 return {...value,pending:value.pending.map(x=>({...x})),inFlight:flight?{...flight,intervals:flight.intervals.map(x=>({...x}))}:null};
}
export function appendLmsObservation(state, measuredInterval) {
 if (!measuredInterval) return state;
 // No reconstruction of past time, no merging across pause/seek/idle gaps.
 if (!interval(measuredInterval) || measuredInterval.startOffsetMs<state.lastEndOffsetMs) return state;
 const pending=state.pending.map(x=>({...x})),last=pending.at(-1);
 if(last && last.endOffsetMs===measuredInterval.startOffsetMs && measuredInterval.endOffsetMs-last.startOffsetMs<=5000) last.endOffsetMs=measuredInterval.endOffsetMs;
 else pending.push({...measuredInterval});
 return {...state,pending,lastEndOffsetMs:measuredInterval.endOffsetMs};
}
export function prepareLmsObservationEvent(state, force = false) {
 if(state.inFlight) return state;
 if(!state.pending.length && state.nextSequence>1 && !force) return state;
 if(state.nextSequence>=2147483647) throw new Error('Start a new timed visit to continue recording.');
 return {...state,pending:state.pending.slice(120),inFlight:{eventId:state.sessionId+':'+state.nextSequence,sequence:state.nextSequence,intervals:state.pending.slice(0,120)}};
}
export function confirmLmsObservationEvent(state,response) {
 const flight=state.inFlight,ms=flight?.intervals.reduce((n,x)=>n+x.endOffsetMs-x.startOffsetMs,0);
 if(!flight || response?.source!=='lms' || response.eventId!==flight.eventId || response.sessionId!==state.sessionId || response.sequence!==flight.sequence || response.status!=='pending' || response.observedMilliseconds!==ms || response.cumulativeObservedMilliseconds!==state.acknowledgedMilliseconds+ms || typeof response.duplicate!=='boolean') throw new Error('The LMS did not confirm the timing details. Your draft is kept for a safe retry.');
 return {...state,nextSequence:state.nextSequence+1,acknowledgedMilliseconds:state.acknowledgedMilliseconds+ms,inFlight:null};
}
