/**
 * Client for the platform API.
 *
 * The dashboard talks only to the Node API on :3000 — never to the AI service
 * or the database directly. That keeps one place where authentication and RBAC
 * are enforced.
 *
 * The token lives in localStorage. That is the pragmatic choice for a dashboard
 * behind a login, and it has a real cost worth stating plainly: any script that
 * runs on this origin can read it, so an XSS becomes a stolen session. A
 * production deployment should move to an httpOnly cookie set by a server route,
 * which JavaScript cannot read at all.
 */

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:3000';
const TOKEN_KEY = 'voiceops.token';
const USER_KEY = 'voiceops.user';

export type Role = 'USER' | 'CX_AGENT' | 'SUPERVISOR' | 'ADMIN';

export interface User {
  sub: string;
  email: string;
  name: string;
  role: Role;
  permissions: string[];
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export const tokens = {
  get: () => (typeof window === 'undefined' ? null : localStorage.getItem(TOKEN_KEY)),
  getUser: (): User | null => {
    if (typeof window === 'undefined') return null;
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  },
  set: (token: string, user: User) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clear: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = tokens.get();
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData)
        ? { 'content-type': 'application/json' }
        : {}),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });

  const body = await response.json().catch(() => ({}));

  if (!response.ok) {
    const error = (body as { error?: { code: string; message: string } }).error;
    // A 401 means the token is gone or expired. Clearing it here means the next
    // render sends the user to the login page instead of looping on failures.
    if (response.status === 401) tokens.clear();
    throw new ApiError(
      response.status,
      error?.code ?? `HTTP_${response.status}`,
      error?.message ?? 'Request failed',
    );
  }
  return body as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ token: string; user: User }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  calls: (params: Record<string, string | number | undefined> = {}) => {
    const query = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== '')
        .map(([k, v]) => [k, String(v)]),
    );
    return request<{ calls: Call[]; total: number }>(`/api/calls?${query}`);
  },

  call: (id: string) =>
    request<{ call: CallDetail; conversation: Turn[] | null; jobs: Job[] }>(`/api/calls/${id}`),

  escalate: (id: string, reason: string) =>
    request(`/api/calls/${id}/escalate`, { method: 'POST', body: JSON.stringify({ reason }) }),

  jobs: (params: Record<string, string | undefined> = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v) as [string, string][],
    );
    return request<{ jobs: Job[]; live: QueueStats }>(`/api/jobs?${query}`);
  },

  job: (id: string) => request<{ job: Job; attempts: Attempt[] }>(`/api/jobs/${id}`),

  deadLetters: () => request<{ jobs: LiveJob[] }>('/api/jobs/dead-letters'),

  retryJob: (id: string) => request(`/api/jobs/${id}/retry`, { method: 'POST' }),

  queueAnalytics: () => request<{ live: QueueStats; history: QueueHistory[] }>('/api/analytics/queue'),

  callAnalytics: (days = 7) => request<CallAnalytics>(`/api/analytics/calls?days=${days}`),

  failureAnalytics: (days = 7) => request<FailureAnalytics>(`/api/analytics/failures?days=${days}`),

  voiceTurn: (message: string, callId?: string) =>
    request<TurnResponse>('/api/voice/turn', {
      method: 'POST',
      body: JSON.stringify({ message, call_id: callId }),
    }),

  voiceAudio: (audio: Blob, callId?: string) => {
    const form = new FormData();
    form.append('audio', audio, 'turn.webm');
    if (callId) form.append('call_id', callId);
    return request<TurnResponse>('/api/voice/turn/audio', { method: 'POST', body: form });
  },
};

// ---------- Shapes returned by the API ----------

export interface Call {
  id: string;
  status: string;
  primary_intent: string | null;
  flow_id: string | null;
  escalation_status: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  channel: string;
  customer_name: string | null;
  customer_phone: string | null;
  turns: number;
}

export interface CallDetail extends Call {
  escalation_reason: string | null;
  summary: string | null;
  metadata: Record<string, unknown>;
  customer_email: string | null;
}

export interface Turn {
  turn_index: number;
  speaker: string;
  message: string;
  intent: string | null;
  confidence: number | null;
  tool_calls: ToolCall[];
  stt_ms: number | null;
  llm_ms: number | null;
  tts_ms: number | null;
  providers: Record<string, string>;
  created_at: string;
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  duration_ms: number;
  error_code: string | null;
  error_message: string | null;
}

export interface Job {
  id: string;
  call_id: string | null;
  job_type: string;
  priority: string;
  status: string;
  attempt_count: number;
  max_attempts: number;
  last_error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface LiveJob {
  id: string;
  job_type: string;
  priority: string;
  status: string;
  attempt: number;
  last_error: string | null;
  last_error_code: string | null;
}

export interface Attempt {
  attempt_number: number;
  status: string;
  worker_id: string | null;
  duration_ms: number | null;
  error_class: string | null;
  error_type: string | null;
  error_message: string | null;
  backoff_ms: number | null;
  finished_at: string | null;
}

export interface QueueStats {
  ready: Record<string, number>;
  ready_total: number;
  scheduled: number;
  processing: number;
  dead_letter: number;
  counters: Record<string, number>;
}

export interface QueueHistory {
  status: string;
  priority: string;
  jobs: number;
}

export interface CallAnalytics {
  window_days: number;
  totals: {
    total: number; successful: number; escalated: number;
    failed: number; abandoned: number; in_progress: number; avg_duration_ms: number | null;
  };
  by_intent: { intent: string; calls: number; escalated: number }[];
  latency: {
    avg_stt_ms: number | null; avg_llm_ms: number | null;
    avg_tts_ms: number | null; p95_llm_ms: number | null;
  };
}

export interface FailureAnalytics {
  window_days: number;
  by_service: {
    service: string; error_class: string; error_type: string;
    occurrences: number; last_seen: string;
  }[];
  retries: {
    total_attempts: number; retries: number;
    avg_backoff_ms: number | null; worst_attempt_count: number | null;
  };
  open: { open: number; retrying: number; dead_letter: number };
}

export interface TurnResponse {
  call_id: string;
  transcript?: string;
  reply: string;
  intent: string | null;
  escalated: boolean;
  tool_calls: ToolCall[];
  timings: Record<string, number>;
  providers: Record<string, string>;
  awaiting_confirmation: boolean;
  flow: { id: string; state: string };
  queued_jobs: { job_id: string; tool: string }[];
  audio?: { mime_type: string; base64: string; audio_ms: number } | null;
}
