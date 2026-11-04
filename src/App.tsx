/**
 * My General Foreman — Geotab Drive add-in.
 *
 *   Header:   title · who you are · current GF
 *   Body:     searchable tap-list of General Foremen
 *   Footer:   Save button (sticky on mobile)
 *   Banners:  offline / standalone / errors / saved
 *
 * Flow: getSession → backend /me + /gf-groups → driver taps a name →
 * Save → backend /assign (service account does the User.Set) → confirmation.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Banner, Button } from "@geotab/zenith";
import type { GeotabApi, GeotabPageState, GeotabSession, GfGroup, MeResponse, Phase } from "./types";
import { assignGf, fetchGfGroups, fetchMe, fetchSession, friendlyError } from "./api/backend";
import { GfPicker } from "./components/GfPicker";

interface AppProps {
  api: GeotabApi | null;
  pageState: GeotabPageState | null;
  /** Bumped by the add-in's focus() so a returning driver gets fresh data. */
  focusNonce: number;
}

export default function App({ api, pageState, focusNonce }: AppProps) {
  const insideDrive = api != null;
  const online = pageState?.online !== false;

  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<GeotabSession | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [groups, setGroups] = useState<GfGroup[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [savedName, setSavedName] = useState<string | null>(null);

  const currentId = me?.current[0]?.id ?? null;
  const currentName = me?.current[0]?.name ?? null;

  const load = useCallback(async () => {
    if (!api) {
      setPhase("ready");
      return;
    }
    setPhase("loading");
    setError(null);
    setSavedName(null);
    try {
      const s = await fetchSession(api);
      setSession(s);
      const [meRes, groupRes] = await Promise.all([fetchMe(s), fetchGfGroups(s)]);
      setMe(meRes);
      setGroups(groupRes);
      setSelectedId(meRes.current[0]?.id ?? null);
      setPhase("ready");
    } catch (e) {
      setError(friendlyError(e));
      setPhase("error");
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load, focusNonce]);

  const onSave = useCallback(async () => {
    if (!session || !selectedId) return;
    setPhase("saving");
    setError(null);
    try {
      const res = await assignGf(session, selectedId);
      const next: MeResponse | null = me ? { ...me, current: res.current } : me;
      setMe(next);
      setSavedName(res.current[0]?.name ?? groups.find((g) => g.id === selectedId)?.name ?? null);
      setPhase("saved");
    } catch (e) {
      setError(friendlyError(e));
      setPhase("ready");
    }
  }, [session, selectedId, me, groups]);

  const dirty = selectedId != null && selectedId !== currentId;
  const selectedName = useMemo(() => groups.find((g) => g.id === selectedId)?.name ?? null, [groups, selectedId]);
  const busy = phase === "loading" || phase === "saving";

  return (
    <div className="gf-page">
      <header className="gf-header">
        <h2>My General Foreman</h2>
        <p>
          Pick the General Foreman you report to. Your pre-trip inspections will show up on their daily report.
        </p>
      </header>

      <div className="gf-banners">
        {!insideDrive && (
          <Banner type="info" header="Standalone preview">
            Open this page from inside Geotab Drive to load your account.
          </Banner>
        )}
        {insideDrive && !online && (
          <Banner type="warning" header="You're offline">
            Reconnect to save your General Foreman selection.
          </Banner>
        )}
        {error && (
          <Banner type="error" header={phase === "error" ? "Couldn't load" : "Couldn't save"}>
            {error}
          </Banner>
        )}
        {phase === "saved" && savedName && (
          <Banner type="success" header="Saved">
            You're now assigned to <strong>{savedName}</strong>.
          </Banner>
        )}
        {me && !me.isDriver && (
          <Banner type="warning" header="Not a driver account">
            {me.displayName} isn't flagged as a driver in MyGeotab, so pre-trip inspections won't be attributed to this login.
          </Banner>
        )}
      </div>

      {me && (
        <section className="gf-card">
          <div className="gf-card-row">
            <span className="gf-label">Signed in as</span>
            <span className="gf-value">{me.displayName || me.userName}</span>
          </div>
          <div className="gf-card-row">
            <span className="gf-label">Current General Foreman</span>
            <span className={`gf-value${currentName ? "" : " gf-value--none"}`}>{currentName ?? "Not set"}</span>
          </div>
        </section>
      )}

      {phase === "loading" && <div className="gf-loading">Loading…</div>}

      {phase !== "loading" && groups.length > 0 && (
        <GfPicker groups={groups} selectedId={selectedId} currentId={currentId} disabled={busy || !online} onSelect={setSelectedId} />
      )}

      {phase !== "loading" && phase !== "error" && insideDrive && groups.length === 0 && (
        <Banner type="warning" header="No General Foremen configured">
          The GPSFMS server returned no General Foreman groups for this database. Contact support@gpsfms.com.
        </Banner>
      )}

      {insideDrive && phase !== "error" && (
        <footer className="gf-footer">
          <Button type="secondary" onClick={() => void load()} disabled={busy}>
            Refresh
          </Button>
          <Button type="primary" onClick={() => void onSave()} disabled={!dirty || busy || !online}>
            {phase === "saving" ? "Saving…" : dirty && selectedName ? `Save — ${selectedName}` : "Save"}
          </Button>
        </footer>
      )}

      {phase === "error" && insideDrive && (
        <footer className="gf-footer">
          <Button type="primary" onClick={() => void load()}>
            Try again
          </Button>
        </footer>
      )}
    </div>
  );
}
