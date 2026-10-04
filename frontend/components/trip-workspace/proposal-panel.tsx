"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import type { ItineraryItem, ProposalDetail, ProposalPreview, TripDetail } from "../../lib/api";
import { ApiError, travelApi } from "../../lib/api";
import { errorMessage } from "../../lib/errors";
import {
  proposalCanApply,
  proposalIsTerminal,
  proposalPresentationState,
} from "../../lib/proposal-state.mjs";
import { safeHttpUrl } from "../../lib/urls.mjs";

const proposalsEnabled = process.env.NEXT_PUBLIC_TRAVEL_PROPOSALS_ENABLED === "true";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function formatOperation(kind: string): string {
  return {
    add_item: "Add itinerary item",
    move_item: "Move itinerary item",
    set_item_times: "Change item time",
    remove_item: "Remove selected item",
  }[kind] ?? "Change itinerary";
}

function changeLabel(value: NonNullable<NonNullable<ProposalPreview["diff"][number]["after"]>>) {
  return `Day ${value.day_index} · ${formatDate(value.date)} · ${value.title}`
    + `${value.start_time ? ` · ${value.start_time}` : " · No start time"}`
    + `${value.end_time ? `–${value.end_time}` : ""}`;
}

function removableItems(trip: TripDetail): { dayIndex: number; item: ItineraryItem }[] {
  return trip.days.flatMap((day) => day.items
    .filter((item) => item.status !== "booked"
      && item.status !== "completed"
      && item.reservation === null)
    .map((item) => ({ dayIndex: day.day_index, item })));
}

