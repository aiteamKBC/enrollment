import { useEffect, useRef, useState } from 'react';
import SignaturePadLib from 'signature_pad';
import { LoaderCircle } from 'lucide-react';
import { btnPrimary, btnSecondary } from '../../components/ui';
import { useAuth } from "@/hooks/useAuth.helpers";
import { fetchSavedSignature, saveSignature } from '@/api/savedSignature';

/**
 * Signature capture, used wherever anyone signs.
 *
 * The name is not editable. It is whoever is actually signing: the signed-in
 * account by default, or the party named by `signatoryName`.
 *
 * When the person signing is the signed-in account, their saved signature is
 * offered first — one click signs with it — and a new one (drawn or uploaded)
 * is saved for next time. When someone signs a box for another person (staff
 * taking a learner's signature, say), neither happens: their own saved
 * signature is never offered for, or overwritten by, somebody else's mark.
 *
 * The committed value remains a PNG data URL, so existing backend validation,
 * PDF rendering, and historical signatures keep working.
 */

/** Largest uploaded image kept, in CSS pixels; and the server's size limit. */
const UPLOAD_MAX_W = 480;
const UPLOAD_MAX_H = 160;
const MAX_SIGNATURE_CHARS = 380_000;

const normaliseName = (value: string) => value.trim().replace(/\s+/g, ' ').toLowerCase();

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('That image could not be read.'));
    img.src = src;
  });
}

/** An uploaded PNG/JPEG, fitted and flattened onto white, as a PNG data URL. */
async function imageFileToSignature(file: File): Promise<string> {
  if (!/^image\/(png|jpeg)$/.test(file.type)) throw new Error('Please upload a PNG or JPEG image of your signature.');
  if (file.size > 5 * 1024 * 1024) throw new Error('That image is over 5 MB. Please use a smaller one.');
  const src = window.URL.createObjectURL(file);
  try {
    const img = await loadImage(src);
    let scale = Math.min(1, UPLOAD_MAX_W / img.width, UPLOAD_MAX_H / img.height);
    for (let attempt = 0; attempt < 4; attempt++) {
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(img.width * scale));
      canvas.height = Math.max(1, Math.round(img.height * scale));
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Signature upload is not available in this browser.');
      context.fillStyle = '#ffffff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(img, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL('image/png');
      if (dataUrl.length <= MAX_SIGNATURE_CHARS) return dataUrl;
      scale *= 0.7;
    }
    throw new Error('That image is too detailed to use as a signature. Please try a simpler one.');
  } finally {
    window.URL.revokeObjectURL(src);
  }
}

type Mode = 'saved' | 'draw' | 'upload';

