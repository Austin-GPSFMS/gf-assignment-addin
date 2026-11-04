/**
 * Tap-to-select list of General Foremen with a search box.
 *
 * A plain list (rather than a Zenith Dropdown) because this runs on a
 * driver's phone/tablet inside Geotab Drive: big touch targets, no popup
 * positioning, and the search filter keeps 20–60 names manageable.
 */

import { useMemo, useState } from "react";
import { SearchInput } from "@geotab/zenith";
import type { GfGroup } from "../types";

interface GfPickerProps {
  groups: GfGroup[];
  selectedId: string | null;
  currentId: string | null;
  disabled?: boolean;
  onSelect: (id: string) => void;
}

export function GfPicker({ groups, selectedId, currentId, disabled, onSelect }: GfPickerProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter((g) => g.name.toLowerCase().includes(q) || (g.parent ?? "").toLowerCase().includes(q));
  }, [groups, query]);

  const showParent = useMemo(() => new Set(groups.map((g) => g.parent ?? "")).size > 1, [groups]);

  return (
    <div className="gf-picker">
      <SearchInput value={query} onChange={setQuery} placeholder="Search by name…" ariaLabel="Search General Foremen" />
      <div className="gf-list" role="listbox" aria-label="General Foremen">
        {filtered.length === 0 && <div className="gf-empty">No General Foreman matches “{query}”.</div>}
        {filtered.map((g) => {
          const isSelected = g.id === selectedId;
          const isCurrent = g.id === currentId;
          return (
            <button
              key={g.id}
              type="button"
              role="option"
              aria-selected={isSelected}
              className={`gf-row${isSelected ? " gf-row--selected" : ""}`}
              disabled={disabled}
              onClick={() => onSelect(g.id)}
            >
              <span className={`gf-radio${isSelected ? " gf-radio--on" : ""}`} aria-hidden="true" />
              <span className="gf-row-text">
                <span className="gf-row-name">{g.name}</span>
                {showParent && g.parent && <span className="gf-row-sub">{g.parent}</span>}
              </span>
              {isCurrent && <span className="gf-pill">Current</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
