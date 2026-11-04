/**
 * Domain types for the "My General Foreman" Geotab Drive add-in.
 *
 * Geotab Drive hands the add-in the same `api` / `state` pair as a MyGeotab
 * page add-in, plus `api.mobile.*` when running inside the native app. We
 * only rely on the parts we use and narrow at the boundary.
 */

export interface GeotabApi {
  call: (
    method: string,
    params: unknown,
    success: (result: unknown) => void,
    failure: (err: unknown) => void
  ) => void;
  getSession?: (
    callback: (
      credentials: { database: string; userName: string; sessionId: string },
      server: string
    ) => void
  ) => void;
  /** Present only inside the native Geotab Drive app. */
  mobile?: {
    exists?: () => boolean;
    user?: { get?: (success: (u: unknown) => void, failure?: (e: unknown) => void) => void };
    notification?: {
      notify?: (message: string, title: string, id?: number | string) => void;
    };
  };
}

export interface GeotabPageState {
  /** Drive exposes this; false when the tablet has no connectivity. */
  online?: boolean;
  getState?: (cb: (state: unknown) => void) => void;
  setState?: (state: unknown) => void;
  translate?: (key: string) => string;
}

/** Everything the GPSFMS backend needs to verify who is asking. */
export interface GeotabSession {
  server: string;
  database: string;
  userName: string;
  sessionId: string;
}

/** A selectable General Foreman group, as served by the backend. */
export interface GfGroup {
  id: string;
  name: string;
  /** Parent group name (e.g. the division), when the backend scans several parents. */
  parent?: string;
}

/** Backend `/api/me` response. */
export interface MeResponse {
  userId: string;
  userName: string;
  displayName: string;
  isDriver: boolean;
  /** Currently assigned GF group(s) — normally 0 or 1. */
  current: GfGroup[];
}

/** Backend `/api/assign` response. */
export interface AssignResponse {
  ok: boolean;
  current: GfGroup[];
  message?: string;
}

export type Phase = "loading" | "ready" | "saving" | "saved" | "error";
