import { useEffect, useRef, useState } from 'react';
import SignaturePad from 'signature_pad';
import { inputClass } from '@/pages/users/components/ui';
import { Modal } from '@/pages/users/components/Modal';
import { AppIcon } from '@/components/feature/AppIcon';
import type { SignatureCaptureMethod } from './api';
import styles from './design.module.css';

const btnPrimary = styles.primaryButton;
const btnSecondary = styles.secondaryButton;

function readSignatureImage(file: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('Could not read this signature image. Please try again.'));
    reader.readAsDataURL(file);
  });
}

export function SignatureCapture({ name, busy, onSave, onDraftStart, onDraftReset, dialogRole, hasSavedSignature, importSignature, saveError, confirmationText }: {
  name: string; busy: boolean; onSave: (blob: Blob, capture: SignatureCaptureMethod) => void;
  onDraftStart?: () => void; onDraftReset?: () => void;
  dialogRole?: 'learner' | 'coach'; hasSavedSignature?: boolean; saveError?: string;
  importSignature?: { url: string; monthLabel: string };
  confirmationText?: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const pad = useRef<SignaturePad | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const drawing = useRef<ReturnType<SignaturePad['toData']>>([]);
  const drawingWidth = useRef(0);
  const [open, setOpen] = useState(!dialogRole);
  const [mode, setMode] = useState<'draw' | 'upload' | 'import'>('draw');
  const [preview, setPreview] = useState('');
  const [blob, setBlob] = useState<Blob | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [hasInk, setHasInk] = useState(false);
  const [captureMethod, setCaptureMethod] = useState<SignatureCaptureMethod>('draw');
  const [loadingImage, setLoadingImage] = useState<'upload' | 'import' | null>(null);
  const [preparing, setPreparing] = useState(false);
  const savingDraft = useRef(false);
  const locked = busy || loadingImage !== null || preparing;
  const hasSignature = captureMethod === 'draw' ? hasInk : Boolean(blob);

  useEffect(() => {
    if (mode !== 'draw' || !open || !canvas.current) return;
    const element = canvas.current;
    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    const width = element.getBoundingClientRect().width || 300;
    element.width = width * ratio;
    element.height = 180 * ratio;
    element.getContext('2d')?.scale(ratio, ratio);
    const instance = new SignaturePad(element, { backgroundColor: 'rgb(255,255,255)' });
    // The capture component owns the draft, even while its dialog is closed.
    // Restore strokes at the new width when reopening on a smaller screen.
    if (drawing.current.length) {
      const scale = drawingWidth.current ? width / drawingWidth.current : 1;
      instance.fromData(drawing.current.map(group => ({ ...group, points: group.points.map(point => ({ ...point, x: point.x * scale })) })));
    }
    drawingWidth.current = width;
    pad.current = instance;
    const changed = () => { setHasInk(!instance.isEmpty()); setPreview(''); setBlob(null); setConfirmed(false); setCaptureMethod('draw'); setError(''); };
    instance.addEventListener('endStroke', changed);
    return () => { drawing.current = instance.toData(); instance.removeEventListener('endStroke', changed); instance.off(); pad.current = null; };
  }, [mode, open]);

  const reset = () => { setPreview(''); setBlob(null); setConfirmed(false); setError(''); setHasInk(false); setCaptureMethod('draw'); drawing.current = []; pad.current?.clear(); onDraftReset?.(); };
  const save = async () => {
    if (locked || savingDraft.current || !confirmed || !hasSignature) return;
    savingDraft.current = true;
    setPreparing(true);
    setError('');
    try {
      let signature = blob;
      if (captureMethod === 'draw') {
        const element = canvas.current;
        if (!element || !pad.current || pad.current.isEmpty()) return;
        signature = await new Promise<Blob | null>(resolve => element.toBlob(resolve, 'image/png'));
      }
      if (!signature) throw new Error('Could not save this signature image. Please try again.');
      onSave(signature, captureMethod);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save this signature. Please try again.');
    } finally {
      savingDraft.current = false;
      setPreparing(false);
    }
  };
  const upload = async (file?: File) => {
    if (locked) return;
    reset();
    if (!file) return;
    onDraftStart?.();
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 2 * 1024 * 1024) {
      setError('Choose a PNG or JPEG image up to 2 MB.'); return;
    }
    setLoadingImage('upload');
    try {
      setPreview(await readSignatureImage(file));
      setBlob(file);
      setCaptureMethod('upload');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not read this image. Please try again.');
    } finally { setLoadingImage(null); }
  };
  const importSavedSignature = async () => {
    if (!importSignature || locked) return;
    reset();
    setMode('import');
    setOpen(true);
    onDraftStart?.();
    setLoadingImage('import');
    try {
      const response = await fetch(importSignature.url, { credentials: 'same-origin' });
      if (!response.ok) throw new Error('Could not import your previous signature. Please try again.');
      const savedBlob = await response.blob();
      if (!savedBlob.type.startsWith('image/')) throw new Error('Your previous signature image is unavailable.');
      setPreview(await readSignatureImage(savedBlob));
      setBlob(savedBlob);
      setCaptureMethod('import');
      setConfirmed(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not import your previous signature. Please try again.');
      onDraftReset?.();
    } finally {
      setLoadingImage(null);
    }
  };

  const importOption = importSignature && !hasSavedSignature && <div className="mt-3 rounded-xl border border-primary-200 bg-primary-50 p-3">
      <p className="text-sm font-semibold text-primary-900">Use your signature from {importSignature.monthLabel}?</p>
      <p className="mt-1 text-xs text-primary-700">Import your previous signature instead of drawing again, then confirm it for this month.</p>
      <button type="button" className={`${btnSecondary} mt-3`} disabled={locked} onClick={() => void importSavedSignature()}>
        <AppIcon className={loadingImage === 'import' ? 'ri-loader-4-line animate-spin' : 'ri-file-download-line'} />
        {loadingImage === 'import' ? 'Importing…' : 'Import previous signature'}
      </button>
    </div>;
  const controls = <fieldset disabled={locked} className={`${styles.capture} space-y-3`}>
    <legend className="mb-3 text-sm font-semibold text-foreground-900">Your signature</legend>
    <p className="text-sm text-foreground-600">Signing as <strong>{name}</strong></p>
    <div className={styles.captureModes}>
      <button type="button" aria-pressed={mode === 'draw'} className={mode === 'draw' ? btnPrimary : btnSecondary} onClick={() => { reset(); setMode('draw'); }}>Draw signature</button>
      <button type="button" aria-pressed={mode === 'upload'} className={mode === 'upload' ? btnPrimary : btnSecondary} onClick={() => { reset(); setMode('upload'); }}>Upload signature image</button>
    </div>
    {mode === 'draw' ? <div className="space-y-3">
      <p className="text-sm text-foreground-500">Draw using your mouse, pen or finger.</p>
      <canvas ref={canvas} onPointerDown={onDraftStart} aria-label="Draw your signature" className={`h-[180px] w-full touch-none rounded-xl border border-foreground-200 bg-white ${locked ? 'pointer-events-none' : ''}`} />
      <button type="button" className={btnSecondary} onClick={reset}>Clear</button>
    </div> : mode === 'upload' ? <label className="block space-y-2 text-sm">PNG or JPEG, up to 2 MB
      <input aria-label="Upload signature image" type="file" accept="image/png,image/jpeg" className={inputClass} onChange={e => void upload(e.target.files?.[0])} />
    </label> : null}
    {importOption}
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {saveError && dialogRole && <p role="alert" className="text-sm text-red-600">{saveError}</p>}
    {preview && <img src={preview} alt="Your signature preview" className="max-h-36 max-w-full rounded-lg border border-foreground-100 bg-white" />}
    <div className="space-y-3 border-t border-foreground-100 pt-4">
      <label className="flex items-start gap-2 text-sm text-foreground-700"><input type="checkbox" checked={confirmed} disabled={!hasSignature} onChange={e => setConfirmed(e.target.checked)} className="mt-1 accent-primary-500" />
        {confirmationText || "I have reviewed this month's record and confirm this is my signature."}</label>
      <button type="button" className={btnPrimary} disabled={!confirmed || !hasSignature || locked} onClick={() => void save()}>{busy || preparing ? 'Saving…' : 'Confirm and save signature'}</button>
    </div>
  </fieldset>;
  if (!dialogRole) return controls;
  return <>
    <button ref={trigger} type="button" className={btnSecondary} disabled={locked} onClick={() => setOpen(true)}>
      <AppIcon className="ri-edit-line" />{hasSavedSignature ? 'Update your signature' : `Sign as ${dialogRole}`}
    </button>
    {!open && importOption}
    {open && <Modal size="max-w-xl" className={`${styles.scope} ${styles.dialog}`} returnFocusRef={trigger}
      title={dialogRole === 'coach' ? 'Coach signature' : 'Learner signature'} onClose={() => { if (!locked) setOpen(false); }}
      footer={<button type="button" className={btnSecondary} disabled={locked} onClick={() => setOpen(false)}>Keep draft and close</button>}>
      <p className="mb-4 text-[13px] leading-relaxed text-foreground-500">{importSignature && !hasSavedSignature
        ? 'Import your previous signature, or draw or upload a new one. Confirm and save when ready.'
        : 'Draw or upload your signature for this month’s record, then confirm and save.'}</p>
      {controls}
    </Modal>}
  </>;
}
