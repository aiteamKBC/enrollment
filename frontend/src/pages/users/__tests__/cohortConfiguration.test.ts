import { expect,it } from 'vitest';
import { copyCohortConfiguration,defaultPeriod,validatePeriod } from '../programme-structure/cohortConfiguration';

it('validates assignment dates using the named year without any delivery start date',()=>{
 const period={startMonth:10,startDay:15,endMonth:2,endDay:15};
 expect(validatePeriod('Feb 2035',period)).toEqual({...period,year:2035});
 expect(validatePeriod('Renamed Cohort',{...period,year:2035})).toEqual({...period,year:2035});
 expect(()=>validatePeriod('No year',period)).toThrow('Include a single year');
 expect(()=>validatePeriod('Feb 2027',{...period,endDay:29})).toThrow('valid assignment period');
});

it('copies delivery details and shared KSB mappings with independent module/topic IDs',()=>{
 const source={modules:[{id:'m1',title:'Marketing',description:'m1',order:2,sessions:16,durationWeeks:8,book:1,progressReviews:3,selection:'required',weeklyTopics:[{id:'topic',title:'Session 1',order:0}]}],mappings:[{moduleId:'m1',cohortModuleId:'m1',ksbId:'stable-K1'},{moduleId:'m1',ksbId:'stable-K2'}],routes:[]};
 const before=structuredClone(source);const copied=copyCohortConfiguration(source) as typeof source;
 expect(copied.modules[0].id).not.toBe('m1');expect(copied.modules[0].weeklyTopics[0].id).not.toBe('topic');
 expect(copied.modules[0]).toMatchObject({title:'Marketing',description:'m1',sessions:16,book:1,progressReviews:3,selection:'required'});
 expect(copied.mappings.map(m=>m.ksbId)).toEqual(['stable-K1','stable-K2']);
 expect(copied.mappings.every(m=>m.moduleId===copied.modules[0].id)).toBe(true);
 copied.modules[0].weeklyTopics[0].title='Changed';expect(source).toEqual(before);
});

it('preserves PCP route credits, counts and owner rules while rewiring nested module identities',()=>{
 const source={routes:[{id:'operational',name:'Operational',targetCredits:6,requiredOptionalModulesCount:2,ownerAllocation:{'stable-K1':'child'},modules:[{id:'group',title:'Group',items:[{id:'child',title:'Module',credits:3,isMandatory:true,weeklyTopics:[]}]}]}],modules:[],mappings:[]};
 const copy=copyCohortConfiguration(source) as typeof source;
 expect(copy.routes[0].id).not.toBe('operational');
 expect(copy.routes[0].modules[0].items[0].id).not.toBe('child');
 expect(copy.routes[0].ownerAllocation['stable-K1']).toBe(copy.routes[0].modules[0].items[0].id);
 expect(copy.routes[0]).toMatchObject({targetCredits:6,requiredOptionalModulesCount:2});
 expect(source.routes[0].ownerAllocation['stable-K1']).toBe('child');
});

it('uses existing custom calendar dates without exposing or rewriting old codes',()=>{
 const old=[{startMonth:2,startDay:20,endMonth:6,endDay:12,active:true}];
 expect(defaultPeriod('2035-06-01',old)).toEqual({startMonth:2,startDay:20,endMonth:6,endDay:12});
 expect(defaultPeriod('2035-10-01',old)).toBeUndefined();
 expect(defaultPeriod('2035-02-01')).toEqual({startMonth:10,startDay:15,endMonth:2,endDay:15});
});
