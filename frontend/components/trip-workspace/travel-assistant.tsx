"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  type PersonalAIContextEntityReference,
  type PersonalAIContextSourceClass,
  type PersonalAISidecarMessage,
  type PersonalAISidecarActionPolicy,
  type PersonalAISidecarPreview,
  type PersonalAISidecarTurnInput,
  type TripDetail,
  travelSidecarActions,
  travelApi,
} from "../../lib/api";
import { errorMessage } from "../../lib/errors";
import styles from "./travel-assistant.module.css";

type AssistantReference = {
  reference: PersonalAIContextEntityReference;
  label: string;
};

type Props = {
  trip: TripDetail;
  open: boolean;
  onClose: () => void;
  viewId: PersonalAISidecarTurnInput["view_id"];
  references: AssistantReference[];
  selectionNote?: string;
  onRemoveReference: (index: number) => void;
};

type Attempt = PersonalAISidecarTurnInput;

const SOURCE_OPTIONS: { id: PersonalAIContextSourceClass; label: string }[] = [
  { id: "global_profile", label: "Shared profile and preferences" },
  { id: "domain_current", label: "Current trip and selected details" },
  { id: "ai_memory", label: "Saved memories" },
  { id: "external_research", label: "Research and external evidence" },
  { id: "conversation", label: "Conversation history" },
  { id: "client_context", label: "Client context" },
  { id: "tool_result", label: "Tool results" },
];

const SOURCE_LABELS: Record<string, string> = {
  travel_current_trip: "Current trip",
  travel_selected_entities: "Selected days, itinerary items, and places",
  travel_selected_research: "Selected cited research",
  travel_booking_summary: "Reservation summary",
  profile_units: "Shared preferred units",
  profile_locale: "Shared language preference",
  profile_response_style: "Shared response-style preference",
  profile_answer_length: "Shared answer-length preference",
  personal_recall: "Saved memory recalled for this request",
};

function storageKey(tripId: string, kind: "conversation" | "create-key") {
  return `travel-personal-ai:${tripId}:${kind}`;
}

function attemptSignature(input: Omit<Attempt, "idempotency_key">): string {
  return JSON.stringify(input);
}

function sourceLabel(category: string): string {
  return SOURCE_LABELS[category] ?? category.replaceAll("_", " ");
}

function fieldLabel(field: string): string {
  return field.replaceAll("_", " ");
}

