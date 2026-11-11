import { expect,it,vi } from 'vitest';
import { newModuleId } from '../programme-structure/moduleIdentity';

it('uses a unique UUID without changing existing identities',()=>{
 const snapshot={programmes:[{modules:[{id:'legacy'}],cohorts:[{modules:[{id:'existing'}],routes:[{modules:[{items:[{id:'route-module'}]}]}]}]}]};
 const before=structuredClone(snapshot);
 const generate=vi.fn().mockReturnValueOnce('route-module').mockReturnValueOnce('existing').mockReturnValueOnce('unique');
 expect(newModuleId(snapshot,generate)).toBe('unique');
 expect(generate).toHaveBeenCalledTimes(3);
 expect(snapshot).toEqual(before);
 expect(newModuleId(snapshot)).toMatch(/^[0-9a-f]{8}-[0-9a-f-]{27}$/i);
});

it('rejects repeated collisions instead of changing an existing Module',()=>{
 expect(()=>newModuleId({modules:[{id:'taken'}]},()=> 'taken')).toThrow('unique Module identity');
});
