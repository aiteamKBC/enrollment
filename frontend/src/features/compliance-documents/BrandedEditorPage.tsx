import assets from './brandingAssets.json';
import { BRAND_WAVES, BRANDED_PURPLE } from './brandedPurpleTheme';
import { headerLogoGeometry } from './documentHeader';

/** Page furniture stays outside contentEditable and never enters saved blocks. */
export function BrandedEditorPage({ page, total, historical = false }: { page: number; total: number; historical?: boolean }) {
  const rgb = (colour: readonly number[]) => `rgb(${colour.join(',')})`;
  return <div className="document-branded-furniture" data-testid="branded-page-furniture">
    <svg className="document-branded-top" viewBox="0 0 210 13" aria-hidden="true">{BRAND_WAVES.slice(0, 4).map((wave, i) => <path key={i} d={wave.path} fill={rgb(wave.colour)} />)}</svg>
    {(['kbcDeepPurple', 'dfe'] as const).map(name => {
      const current = headerLogoGeometry(name);
      // Existing v3 draft presentations retain their saved first/later layout.
      const source = historical && name === 'kbcDeepPurple' ? 'kbc' : name;
      const first = page === 1, width = name === 'dfe' ? first ? 37 : 24 : first ? 47 : 30;
      const geometry = historical ? { x: name === 'dfe' ? first ? 164 : 176 : 10, y: name === 'dfe' ? first ? -5 : -4 : first ? 2 : 0, width, height: width * assets[source].height / assets[source].width } : current;
      return <img key={name} draggable={false} alt={name === 'dfe' ? 'Department for Education' : 'Kent Business College'} src={assets[source].data}
        style={{ left: `${geometry.x}mm`, top: `${geometry.y}mm`, width: `${geometry.width}mm`, height: `${geometry.height}mm` }} />;
    })}
    <svg className="document-branded-bottom" viewBox="0 273 210 24" aria-hidden="true">{BRAND_WAVES.slice(4).map((wave, i) => <path key={i} d={wave.path} fill={rgb(wave.colour)} />)}</svg>
    <span className="document-branded-footer-line" style={{ background: rgb(BRANDED_PURPLE.accent) }} />
    <span className="document-branded-page-number" aria-label={`Editor page ${page} of ${total}`} style={{ background: rgb(BRANDED_PURPLE.secondary) }}>Page {page} of {total}</span>
  </div>;
}
