"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { PlaceSummary, Reservation, ReservationType, SavedPlace, TripDetail } from "../../lib/api";
import {
  createEntryError,
  emptyCandidateDraft,
  isDefinitiveUploadFailure,
  normalizeReservationType,
} from "../../lib/booking-import-form.mjs";
import {
  bookingImportApi,
  bookingImportsEnabled,
  type BookingCandidate,
  type BookingImportReview,
  type BookingImportSummary,
  type CandidateEdit,
  type ConfirmBookingsInput,
  type ConfirmationEntry,
  type ImportDecision,
  type ReservationStatus,
  type SourceRetention,
} from "../../lib/booking-imports";
import { ApiError } from "../../lib/api-request.mjs";
import { errorMessage } from "../../lib/errors";

type Draft = CandidateEdit & {
  place_id: string;
  itinerary_item_id: string;
  reservation_status: ReservationStatus | "";
  acknowledgedUncertainty: boolean;
};

type PendingUpload = {
  body: Blob | string;
  mediaType: "text/plain" | "application/pdf";
  filename?: string;
  retention: SourceRetention;
};

function initialDrafts(candidates: BookingCandidate[]): Record<string, Draft> {
  return Object.fromEntries(
    candidates.map((candidate) => [candidate.candidate_id, emptyCandidateDraft(candidate)]),
  );
}

function currentTimeZoneHint(candidate: BookingCandidate, endpoint: "starts_at" | "ends_at") {
  const current = candidate.current[`${endpoint}_trip_local`];
  if (!current) return "Trip-local time will appear when a source timezone is known.";
  return `Trip time: ${current.date} at ${current.time}`;
}

