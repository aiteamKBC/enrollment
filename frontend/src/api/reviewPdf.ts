/** Save only a PDF returned by the authenticated, signature-gated endpoint. */
export async function saveReviewPdfResponse(response: Response): Promise<void> {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || data.error || 'The signed PDF could not be downloaded.');
  }
  if (!response.headers.get('Content-Type')?.toLowerCase().startsWith('application/pdf')) {
    throw new Error('The server did not return a PDF. Please try again.');
  }
  const blob = await response.blob();
  const filename = response.headers.get('Content-Disposition')?.match(/filename="([A-Za-z0-9_.-]+)"/)?.[1]
    || 'Monthly-Coaching-Meeting.pdf';
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Let the browser begin reading the object URL before releasing it.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
