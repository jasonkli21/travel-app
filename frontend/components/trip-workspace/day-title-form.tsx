"use client";

import { useState } from "react";

export default function DayTitleForm({
  dayId,
  title,
  pending,
  disabled,
  onSave,
}: {
  dayId: string;
  title: string | null;
  pending: boolean;
  disabled: boolean;
  onSave: (title: string | null) => Promise<boolean>;
}) {
  const [value, setValue] = useState(title ?? "");
  return (
    <form className="dayTitleForm" onSubmit={(event) => { event.preventDefault(); void onSave(value.trim() || null); }}>
      <label className="srOnly" htmlFor={`day-title-${dayId}`}>Day title</label>
      <input id={`day-title-${dayId}`} value={value} onChange={(event) => setValue(event.target.value)} placeholder="Add a day title" maxLength={200} disabled={disabled} />
      <button className="iconButton" type="submit" disabled={disabled} aria-label="Save day title">{pending ? "Saving…" : "Save"}</button>
    </form>
  );
}

