import {afterEach,expect,it,vi} from 'vitest';
import {webcrypto,createHash} from 'node:crypto';
import {Blob as NodeBlob} from 'node:buffer';
import {fetchVerifiedInstaller} from './installerTransfer';
afterEach(()=>vi.unstubAllGlobals());

const bytes=new Uint8Array(2300000).map((_,i)=>i%251);
const hash=createHash('sha256').update(bytes).digest('hex');
function source(overrides:(call:number,part:Uint8Array,headers:Record<string,string>)=>Response|undefined=()=>undefined){
 let calls=0;
 return vi.fn(async(_url:string,init:RequestInit)=>{
  calls++;const headers=init.headers as Record<string,string>, match=/bytes=(\d+)-(\d+)/.exec(headers.Range)!;
  const start=Number(match[1]),end=Math.min(Number(match[2])+1,bytes.length),part=bytes.slice(start,end);
  const responseHeaders={'Content-Range':`bytes ${start}-${end-1}/${bytes.length}`,'ETag':'"'+hash+'"','X-APK-SHA256':hash};
  return overrides(calls,part,responseHeaders)||new Response(part,{status:206,headers:responseHeaders});
 });
}
const run=(fetcher:ReturnType<typeof source>,signal=new AbortController().signal)=>{
 vi.stubGlobal('Blob',NodeBlob);
 return fetchVerifiedInstaller(()=>{},signal,fetcher as typeof fetch,data=>webcrypto.subtle.digest('SHA-256',data));
};
it('reassembles a complete installer from authenticated ranges and checks its digest',async()=>{
 const fetcher=source(),blob=await run(fetcher);
 expect(Buffer.from(await blob.arrayBuffer()).equals(Buffer.from(bytes))).toBe(true);
 expect(fetcher).toHaveBeenCalledTimes(3);
 expect(fetcher.mock.calls[1][1]).toMatchObject({credentials:'same-origin',redirect:'error',headers:{'If-Range':'"'+hash+'"'}});
});
it('retries a truncated chunk at the same offset instead of saving an incomplete APK',async()=>{
 const fetcher=source((call,part,headers)=>call===2?new Response(part.slice(0,8),{status:206,headers}):undefined);
 const blob=await run(fetcher);
 expect(blob.size).toBe(bytes.length);
 expect(fetcher.mock.calls[1][1].headers).toEqual(fetcher.mock.calls[2][1].headers);
});
it('retries a temporary server failure without changing the requested range',async()=>{
 const fetcher=source(call=>call===1?new Response('',{status:503}):undefined);
 expect((await run(fetcher)).size).toBe(bytes.length);
 expect(fetcher.mock.calls[0][1].headers).toEqual(fetcher.mock.calls[1][1].headers);
});
it('accepts the gzip middleware weak ETag while verifying the decoded APK bytes',async()=>{
 const fetcher=source((_call,part,headers)=>new Response(part,{status:206,headers:{...headers,ETag:'W/'+headers.ETag}}));
 const blob=await run(fetcher);
 expect(Buffer.from(await blob.arrayBuffer()).equals(Buffer.from(bytes))).toBe(true);
 expect(fetcher.mock.calls[1][1].headers).toMatchObject({'If-Range':'"'+hash+'"'});
});
it('refuses mixed releases even when every chunk has the expected length',async()=>{
 const fetcher=source((call,part,headers)=>call===2?new Response(part,{status:206,headers:{...headers,ETag:'"'+('a'.repeat(64))+'"','X-APK-SHA256':'a'.repeat(64)}}):undefined);
 await expect(run(fetcher)).rejects.toThrow('verified');
});
it('refuses corruption before offering a file to install',async()=>{
 const fetcher=source((call,part,headers)=>{if(call===1){part[5]^=1;return new Response(part,{status:206,headers});}});
 await expect(run(fetcher)).rejects.toThrow('damaged');
});
it('fails closed when authentication expires and cancels without another request',async()=>{
 const fetcher=source(()=>new Response('',{status:401}));
 await expect(run(fetcher)).rejects.toThrow('Sign in again');
 expect(fetcher).toHaveBeenCalledTimes(1);
 const controller=new AbortController();controller.abort();
 const other=source();await expect(run(other,controller.signal)).rejects.toThrow();
 expect(other).not.toHaveBeenCalled();
});

