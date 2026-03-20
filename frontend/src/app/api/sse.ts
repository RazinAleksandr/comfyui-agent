/**
 * SSE (Server-Sent Events) connection singleton and React hook.
 *
 * Replaces per-job polling with a single persistent connection that
 * receives all job progress, state changes, and server events in real-time.
 */

type SSECallback = (data: unknown) => void;

const listeners: Map<string, Set<SSECallback>> = new Map();
let eventSource: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = 1000;

const SSE_URL = "/api/v1/events/stream";

function connect(): void {
  if (eventSource) return;

  const token = localStorage.getItem("auth_token");
  if (!token) return; // Don't connect without auth

  eventSource = new EventSource(`${SSE_URL}?token=${encodeURIComponent(token)}`);

  eventSource.onopen = () => {
    reconnectDelay = 1000; // Reset backoff on successful connect
    listeners.get("__open__")?.forEach((fn) => fn({}));
  };

  // Listen for typed events from the backend
  for (const eventType of ["job_progress", "job_state", "server_change", "qa_review"]) {
    eventSource.addEventListener(eventType, (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        listeners.get(eventType)?.forEach((fn) => fn(data));
      } catch {
        // Ignore parse errors
      }
    });
  }

  eventSource.onerror = () => {
    listeners.get("__error__")?.forEach((fn) => fn({}));
    // EventSource auto-reconnects, but we add exponential backoff
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (listeners.size > 0) connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  };
}

function ensureConnected(): void {
  if (!eventSource) connect();
}

/**
 * Subscribe to an SSE event type. Returns an unsubscribe function.
 *
 * Event types:
 * - "job_progress" — real-time progress updates for a job
 * - "job_state" — status transitions (pending → running → completed/failed)
 * - "server_change" — server allocation / shutdown events
 * - "__open__" / "__error__" — connection lifecycle
 */
export function subscribe(event: string, callback: SSECallback): () => void {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event)!.add(callback);
  ensureConnected();
  return () => {
    listeners.get(event)?.delete(callback);
    // Disconnect if no more listeners
    if (Array.from(listeners.values()).every((s) => s.size === 0)) {
      eventSource?.close();
      eventSource = null;
    }
  };
}

/**
 * Disconnect the SSE connection entirely.
 */
export function disconnect(): void {
  eventSource?.close();
  eventSource = null;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}