export default function BookingImportPanel({
  trip,
  reservations,
  savedPlaces,
  disabled = false,
  onTripChanged,
}: {
  trip: TripDetail;
  reservations: Reservation[];
  savedPlaces: SavedPlace[];
  disabled?: boolean;
  onTripChanged: () => Promise<boolean>;
}) {
  const [imports, setImports] = useState<BookingImportSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [review, setReview] = useState<BookingImportReview | null>(null);
  const [decisions, setDecisions] = useState<Record<string, ImportDecision>>({});
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [linkedReservations, setLinkedReservations] = useState<Record<string, string>>({});
  const [retention, setRetention] = useState<SourceRetention>("delete_after_confirmation");
  const [pastedText, setPastedText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [pendingUploadKey, setPendingUploadKey] = useState<string | null>(null);
  const [pendingUploadPayload, setPendingUploadPayload] = useState<PendingUpload | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<ConfirmBookingsInput | null>(null);
  const mutationInFlight = useRef(false);
  const [uploadDefinitelyFailed, setUploadDefinitelyFailed] = useState(false);

  const beginMutation = () => {
    if (mutationInFlight.current) return false;
    mutationInFlight.current = true;
    return true;
  };

  const tripItems = useMemo(
    () => trip.days.flatMap((day) => day.items.map((item) => ({ day, item }))),
    [trip.days],
  );
  const tripPlaces = useMemo(() => {
    const byId = new Map<string, PlaceSummary>();
    for (const saved of savedPlaces) byId.set(saved.place.id, saved.place);
    for (const { item } of tripItems) if (item.place) byId.set(item.place.id, item.place);
    for (const reservation of reservations) if (reservation.place) byId.set(reservation.place.id, reservation.place);
    return [...byId.values()].sort((left, right) => left.name.localeCompare(right.name));
  }, [reservations, savedPlaces, tripItems]);

  const loadImports = useCallback(async () => {
    const loaded = await bookingImportApi.list(trip.id);
    setImports(loaded);
    return loaded;
  }, [trip.id]);

  const openImport = useCallback(async (importId: string) => {
    setPending("open");
    setError(null);
    try {
      const loaded = await bookingImportApi.get(trip.id, importId);
      setReview(loaded);
      setActiveId(importId);
      setDrafts(initialDrafts(loaded.candidates));
      setDecisions(Object.fromEntries(loaded.candidates.map((candidate) => [candidate.candidate_id, "skip"])));
      setLinkedReservations({});
      setPendingConfirm(null);
      setStale(false);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setPending(null);
    }
  }, [trip.id]);

  useEffect(() => {
    let active = true;
    void bookingImportApi.list(trip.id).then((loaded) => {
      if (active) setImports(loaded);
    }).catch((nextError: unknown) => {
      if (active) setError(errorMessage(nextError));
    });
    return () => { active = false; };
  }, [trip.id]);

  const upload = async () => {
    if (pending || disabled || stale || !beginMutation()) return;
    const selectedFile = file;
    const text = selectedFile ? "" : pastedText;
    if (!pendingUploadPayload && !selectedFile && !text.trim()) {
      setError("Paste a booking document or choose a PDF/text file.");
      mutationInFlight.current = false;
      return;
    }
    const isPdf = selectedFile?.type === "application/pdf" || selectedFile?.name.toLowerCase().endsWith(".pdf");
    const isText = selectedFile?.type === "text/plain" || selectedFile?.name.toLowerCase().endsWith(".txt");
    if (!pendingUploadPayload && selectedFile && !isPdf && !isText) {
      setError("Choose a plain text file or a PDF.");
      mutationInFlight.current = false;
      return;
    }
    const uploadPayload = pendingUploadPayload ?? {
      body: selectedFile ?? text,
      mediaType: isPdf ? "application/pdf" as const : "text/plain" as const,
      filename: selectedFile?.name,
      retention,
    };
    const byteLength = typeof uploadPayload.body === "string"
      ? new TextEncoder().encode(uploadPayload.body).length
      : uploadPayload.body.size;
    const maxBytes = uploadPayload.mediaType === "application/pdf" ? 10 * 1024 * 1024 : 1024 * 1024;
    if (byteLength < 1 || byteLength > maxBytes) {
      setError(`This source must be between 1 byte and ${uploadPayload.mediaType === "application/pdf" ? "10 MiB" : "1 MiB"}.`);
      mutationInFlight.current = false;
      return;
    }
    const key = pendingUploadKey ?? crypto.randomUUID();
    setPendingUploadKey(key);
    setPendingUploadPayload(uploadPayload);
    setPending("upload");
    setError(null);
    setNotice(null);
    setUploadDefinitelyFailed(false);
    try {
      const uploaded = await bookingImportApi.upload(
        trip.id,
        uploadPayload.body,
        uploadPayload.mediaType,
        key,
        uploadPayload.retention,
        uploadPayload.filename,
      );
      setPendingUploadKey(null);
      setPendingUploadPayload(null);
      setUploadDefinitelyFailed(false);
      setPastedText("");
      setFile(null);
      await loadImports();
      await openImport(uploaded.id);
      setNotice("The private source is stored for this import. Start extraction when you are ready.");
    } catch (nextError) {
      const definite = isDefinitiveUploadFailure(
        nextError instanceof ApiError ? nextError.status : null,
      );
      setUploadDefinitelyFailed(definite);
      if (definite) {
        setPendingUploadKey(null);
        setPendingUploadPayload(null);
      }
      setError(definite
        ? `${errorMessage(nextError)} This upload was rejected before it was saved; correct the input and submit again.`
        : `${errorMessage(nextError)} If the upload may have reached the server, retry this same upload to recover it safely.`);
    } finally {
      setPending(null);
      mutationInFlight.current = false;
    }
  };

  const extract = async () => {
    if (!activeId || pending || disabled || stale || !beginMutation()) return;
    setPending("extract");
    setError(null);
    try {
      const updated = await bookingImportApi.extract(trip.id, activeId);
      setReview(updated);
      await loadImports();
      if (updated.state === "review_ready") {
        setDrafts(initialDrafts(updated.candidates));
        setDecisions(Object.fromEntries(updated.candidates.map((candidate) => [candidate.candidate_id, "skip"])));
        setNotice("Extraction is ready. Review each candidate and choose create, link, or skip.");
      } else if (updated.outcome_unknown) {
        setNotice("The extraction outcome is being reconciled by its saved key. Check status to continue recovery.");
      }
    } catch (nextError) {
      setError(`${errorMessage(nextError)} Use “Check extraction status” to recover by the saved key.`);
    } finally {
      setPending(null);
      mutationInFlight.current = false;
    }
  };

  const saveCorrections = async () => {
    if (!review || !activeId || pending || disabled || stale || !beginMutation()) return;
    setPending("save-edits");
    setError(null);
    try {
      const edits = review.candidates.map((candidate) => {
        const draft = drafts[candidate.candidate_id] ?? emptyCandidateDraft(candidate);
        return {
          candidate_id: candidate.candidate_id,
          reservation_type: normalizeReservationType(draft.reservation_type),
          reservation_status: draft.reservation_status || null,
          provider_name: draft.provider_name?.trim() || null,
          confirmation_code: draft.confirmation_code?.trim() || null,
          starts_at_date: draft.starts_at_date ?? null,
          starts_at_time: draft.starts_at_time ?? null,
          starts_at_timezone: draft.starts_at_timezone || null,
          ends_at_date: draft.ends_at_date ?? null,
          ends_at_time: draft.ends_at_time ?? null,
          ends_at_timezone: draft.ends_at_timezone || null,
        };
      });
      const updated = await bookingImportApi.saveEdits(
        trip.id,
        activeId,
        review.review_revision,
        edits,
      );
      setReview(updated);
      setDrafts(initialDrafts(updated.candidates));
      setNotice("Corrections saved. The source evidence and original extraction remain visible.");
      await loadImports();
    } catch (nextError) {
      setError(errorMessage(nextError));
      setStale(true);
    } finally {
      setPending(null);
      mutationInFlight.current = false;
    }
  };

  const buildConfirmInput = (): ConfirmBookingsInput | null => {
    if (!review) return null;
    const entries: ConfirmationEntry[] = review.candidates.map((candidate) => {
      const decision = decisions[candidate.candidate_id] ?? "skip";
      if (decision === "skip") return { candidate_id: candidate.candidate_id, decision };
      if (decision === "link_existing") {
        return {
          candidate_id: candidate.candidate_id,
          decision,
          existing_reservation_id: linkedReservations[candidate.candidate_id] ?? null,
          itinerary_item_id: drafts[candidate.candidate_id]?.itinerary_item_id || null,
        };
      }
      const draft = drafts[candidate.candidate_id] ?? emptyCandidateDraft(candidate);
      return {
        candidate_id: candidate.candidate_id,
        decision,
        reservation_type: normalizeReservationType(draft.reservation_type),
        reservation_status: draft.reservation_status || null,
        provider_name: draft.provider_name?.trim() || null,
        confirmation_code: draft.confirmation_code?.trim() || null,
        starts_at_date: draft.starts_at_date || null,
        starts_at_time: draft.starts_at_time || null,
        starts_at_timezone: draft.starts_at_timezone || null,
        ends_at_date: draft.ends_at_date || null,
        ends_at_time: draft.ends_at_time || null,
        ends_at_timezone: draft.ends_at_timezone || null,
        place_id: draft.place_id || null,
        itinerary_item_id: draft.itinerary_item_id || null,
      };
    });
    return {
      confirmation_key: pendingConfirm?.confirmation_key ?? crypto.randomUUID(),
      expected_trip_revision: trip.revision,
      expected_import_revision: review.review_revision,
      entries,
    };
  };

  const confirm = async () => {
    if (!review || !activeId || pending || disabled || stale || !beginMutation()) return;
    const input = buildConfirmInput();
    if (!input || !input.entries.some((entry) => entry.decision !== "skip")) {
      setError("Choose at least one candidate to create or link. You can skip the others.");
      mutationInFlight.current = false;
      return;
    }
    if (input.entries.some((entry) => entry.decision === "link_existing" && !entry.existing_reservation_id)) {
      setError("Choose the existing reservation for each candidate you want to link.");
      mutationInFlight.current = false;
      return;
    }
    for (const entry of input.entries) {
      if (entry.decision !== "create_separate") continue;
      const candidate = review.candidates.find((value) => value.candidate_id === entry.candidate_id);
      const draft = drafts[entry.candidate_id] ?? (candidate ? emptyCandidateDraft(candidate) : null);
      const entryError = candidate && draft ? createEntryError(entry, candidate, draft) : "Choose a candidate to review.";
      if (entryError) {
        setError(entryError);
        mutationInFlight.current = false;
        return;
      }
    }
    setPendingConfirm(input);
    setPending("confirm");
    setError(null);
    setNotice(null);
    try {
      const outcome = await bookingImportApi.confirm(trip.id, activeId, input);
      const loaded = await bookingImportApi.get(trip.id, activeId);
      setReview({ ...loaded, confirmation_outcome: outcome });
      setPendingConfirm(null);
      await loadImports();
      const refreshed = await onTripChanged();
      if (!refreshed) {
        setStale(true);
        setError("Bookings were saved, but the trip refresh failed. Reload the workspace before making another change.");
        return;
      }
      setNotice("The selected bookings were saved to the trip. The original source followed its retention choice.");
    } catch (nextError) {
      try {
        const recovered = await bookingImportApi.get(trip.id, activeId);
        setReview(recovered);
        await loadImports();
        if (recovered.confirmation_outcome) {
          setPendingConfirm(null);
          setNotice("The saved confirmation was recovered. No second confirmation was sent.");
          if (recovered.upstream_delete_pending) {
            setError("The booking outcome was saved, but deletion of the AI result is still pending.");
          }
          if (!await onTripChanged()) setStale(true);
        } else {
          setStale(true);
          setError(`${errorMessage(nextError)} The import has been reloaded and no saved confirmation was found. Reload the trip before deciding whether to retry.`);
        }
      } catch {
        setStale(true);
        setError("The confirmation may have been saved, but its outcome could not be read. Reload the import before retrying.");
      }
    } finally {
      setPending(null);
      mutationInFlight.current = false;
    }
  };

  const reject = async () => {
    if (!activeId || pending || disabled || stale || !beginMutation()) return;
    if (!window.confirm("Reject this extraction and discard its candidate text? The original follows the retention choice shown for this import.")) {
      mutationInFlight.current = false;
      return;
    }
    setPending("reject");
    setError(null);
    try {
      const updated = await bookingImportApi.reject(trip.id, activeId);
      setReview(updated);
      await loadImports();
      setNotice("The extraction was rejected.");
    } catch (nextError) {
      try {
        const recovered = await bookingImportApi.get(trip.id, activeId);
        setReview(recovered);
        await loadImports();
        setError(recovered.upstream_delete_pending
          ? "The rejection was saved, but deletion of the AI result is still pending."
          : recovered.state === "rejected" ? "The rejection was saved." : errorMessage(nextError));
      } catch {
        setStale(true);
        setError("The rejection outcome is unknown. Reload the import before trying again.");
      }
    } finally {
      setPending(null);
      mutationInFlight.current = false;
    }
  };

  const deleteSource = async () => {
    if (!activeId || pending || disabled || !beginMutation()) return;
    if (!window.confirm("Delete the stored original and its retained candidate source excerpts? Saved booking outcomes will remain.")) {
      mutationInFlight.current = false;
      return;
    }
    setPending("delete-source");
    setError(null);
    try {
      await bookingImportApi.deleteSource(trip.id, activeId);
      const updated = await bookingImportApi.get(trip.id, activeId);
      setReview(updated);
      await loadImports();
      setNotice("The original and extracted source excerpts were deleted. Confirmation outcomes remain in the trip history.");
    } catch (nextError) {
      try {
        const recovered = await bookingImportApi.get(trip.id, activeId);
        setReview(recovered);
        await loadImports();
        setError(recovered.upstream_delete_pending
          ? "The local source is deleted. Retry AI result deletion to finish cleanup."
          : errorMessage(nextError));
      } catch {
        setError(errorMessage(nextError));
      }
    } finally {
      setPending(null);
      mutationInFlight.current = false;
    }
  };

  const retryDeletion = async () => {
    if (!activeId || pending || disabled || !beginMutation()) return;
    setPending("retry-deletion");
    setError(null);
    try {
      const result = await bookingImportApi.retryPendingDeletions();
      const updated = await bookingImportApi.get(trip.id, activeId);
      setReview(updated);
      await loadImports();
      setNotice(result.pending === 0
        ? "Pending AI result deletion completed."
        : `AI result deletion is still pending for ${result.pending} import(s).`);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setPending(null);
      mutationInFlight.current = false;
    }
  };

  if (!bookingImportsEnabled) return null;
  const writeDisabled = pending !== null || disabled || stale;

  return (
    <section className="bookingImportPanel" id="booking-imports" aria-labelledby="booking-import-title">
      <div className="sectionHeading">
        <div>
          <p className="eyebrow">PRIVATE BOOKING REVIEW</p>
          <h2 id="booking-import-title">Import a booking document</h2>
          <p className="muted">Extracted suggestions stay separate until you confirm selected changes.</p>
        </div>
      </div>
      {error ? <p className="errorBanner" role="alert">{error}</p> : null}
      {notice ? <p className="successBanner" role="status">{notice}</p> : null}
      <div className="importDisclosure">
        <strong>Before you submit</strong>
        <p>Submitting sends the document’s extracted text from this authenticated travel service to the configured AI service for booking extraction. The model receives the text as untrusted data; it cannot follow links, browse, search, or access memory.</p>
        <p>The original stays in private local source storage. Validated candidate excerpts are retained for up to seven days unless you delete them sooner. Confirmed reservation outcomes remain in the trip.</p>
      </div>
      <div className="importUploadGrid">
        <label>
          Paste booking text
          <textarea
            rows={5}
            maxLength={200_000}
            value={pastedText}
            onChange={(event) => { setPastedText(event.target.value); setFile(null); }}
            disabled={writeDisabled || pendingUploadKey !== null}
            placeholder="Paste a booking confirmation or itinerary excerpt"
          />
        </label>
        <label>
          Or choose a PDF or text file
          <input
            type="file"
            accept="application/pdf,text/plain,.pdf,.txt"
            onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPastedText(""); }}
            disabled={writeDisabled || pendingUploadKey !== null}
          />
        </label>
      </div>
      <fieldset className="retentionChoice" disabled={writeDisabled || pendingUploadKey !== null}>
        <legend>Original source retention</legend>
        <label><input type="radio" name="source-retention" checked={retention === "delete_after_confirmation"} onChange={() => setRetention("delete_after_confirmation")} /> Delete the original after confirmation or rejection (recommended)</label>
        <label><input type="radio" name="source-retention" checked={retention === "keep_until_expiry"} onChange={() => setRetention("keep_until_expiry")} /> Keep the original until its seven-day expiry</label>
      </fieldset>
      {pendingUploadKey ? <p className="muted">Retrying the same upload key to recover its saved import.</p> : null}
      <button className="primary" type="button" onClick={() => void upload()} disabled={writeDisabled}>
        {pending === "upload" ? "Uploading…" : pendingUploadKey ? "Retry same upload" : "Save private source"}
      </button>
      {uploadDefinitelyFailed ? <button className="secondary" type="button" onClick={() => {
        setUploadDefinitelyFailed(false);
        setPastedText("");
        setFile(null);
        setError(null);
      }} disabled={writeDisabled}>Clear rejected upload</button> : null}

      <div className="importList" aria-label="Saved booking imports">
        <h3>Saved imports</h3>
        {imports.length === 0 ? <p className="muted">No booking sources have been submitted for this trip.</p> : imports.map((item) => (
          <button className="importListItem" key={item.id} type="button" onClick={() => void openImport(item.id)} disabled={pending !== null} aria-current={activeId === item.id ? "true" : undefined}>
            <span>{item.display_filename ?? "Pasted booking text"}</span>
            <span className={`badge badge-${item.state}`}>{item.state.replaceAll("_", " ")}</span>
          </button>
        ))}
      </div>

      {review ? (
        <div className="importReview" aria-labelledby="import-review-heading">
          <div className="reviewHeader">
            <div><p className="eyebrow">{review.media_type} · {review.trip_timezone}</p><h3 id="import-review-heading">Review extracted candidates</h3></div>
            <div className="reviewActions">
              {review.source_state === "ready" ? <a className="secondary compact" href={bookingImportApi.sourceUrl(trip.id, review.id)}>Download original</a> : <span className="muted">Original source {review.source_state}</span>}
              {review.source_state === "ready" ? <button className="secondary compact" type="button" onClick={() => void deleteSource()} disabled={writeDisabled}>Delete original and excerpts</button> : null}
              {review.state === "received" || review.state === "extracting" ? <button className="primary compact" type="button" onClick={() => void extract()} disabled={writeDisabled}>{review.outcome_unknown ? "Check extraction status" : pending === "extract" ? "Checking…" : "Extract booking details"}</button> : null}
              {review.upstream_delete_pending ? <button className="secondary compact" type="button" onClick={() => void retryDeletion()} disabled={writeDisabled}>{pending === "retry-deletion" ? "Retrying deletion…" : "Retry AI result deletion"}</button> : null}
            </div>
          </div>
          {review.upstream_delete_pending ? <p className="importRecovery" role="status">The original was deleted locally. Removal of the AI result is still pending; retry deletion to finish cleanup.</p> : null}
          {review.outcome_unknown ? <p className="importRecovery" role="status">The previous request may have completed. Checking status reads by the saved key and never starts another extraction.</p> : null}
          {review.failure_code ? <p className="errorBanner" role="status">Extraction ended with a safe status: {review.failure_code.replaceAll("_", " ")}.</p> : null}
          {review.confirmation_outcome ? <div className="importOutcome" role="status"><strong>Saved confirmation</strong><p>{review.confirmation_outcome.outcomes.map((outcome) => `${outcome.candidate_id}: ${outcome.outcome}`).join(" · ")}</p><p>Trip revision {review.confirmation_outcome.trip_revision}</p></div> : null}
          {review.state === "review_ready" ? (
            <>
              <div className="reviewExpiry">Validated candidates expire {review.upstream_result_expires_at ? new Date(review.upstream_result_expires_at).toLocaleString() : "soon"}. Original retention: {review.retention_choice.replaceAll("_", " ")}.</div>
              {review.duplicate_suggestions.length > 0 ? <div className="duplicateNotice" role="status"><strong>Possible existing reservations</strong><p>These are suggestions based on provider, confirmation reference, or schedule. Nothing will be linked or overwritten unless you choose it below.</p></div> : null}
              {review.candidates.length === 0 ? <p className="muted">No booking candidates were found.</p> : review.candidates.map((candidate) => {
                const draft = drafts[candidate.candidate_id] ?? emptyCandidateDraft(candidate);
                const matches = review.duplicate_suggestions.filter((suggestion) => suggestion.candidate_id === candidate.candidate_id);
                const updateDraft = (patch: Partial<Draft>) => setDrafts((current) => ({
                  ...current,
                  [candidate.candidate_id]: { ...(current[candidate.candidate_id] ?? emptyCandidateDraft(candidate)), ...patch },
                }));
                return (
                  <article className="candidateReviewCard" key={candidate.candidate_id}>
                    <div className="candidateReviewHeading">
                      <div><p className="eyebrow">{candidate.reservation_type ?? "reservation type unclear"}</p><h4>{draft.provider_name || "Provider needs review"}</h4></div>
                      <span className="candidateId">Candidate {candidate.candidate_id.slice(-8)}</span>
                    </div>
                    {candidate.uncertain_fields.length > 0 ? <p className="uncertaintyNotice">Needs review: {candidate.uncertain_fields.map((field) => field.replaceAll("_", " ")).join(", ")}</p> : null}
                    <p className="sourceEvidenceLabel">Source evidence · characters {candidate.source_start}–{candidate.source_end}</p>
                    <blockquote className="sourceEvidence">{candidate.source_excerpt}</blockquote>
                    {matches.length > 0 ? <p className="duplicateSuggestion">Possible duplicate: {matches.map((match) => `${reservations.find((reservation) => reservation.id === match.reservation_id)?.provider_name ?? "existing reservation"} (${match.reason.replaceAll("_", " ")})`).join(", ")}</p> : null}
                    <div className="candidateFields">
                      <label>Action
                        <select value={decisions[candidate.candidate_id] ?? "skip"} onChange={(event) => setDecisions((current) => ({ ...current, [candidate.candidate_id]: event.target.value as ImportDecision }))} disabled={writeDisabled}>
                          <option value="skip">Skip this candidate</option><option value="create_separate">Create a separate reservation</option><option value="link_existing">Link to an existing reservation</option>
                        </select>
                      </label>
                      {decisions[candidate.candidate_id] === "link_existing" ? <>
                        <label>Existing reservation
                          <select value={linkedReservations[candidate.candidate_id] ?? ""} onChange={(event) => setLinkedReservations((current) => ({ ...current, [candidate.candidate_id]: event.target.value }))} disabled={writeDisabled}>
                            <option value="">Choose an existing reservation</option>
                            {reservations.map((reservation) => <option key={reservation.id} value={reservation.id}>{reservation.provider_name}{reservation.confirmation_code ? ` · ${reservation.confirmation_code}` : ""}</option>)}
                          </select>
                        </label>
                        <label>Link itinerary item (optional)
                          <select value={draft.itinerary_item_id} onChange={(event) => updateDraft({ itinerary_item_id: event.target.value })} disabled={writeDisabled}>
                            <option value="">No itinerary link</option>{tripItems.map(({ day, item }) => <option key={item.id} value={item.id}>Day {day.day_index} · {item.title}</option>)}
                          </select>
                        </label>
                      </> : null}
                      {decisions[candidate.candidate_id] === "create_separate" ? <>
                        <label>Reservation status
                          <select value={draft.reservation_status} onChange={(event) => updateDraft({ reservation_status: event.target.value as ReservationStatus })} disabled={writeDisabled}>
                            <option value="">Choose a status</option><option value="tentative">Tentative</option><option value="confirmed">Confirmed</option>
                          </select>
                        </label>
                        <label>Reservation type
                          <select value={normalizeReservationType(draft.reservation_type) ?? ""} onChange={(event) => updateDraft({ reservation_type: (event.target.value || null) as Draft["reservation_type"] })} disabled={writeDisabled}>
                            <option value="">Choose a type</option>{(["flight", "lodging", "train", "car_rental", "activity", "dining", "other"] as ReservationType[]).map((type) => <option key={type} value={type}>{type.replaceAll("_", " ")}</option>)}
                          </select>
                        </label>
                        <label>Provider<input value={draft.provider_name ?? ""} onChange={(event) => updateDraft({ provider_name: event.target.value })} maxLength={200} disabled={writeDisabled} /></label>
                        <label>Confirmation reference<input value={draft.confirmation_code ?? ""} onChange={(event) => updateDraft({ confirmation_code: event.target.value })} maxLength={160} disabled={writeDisabled} /></label>
                        <label>Place on this trip (optional)
                          <select value={draft.place_id} onChange={(event) => updateDraft({ place_id: event.target.value })} disabled={writeDisabled}>
                            <option value="">No place</option>{tripPlaces.map((place) => <option key={place.id} value={place.id}>{place.name}</option>)}
                          </select>
                        </label>
                        <label>Link itinerary item (optional)
                          <select value={draft.itinerary_item_id} onChange={(event) => updateDraft({ itinerary_item_id: event.target.value })} disabled={writeDisabled}>
                            <option value="">No itinerary link</option>{tripItems.map(({ day, item }) => <option key={item.id} value={item.id}>Day {day.day_index} · {item.title}</option>)}
                          </select>
                        </label>
                        <div className="scheduleEdit">
                          <strong>Start schedule</strong>
                          <label>Local date<input type="date" value={draft.starts_at_date ?? ""} onChange={(event) => updateDraft({ starts_at_date: event.target.value || null })} disabled={writeDisabled} /></label>
                          <label>Local time<input type="time" value={draft.starts_at_time ?? ""} onChange={(event) => updateDraft({ starts_at_time: event.target.value || null })} disabled={writeDisabled} /></label>
                          <label>Timezone or UTC offset<input value={draft.starts_at_timezone ?? ""} onChange={(event) => updateDraft({ starts_at_timezone: event.target.value || null })} placeholder="America/New_York or +09:00" disabled={writeDisabled} /></label>
                          <span className="muted">{currentTimeZoneHint(candidate, "starts_at")}</span>
                        </div>
                        <div className="scheduleEdit">
                          <strong>End schedule (optional)</strong>
                          <label>Original date<input type="date" value={draft.ends_at_date ?? ""} onChange={(event) => updateDraft({ ends_at_date: event.target.value || null })} disabled={writeDisabled} /></label>
                          <label>Original time<input type="time" value={draft.ends_at_time ?? ""} onChange={(event) => updateDraft({ ends_at_time: event.target.value || null })} disabled={writeDisabled} /></label>
                          <label>Source timezone or UTC offset<input value={draft.ends_at_timezone ?? ""} onChange={(event) => updateDraft({ ends_at_timezone: event.target.value || null })} placeholder="America/New_York or +09:00" disabled={writeDisabled} /></label>
                          <span className="muted">{currentTimeZoneHint(candidate, "ends_at")}</span>
                        </div>
                        {candidate.uncertain_fields.length > 0 ? <label className="uncertaintyAcknowledgement"><input type="checkbox" checked={draft.acknowledgedUncertainty} onChange={(event) => updateDraft({ acknowledgedUncertainty: event.target.checked })} disabled={writeDisabled} /> I reviewed the uncertain fields above and accept any values I leave unspecified.</label> : null}
                      </> : null}
                    </div>
                  </article>
                );
              })}
              <div className="formActions">
                <button className="secondary" type="button" onClick={() => void saveCorrections()} disabled={writeDisabled || review.candidates.length === 0}>{pending === "save-edits" ? "Saving corrections…" : "Save corrections"}</button>
                <button className="primary" type="button" onClick={() => void confirm()} disabled={writeDisabled || review.candidates.length === 0}>{pending === "confirm" ? "Confirming…" : "Confirm selected actions"}</button>
                <button className="secondary" type="button" onClick={() => void reject()} disabled={writeDisabled}>Reject extraction</button>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
