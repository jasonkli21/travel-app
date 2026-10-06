"use client";

import type { FormEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  type Reservation,
  type TripAttachment,
  type TripDetail,
  type TripExportFormat,
  travelApi,
  tripAttachmentsEnabled,
} from "../../lib/api";
import { errorMessage } from "../../lib/errors";

const MEDIA_TYPES = new Set(["text/plain", "application/pdf", "image/jpeg", "image/png"]);

function reservationLabel(reservation: Reservation): string {
  const schedule = reservation.start_date ? ` · ${reservation.start_date}` : "";
  return `${reservation.provider_name} · ${reservation.reservation_type}${schedule}`;
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function TripAttachmentsPanel({
  trip,
  reservations,
  disabled,
  onChanged,
}: {
  trip: TripDetail;
  reservations: Reservation[];
  disabled: boolean;
  onChanged: () => Promise<boolean>;
}) {
  const [attachments, setAttachments] = useState<TripAttachment[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [reservationId, setReservationId] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const requestKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setAttachments(await travelApi.listAttachments(trip.id));
      setError(null);
      return true;
    } catch (nextError) {
      setError(errorMessage(nextError));
      return false;
    }
  }, [trip.id]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh, trip.revision]);

  const upload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setMessage(null);
    if (!file) {
      setError("Choose a document to upload.");
      return;
    }
    if (!MEDIA_TYPES.has(file.type)) {
      setError("Choose UTF-8 text, PDF, JPEG, or PNG.");
      return;
    }
    const limit = file.type === "text/plain" ? 1024 * 1024 : 10 * 1024 * 1024;
    if (file.size < 1 || file.size > limit) {
      setError(`This file exceeds the ${file.type === "text/plain" ? "1 MiB" : "10 MiB"} limit.`);
      return;
    }
    const fingerprint = `${file.name}\0${file.size}\0${file.lastModified}\0${reservationId}`;
    if (requestKey.current?.fingerprint !== fingerprint) {
      requestKey.current = { fingerprint, key: crypto.randomUUID() };
    }
    setPending("upload");
    try {
      await travelApi.uploadAttachment(
        trip.id,
        file,
        trip.revision,
        requestKey.current.key,
        reservationId || null,
      );
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      setReservationId("");
      requestKey.current = null;
      const reloaded = await onChanged();
      const listed = await refresh();
      setMessage(
        reloaded && listed
          ? "Document saved privately with this trip."
          : "Document saved. Reload the trip to refresh its revision and document list.",
      );
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setPending(null);
    }
  };

  const update = async (event: FormEvent<HTMLFormElement>, attachment: TripAttachment) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const displayFilename = String(values.get("display_filename") ?? "").trim() || null;
    const nextReservationId = String(values.get("reservation_id") ?? "") || null;
    setPending(attachment.id);
    setError(null);
    setMessage(null);
    try {
      await travelApi.updateAttachment(
        trip.id,
        attachment.id,
        { display_filename: displayFilename, reservation_id: nextReservationId },
        trip.revision,
      );
      const reloaded = await onChanged();
      await refresh();
      setMessage(reloaded ? "Document details updated." : "Saved. Reload the trip to refresh its revision.");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setPending(null);
    }
  };

  const remove = async (attachment: TripAttachment) => {
    if (!window.confirm(`Permanently delete “${attachment.display_filename}”?`)) return;
    setPending(attachment.id);
    setError(null);
    setMessage(null);
    try {
      await travelApi.deleteAttachment(trip.id, attachment.id, trip.revision);
      const reloaded = await onChanged();
      await refresh();
      setMessage(reloaded ? "Document deleted." : "Deleted. Reload the trip to refresh its revision.");
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setPending(null);
    }
  };

  return (
    <section className="privateAttachmentPanel" id="attachments">
      <div className="sectionHeading">
        <div>
          <p className="eyebrow">PRIVATE TRIP DOCUMENTS</p>
          <h2>Attachments</h2>
          <p className="muted">Files stay in the private store. They are downloaded as attachments and never previewed in the page.</p>
        </div>
      </div>
      <p className="attachmentDisclosure">
        Text is limited to 1 MiB; PDF, JPEG, and PNG are limited to 10 MiB. PDFs are capped at 100 pages and images at 40 megapixels. A trip keeps its documents until you delete them or delete the trip. Deleting a reservation clears its link but keeps the document.
      </p>
      {error ? <p className="formError" role="alert">{error}</p> : null}
      {message ? <p className="successBanner" role="status">{message}</p> : null}
      <form className="attachmentUpload" onSubmit={(event) => void upload(event)}>
        <label>
          Document
          <input
            ref={fileInput}
            type="file"
            accept="text/plain,application/pdf,image/jpeg,image/png"
            disabled={disabled || pending !== null}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              requestKey.current = null;
              setError(null);
            }}
          />
        </label>
        <label>
          Link to reservation <span className="optionalLabel">optional</span>
          <select
            value={reservationId}
            disabled={disabled || pending !== null}
            onChange={(event) => {
              setReservationId(event.target.value);
              requestKey.current = null;
            }}
          >
            <option value="">No reservation link</option>
            {reservations.map((reservation) => (
              <option key={reservation.id} value={reservation.id}>{reservationLabel(reservation)}</option>
            ))}
          </select>
        </label>
        <button className="primary" type="submit" disabled={disabled || pending !== null || !file}>
          {pending === "upload" ? "Uploading…" : "Save document"}
        </button>
      </form>
      {attachments.length === 0 ? <p className="emptyText">No trip documents have been added.</p> : (
        <ul className="attachmentList">
          {attachments.map((attachment) => {
            const linkedReservation = reservations.find((item) => item.id === attachment.reservation_id);
            return (
              <li className="attachmentRow" key={attachment.id}>
                <div className="attachmentFileSummary">
                  <strong>{attachment.display_filename}</strong>
                  <span>{attachment.media_type} · {(attachment.byte_size / (1024 * 1024)).toFixed(2)} MiB</span>
                  {linkedReservation ? <span>Linked to {reservationLabel(linkedReservation)}</span> : null}
                  {attachment.state === "missing" ? <span className="formError">File unavailable; refresh or delete this record.</span> : null}
                  {attachment.download_available ? (
                    <a href={travelApi.attachmentDownloadUrl(trip.id, attachment.id)}>Download document</a>
                  ) : null}
                </div>
                <form className="attachmentEdit" onSubmit={(event) => void update(event, attachment)}>
                  <label>
                    Display label
                    <input name="display_filename" maxLength={120} defaultValue={attachment.display_filename} disabled={disabled || pending !== null} />
                  </label>
                  <label>
                    Reservation link
                    <select name="reservation_id" defaultValue={attachment.reservation_id ?? ""} disabled={disabled || pending !== null}>
                      <option value="">No reservation link</option>
                      {reservations.map((reservation) => (
                        <option key={reservation.id} value={reservation.id}>{reservationLabel(reservation)}</option>
                      ))}
                    </select>
                  </label>
                  <div className="formActions">
                    <button className="secondary compact" type="submit" disabled={disabled || pending !== null || attachment.state !== "ready"}>
                      {pending === attachment.id ? "Saving…" : "Save details"}
                    </button>
                    <button className="danger compact" type="button" onClick={() => void remove(attachment)} disabled={disabled || pending !== null}>
                      Delete
                    </button>
                  </div>
                </form>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function TripExportPanel({ trip }: { trip: TripDetail }) {
  const [format, setFormat] = useState<TripExportFormat>("html");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [includePrivateFields, setIncludePrivateFields] = useState(false);
  const [includeDocuments, setIncludeDocuments] = useState(false);
  const [includeLinkedReservations, setIncludeLinkedReservations] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const generate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (Boolean(startDate) !== Boolean(endDate)) {
      setError("Set both dates for a selected date range, or leave both blank for the whole trip.");
      return;
    }
    if (startDate && endDate && startDate > endDate) {
      setError("The start date must be on or before the end date.");
      return;
    }
    setPending(true);
    setError(null);
    setMessage(null);
    try {
      const artifact = await travelApi.createTripExport(trip.id, {
        format,
        ...(startDate ? { start_date: startDate, end_date: endDate } : {}),
        include_private_fields: includePrivateFields,
        include_documents: includeDocuments && tripAttachmentsEnabled,
        include_linked_reservations: format === "ics" && includeLinkedReservations,
      });
      saveBlob(artifact.blob, artifact.filename);
      const scope = `${startDate || trip.start_date} through ${endDate || trip.end_date}`;
      setMessage(
        `Downloaded ${scope} at trip revision ${artifact.tripRevision ?? trip.revision}; ` +
        `${includePrivateFields ? "private fields included" : "private fields omitted"}; ` +
        `${includeDocuments && tripAttachmentsEnabled ? "documents included" : "documents omitted"}` +
        `${artifact.generatedAt ? ` · generated ${new Date(artifact.generatedAt).toLocaleString()}` : ""}.`,
      );
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="tripExportPanel" id="exports">
      <div className="sectionHeading">
        <div>
          <p className="eyebrow">DOWNLOADABLE SNAPSHOT</p>
          <h2>Travel mode and exports</h2>
          <p className="muted">Create a dated calendar, printable HTML, or versioned JSON copy. Downloaded files work offline and do not sync changes.</p>
        </div>
      </div>
      {error ? <p className="formError" role="alert">{error}</p> : null}
      {message ? <p className="successBanner" role="status">{message}</p> : null}
      <form className="tripExportForm" onSubmit={(event) => void generate(event)}>
        <div className="formGrid">
          <label>
            Snapshot format
            <select value={format} disabled={pending} onChange={(event) => setFormat(event.target.value as TripExportFormat)}>
              <option value="html">Printable static HTML</option>
              <option value="ics">Calendar file (ICS)</option>
              <option value="json">Versioned JSON</option>
            </select>
          </label>
          <label>
            Start date <span className="optionalLabel">optional</span>
            <input type="date" value={startDate} min={trip.start_date} max={trip.end_date} disabled={pending} onChange={(event) => setStartDate(event.target.value)} />
          </label>
          <label>
            End date <span className="optionalLabel">optional</span>
            <input type="date" value={endDate} min={startDate || trip.start_date} max={trip.end_date} disabled={pending} onChange={(event) => setEndDate(event.target.value)} />
          </label>
        </div>
        <label className="attachmentCheck">
          <input type="checkbox" checked={includePrivateFields} disabled={pending} onChange={(event) => setIncludePrivateFields(event.target.checked)} />
          <span>Include private notes, confirmation codes, and source references</span>
        </label>
        {tripAttachmentsEnabled ? (
          <label className="attachmentCheck">
            <input type="checkbox" checked={includeDocuments} disabled={pending} onChange={(event) => setIncludeDocuments(event.target.checked)} />
            <span>Include all ready trip documents in a ZIP bundle</span>
          </label>
        ) : null}
        {format === "ics" ? (
          <label className="attachmentCheck">
            <input type="checkbox" checked={includeLinkedReservations} disabled={pending} onChange={(event) => setIncludeLinkedReservations(event.target.checked)} />
            <span>Also add reservations that are already linked to itinerary events</span>
          </label>
        ) : null}
        <p className="formHint">By default, private fields and documents are omitted. The snapshot records its generation time, timezone, date scope, and trip revision. Calendar imports are not live subscriptions and may not mirror future changes.</p>
        <button className="primary" type="submit" disabled={pending}>
          {pending ? "Preparing snapshot…" : "Download snapshot"}
        </button>
      </form>
    </section>
  );
}
