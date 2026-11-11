// User-requested selective pixel replacement: never redraw/filter the artwork.
import { decode, encode } from '../frontend/node_modules/fast-png/lib/index.js';

export function imageMetadata(bytes) {
  const image = decode(bytes);
  if (image.depth !== 8 || image.channels !== 4) throw Error('Document logos must be 8-bit RGBA PNGs.');
  let left=image.width, top=image.height, right=0, bottom=0;
  for(let y=0;y<image.height;y++)for(let x=0;x<image.width;x++)if(image.data[(y*image.width+x)*4+3]) {
    left=Math.min(left,x);top=Math.min(top,y);right=Math.max(right,x+1);bottom=Math.max(bottom,y+1);
  }
  return {width:image.width,height:image.height,bounds:{left,top,right,bottom}};
}

export function deepPurpleVariant(bytes, target) {
  const image=decode(bytes);
  if(image.depth!==8||image.channels!==4)throw Error('KBC logo must be an 8-bit RGBA PNG.');
  const pixels=new Uint8Array(image.data);
  // Dominant source purple measured from the supplied PNG. White-matted edge
  // pixels receive only the corresponding purple component delta; alpha stays exact.
  const source=[94,27,229];let changed=0;
  for(let i=0;i<pixels.length;i+=4) {
    const [r,g,b,a]=pixels.subarray(i,i+4);
    if(!a||b<r+5||r<g+5||b<g+10)continue;
    const coverage=Math.min(1,(b-g)/(source[2]-source[1]));
    for(let channel=0;channel<3;channel++) {
      pixels[i+channel]=coverage>.85&&g<70 ? target[channel] : Math.round(Math.max(0,Math.min(255,pixels[i+channel]+(target[channel]-source[channel])*coverage)));
    }
    changed++;
  }
  if(!changed)throw Error('No purple artwork was identified in the supplied KBC logo.');
  return {bytes:Buffer.from(encode({width:image.width,height:image.height,channels:4,depth:8,data:pixels})),changed};
}
