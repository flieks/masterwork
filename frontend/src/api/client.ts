import axios, { AxiosError, type AxiosInstance } from 'axios';
import {
  AssetsApi,
  ChatApi,
  InstructionsApi,
  ProjectsApi,
  ProposalsApi,
  SimulationsApi,
  Configuration,
} from './generated';

const baseURL = import.meta.env.VITE_API_URL ?? 'http://localhost:8008';

// Generous default. The generated per-operation paths already bake in `/api/v1`,
// so the axios instance's baseURL must be the ORIGIN only (basePath = '').
const DEFAULT_TIMEOUT_MS = 30_000;

// A `claude -p` round trip takes 30–120s (backend caps at 300s). Disable the
// client timeout for these long calls so a valid in-flight request is never
// aborted; pass via the generated method's `options` arg.
export const CHAT_TIMEOUT_MS = 0;
// Diagram generation is the same one-shot claude -p round trip — no timeout.
export const GENERATE_TIMEOUT_MS = 0;

/** Shared axios instance. baseURL is the origin; `/api/v1` is baked into paths. */
export const http: AxiosInstance = axios.create({
  baseURL,
  timeout: DEFAULT_TIMEOUT_MS,
});

const configuration = new Configuration({ basePath: '' });

/** Thin, domain-shaped facade over the generated typescript-axios client. */
export const api = {
  assets: new AssetsApi(configuration, '', http),
  chat: new ChatApi(configuration, '', http),
  instructions: new InstructionsApi(configuration, '', http),
  projects: new ProjectsApi(configuration, '', http),
  proposals: new ProposalsApi(configuration, '', http),
  simulations: new SimulationsApi(configuration, '', http),
};

/** True for a 404 from the backend — used to treat "not generated yet" as empty. */
export function isNotFoundError(err: unknown): boolean {
  return err instanceof AxiosError && err.response?.status === 404;
}

/** Pull a readable message out of a FastAPI error (`{ detail: ... }`). */
export function apiErrorMessage(err: unknown, fallback = 'Something went wrong'): string {
  if (err instanceof AxiosError) {
    if (err.code === 'ECONNABORTED') return 'The request timed out.';
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (first && typeof first.msg === 'string') return first.msg;
    }
    if (!err.response) return 'Cannot reach the backend. Is it running on ' + baseURL + '?';
    return err.message || fallback;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}
