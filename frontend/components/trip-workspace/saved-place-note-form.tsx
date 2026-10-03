"use client";

import { useState } from "react";
import type { SavedPlace } from "../../lib/api";

export default function SavedPlaceNoteForm({
  savedPlace,
  pending,
  disabled,
  onSave,
}: {
  savedPlace: SavedPlace;
  pending: boolean;
  disabled: boolean;
  onSave: (note: string | null) => Promise<boolean>;
}) {
  const [note, setNote] = useState(savedPlace.note ?? "");
  return (
    <form className="savedNoteForm" onSubmit={(event) => { event.preventDefault(); void onSave(note.trim() || null); }}>
      <label className="srOnly" htmlFor={`saved-note-${savedPlace.id}`}>Candidate note for {savedPlace.place.name}</label>
      <input maxLength={1000} id={`saved-note-${savedPlace.id}`} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Candidate note" disabled={disabled} />
      <button className="iconButton" type="submit" disabled={disabled}>{pending ? "Saving…" : "Save note"}</button>
    </form>
  );
}

