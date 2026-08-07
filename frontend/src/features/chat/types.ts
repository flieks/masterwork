/**
 * Everything the shared chat components need to work either as the global chat
 * or as a project-scoped chat. The global chat and project chat differ only in
 * how sessions are listed/created and how their URLs are built — this abstracts
 * that so the chat UI is never forked.
 */
export interface ChatScope {
  /** Passed to listChatSessions: 'none' (global) or a project uuid. */
  listScope: string;
  /** Sent as project_id when creating a session; null for the global chat. */
  projectId: string | null;
  /** Currently-open session id, if any. */
  activeSessionId?: string;
  /** Navigation target for opening a session. */
  sessionHref: (sessionId: string) => string;
  /** Where to go when no session is open / after deleting the active one. */
  emptyHref: string;
}

/** listChatSessions value for the unscoped global chat (project_id IS NULL). */
export const GLOBAL_SCOPE = 'none';
