import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchLearnerCalendarEvents, invalidateLearnerCalendarCache, bookLearnerCalendarSession } from '../learnerCalendar';
const response=(data:unknown)=>({ok:true,status:200,text:async()=>JSON.stringify(data),json:async()=>data}) as Response;
const before={events:[{eventKey:'review-1',status:'not-scheduled'}]},after={events:[{eventKey:'review-1',status:'scheduled'}]};
beforeEach(()=>invalidateLearnerCalendarCache());
afterEach(()=>{invalidateLearnerCalendarCache();vi.unstubAllGlobals();});
describe('Training Plan calendar destination freshness',()=>{
  it('loads the updated booking when opening the calendar after a Training Plan refresh',async()=>{
    let finish!:(value:Response)=>void;
    const fetch=vi.fn().mockImplementationOnce(()=>new Promise<Response>(resolve=>{finish=resolve;})).mockResolvedValue(response(after));
    vi.stubGlobal('fetch',fetch);
    const stale=fetchLearnerCalendarEvents('commercial','125');
    expect(await fetchLearnerCalendarEvents('commercial','125',{force:true})).toEqual(after);
    finish(response(before));await stale;
    expect(await fetchLearnerCalendarEvents('commercial','125')).toEqual(after);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it('does not recache an old request after a successful booking',async()=>{
    let finish!:(value:Response)=>void;
    const fetch=vi.fn().mockImplementationOnce(()=>new Promise<Response>(resolve=>{finish=resolve;}))
      .mockResolvedValueOnce(response({event:after.events[0]})).mockResolvedValue(response(after));
    vi.stubGlobal('fetch',fetch);
    const stale=fetchLearnerCalendarEvents('commercial','125');
    await bookLearnerCalendarSession('commercial','125',{sessionType:'progress-review',eventKey:'review-1',scheduledDate:'2026-09-14',scheduledTime:'14:00',durationMinutes:60});
    finish(response(before));await stale;
    expect(await fetchLearnerCalendarEvents('commercial','125')).toEqual(after);
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
