"use client";

import { useEffect, useState } from "react";
import { type TripDetail } from "../../lib/api";
import { errorMessage } from "../../lib/errors";
import {
  bindShoppingTaskOwner,
  clearShoppingTaskRecovery,
  phaseAfterTaskFailure,
  phaseForTaskStatus,
  readShoppingTaskRecovery,
  writeShoppingTaskRecovery,
  type ShoppingTaskRecovery,
} from "../../lib/shopping-task-recovery.mjs";
import {
  shoppingTasks,
  type ShoppingProjectChoices,
  type ShoppingTaskChoices,
  type ShoppingTaskStatus,
} from "../../lib/shopping-tasks";
import { ShoppingProjectSelect } from "./shopping-project-select.mjs";

type Phase =
  | "draft"
  | "creating_command"
  | "command_created"
  | "submitting"
  | "proposed"
  | "validating"
  | "validated"
  | "confirming"
  | "confirmed"
  | "applying"
  | "effect_unknown"
  | "terminal";

export default function ShoppingTaskPanel({ trip }: { trip: TripDetail }) {
  const [capabilities, setCapabilities] = useState<{
    enabled: boolean;
    recent_authentication: boolean;
  } | null>(null);
  const [ownerKey, setOwnerKey] = useState<string | null>(null);
  const [choices, setChoices] = useState<ShoppingTaskChoices["choices"]>([]);
  const [projects, setProjects] = useState<ShoppingProjectChoices["choices"]>([]);
  const [choice, setChoice] = useState("");
  const [projectId, setProjectId] = useState("");
  const [record, setRecord] = useState<ShoppingTaskRecovery | null>(null);
  const [phase, setPhase] = useState<Phase>("draft");
  const [status, setStatus] = useState<ShoppingTaskStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    async function restore() {
      try {
        const [capability, sessionResponse] = await Promise.all([
          shoppingTasks.capabilities(),
          fetch("/api/v1/auth/session", { cache: "no-store", credentials: "same-origin" }),
        ]);
        const session = await sessionResponse.json().catch(() => null);
        const key =
          sessionResponse.ok && session?.authenticated === true &&
          typeof session.recovery_owner_key === "string"
            ? session.recovery_owner_key
            : null;
        bindShoppingTaskOwner(key);
        if (!active) return;
        setCapabilities(capability);
        setOwnerKey(key);
        const saved = readShoppingTaskRecovery(key);
        if (!saved) return;
        setRecord(saved);
        setPhase(saved.phase as Phase);
        setChoice(String(saved.create_body.context_choice_id ?? ""));
        setProjectId(String(saved.create_body.shopping_project_id ?? ""));
        if (![
          "creating_command",
          "command_created",
          "submitting",
        ].includes(saved.phase)) {
          const current = await shoppingTasks.status(saved.command_id);
          if (active) {
            setStatus(current);
            const recovered = {
              ...saved,
              phase: phaseForTaskStatus(current.state),
              confirmation_id: current.confirmation_id ?? saved.confirmation_id,
            };
            setRecord(recovered);
            setPhase(recovered.phase as Phase);
            writeShoppingTaskRecovery(recovered);
          }
        }
      } catch {
        if (active) setCapabilities({ enabled: false, recent_authentication: false });
      }
    }
    void restore();
    return () => { active = false; };
  }, []);

  async function run(work: (owner: string) => Promise<void>) {
    if (busy || !ownerKey) return;
    setBusy(true);
    setError("");
    try {
      await work(ownerKey);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function persist(next: ShoppingTaskRecovery, owner: string) {
    if (next.owner_key !== owner || owner !== ownerKey) return false;
    writeShoppingTaskRecovery(next);
    setRecord(next);
    setPhase(next.phase as Phase);
    return true;
  }

  async function acceptStatus(
    nextStatus: ShoppingTaskStatus,
    current: ShoppingTaskRecovery,
    owner: string,
  ) {
    const next = {
      ...current,
      phase: phaseForTaskStatus(nextStatus.state),
      confirmation_id: nextStatus.confirmation_id ?? current.confirmation_id,
    };
    setStatus(nextStatus);
    await persist(next, owner);
  }

  async function submitOriginal(current: ShoppingTaskRecovery, owner: string) {
    const submitting = { ...current, phase: "submitting" };
    if (!(await persist(submitting, owner))) return;
    try {
      const next = await shoppingTasks.submit(submitting.command_id);
      await acceptStatus(next, submitting, owner);
    } catch (cause) {
      const outcome = phaseAfterTaskFailure("submitting", cause);
      if (outcome.discard) {
        const rejected = { ...submitting, phase: "terminal" };
        await persist(rejected, owner);
        setError("Personal AI rejected this source command. Correct consent or revision and start a new task.");
      } else {
        await persist({ ...submitting, phase: outcome.phase }, owner);
        setError("The source command exists, but submission was not acknowledged. Retry the original with the same command ID.");
      }
    }
  }

  async function createAndSubmit(owner: string) {
    const selected = projects.find((project) => project.project_id === projectId);
    if (!selected) return;
    const commandId = crypto.randomUUID();
    const body = {
      command_id: commandId,
      trip_id: trip.id,
      expected_trip_revision: trip.revision,
      context_choice_id: choice,
      shopping_project_id: selected.project_id,
      expected_project_revision: selected.revision,
    };
    const creating: ShoppingTaskRecovery = {
      version: 1,
      owner_key: owner,
      command_id: commandId,
      create_body: body,
      phase: "creating_command",
      confirmation_id: null,
    };
    if (!(await persist(creating, owner))) return;
    try {
      await shoppingTasks.create(body);
    } catch (cause) {
      const outcome = phaseAfterTaskFailure("creating_command", cause);
      if (outcome.discard) {
        clearShoppingTaskRecovery();
        setRecord(null);
        setPhase("draft");
        setStatus(null);
        setError("Shopping rejected command creation. Review consent, trip revision, and target selection before trying again.");
      } else {
        await persist({ ...creating, phase: outcome.phase }, owner);
        setError("Command creation may have completed. Recover it with the same command ID.");
      }
      return;
    }
    const created = { ...creating, phase: "command_created" };
    if (await persist(created, owner)) await submitOriginal(created, owner);
  }

  async function recoverOriginal(owner: string) {
    if (!record) return;
    if (record.phase === "creating_command") {
      try {
        await shoppingTasks.create(record.create_body as Parameters<typeof shoppingTasks.create>[0]);
        const created = { ...record, phase: "command_created" };
        if (await persist(created, owner)) await submitOriginal(created, owner);
      } catch (cause) {
        const outcome = phaseAfterTaskFailure("creating_command", cause);
        if (outcome.discard) {
          clearShoppingTaskRecovery();
          setRecord(null);
          setPhase("draft");
          setError("Shopping rejected command creation. Correct the request before retrying.");
        } else {
          await persist({ ...record, phase: outcome.phase }, owner);
          setError("Command creation is still unconfirmed. Keep its command ID and retry later.");
        }
      }
      return;
    }
    if (["command_created", "submitting"].includes(record.phase)) {
      await submitOriginal(record, owner);
      return;
    }
    await checkOriginal(owner);
  }

  async function validate(owner: string) {
    if (!record) return;
    const validating = { ...record, phase: "validating" };
    await persist(validating, owner);
    try {
      await acceptStatus(await shoppingTasks.control(record.command_id, "validate"), validating, owner);
    } catch (cause) {
      const outcome = phaseAfterTaskFailure("validating", cause);
      await persist({ ...validating, phase: outcome.phase }, owner);
      setError(outcome.discard
        ? "Shopping rejected validation. Review current consent and target revision before starting a new task."
        : "Validation acknowledgement is missing. Check the original task before retrying.");
    }
  }

  async function confirmAndApply(owner: string) {
    if (!record || !status) return;
    const confirmationId = status.confirmation_id ?? record.confirmation_id ?? crypto.randomUUID();
    const confirmation = { confirmation_id: confirmationId, binding: status.binding };
    let currentStatus = status;
    let currentRecord = record;
    if (currentStatus.state !== "confirmed") {
      const confirming = { ...currentRecord, phase: "confirming", confirmation_id: confirmationId };
      await persist(confirming, owner);
      try {
        currentStatus = await shoppingTasks.control(record.command_id, "confirm", confirmation);
        await acceptStatus(currentStatus, confirming, owner);
        currentRecord = { ...confirming, phase: "confirmed", confirmation_id: currentStatus.confirmation_id ?? confirmationId };
        await persist(currentRecord, owner);
      } catch (cause) {
        const outcome = phaseAfterTaskFailure("confirming", cause);
        await persist({ ...confirming, phase: outcome.phase }, owner);
        setError(outcome.discard
          ? "Shopping rejected confirmation. Review the preview before starting a new task."
          : "Confirmation may have completed. Check the original task to recover its server identity.");
        return;
      }
    }
    const applying = { ...currentRecord, phase: "applying" };
    await persist(applying, owner);
    try {
      await acceptStatus(await shoppingTasks.control(record.command_id, "apply", confirmation), applying, owner);
    } catch (cause) {
      const outcome = phaseAfterTaskFailure("applying", cause);
      await persist({ ...applying, phase: outcome.phase }, owner);
      setError(outcome.phase === "effect_unknown"
        ? "The destination may have applied this change. Reconcile the original execution; do not create a replacement."
        : "Shopping definitively rejected apply. Correct the cause before starting a new task.");
    }
  }

  async function checkOriginal(owner: string) {
    if (!record) return;
    const current = await shoppingTasks.status(record.command_id);
    if (["executing", "reconciling", "uncertain"].includes(current.state)) {
      await acceptStatus(await shoppingTasks.control(record.command_id, "reconcile"), record, owner);
      return;
    }
    await acceptStatus(current, record, owner);
  }

  function reset() {
    clearShoppingTaskRecovery();
    setRecord(null);
    setStatus(null);
    setPhase("draft");
    setError("");
  }

  if (!capabilities?.enabled) return null;
  const selectedProject = projects.find((project) => project.project_id === projectId);
  return <section aria-label="Shopping trip task" className="panel">
    <h2>Share trip context with Shopping</h2>
    <p>Append the shared destination and dates as a project note. Shopping keeps its budget, requirements, and existing notes.</p>
    {!capabilities.recent_authentication && <p role="alert">Authenticate again to review or apply this task.</p>}
    <fieldset disabled={busy || !!record || !capabilities.recent_authentication}>
      <button type="button" onClick={() => void run(async () => setChoices((await shoppingTasks.choices()).choices))}>Load granted sources</button>
      <label>Granted trip <select value={choice} onChange={event => setChoice(event.target.value)}><option value="">Select a grant</option>{choices.map(item => <option key={item.id} value={item.id}>{item.category_label}</option>)}</select></label>
      <button type="button" onClick={() => void run(async () => setProjects((await shoppingTasks.projects()).choices))}>Load Shopping projects</button>
      <ShoppingProjectSelect choices={projects} selectedId={projectId} onChange={setProjectId} disabled={busy || !!record || !capabilities.recent_authentication} />
      <button type="button" disabled={!trip.cross_domain_sharing_allowed || !choice || !selectedProject} onClick={() => void run(createAndSubmit)}>Prepare Shopping task</button>
    </fieldset>
    {!trip.cross_domain_sharing_allowed && <p>Enable trip sharing in the trip settings first.</p>}
    {record && <p>Recovery command ID: <code>{record.command_id}</code></p>}
    {status && <p role="status">{status.state} {status.outcome_code ?? ""}<br />{status.preview}</p>}
    <fieldset disabled={busy || !capabilities.recent_authentication}>
      <button type="button" disabled={!record || phase !== "proposed"} onClick={() => void run(validate)}>Validate and preview</button>
      <button type="button" disabled={!record || !status || !(status.state === "validated" || status.state === "confirmed") || phase === "effect_unknown"} onClick={() => void run(confirmAndApply)}>Confirm and apply this note</button>
      <button type="button" disabled={!record || !["creating_command", "command_created", "submitting"].includes(phase)} onClick={() => void run(recoverOriginal)}>Recover original command or submission</button>
      <button type="button" disabled={!record} onClick={() => void run(checkOriginal)}>Check or reconcile original result</button>
    </fieldset>
    {phase === "effect_unknown" && <p role="alert">The destination may have applied this change. Reconcile the original execution; do not create a replacement task.</p>}
    {record && phase === "terminal" && <button type="button" disabled={busy} onClick={reset}>Start a new task</button>}
    {error && <p className="formError" role="alert">{error}</p>}
  </section>;
}