export function SignaturePad({
  onCommit,
  onCancel,
  signatoryName,
  /** @deprecated Use `signatoryName`. Kept so older call sites keep compiling. */
  defaultName,
}: {
  /** Return the save's promise to keep "Signing..." up until it finishes. */
  onCommit: (dataUrl: string) => void | Promise<unknown>;
  onCancel: () => void;
  /** Who is signing. Falls back to the signed-in account when omitted. */
  signatoryName?: string;
  defaultName?: string;
}) {
  const { auth } = useAuth();
  const given = (signatoryName || defaultName || '').trim();
  const name = (given || auth.user?.fullName || '').trim();

  // Learners and employers only ever sign as themselves. Staff also sign boxes
  // for others, so for them the box must name the signed-in account.
  const account = auth.account;
  const accountKey = account ? `${account.subjectType}:${account.subjectId}` : '';
  const signsAsSelf = Boolean(
    account &&
      (account.role === 'learner' ||
        account.role === 'employer' ||
        !given ||
        normaliseName(given) === normaliseName(auth.user?.fullName || '')),
  );

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const padRef = useRef<SignaturePadLib | null>(null);
  const drawingRef = useRef<ReturnType<SignaturePadLib['toData']>>([]);
  const drawingWidthRef = useRef(0);
  const [mode, setMode] = useState<Mode>('draw');
  const [saved, setSaved] = useState('');
  const [uploaded, setUploaded] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [hasInk, setHasInk] = useState(false);
  const touchedRef = useRef(false);
  const signingRef = useRef(false);

  // Offer the saved signature once it has loaded — unless the person has
  // already started signing, which it must not interrupt.
  useEffect(() => {
    if (!signsAsSelf || !accountKey) return undefined;
    let cancelled = false;
    fetchSavedSignature(accountKey)
      .then((res) => {
        if (cancelled || !res.signature) return;
        setSaved(res.signature);
        if (!touchedRef.current) setMode('saved');
      })
      // No saved signature to offer: the person simply signs afresh.
      .catch(() => {});
    return () => { cancelled = true; };
  }, [signsAsSelf, accountKey]);

  useEffect(() => {
    if (mode !== 'draw') return undefined;
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    const width = canvas.getBoundingClientRect().width || 360;
    const height = 160;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const context = canvas.getContext('2d');
    if (!context) {
      setErr('Signature capture is not available in this browser.');
      return undefined;
    }
    context.scale(ratio, ratio);

    const pad = new SignaturePadLib(canvas, {
      backgroundColor: 'rgb(255,255,255)',
      penColor: 'rgb(15,23,42)',
    });
    if (drawingRef.current.length) {
      const scale = drawingWidthRef.current ? width / drawingWidthRef.current : 1;
      pad.fromData(drawingRef.current.map(group => ({
        ...group,
        points: group.points.map(point => ({ ...point, x: point.x * scale })),
      })));
    }
    drawingWidthRef.current = width;
    padRef.current = pad;
    setHasInk(!pad.isEmpty());

    const changed = () => {
      touchedRef.current = true;
      setHasInk(!pad.isEmpty());
      setErr(null);
    };
    pad.addEventListener('endStroke', changed);
    return () => {
      drawingRef.current = pad.toData();
      pad.removeEventListener('endStroke', changed);
      pad.off();
      padRef.current = null;
    };
  }, [mode]);

  const sign = async (dataUrl: string) => {
    if (!name) {
      setErr('No name is on record for the signatory, so this cannot be signed.');
      return;
    }
    // One signature per press: a second click while the first is still being
    // recorded would send the same sign-off twice.
    if (signingRef.current) return;
    signingRef.current = true;
    setSaving(true);
    setErr(null);
    try {
      // Awaited when the caller returns its request, so "Signing..." stays up
      // and the buttons stay disabled until the document is actually signed.
      await onCommit(dataUrl);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not sign. Please try again.');
      return;
    } finally {
      signingRef.current = false;
      setSaving(false);
    }
    // Kept for next time. Best-effort: the signature the person just gave has
    // already been handed over, so a failed save must not undo it.
    if (signsAsSelf && accountKey && dataUrl !== saved) {
      saveSignature(accountKey, dataUrl).then((res) => setSaved(res.signature)).catch(() => {});
    }
  };

  const commitDrawn = () => {
    if (!padRef.current || padRef.current.isEmpty()) {
      setErr('Please draw your signature before signing.');
      return;
    }
    sign(padRef.current.toDataURL('image/png'));
  };

  const clear = () => {
    padRef.current?.clear();
    drawingRef.current = [];
    setHasInk(false);
    setErr(null);
  };

  const pickUpload = async (file: File | undefined) => {
    if (!file) return;
    touchedRef.current = true;
    setErr(null);
    try {
      setUploaded(await imageFileToSignature(file));
    } catch (e) {
      setUploaded('');
      setErr(e instanceof Error ? e.message : 'That image could not be used.');
    }
  };

  const tab = (value: Mode, label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={mode === value}
      onClick={() => { touchedRef.current = true; setErr(null); setMode(value); }}
      className={`px-3 py-1.5 text-[12px] font-medium rounded-lg transition-smooth cursor-pointer ${mode === value ? 'bg-primary-50 text-primary-700' : 'text-foreground-500 hover:bg-background-100'}`}
    >
      {label}
    </button>
  );

  return (
    <div className="border border-foreground-200 rounded-xl p-3 max-w-md space-y-3">
      <div>
        <p className="text-[12px] text-foreground-700 mb-1">Signing as</p>
        <p className="text-[13px] font-medium text-foreground-900">
          {name || <span className="text-red-600">No name on record</span>}
        </p>
      </div>

      {mode === 'saved' ? (
        <div className="space-y-2">
          <p className="text-[12px] text-foreground-700">Your saved signature</p>
          <div className="rounded-lg border border-foreground-200 bg-white p-2 flex items-center justify-center h-28">
            <img src={saved} alt="Your saved signature" className="max-h-full max-w-full object-contain" />
          </div>
          <p className="text-[11px] text-foreground-400">
            Signing with it below has the same effect as signing by hand.
          </p>
        </div>
      ) : (
        <>
          <div role="tablist" aria-label="How to sign" className="flex items-center gap-1">
            {tab('draw', 'Draw')}
            {tab('upload', 'Upload')}
          </div>

          {mode === 'draw' ? (
            <div className="space-y-2">
              <canvas
                ref={canvasRef}
                aria-label="Draw your signature"
                className="h-40 w-full touch-none rounded-lg border border-foreground-200 bg-white shadow-sm"
              />
              <button type="button" className={btnSecondary} onClick={clear} disabled={saving || !hasInk}>
                Clear
              </button>
              <p className="text-[11px] text-foreground-400">
                Draw using your mouse, pen or finger. Confirming below has the same effect as signing by hand.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="rounded-lg border border-dashed border-foreground-200 bg-white p-2 flex items-center justify-center h-28">
                {uploaded ? (
                  <img src={uploaded} alt="Uploaded signature" className="max-h-full max-w-full object-contain" />
                ) : (
                  <span className="text-[12px] text-foreground-400">No image chosen</span>
                )}
              </div>
              <label className="relative inline-flex items-center gap-2 px-3 py-1.5 text-[12px] bg-background-100 text-foreground-600 rounded-lg border border-background-200 hover:bg-background-200 transition-smooth cursor-pointer">
                <i className="ri-upload-2-line" />
                {uploaded ? 'Choose a different image' : 'Choose an image'}
                <input
                  type="file"
                  accept="image/png,image/jpeg"
                  aria-label="Upload your signature"
                  className="sr-only"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = '';
                    void pickUpload(file);
                  }}
                />
              </label>
              <p className="text-[11px] text-foreground-400">
                A clear photo or scan of your signature (PNG or JPEG). Confirming below has the same effect as signing by hand.
              </p>
            </div>
          )}

          {signsAsSelf && (
            <p className="text-[11px] text-foreground-500">
              <i className="ri-save-line mr-1" />It will be saved as your signature for next time.
            </p>
          )}
        </>
      )}

      {err && <p className="text-[12px] text-red-600">{err}</p>}

      <div className="flex flex-wrap items-center gap-2">
        {mode === 'saved' ? (
          <>
            <button type="button" className={btnPrimary} onClick={() => sign(saved)} disabled={saving || !name}>
              {saving
                ? <LoaderCircle data-testid="signature-action-spinner" aria-hidden="true" className="h-4 w-4 animate-spin" />
                : <i className="ri-check-line" aria-hidden="true" />}
              {saving ? 'Signing...' : 'Sign with this signature'}
            </button>
            <button type="button" className={btnSecondary} onClick={() => { touchedRef.current = true; setMode('draw'); }} disabled={saving}>
              Use a different signature
            </button>
          </>
        ) : mode === 'draw' ? (
          <button type="button" className={btnPrimary} onClick={commitDrawn} disabled={saving || !name || !hasInk}>
            {saving
              ? <LoaderCircle data-testid="signature-action-spinner" aria-hidden="true" className="h-4 w-4 animate-spin" />
              : <i className="ri-check-line" aria-hidden="true" />}
            {saving ? 'Signing...' : 'Sign'}
          </button>
        ) : (
          <button type="button" className={btnPrimary} onClick={() => sign(uploaded)} disabled={saving || !name || !uploaded}>
            {saving
              ? <LoaderCircle data-testid="signature-action-spinner" aria-hidden="true" className="h-4 w-4 animate-spin" />
              : <i className="ri-check-line" aria-hidden="true" />}
            {saving ? 'Signing...' : 'Sign'}
          </button>
        )}
        <button type="button" className={btnSecondary} onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </div>
  );
}
