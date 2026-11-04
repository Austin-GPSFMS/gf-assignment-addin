/**
 * Entry point.
 *
 * Geotab Drive calls the function registered at
 *   window.geotab.addin.gfAssignment
 * which returns { initialize, focus, blur, startup }.
 *
 * Same shape as recall-harness-swap-addin/src/main.tsx (page add-in
 * lifecycle + standalone preview fallback + focus nonce). `startup` only
 * runs when config.json sets "onStartup": true — it nudges a driver who has
 * no General Foreman yet via a Drive notification, when the native app
 * exposes one.
 */

import { StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import App from "./App";
import { ADDIN_NAME } from "./config";
import { fetchMe, fetchSession } from "./api/backend";
import type { GeotabApi, GeotabPageState } from "./types";
import "./styles.css";

type AddinLifecycle = {
  initialize: (api: GeotabApi, state: GeotabPageState, callback: () => void) => void;
  focus: (api: GeotabApi, state: GeotabPageState) => void;
  blur: () => void;
  startup?: (api: GeotabApi, state: GeotabPageState, callback: () => void) => void;
};

declare global {
  interface Window {
    geotab?: { addin?: Record<string, () => AddinLifecycle> };
  }
}

let root: Root | null = null;
let currentApi: GeotabApi | null = null;
let currentState: GeotabPageState | null = null;
let focusNonce = 0;

function mount() {
  const container = document.getElementById("root");
  if (!container) {
    console.error("[GF] #root element not found");
    return;
  }
  if (!root) root = createRoot(container);
  root.render(
    <StrictMode>
      <App api={currentApi} pageState={currentState} focusNonce={focusNonce} />
    </StrictMode>
  );
}

function lifecycle(): AddinLifecycle {
  return {
    initialize(api, state, callback) {
      currentApi = api;
      currentState = state;
      try {
        mount();
      } catch (e) {
        console.error("[GF] initialize failed:", e);
      }
      callback();
    },
    focus(api, state) {
      currentApi = api;
      currentState = state;
      focusNonce += 1;
      mount();
    },
    blur() {
      // No-op.
    },
    startup(api, _state, callback) {
      // Fire-and-forget: never hold up Drive's login flow.
      callback();
      void (async () => {
        try {
          const s = await fetchSession(api);
          const me = await fetchMe(s);
          if (me.current.length === 0 && api.mobile?.notification?.notify) {
            api.mobile.notification.notify(
              "Open “My General Foreman” and pick who you report to.",
              "General Foreman not set",
              "gf-assignment"
            );
          }
        } catch (e) {
          console.warn("[GF] startup check skipped:", e);
        }
      })();
    },
  };
}

window.geotab = window.geotab || {};
window.geotab.addin = window.geotab.addin || {};
window.geotab.addin[ADDIN_NAME] = lifecycle;

// Standalone load: paint the App with no api if Drive never calls
// initialize within 500ms (someone opened dist/index.html directly).
if (typeof window !== "undefined") {
  const standaloneTimer = window.setTimeout(() => {
    if (!root) {
      console.warn("[GF] No Geotab initialize() detected — rendering standalone preview.");
      mount();
    }
  }, 500);
  window.geotab.addin[ADDIN_NAME] = function () {
    window.clearTimeout(standaloneTimer);
    return lifecycle();
  };
}
