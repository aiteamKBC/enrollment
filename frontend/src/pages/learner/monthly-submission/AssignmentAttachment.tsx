import { lazy, Suspense, useId, useState } from 'react';
import { Download, Eye, Paperclip, X } from 'lucide-react';
import styles from './monthlySubmission.module.css';

const AttachmentPreview = lazy(() => import('../video-watch/page').then(module => ({ default: module.InlineAttachmentPreview })));

function attachmentName(url: string, fileName?: string | null): string {
  if (fileName?.trim()) return fileName.trim();
  try {
    const path = new URL(url, window.location.origin).pathname;
    return decodeURIComponent(path.split('/').filter(Boolean).pop() || 'Assignment file');
  } catch {
    return 'Assignment file';
  }
}

export function AssignmentAttachment({ url, fileName, title, defaultExpanded = false, pdfTools = true }: {
  url: string; fileName?: string | null; title: string; defaultExpanded?: boolean; pdfTools?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const previewId = useId();
  const name = attachmentName(url, fileName);

  return <section className={styles.attachment} aria-label="Assignment attachment">
    <div className={styles.attachmentHeader}>
      <div className={styles.attachmentName}><Paperclip size={19} aria-hidden="true" /><div>
        <p className={styles.eyebrow}>Assignment file</p><strong>{name}</strong>
      </div></div>
      <div className={styles.actions}>
        <button type="button" className={styles.secondary} aria-expanded={expanded} aria-controls={previewId} onClick={() => setExpanded(value => !value)}>
          {expanded ? <X size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}{expanded ? 'Hide preview' : 'View file'}
        </button>
        <a className={styles.secondary} href={url} download={name}><Download size={16} aria-hidden="true" />Download file</a>
      </div>
    </div>
    <div id={previewId} hidden={!expanded} className={styles.attachmentPreview}>
      {expanded && <Suspense fallback={<p role="status">Loading file preview…</p>}>
        <AttachmentPreview url={url} fileName={name} title={title} pdfTools={pdfTools} allowAnnotatedDownload />
      </Suspense>}
    </div>
  </section>;
}
