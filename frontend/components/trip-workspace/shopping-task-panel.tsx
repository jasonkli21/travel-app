"use client";

import { useEffect, useState } from "react";
import { type TripDetail } from "../../lib/api";
import { errorMessage } from "../../lib/errors";
import { shoppingTasks, type ShoppingTaskChoices, type ShoppingTaskStatus } from "../../lib/shopping-tasks";

export default function ShoppingTaskPanel({ trip }: { trip: TripDetail }) {
  const [capabilities, setCapabilities] = useState<{ enabled: boolean; recent_authentication: boolean } | null>(null);
  const [choices, setChoices] = useState<ShoppingTaskChoices["choices"]>([]);
  const [choice, setChoice] = useState("");
  const [project, setProject] = useState("");
  const [revision, setRevision] = useState("");
  const [command, setCommand] = useState("");
  const [resumeId, setResumeId] = useState("");
  const [status, setStatus] = useState<ShoppingTaskStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [confirmation] = useState(() => crypto.randomUUID());
  useEffect(() => {
    let active = true;
    void shoppingTasks.capabilities().then(value => {
      if (active) setCapabilities(value);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  async function run(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError("");
    try { await work(); } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  }
  async function observe(id: string) {
    const value = await shoppingTasks.status(id);
    setCommand(id); setStatus(value); setUncertain(false);
  }
  if (!capabilities?.enabled) return null;
  return <section aria-label="Shopping trip task" className="panel">
    <h2>Share trip context with Shopping</h2>
    <p>Append the shared destination and dates as a project note. Shopping keeps its budget, requirements, and existing notes.</p>
    {!capabilities.recent_authentication && <p role="alert">Authenticate again to review or apply this task.</p>}
    <fieldset disabled={busy || !!command || !capabilities.recent_authentication}>
      <button type="button" onClick={() => void run(async () => setChoices((await shoppingTasks.choices()).choices))}>Load granted sources</button>
      <label>Granted trip <select value={choice} onChange={e => setChoice(e.target.value)}><option value="">Select a grant</option>{choices.map(item => <option key={item.id} value={item.id}>{item.category_label}</option>)}</select></label>
      <label>Shopping project ID <input value={project} onChange={e => setProject(e.target.value)} /></label>
      <label>Current project revision <input inputMode="numeric" value={revision} onChange={e => setRevision(e.target.value)} /></label>
      <button type="button" disabled={!trip.cross_domain_sharing_allowed || !choice || !project || !Number.isSafeInteger(Number(revision)) || Number(revision) < 1} onClick={() => void run(async () => {
        const id = crypto.randomUUID(); setCommand(id); setUncertain(true);
        await shoppingTasks.create({ command_id: id, trip_id: trip.id, expected_trip_revision: trip.revision, context_choice_id: choice, shopping_project_id: project, expected_project_revision: Number(revision) });
        setStatus(await shoppingTasks.submit(id)); setUncertain(false);
      })}>Prepare Shopping task</button>
    </fieldset>
    {!trip.cross_domain_sharing_allowed && <p>Enable trip sharing in the trip settings first.</p>}
    {command && <p>Recovery command ID: <code>{command}</code></p>}
    {status && <p role="status">{status.state} {status.outcome_code ?? ""}<br />{status.preview}</p>}
    <fieldset disabled={busy || !capabilities.recent_authentication}>
      <button type="button" disabled={!command || uncertain || status?.state !== "proposed"} onClick={() => void run(async () => setStatus(await shoppingTasks.control(command, "validate")))}>Validate and preview</button>
      <button type="button" disabled={!command || uncertain || !(status?.state === "validated" || status?.state === "confirmed")} onClick={() => void run(async () => {
        if (!status) return;
        const binding = { confirmation_id: status.confirmation_id ?? confirmation, binding: status.binding };
        setUncertain(true);
        if (status.state !== "confirmed") await shoppingTasks.control(command, "confirm", binding);
        setStatus(await shoppingTasks.control(command, "apply", binding)); setUncertain(false);
      })}>Confirm and apply this note</button>
      <button type="button" disabled={!command} onClick={() => void run(() => observe(command))}>Check original result</button>
      <button type="button" disabled={!command} onClick={() => void run(async () => {
        setStatus(await shoppingTasks.control(command, "reconcile")); setUncertain(false);
      })}>Reconcile original task</button>
      <button type="button" disabled={!command || !!status || !uncertain} onClick={() => void run(async () => {
        setStatus(await shoppingTasks.submit(command)); setUncertain(false);
      })}>Recover original submission</button>
    </fieldset>
    {uncertain && <p role="alert">An acknowledgement is missing. Keep this command ID and check its result before starting another task.</p>}
    <label>Resume command ID <input value={resumeId} disabled={busy || !!command} onChange={e => setResumeId(e.target.value)} /></label>
    <button type="button" disabled={busy || !!command || !/^[0-9a-f-]{36}$/i.test(resumeId)} onClick={() => void run(() => observe(resumeId))}>Resume original task</button>
    {error && <p role="alert">{error}</p>}
  </section>;
}
