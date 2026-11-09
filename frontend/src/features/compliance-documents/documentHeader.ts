import assets from './brandingAssets.json';

/** Visible artwork bounds account for the supplied DfE PNG's transparent padding.
 * One A4 layout for every page; these are not first/continuation alternatives. */
const GEOMETRY = {
  top:16, kbcHeight:18, dfeHeight:18, left:10, right:10,
  clearance:8,
} as const;
export const PDF_HEADER = {...GEOMETRY,contentTop:GEOMETRY.top+Math.max(GEOMETRY.kbcHeight,GEOMETRY.dfeHeight)+GEOMETRY.clearance} as const;

export function headerLogoGeometry(name:'kbcDeepPurple'|'dfe') {
  const asset=assets[name], bounds=asset.bounds;
  const visibleHeight=name==='dfe'?PDF_HEADER.dfeHeight:PDF_HEADER.kbcHeight;
  const scale=visibleHeight/(bounds.bottom-bounds.top);
  const width=asset.width*scale,height=asset.height*scale;
  const visibleWidth=(bounds.right-bounds.left)*scale;
  const visibleLeft=name==='dfe'?210-PDF_HEADER.right-visibleWidth:PDF_HEADER.left;
  return {x:visibleLeft-bounds.left*scale,y:PDF_HEADER.top-bounds.top*scale,width,height,
    visible:{x:visibleLeft,y:PDF_HEADER.top,width:visibleWidth,height:visibleHeight}};
}
