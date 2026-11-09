import { useEffect, useRef, useState } from 'react';
import SignaturePad from 'signature_pad';
import { CheckCircle2, LoaderCircle, Trash2, Upload } from 'lucide-react';
import { fetchSavedSignature, saveSignature } from '@/api/savedSignature';
import styles from './accountSettings.module.css';

const MAX_SIGNATURE_CHARS = 380_000;
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

async function imageFileToPng(file: File): Promise<string> {
  if (!/^image\/(png|jpeg)$/.test(file.type)) throw new Error('Choose a PNG or JPEG image.');
  if (file.size > MAX_UPLOAD_BYTES) throw new Error('Choose an image no larger than 5 MB.');
  const src = URL.createObjectURL(file);
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const element = new Image();
      element.onload = () => resolve(element);
      element.onerror = () => reject(new Error('That image could not be read.'));
      element.src = src;
    });
    let scale = Math.min(1, 480 / image.width, 160 / image.height);
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(image.width * scale));
      canvas.height = Math.max(1, Math.round(image.height * scale));
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Image upload is not available in this browser.');
      context.fillStyle = '#ffffff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const value = canvas.toDataURL('image/png');
      if (value.length <= MAX_SIGNATURE_CHARS) return value;
      scale *= 0.7;
    }
    throw new Error('That image is too detailed. Try a simpler signature image.');
  } finally {
    URL.revokeObjectURL(src);
  }
}

export function SignatureSettings({ accountKey }: { accountKey: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const padRef = useRef<SignaturePad | null>(null);
  const [saved, setSaved] = useState('');
  const [candidate, setCandidate] = useState('');
  const [mode, setMode] = useState<'draw' | 'upload'>('draw');
  const [hasInk, setHasInk] = useState(false);
  const [savedAt, setSavedAt] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    fetchSavedSignature(accountKey)
      .then((value) => {
        if (cancelled) return;
        setSaved(value.signature);
        setSavedAt(value.savedAt);
      })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load your saved signature.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [accountKey]);

  useEffect(() => {
    if (mode !== 'draw') return undefined;
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    const width = canvas.getBoundingClientRect().width || 600;
    canvas.width = width * ratio;
    canvas.height = 180 * ratio;
    const context = canvas.getContext('2d');
    if (!context) return undefined;
    context.scale(ratio, ratio);
    const pad = new SignaturePad(canvas, { backgroundColor: '#ffffff', penColor: '#17131d' });
    const changed = () => {
      setHasInk(!pad.isEmpty());
      setCandidate('');
      setMessage('');
      setError('');
    };
    pad.addEventListener('endStroke', changed);
    padRef.current = pad;
    return () => {
      pad.removeEventListener('endStroke', changed);
      pad.off();
      padRef.current = null;
    };
  }, [mode]);

  const clearCanvas = () => {
    padRef.current?.clear();
    setHasInk(false);
    setCandidate('');
    setError('');
  };

  const chooseFile = async (file?: File) => {
    if (!file) return;
    setError('');
    setMessage('');
    try {
      setCandidate(await imageFileToPng(file));
    } catch (reason) {
      setCandidate('');
      setError(reason instanceof Error ? reason.message : 'That image could not be used.');
    }
  };

  const persist = async () => {
    const value = mode === 'draw' ? padRef.current?.toDataURL('image/png') || '' : candidate;
    if ((mode === 'draw' && !hasInk) || !value) {
      setError(mode === 'draw' ? 'Draw your signature before saving.' : 'Choose a signature image before saving.');
      return;
    }
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const result = await saveSignature(accountKey, value);
      setSaved(result.signature);
      setSavedAt(result.savedAt);
      setMessage('Your signature sample has been saved.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Your signature could not be saved.');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    try {
      await saveSignature(accountKey, '');
      setSaved('');
      setSavedAt('');
      clearCanvas();
      setMessage('Your saved signature sample has been cleared.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Your signature could not be cleared.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={styles.signatureLayout}>
      <section className={styles.settingsRow} aria-labelledby="signature-current-title">
        <div className={styles.sectionIntro}>
          <h2 id="signature-current-title">Saved sample</h2>
          <p>This reusable sample is offered when you choose to sign. Saving it does not sign or agree to anything.</p>
        </div>
        <div className={styles.panel}>
          {loading ? <p className={styles.muted}>Loading your signature sample…</p> : saved ? (
            <>
              <div className={styles.savedSignature}><img src={saved} alt="Your saved signature sample" /></div>
              <div className={styles.panelFooter}>
                <p className={styles.muted}>{savedAt ? `Saved ${new Date(savedAt).toLocaleDateString('en-GB')}` : 'Saved signature'}</p>
                <button type="button" className={styles.dangerButton} onClick={() => void remove()} disabled={saving}>
                  <Trash2 size={16} aria-hidden="true" /> Clear signature
                </button>
              </div>
            </>
          ) : <p className={styles.emptyState}>You do not have a saved signature sample yet.</p>}
        </div>
      </section>

      <section className={styles.settingsRow} aria-labelledby="signature-new-title">
        <div className={styles.sectionIntro}>
          <h2 id="signature-new-title">Create a signature sample</h2>
          <p>Draw with a mouse, pen or finger, or upload a clear PNG or JPEG. Previously signed documents keep their original signature and date.</p>
        </div>
        <div className={styles.panel}>
          <div className={styles.tabs} role="tablist" aria-label="Signature method">
            <button type="button" role="tab" aria-selected={mode === 'draw'} className={mode === 'draw' ? styles.activeTab : ''} onClick={() => { setMode('draw'); setError(''); }}>Draw</button>
            <button type="button" role="tab" aria-selected={mode === 'upload'} className={mode === 'upload' ? styles.activeTab : ''} onClick={() => { setMode('upload'); setError(''); }}>Upload image</button>
          </div>
          {mode === 'draw' ? (
            <>
              <canvas ref={canvasRef} className={styles.signatureCanvas} aria-label="Draw your signature sample" />
              <button type="button" className={styles.secondaryButton} onClick={clearCanvas} disabled={!hasInk || saving}>Clear drawing</button>
            </>
          ) : (
            <div className={styles.uploadArea}>
              {candidate ? <img src={candidate} alt="Selected signature preview" /> : <p>Choose a PNG or JPEG up to 5 MB.</p>}
              <label className={styles.secondaryButton}>
                <Upload size={16} aria-hidden="true" /> {candidate ? 'Choose another image' : 'Choose image'}
                <input type="file" accept="image/png,image/jpeg" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; void chooseFile(file); }} />
              </label>
            </div>
          )}
          {error && <p className={styles.error} role="alert">{error}</p>}
          {message && <p className={styles.success} role="status"><CheckCircle2 size={17} aria-hidden="true" />{message}</p>}
          <div className={styles.formActions}>
            <button type="button" className={styles.primaryButton} onClick={() => void persist()} disabled={saving || (mode === 'draw' ? !hasInk : !candidate)}>
              {saving && <LoaderCircle size={16} className={styles.spinner} aria-hidden="true" />}
              {saving ? 'Saving…' : 'Save signature'}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
