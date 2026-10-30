// ============================================================================
// "I have no access yet — please grant me some."
//
// Sent server-side rather than as a mailto: link, so it does not depend on the
// person having a mail client configured. The backend mails an administrator via
// the same Graph sender the invitations use.
// ============================================================================

export interface AccessRequestResult {
  ok: boolean;
  /** True when an identical request was already sent inside the throttle window. */
  alreadySent?: boolean;
  /** The administrator address, so the page can show it either way. */
  sentTo: string;
  message?: string;
  error?: string;
}

export async function requestAccess(): Promise<AccessRequestResult> {
  let res: Response;
  try {
    res = await fetch('/login_api/request-access/', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
    });
  } catch {
    throw new Error('Could not reach the server. Please check your connection and try again.');
  }
  const text = await res.text();
  const data = text ? (JSON.parse(text) as AccessRequestResult) : null;
  if (!data) throw new Error('The server returned an empty response.');
  // A failed send still carries `sentTo`, which the page needs in order to tell
  // the person who to email directly — so this is returned, not thrown.
  return data;
}
