/**
 * Calls to the GPSFMS GF-assignment backend.
 *
 * Why a backend at all: a Geotab Drive user authenticates with the driver's
 * own clearance, which (by design) cannot `Set` a User — not even its own
 * record — so the add-in cannot change group membership directly. Instead
 * it hands the backend the driver's *session* (database / userName /
 * sessionId / server). The backend re-validates that session against
 * MyGeotab (so nobody can assign someone else) and then performs the
 * `User.Set` with a GPSFMS service account.
 *
 * Nothing secret ever lives in this bundle.
 */

import { BACKEND_URL, REQUEST_TIMEOUT_MS } from "../config";
import type { AssignResponse, GeotabApi, GeotabSession, GfGroup, MeResponse } from "../types";

export class BackendError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** Resolve the driver's current session from the Drive API. */
export function fetchSession(api: GeotabApi): Promise<GeotabSession> {
  return new Promise((resolve, reject) => {
    if (!api.getSession) {
      reject(new Error("api.getSession is not available in this Drive version"));
      return;
    }
    try {
      api.getSession((creds, server) => {
        if (!creds?.sessionId || !creds.database || !creds.userName) {
          reject(new Error("Drive did not return a complete session"));
          return;
        }
        resolve({
          server: normaliseServer(server),
          database: creds.database,
          userName: creds.userName,
          sessionId: creds.sessionId,
        });
      });
    } catch (e) {
      reject(e);
    }
  });
}

/** `my123.geotab.com`, `https://my123.geotab.com/` → `my123.geotab.com`. */
export function normaliseServer(server: string | undefined): string {
  const s = (server || "my.geotab.com").trim();
  return s.replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
}

async function request<T>(path: string, body: unknown): Promise<T> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${BACKEND_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
      credentials: "omit",
    });
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      /* non-JSON body */
    }
    if (!res.ok) {
      let detail = "";
      if (data && typeof data === "object" && "detail" in data) {
        detail = String((data as { detail: unknown }).detail);
      }
      throw new BackendError(detail || text || `HTTP ${res.status}`, res.status);
    }
    return data as T;
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new BackendError("The GPSFMS server did not answer in time. Check your connection and try again.", 0);
    }
    if (e instanceof BackendError) throw e;
    throw new BackendError(
      e instanceof Error && /Failed to fetch|NetworkError|Load failed/i.test(e.message)
        ? "Could not reach the GPSFMS server. Check your connection and try again."
        : friendlyError(e),
      0
    );
  } finally {
    window.clearTimeout(timer);
  }
}

/** Who am I + what I'm currently assigned to. */
export function fetchMe(session: GeotabSession): Promise<MeResponse> {
  return request<MeResponse>("/api/me", session);
}

/** The selectable General Foreman groups for this database. */
export async function fetchGfGroups(session: GeotabSession): Promise<GfGroup[]> {
  const r = await request<{ groups: GfGroup[] }>("/api/gf-groups", session);
  return r.groups ?? [];
}

/** Assign the calling driver to a GF group (replacing any previous GF group). */
export function assignGf(session: GeotabSession, groupId: string): Promise<AssignResponse> {
  return request<AssignResponse>("/api/assign", { ...session, groupId });
}

export function friendlyError(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  if (err && typeof err === "object" && "message" in err) return String((err as { message: unknown }).message);
  try {
    return JSON.stringify(err);
  } catch {
    return String(err);
  }
}
