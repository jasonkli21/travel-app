"use client";

import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";

import {
  type CreateManualSavedPlaceInput,
  type ResearchFreshness,
  type TripDetail,
  type TripResearchResult,
  travelApi,
} from "../lib/api";

import { errorMessage } from "../lib/errors";
import { safeHttpUrl } from "../lib/urls.mjs";
import { researchContextKey } from "../lib/research-context.mjs";

const EMPTY_CANDIDATE = {
  name: "",
  category: "",
  address: "",
  phone: "",
  website_url: "",
  note: "",
};

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function resultStatus(result: TripResearchResult): string {
  switch (result.state) {
    case "completed":
      return "Research is complete. Check the citations and source times before relying on details.";
    case "insufficient":
      return "There was not enough usable evidence to answer this question.";
    case "expired":
      return "This research result has expired and is no longer shown as current.";
    case "failed":
      return `Research could not be completed${result.failure_code ? ` (${result.failure_code})` : ""}.`;
    case "pending":
    case "running":
      return "The research session is still running. No result is available yet; try a new request later.";
  }
}

export default function TripResearchPanel({
  trip,
  pending,
  onSaveCandidate,
}: {
  trip: TripDetail;
  pending: boolean;
  onSaveCandidate: (input: CreateManualSavedPlaceInput) => Promise<boolean>;
}) {
  const [selectedDayId, setDayId] = useState(trip.days[0]?.id ?? "");
  const dayId = trip.days.some((day) => day.id === selectedDayId) ? selectedDayId : trip.days[0]?.id ?? "";
  const [question, setQuestion] = useState("");
  const [freshness, setFreshness] = useState<ResearchFreshness>("current");
  const [researchPending, setResearchPending] = useState(false);
  const researchInFlight = useRef(false);
  const [savePending, setSavePending] = useState(false);
  const [resultSnapshot, setResultSnapshot] = useState<{
    contextKey: string; result: TripResearchResult;
  } | null>(null);
  const contextKey = researchContextKey(trip, dayId);
  const result = resultSnapshot?.contextKey === contextKey ? resultSnapshot.result : null;
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);
  const [candidate, setCandidate] = useState(EMPTY_CANDIDATE);

  const resultExpiry = result?.state === "completed"
    ? Math.min(
        Date.parse(result.expires_at),
        ...result.citations.map((citation) => Date.parse(citation.expires_at)),
      )
    : null;
  const resultExpired = result?.state === "completed"
    && (resultExpiry === null || !Number.isFinite(resultExpiry) || resultExpiry <= now);
  const displayedResult: TripResearchResult | null = resultExpired && result
    ? { ...result, state: "expired", answer: null, citations: [] }
    : result;

  useEffect(() => {
    if (resultExpiry === null || resultExpired) return;
    const maxTimerDelay = 2_147_483_647;
    const delay = Math.min(Math.max(0, resultExpiry - Date.now() + 10), maxTimerDelay);
    const timer = window.setTimeout(() => setNow(Date.now()), delay);
    return () => window.clearTimeout(timer);
  }, [now, result, resultExpired, resultExpiry]);

  const submitResearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!dayId || !question.trim() || researchInFlight.current || pending) return;
    researchInFlight.current = true;
    setResearchPending(true);
    setError(null);
    setResultSnapshot(null);
    try {
      const response = await travelApi.researchTripDay(trip.id, {
        day_id: dayId,
        question: question.trim(),
        freshness,
        idempotency_key: crypto.randomUUID(),
      });
      setNow(Date.now());
      setResultSnapshot({ contextKey, result: response });
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      researchInFlight.current = false;
      setResearchPending(false);
    }
  };

  const saveCandidate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!candidate.name.trim()) return;
    setSavePending(true);
    setError(null);
    try {
      const saved = await onSaveCandidate({
        name: candidate.name.trim(),
        category: candidate.category.trim() || null,
        address: candidate.address.trim() || null,
        phone: candidate.phone.trim() || null,
        website_url: candidate.website_url.trim() || null,
        note: candidate.note.trim() || null,
      });
      if (!saved) {
        setError("The candidate was not saved. Review the trip error and try again.");
        return;
      }
      setCandidate(EMPTY_CANDIDATE);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setSavePending(false);
    }
  };

  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  return (
    <section className="researchSection" id="research" aria-labelledby="research-heading">
      <div className="sectionHeading">
        <div>
          <p className="eyebrow">OPTIONAL AI RESEARCH</p>
          <h2 id="research-heading">Research this trip day</h2>
          <p className="muted">Ask a question, review cited evidence, then decide what belongs in your plan.</p>
        </div>
      </div>

      <p className="researchDisclosure">
        Your question and a short projection of the selected day (trip dates and timezone, day title,
        and up to three item/place labels and times) go to the configured AI service and may be sent
        to its configured search provider. It excludes itinerary notes and reservation details.
        Research can be incomplete or become stale; verify sources before making plans.
      </p>

      <form className="researchForm" onSubmit={submitResearch}>
        <fieldset disabled={researchPending || pending || trip.days.length === 0}>
          <div className="formGrid">
            <label>
              Trip day
              <select
                value={dayId}
                onChange={(event) => {
                  setDayId(event.target.value);
                  setResultSnapshot(null);
                  setError(null);
                }}
                required
              >
                {trip.days.map((day) => (
                  <option key={day.id} value={day.id}>Day {day.day_index} · {day.date}{day.title ? ` · ${day.title}` : ""}</option>
                ))}
              </select>
            </label>
            <label>
              Freshness
              <select
                value={freshness}
                onChange={(event) => {
                  setFreshness(event.target.value as ResearchFreshness);
                  setResultSnapshot(null);
                  setError(null);
                }}
              >
                <option value="current">Current information</option>
                <option value="general">General background</option>
              </select>
            </label>
          </div>
          <label>
            What would you like to research?
            <textarea
              value={question}
              onChange={(event) => {
                setQuestion(event.target.value);
                setResultSnapshot(null);
                setError(null);
              }}
              maxLength={300}
              minLength={1}
              rows={3}
              placeholder="Find a quiet vegetarian dinner near the places on this day"
              required
            />
          </label>
          <div className="formActions">
            <button className="primary" type="submit" disabled={!question.trim() || !dayId}>
              {researchPending ? "Researching…" : "Research day"}
            </button>
            {researchPending ? <span role="status">Research is running. This can take a short while.</span> : null}
          </div>
        </fieldset>
      </form>

      {error ? <p className="formError locationError" role="alert">{error}</p> : null}
      {displayedResult ? (
        <div className="researchResult" aria-live="polite">
          <p className={`researchState researchState-${displayedResult.state}`} role="status">{resultStatus(displayedResult)}</p>
          {displayedResult.state === "completed" && displayedResult.answer && displayedResult.citations.length > 0 ? (
            <>
              <div className="researchAnswer" aria-label="Cited research answer">{displayedResult.answer}</div>
              <h3>Sources</h3>
              <ol className="researchCitations">
                {displayedResult.citations.map((citation) => {
                  const href = safeHttpUrl(citation.url);
                  return (
                    <li key={`${citation.evidence_id}-${citation.number}`}>
                      <div>
                        <strong>[{citation.number}] </strong>
                        {href ? <a href={href} target="_blank" rel="noopener noreferrer">{citation.title || href}</a> : <span>{citation.title || "Invalid source link"}</span>}
                        <span className="researchSourceUrl">{citation.url}</span>
                        <span className="researchSourceTimes">
                          Observed {formatTimestamp(citation.observed_at)} ({timezone});
                          expires {formatTimestamp(citation.expires_at)} ({timezone})
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ol>
              <p className="formHint">Sources are observations with an expiry time. They are not permanent facts in your trip.</p>
              <form className="researchCandidateForm" onSubmit={saveCandidate}>
                <h3>Save a manual place candidate</h3>
                <p className="formHint">Enter the place details yourself. Research text is never parsed into a saved place.</p>
                <fieldset disabled={savePending || pending}>
                  <label>Name<input value={candidate.name} onChange={(event) => setCandidate({ ...candidate, name: event.target.value })} maxLength={240} required /></label>
                  <div className="formGrid">
                    <label>Category<input value={candidate.category} onChange={(event) => setCandidate({ ...candidate, category: event.target.value })} maxLength={120} /></label>
                    <label>Phone<input value={candidate.phone} onChange={(event) => setCandidate({ ...candidate, phone: event.target.value })} maxLength={64} /></label>
                  </div>
                  <label>Address<input value={candidate.address} onChange={(event) => setCandidate({ ...candidate, address: event.target.value })} maxLength={500} /></label>
                  <label>Website<input type="url" value={candidate.website_url} onChange={(event) => setCandidate({ ...candidate, website_url: event.target.value })} maxLength={500} placeholder="https://…" /></label>
                  <label>Candidate note<textarea value={candidate.note} onChange={(event) => setCandidate({ ...candidate, note: event.target.value })} maxLength={1000} rows={2} /></label>
                  <button className="secondary" type="submit" disabled={!candidate.name.trim()}>{savePending ? "Saving…" : "Save candidate"}</button>
                </fieldset>
              </form>
            </>
          ) : null}
          {displayedResult.state === "completed" && (!displayedResult.answer || displayedResult.citations.length === 0) ? (
            <p className="formError" role="alert">A complete cited answer was not returned, so there is nothing to show.</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