function Preview({ preview }: { preview: ProposalPreview }) {
  return (
    <div className="proposalPreview">
      <section aria-labelledby="proposal-changes-heading">
        <h4 id="proposal-changes-heading">Operation diff</h4>
        <ol className="proposalDiff">
          {preview.diff.map((change) => (
            <li key={change.operation_index}>
              <strong>{formatOperation(change.kind)}</strong>
              <span>{change.before ? changeLabel(change.before) : "Nothing before"}</span>
              <span aria-hidden="true">→</span>
              <span>{change.after ? changeLabel(change.after) : "Removed"}</span>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="proposal-result-heading">
        <h4 id="proposal-result-heading">Resulting itinerary</h4>
        <div className="proposalResultDays">
          {preview.after.map((day) => (
            <article className="proposalResultDay" key={day.handle}>
              <h5>Day {day.day_index} · {formatDate(day.date)}{day.title ? ` · ${day.title}` : ""}</h5>
              {day.items.length === 0 ? <p className="emptyText">No itinerary items.</p> : (
                <ol>
                  {day.items.map((item) => (
                    <li key={item.handle}>
                      <span>{item.start_time ?? "Unscheduled"}{item.end_time ? `–${item.end_time}` : ""}</span>
                      <strong>{item.title}</strong>
                      <small>{item.item_type} · {item.status}</small>
                    </li>
                  ))}
                </ol>
              )}
            </article>
          ))}
        </div>
      </section>

      {preview.warnings.length > 0 ? (
        <section className="proposalWarnings" aria-labelledby="proposal-warnings-heading" role="status">
          <h4 id="proposal-warnings-heading">Scheduling warnings</h4>
          <ul>{preview.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>{warning.message}</li>)}</ul>
        </section>
      ) : <p className="proposalNoWarnings">No schedule conflicts found in this preview.</p>}
    </div>
  );
}

export default function ProposalPanel({
  trip,
  workspacePlaceFootprint,
  busy,
  disabled,
  onCommitted,
  onPendingChange,
}: {
  trip: TripDetail;
  workspacePlaceFootprint: string;
  busy: boolean;
  disabled: boolean;
  onCommitted: () => Promise<boolean>;
  onPendingChange: (pending: boolean) => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [selectedRemovals, setSelectedRemovals] = useState<string[]>([]);
  const [proposal, setProposal] = useState<ProposalDetail | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);
  const [requestMessage, setRequestMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<"generate" | "status" | "apply" | "reject" | null>(null);
  const [clockNow, setClockNow] = useState(() => Date.now());
  const requestInFlight = useRef(false);
  const proposalRef = useRef(proposal);
  const eligibleItems = useMemo(() => removableItems(trip), [trip]);
  const setTaskPending = (next: "generate" | "status" | "apply" | "reject" | null) => {
    setPending(next);
    onPendingChange(next !== null);
  };

  useEffect(() => {
    proposalRef.current = proposal;
  }, [proposal]);

  useEffect(() => {
    if (proposal?.lifecycle_state !== "ready" || !proposal.expires_at) return;
    const delay = Date.parse(proposal.expires_at) - Date.now() + 1;
    const timer = window.setTimeout(() => setClockNow(Date.now()), Math.max(0, delay));
    return () => window.clearTimeout(timer);
  }, [proposal?.expires_at, proposal?.lifecycle_state, proposal?.proposal_id]);

  useEffect(() => {
    const currentProposal = proposalRef.current;
    if (!currentProposal) return;
    if (currentProposal.lifecycle_state !== "ready") return;
    if (currentProposal.base_trip_revision !== trip.revision) {
      setProposal((current) => current ? { ...current, state: "stale" } : current);
      return;
    }
    void travelApi.getProposal(trip.id, currentProposal.proposal_id)
      .then(setProposal)
      .catch(() => undefined);
  }, [trip.id, trip.revision, workspacePlaceFootprint]); // Refresh after trip or shared-place edits.

  const clearForNewRequest = () => {
    setProposal(null);
    setIdempotencyKey(null);
    setRequestMessage(null);
    setInstruction("");
    setSelectedRemovals([]);
  };

  const checkRequest = async () => {
    if (!idempotencyKey || requestInFlight.current) return;
    requestInFlight.current = true;
    setTaskPending("status");
    setRequestMessage(null);
    try {
      const detail = await travelApi.getProposalByKey(trip.id, idempotencyKey);
      setProposal(detail);
      setRequestMessage(detail.state === "outcome_unknown"
        ? "The travel service still cannot confirm generation. Check again before starting another request."
        : null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setIdempotencyKey(null);
        setRequestMessage("No proposal was stored for that key. Edit the request and try again.");
        return;
      }
      setRequestMessage(errorMessage(error));
    } finally {
      requestInFlight.current = false;
      setTaskPending(null);
    }
  };

  const generate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (requestInFlight.current || busy || disabled || !trip || instruction.trim().length === 0) return;
    requestInFlight.current = true;
    setTaskPending("generate");
    setProposal(null);
    setRequestMessage(null);
    const key = globalThis.crypto.randomUUID();
    setIdempotencyKey(key);
    try {
      const detail = await travelApi.createProposal(trip.id, {
        idempotency_key: key,
        instruction: instruction.trim(),
        removable_item_ids: selectedRemovals,
      }, trip.revision);
      setProposal(detail);
      if (detail.state === "outcome_unknown") {
        setRequestMessage("Generation may still be running. Check this request by its existing key before continuing.");
      }
    } catch (error) {
      if (error instanceof ApiError
          && (error.code === "proposal_unavailable"
            || (error.status !== null && error.status < 500 && error.code !== "proposal_busy"))) {
        setIdempotencyKey(null);
        setRequestMessage(errorMessage(error));
        return;
      }
      // The POST may have committed upstream even when its response was lost.
      // Reconcile with the same key before offering any new generation.
      try {
        const detail = await travelApi.getProposalByKey(trip.id, key);
        setProposal(detail);
        setRequestMessage(detail.state === "outcome_unknown"
          ? "Generation is still being reconciled. Check this request again before starting another."
          : null);
      } catch (lookupError) {
        setRequestMessage(`Generation could not be confirmed. Check status using this request key before trying again. ${errorMessage(lookupError)}`);
      }
    } finally {
      requestInFlight.current = false;
      setTaskPending(null);
    }
  };

  const refreshProposal = async (): Promise<ProposalDetail | null> => {
    if (!proposal) return null;
    const latest = await travelApi.getProposal(trip.id, proposal.proposal_id);
    setProposal(latest);
    return latest;
  };

  const reconcileApplyFailure = async () => {
    try {
      const latest = await refreshProposal();
      if (latest?.lifecycle_state === "applied" && latest.applied_outcome) {
        const refreshed = await onCommitted();
        setRequestMessage(refreshed
          ? "This proposal was applied. The itinerary was refreshed from its stored outcome."
          : "This proposal was applied. Reload the workspace before editing again.");
        return;
      }
      setRequestMessage("Apply did not return a confirmed result. Proposal detail has been checked; review its current state before retrying.");
    } catch (lookupError) {
      setRequestMessage(`Apply status could not be confirmed. Reload proposal detail before retrying. ${errorMessage(lookupError)}`);
    }
  };

  const apply = async () => {
    if (!proposal || requestInFlight.current || busy || disabled) return;
    requestInFlight.current = true;
    setTaskPending("apply");
    setRequestMessage(null);
    try {
      // A detail read immediately before apply catches shared-place revisions
      // that do not advance the trip revision.
      const latest = await refreshProposal();
      if (!latest || latest.state !== "ready" || latest.lifecycle_state !== "ready"
          || latest.expires_at === null || Date.parse(latest.expires_at) <= Date.now()
          || !proposalCanApply(latest, trip.revision)) {
        setRequestMessage("This proposal is stale, expired, or no longer ready. Review the refreshed status before applying.");
        return;
      }
      const outcome = await travelApi.applyProposal(trip.id, latest.proposal_id, trip.revision);
      setProposal({ ...latest, state: "applied", lifecycle_state: "applied", applied_outcome: outcome });
      setInstruction("");
      setSelectedRemovals([]);
      const refreshed = await onCommitted();
      setRequestMessage(refreshed
        ? "Proposal applied. The itinerary now reflects the stored result."
        : "Proposal applied. The saved result is shown below; reload the workspace before editing again.");
    } catch {
      await reconcileApplyFailure();
    } finally {
      requestInFlight.current = false;
      setTaskPending(null);
    }
  };

  const reject = async () => {
    if (!proposal || requestInFlight.current || busy || disabled) return;
    requestInFlight.current = true;
    setTaskPending("reject");
    setRequestMessage(null);
    try {
      setProposal(await travelApi.rejectProposal(trip.id, proposal.proposal_id));
      setRequestMessage("Proposal rejected. It did not change the itinerary.");
    } catch {
      try {
        setProposal(await travelApi.getProposal(trip.id, proposal.proposal_id));
        setRequestMessage("Proposal detail has been checked after the rejection response was lost.");
      } catch (lookupError) {
        setRequestMessage(`Rejection status could not be confirmed. ${errorMessage(lookupError)}`);
      }
    } finally {
      requestInFlight.current = false;
      setTaskPending(null);
    }
  };

  const localState = proposalPresentationState(proposal, trip.revision, clockNow);
  const canApply = proposalsEnabled
    && proposalCanApply(proposal, trip.revision, clockNow)
    && !busy
    && !disabled
    && pending === null;
  const canReject = proposal?.lifecycle_state === "ready" && localState === "ready"
    && !busy && !disabled && pending === null;

  if (!proposalsEnabled) {
    return (
      <section className="proposalPanel" id="proposals" aria-labelledby="proposal-heading">
        <p className="eyebrow">OPTIONAL AI PROPOSALS</p>
        <h2 id="proposal-heading">Itinerary proposals</h2>
        <p className="muted">Proposal generation is disabled in this local web build.</p>
      </section>
    );
  }

  const hasUnresolvedRequest = idempotencyKey !== null && proposal === null;
  const terminal = proposalIsTerminal(proposal, trip.revision);

  return (
    <section className="proposalPanel" id="proposals" aria-labelledby="proposal-heading">
      <div>
        <p className="eyebrow">OPTIONAL AI PROPOSALS</p>
        <h2 id="proposal-heading">Itinerary proposals</h2>
        <p className="muted">Review a deterministic preview, then choose whether to apply it. Your saved itinerary stays authoritative.</p>
      </div>

      {!proposal && !hasUnresolvedRequest ? (
        <form className="proposalForm" onSubmit={generate}>
          <label>
            What would you like to change?
            <textarea
              value={instruction}
              maxLength={2000}
              rows={3}
              onChange={(event) => setInstruction(event.target.value)}
              disabled={busy || disabled || pending !== null}
              required
            />
          </label>
          <fieldset className="proposalAllowlist" disabled={busy || disabled || pending !== null}>
            <legend>Items the proposal may remove</legend>
            <p className="formHint">Removal is disabled unless you select each eligible item here. Booked, completed, and reservation-linked items cannot be selected.</p>
            {eligibleItems.length === 0 ? <p className="emptyText">No items are eligible for removal.</p> : eligibleItems.map(({ dayIndex, item }) => (
              <label className="proposalCheckbox" key={item.id}>
                <input
                  type="checkbox"
                  checked={selectedRemovals.includes(item.id)}
                  onChange={(event) => setSelectedRemovals((current) => event.target.checked
                    ? [...current, item.id]
                    : current.filter((id) => id !== item.id))}
                />
                <span>Day {dayIndex} · {item.title}</span>
              </label>
            ))}
          </fieldset>
          <div className="proposalDisclosure" role="note">
            <strong>What goes to the AI service</strong>
            <p>Your instruction and a bounded trip outline with dates, timezone, item names, types, schedule times, and saved candidate names. The travel service sends opaque handles for those records.</p>
            <p>Booking confirmations, reservation notes, itinerary notes, source references, and provider payloads are excluded. No research evidence is attached to this request.</p>
          </div>
          <p className="formHint">Current trip revision: {trip.revision}. This request uses context-only support and expires within 24 hours.</p>
          <button className="primary" type="submit" disabled={busy || disabled || pending !== null || instruction.trim().length === 0}>
            {pending === "generate" ? "Generating preview…" : "Generate preview"}
          </button>
        </form>
      ) : null}

      {hasUnresolvedRequest ? (
        <div className="proposalState" role="status">
          <p>Generation outcome has not been confirmed. Check the existing request key before starting another proposal.</p>
          {requestMessage ? <p>{requestMessage}</p> : null}
          <button className="secondary" type="button" onClick={() => void checkRequest()} disabled={busy || pending !== null}>
            {pending === "status" ? "Checking…" : "Check request status"}
          </button>
        </div>
      ) : null}

      {proposal ? (
        <div className="proposalReview" aria-live="polite">
          <div className={`proposalState proposalState-${localState ?? proposal.state}`} role="status">
            <strong>{localState === "ready" ? "Ready for review" : `Proposal ${localState?.replaceAll("_", " ") ?? "status"}`}</strong>
            <span>Support: {proposal.support_mode === "context_only" ? "traveler-provided itinerary context only" : "research evidence"}</span>
            <span>Trip revision {proposal.base_trip_revision} → {proposal.current_trip_revision ?? "unknown"}</span>
            {proposal.expires_at ? <span>Proposal expires: {new Date(proposal.expires_at).toLocaleString()}</span> : null}
            {proposal.failure_code ? <span>Failure: {proposal.failure_code.replaceAll("_", " ")}</span> : null}
          </div>

          {proposal.preview ? <Preview preview={proposal.preview} /> : null}

          {proposal.citations.length > 0 ? (
            <section aria-labelledby="proposal-evidence-heading">
              <h4 id="proposal-evidence-heading">Evidence</h4>
              <ul className="researchCitations">
                {proposal.citations.map((citation) => {
                  const url = safeHttpUrl(citation.url);
                  return <li key={citation.evidence_handle}><div>
                    {url ? <a href={url} target="_blank" rel="noreferrer">{citation.title ?? citation.url}</a> : <strong>{citation.title ?? "Source"}</strong>}
                    <span className="researchSourceTimes">Observed {new Date(citation.observed_at).toLocaleString()} · Expires {new Date(citation.expires_at).toLocaleString()}</span>
                  </div></li>;
                })}
              </ul>
            </section>
          ) : null}

          {requestMessage ? <p className="proposalMessage" role="status">{requestMessage}</p> : null}

          <div className="formActions">
            <button className="primary" type="button" onClick={() => void apply()} disabled={!canApply}>
              {pending === "apply" ? "Checking and applying…" : proposal.lifecycle_state === "applied" ? "Applied" : "Apply proposal"}
            </button>
            <button className="secondary" type="button" onClick={() => void reject()} disabled={!canReject}>
              {pending === "reject" ? "Rejecting…" : "Reject proposal"}
            </button>
            {proposal.lifecycle_state === "outcome_unknown" || proposal.lifecycle_state === "generating" ? <button className="secondary" type="button" onClick={() => void checkRequest()} disabled={pending !== null}>
              {pending === "status" ? "Checking…" : "Check generation status"}
            </button> : null}
            {terminal && !disabled ? <button className="secondary" type="button" onClick={clearForNewRequest} disabled={busy || pending !== null}>Start another proposal</button> : null}
          </div>
          {proposal.lifecycle_state !== "ready" || !canApply ? <p className="formHint">Apply is disabled for stale, expired, failed, unknown, rejected, or already applied proposals.</p> : null}
        </div>
      ) : null}

      {requestMessage && !proposal && !hasUnresolvedRequest ? <p className="errorBanner" role="alert">{requestMessage}</p> : null}
    </section>
  );
}