export default function TravelAssistant({
  trip,
  open,
  onClose,
  viewId,
  references,
  selectionNote,
  onRemoveReference,
}: Props) {
  const [messages, setMessages] = useState<PersonalAISidecarMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [excludedSources, setExcludedSources] = useState<PersonalAIContextSourceClass[]>([]);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [preview, setPreview] = useState<PersonalAISidecarPreview | null>(null);
  const [previewSignature, setPreviewSignature] = useState<string | null>(null);
  const [actionPolicyState, setActionPolicyState] = useState<{
    tripId: string;
    policy: PersonalAISidecarActionPolicy;
  } | null>(null);
  const [busy, setBusy] = useState<"preview" | "continue" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copyMessageId, setCopyMessageId] = useState<string | null>(null);
  const dialog = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const submitInFlight = useRef(false);

  const selectedReferenceList = useMemo(
    () => references.map((item) => item.reference),
    [references],
  );
  const sourceSelection = useMemo(() => ({
    content: draft.trim(),
    entity_refs: selectedReferenceList,
    view_id: viewId,
    excluded_source_classes: excludedSources,
  }), [draft, excludedSources, selectedReferenceList, viewId]);
  const currentSignature = attemptSignature(sourceSelection);
  const previewIsCurrent = preview !== null && previewSignature === currentSignature;
  const actionPolicy = actionPolicyState?.tripId === trip.id ? actionPolicyState.policy : null;
  const actions = travelSidecarActions(actionPolicy);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void travelApi.getAssistantActionPolicy(trip.id)
      .then((policy) => {
        if (!cancelled) setActionPolicyState({ tripId: trip.id, policy });
      })
      .catch((failure: unknown) => {
        if (!cancelled) setError(errorMessage(failure));
      });
    return () => { cancelled = true; };
  }, [open, trip.id]);

  useEffect(() => {
    const savedConversation = window.sessionStorage.getItem(storageKey(trip.id, "conversation"));
    if (!savedConversation) return;

    let cancelled = false;
    void travelApi.getAssistantConversation(trip.id, savedConversation)
      .then((history) => {
        if (!cancelled) setMessages(history.messages);
      })
      .catch((failure: unknown) => {
        if (!cancelled) setError(errorMessage(failure));
      })
    return () => { cancelled = true; };
  }, [trip.id]);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement : null;
    heading.current?.focus();
    const element = dialog.current;
    if (!element) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && busy === null) {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(element.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), a[href]',
      ));
      if (!focusable.length) return;
      if (event.shiftKey && document.activeElement === focusable[0]) {
        event.preventDefault();
        focusable[focusable.length - 1].focus();
      } else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) {
        event.preventDefault();
        focusable[0].focus();
      }
    };
    element.addEventListener("keydown", onKeyDown);
    return () => {
      element.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [busy, onClose, open]);

  async function ensureConversation(): Promise<string> {
    const savedConversation = window.sessionStorage.getItem(storageKey(trip.id, "conversation"));
    if (savedConversation) return savedConversation;
    let key = window.sessionStorage.getItem(storageKey(trip.id, "create-key"));
    if (!key) {
      key = crypto.randomUUID();
      window.sessionStorage.setItem(storageKey(trip.id, "create-key"), key);
    }
    const created = await travelApi.createAssistantConversation(trip.id, key);
    window.sessionStorage.setItem(storageKey(trip.id, "conversation"), created.id);
    return created.id;
  }

  function buildAttempt(): Attempt {
    const withoutKey = sourceSelection;
    const existingMatches = attempt
      && attemptSignature({
        content: attempt.content,
        entity_refs: attempt.entity_refs,
        view_id: attempt.view_id,
        excluded_source_classes: attempt.excluded_source_classes,
      }) === attemptSignature(withoutKey);
    return existingMatches ? attempt : {
      ...withoutKey,
      idempotency_key: crypto.randomUUID(),
    };
  }

  async function previewContext() {
    if (!sourceSelection.content || submitInFlight.current) return;
    submitInFlight.current = true;
    const nextAttempt = buildAttempt();
    setAttempt(nextAttempt);
    setError(null);
    setNotice(null);
    setPreview(null);
    setPreviewSignature(null);
    setBusy("preview");
    try {
      const id = await ensureConversation();
      const result = await travelApi.previewAssistantContext(trip.id, id, nextAttempt);
      setPreview(result);
      setPreviewSignature(currentSignature);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      submitInFlight.current = false;
      setBusy(null);
    }
  }

  async function continueTurn() {
    if (!attempt || !previewIsCurrent || submitInFlight.current) return;
    submitInFlight.current = true;
    setError(null);
    setNotice(null);
    setBusy("continue");
    try {
      const id = await ensureConversation();
      const response = await travelApi.continueAssistantTurn(trip.id, id, attempt);
      const completedMessages: PersonalAISidecarMessage[] = [
        {
          id: attempt.idempotency_key,
          role: "user",
          content: attempt.content,
          status: "completed",
        },
        {
          id: response.message_id,
          role: "assistant",
          content: response.answer,
          status: "completed",
        },
      ];
      setMessages((current) => [...current, ...completedMessages].slice(-40));
      setDraft("");
      setAttempt(null);
      setPreview(null);
      setPreviewSignature(null);
      setNotice("The response is saved in this Personal AI conversation. It has not changed your trip.");
    } catch (failure) {
      setError(errorMessage(failure));
      setNotice("If the turn outcome is uncertain, retry with the same request key.");
    } finally {
      submitInFlight.current = false;
      setBusy(null);
    }
  }

  async function copyAnswer(message: PersonalAISidecarMessage) {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopyMessageId(message.id);
      window.setTimeout(() => setCopyMessageId((current) => current === message.id ? null : current), 1800);
    } catch {
      setError("Clipboard access is unavailable. Select the response text and copy it manually.");
    }
  }

  function insertIntoDraft(message: PersonalAISidecarMessage) {
    setDraft(message.content);
    setNotice("Inserted into the editable assistant prompt. Your trip records have not changed.");
  }

  function toggleSource(source: PersonalAIContextSourceClass) {
    setExcludedSources((current) => current.includes(source)
      ? current.filter((item) => item !== source)
      : [...current, source]);
    setAttempt(null);
    setPreview(null);
    setPreviewSignature(null);
  }

  function editPrompt(value: string) {
    setDraft(value);
    setAttempt(null);
    setPreview(null);
    setPreviewSignature(null);
  }

  if (!open) return null;

  return (
    <div className={styles.overlay}>
      <section
        ref={dialog}
        className={styles.drawer}
        role="dialog"
        aria-modal="true"
        aria-labelledby="travel-assistant-heading"
      >
        <header className={styles.header}>
          <div>
            <p className="eyebrow">PERSONAL AI SIDECAR</p>
            <h2 id="travel-assistant-heading" ref={heading} tabIndex={-1}>Ask about this trip</h2>
            <p className="muted">{trip.title}</p>
          </div>
          <button className="iconButton" type="button" onClick={onClose} disabled={busy !== null} aria-label="Close assistant">Close</button>
        </header>

        <p className={styles.disclosure}>
          Review the planned context before sending. Travel checks trip ownership again when it reads
          records; Personal AI checks the shared source policy again before generation. Responses,
          Copy, and prompt drafts never update itinerary, place, reservation, or booking records.
        </p>

        <section className={styles.context} aria-labelledby="travel-assistant-context-heading">
          <h3 id="travel-assistant-context-heading">Selected Travel context</h3>
          <p className={styles.contextPill}>Current trip · {trip.title}</p>
          {references.length ? (
            <ul className={styles.referenceList}>
              {references.map((item, index) => (
                <li key={`${item.reference.entity_type}:${item.reference.entity_id}`}>
                  <span>{item.label}</span>
                  <button
                    className="iconButton"
                    type="button"
                    disabled={busy !== null}
                    aria-label={`Remove ${item.label} from selected context`}
                    onClick={() => onRemoveReference(index)}
                  >Remove</button>
                </li>
              ))}
            </ul>
          ) : <p className="muted">No selected day, place, or research. The current trip remains available.</p>}
          {selectionNote ? <p className="formHint">{selectionNote}</p> : null}
        </section>

        <fieldset className={styles.narrowing} disabled={busy !== null}>
          <legend>Narrow source categories</legend>
          {SOURCE_OPTIONS.map((source) => (
            <label key={source.id}>
              <input
                type="checkbox"
                checked={!excludedSources.includes(source.id)}
                onChange={() => toggleSource(source.id)}
              />
              <span>{source.label}</span>
            </label>
          ))}
          <p className="formHint">Preferences, memories, research, and history are considered only when policy and your request select them.</p>
        </fieldset>

        <div className={styles.history} aria-live="polite" aria-label="Assistant conversation">
          {messages.map((message) => (
            <article className={message.role === "assistant" ? styles.assistantMessage : styles.userMessage} key={message.id}>
              <p className={styles.messageRole}>{message.role === "assistant" ? "Personal AI" : "You"}</p>
              <p className={styles.messageText}>{message.content}</p>
              {message.role === "assistant" && message.status === "completed"
                && (actions.copy || actions.draft) ? (
                <div className={styles.messageActions}>
                  {actions.copy ? <button className="secondary compact" type="button" onClick={() => void copyAnswer(message)}>{copyMessageId === message.id ? "Copied" : "Copy"}</button> : null}
                  {actions.draft ? <button className="secondary compact" type="button" onClick={() => insertIntoDraft(message)}>Use in prompt draft</button> : null}
                </div>
              ) : null}
            </article>
          ))}
        </div>

        <form className={styles.composer} onSubmit={(event) => { event.preventDefault(); void previewContext(); }}>
          <label htmlFor="travel-assistant-prompt">Message</label>
          <textarea
            id="travel-assistant-prompt"
            value={draft}
            onChange={(event) => editPrompt(event.target.value)}
            maxLength={20_000}
            rows={4}
            disabled={busy !== null}
            placeholder="Ask for read-only help with your selected trip context"
          />
          <button className="secondary" type="submit" disabled={busy !== null || !draft.trim()}>
            {busy === "preview" ? "Checking context…" : "Preview context"}
          </button>
        </form>

        {previewIsCurrent && preview ? (
          <section className={styles.preview} aria-labelledby="travel-assistant-preview-heading">
            <h3 id="travel-assistant-preview-heading">Planned context before disclosure</h3>
            <p>{preview.selected_message_count} active conversation messages are eligible for history.</p>
            {preview.planned_sources.length ? (
              <ul>
                {preview.planned_sources.map((source, index) => (
                  <li key={`${source.category}:${source.operation}:${index}`}>
                    <strong>{sourceLabel(source.category)}</strong>
                    {source.category === "travel_booking_summary" ? <span className={styles.sensitiveTag}>Sensitive</span> : null}
                    {source.source_class === "external_research" ? <span className={styles.externalTag}>External evidence · expiring</span> : null}
                    <span className={styles.fields}>{source.fields.map(fieldLabel).join(", ")}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="muted">No additional source matched this request. The conversation can still be sent.</p>}
            {preview.excluded_sources.length ? (
              <details>
                <summary>Sources not included ({preview.excluded_sources.length})</summary>
                <ul>{preview.excluded_sources.map((source, index) => <li key={`${source.category}:${index}`}>{sourceLabel(source.category)} · {source.reason.replaceAll("_", " ")}</li>)}</ul>
              </details>
            ) : null}
            {preview.planned_sources.some((source) => source.category === "travel_booking_summary") ? (
              <p className={styles.sensitiveNotice}>A minimized reservation summary is included because this request asks for reservation details. Confirmation codes, notes, and booking documents are excluded.</p>
            ) : null}
            <button className="primary" type="button" onClick={() => void continueTurn()} disabled={busy !== null}>
              {busy === "continue" ? "Sending…" : attempt && messages.some((message) => message.id === attempt.idempotency_key) ? "Retry this turn" : "Send with this context"}
            </button>
          </section>
        ) : null}

        {error ? <p className="formError" role="alert">{error}</p> : null}
        {notice ? <p className="formHint" role="status">{notice}</p> : null}
      </section>
    </div>
  );
}
