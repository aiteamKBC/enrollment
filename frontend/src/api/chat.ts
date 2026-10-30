export type ChatParticipantType = 'coach' | 'learner';

export interface ChatParticipant {
  id: string;
  type: ChatParticipantType;
  name: string;
  email: string;
  avatar: string | null;
}

export interface ChatSender {
  id: string;
  type: ChatParticipantType;
  name?: string;
  email?: string;
  avatar?: string | null;
}

export interface ChatMessage {
  id: number;
  conversation: number;
  sender: ChatSender;
  body: string;
  created_at: string;
  edited_at: string | null;
  is_deleted: boolean;
  is_mine: boolean;
  read_at: string | null;
}

export interface ChatConversation {
  id: number;
  participant: ChatParticipant;
  latest_message: Pick<ChatMessage, 'id' | 'body' | 'created_at' | 'edited_at' | 'is_deleted' | 'sender'> | null;
  updated_at: string;
  unread_count: number;
}

export interface PaginatedMessages {
  count: number;
  next: string | null;
  previous: string | null;
  results: ChatMessage[];
}

export interface ChatSocketMessage {
  id: number;
  conversation: number;
  sender: ChatSender;
  body: string;
  created_at: string;
  edited_at: string | null;
  is_deleted: boolean;
}

export class ChatApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ChatApiError';
    this.status = status;
  }
}

const productionChatBase = '/api/chat';
const CHAT_BASE = (import.meta.env.VITE_CHAT_API_BASE_URL || productionChatBase).replace(/\/$/, '');

function readCookie(name: string): string | null {
  const encodedName = `${encodeURIComponent(name)}=`;
  const cookie = document.cookie.split('; ').find(value => value.startsWith(encodedName));
  return cookie ? decodeURIComponent(cookie.slice(encodedName.length)) : null;
}

async function ensureChatCsrfCookie(): Promise<void> {
  const response = await fetch(`${CHAT_BASE}/session/`, { credentials: 'include' });
  if (!response.ok) throw new ChatApiError('Could not initialise the chat session.', response.status);
}

export async function bootstrapChatSession(
  email: string,
  options: { learnerSourceId?: string } = {},
): Promise<void> {
  await ensureChatCsrfCookie();
  const headers = new Headers({ 'Content-Type': 'application/json' });
  const csrfToken = readCookie('csrftoken');
  if (csrfToken) headers.set('X-CSRFToken', csrfToken);

  const response = await fetch(`${CHAT_BASE}/session/`, {
    method: 'POST',
    headers,
    credentials: 'include',
    body: JSON.stringify({
      email,
      learner_source_id: options.learnerSourceId,
    }),
  });
  if (!response.ok) {
    let detail = 'Could not initialise the chat session.';
    try {
      const payload = await response.json() as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // Keep the stable fallback when an upstream proxy returns non-JSON.
    }
    throw new ChatApiError(detail, response.status);
  }
}

export async function clearChatSession(): Promise<void> {
  try {
    await ensureChatCsrfCookie();
    const headers = new Headers();
    const csrfToken = readCookie('csrftoken');
    if (csrfToken) headers.set('X-CSRFToken', csrfToken);
    await fetch(`${CHAT_BASE}/session/logout/`, {
      method: 'POST',
      headers,
      credentials: 'include',
    });
  } catch {
    // Local frontend logout should still complete if the backend is offline.
  }
}

type ChatRequestInit = NonNullable<Parameters<typeof fetch>[1]>;

async function request<T>(path: string, init: ChatRequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (init.method && init.method !== 'GET') {
    const csrfToken = readCookie('csrftoken');
    if (csrfToken) headers.set('X-CSRFToken', csrfToken);
  }

  let response: Response;
  try {
    response = await fetch(`${CHAT_BASE}${path}`, {
      ...init,
      headers,
      credentials: 'include',
    });
  } catch {
    throw new ChatApiError('Could not reach the chat server.', 0);
  }

  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    const payload = data as { detail?: string; body?: string[]; scope?: string[] | string } | null;
    const scopeMessage = Array.isArray(payload?.scope) ? payload.scope[0] : payload?.scope;
    const message = response.status === 401 || response.status === 403
      ? 'Your web session is not authenticated for chat. Please sign in through Django.'
      : payload?.detail || payload?.body?.[0] || scopeMessage || `Chat request failed (${response.status}).`;
    throw new ChatApiError(message, response.status);
  }

  return data as T;
}

export function fetchChatConversations(): Promise<ChatConversation[]> {
  return request<ChatConversation[]>('/conversations/');
}

export function fetchChatMessages(
  conversationId: number,
  page = 1,
  pageSize = 100,
): Promise<PaginatedMessages> {
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  return request<PaginatedMessages>(`/conversations/${conversationId}/messages/?${query.toString()}`);
}

export async function fetchAllChatMessages(conversationId: number): Promise<PaginatedMessages> {
  const results: ChatMessage[] = [];
  let pageNumber = 1;
  let response: PaginatedMessages | null = null;

  do {
    response = await fetchChatMessages(conversationId, pageNumber, 100);
    results.push(...response.results);
    pageNumber += 1;
  } while (response.next && response.results.length > 0);

  return {
    count: response?.count ?? results.length,
    next: null,
    previous: null,
    results,
  };
}

export function fetchLearnerMessages(page = 1, pageSize = 100): Promise<PaginatedMessages> {
  return request<PaginatedMessages>(`/learner-messages/?page=${page}&page_size=${pageSize}`);
}

export async function fetchAllLearnerMessages(): Promise<ChatMessage[]> {
  const messages: ChatMessage[] = [];
  let page = 1;
  let response: PaginatedMessages;

  do {
    response = await fetchLearnerMessages(page, 100);
    messages.push(...response.results);
    page += 1;
  } while (response.next && response.results.length > 0);

  return messages;
}

export function createChatMessage(conversationId: number, body: string): Promise<ChatMessage> {
  return request<ChatMessage>(`/conversations/${conversationId}/messages/`, {
    method: 'POST',
    body: JSON.stringify({ body }),
  });
}

export function updateChatMessage(messageId: number, body: string): Promise<ChatMessage> {
  return request<ChatMessage>(`/messages/${messageId}/`, {
    method: 'PATCH',
    body: JSON.stringify({ body }),
  });
}

export function deleteChatMessage(messageId: number, scope: 'me' | 'everyone'): Promise<ChatMessage | { message: number; scope: 'me' }> {
  return request<ChatMessage | { message: number; scope: 'me' }>(`/messages/${messageId}/delete/`, {
    method: 'POST',
    body: JSON.stringify({ scope }),
  });
}

export function markChatMessageRead(messageId: number): Promise<{ message: number; read_at: string }> {
  return request<{ message: number; read_at: string }>(`/messages/${messageId}/read/`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export function chatSocketUrl(conversationId: number): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socketBase = import.meta.env.VITE_CHAT_WS_BASE_URL
    || (window.location.hostname === 'lms.kentbusinesscollege.net'
      ? 'wss://api.kentbusinesscollege.net/ws'
      : `${protocol}//${window.location.host}/ws`);
  return `${socketBase.replace(/\/$/, '')}/chat/${conversationId}/`;
}
