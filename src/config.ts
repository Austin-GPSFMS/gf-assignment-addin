/**
 * Add-in configuration. Edit before `npm run build`.
 *
 * BACKEND_URL — the GPSFMS GF-assignment API (see backend/). Must be HTTPS:
 *   Geotab Drive is served over HTTPS and the browser blocks mixed content.
 *   No trailing slash.
 */
export const BACKEND_URL = "https://gf.gpsfms.com";

/** How long to wait for the backend before telling the driver to retry. */
export const REQUEST_TIMEOUT_MS = 20_000;

/** Drive add-in registration name — must match config.json's menu entry. */
export const ADDIN_NAME = "gfAssignment";
