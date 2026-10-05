"use client";

import type { FormEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import type {
  PlaceSummary,
  SavedPlace,
  SaveTravelComparisonCandidateInput,
  TravelComparisonCandidate,
  TravelComparisonCategory,
  TravelComparisonRequest,
  TravelComparisonResult,
  TripDetail,
} from "../lib/api";
import { travelApi } from "../lib/api";
import { errorMessage } from "../lib/errors";
import { safeHttpUrl } from "../lib/urls.mjs";

const comparisonsEnabled = process.env.NEXT_PUBLIC_TRAVEL_COMPARISONS_ENABLED === "true";

const CATEGORY_LABELS: Record<TravelComparisonCategory, string> = {
  food: "Food",
  activity: "Activities",
  neighborhood: "Neighborhoods",
  day_trip: "Day trips",
};

type CandidateDraft = {
  candidate: TravelComparisonCandidate;
  name: string;
  address: string;
  category: string;
  note: string;
};

function uniqueTripPlaces(trip: TripDetail, savedPlaces: SavedPlace[]): PlaceSummary[] {
  const places = new Map<string, PlaceSummary>();
  for (const day of trip.days) {
    for (const item of day.items) {
      if (item.place?.latitude !== null && item.place?.latitude !== undefined
        && item.place?.longitude !== null && item.place?.longitude !== undefined) {
        places.set(item.place.id, item.place);
      }
    }
  }
  for (const saved of savedPlaces) {
    if (saved.place.latitude !== null && saved.place.longitude !== null) {
      places.set(saved.place.id, saved.place);
    }
  }
  return [...places.values()].sort((left, right) => left.name.localeCompare(right.name));
}

function timestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function stateLabel(result: TravelComparisonResult): string {
  switch (result.state) {
    case "recommended": return "Candidates passed the category and distance checks. Review each source.";
    case "eligible_unranked": return "Some candidates passed the checks, but there was not enough comparable data to rank them.";
    case "research_needed": return "No candidate had enough verified evidence to recommend.";
    case "no_verified_match": return "No verified candidates matched this search.";
    case "expired": return "The source evidence expired. Run a new comparison before relying on it.";
    case "insufficient": return "There was not enough current typed evidence to show candidates safely.";
  }
}

function openMapUrl(candidate: TravelComparisonCandidate): string | null {
  if (candidate.latitude === null || candidate.longitude === null) return null;
  return `https://www.openstreetmap.org/?mlat=${candidate.latitude}&mlon=${candidate.longitude}#map=16/${candidate.latitude}/${candidate.longitude}`;
}

export default function TripComparisonPanel({
  trip,
  savedPlaces,
  pending,
  onSaveCandidate,
  onArrangeCandidate,
}: {
  trip: TripDetail;
  savedPlaces: SavedPlace[];
  pending: boolean;
  onSaveCandidate: (
    comparisonId: string,
    candidateId: string,
    input: SaveTravelComparisonCandidateInput,
  ) => Promise<boolean>;
  onArrangeCandidate: (name: string) => void;
}) {
  const referencePlaces = useMemo(() => uniqueTripPlaces(trip, savedPlaces), [trip, savedPlaces]);
  const [category, setCategory] = useState<TravelComparisonCategory>("food");
  const [query, setQuery] = useState("");
  const [referencePlaceId, setReferencePlaceId] = useState(referencePlaces[0]?.id ?? "");
  const selectedReference = referencePlaces.find((place) => place.id === referencePlaceId)
    ?? referencePlaces[0] ?? null;
  const [radius, setRadius] = useState("5");
  const [maxResults, setMaxResults] = useState("8");
  const [requestPending, setRequestPending] = useState(false);
  const requestInFlight = useRef(false);
  const [retryRequest, setRetryRequest] = useState<TravelComparisonRequest | null>(null);
  const [result, setResult] = useState<TravelComparisonResult | null>(null);
  const [candidateDraft, setCandidateDraft] = useState<CandidateDraft | null>(null);
  const [savedCandidateName, setSavedCandidateName] = useState<string | null>(null);
  const [savingCandidate, setSavingCandidate] = useState(false);
  const [savedCandidateIds, setSavedCandidateIds] = useState<string[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);

  const contextStale = result !== null && (
    trip.revision !== result.trip_revision
    || selectedReference?.id !== result.reference_place_id
    || selectedReference?.revision !== result.reference_place_revision
  );
  const resultExpired = result?.expires_at !== null && result?.expires_at !== undefined
    && Date.parse(result.expires_at) <= now;

  useEffect(() => {
    if (!result?.expires_at || resultExpired) return;
    const delay = Math.max(0, Date.parse(result.expires_at) - Date.now() + 1);
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(delay, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [result?.expires_at, resultExpired]);

  const resetResult = () => {
    setResult(null);
    setRetryRequest(null);
    setSavedCandidateIds([]);
    setCandidateDraft(null);
    setError(null);
  };

  const runComparison = async (request: TravelComparisonRequest) => {
    if (requestInFlight.current || pending) return;
    requestInFlight.current = true;
    setRequestPending(true);
    setError(null);
    setRetryRequest(request);
    setResult(null);
    try {
      const nextResult = await travelApi.compareTripPlaces(trip.id, request);
      setNow(Date.now());
      setResult(nextResult);
      setRetryRequest(null);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      requestInFlight.current = false;
      setRequestPending(false);
    }
  };

  const submitComparison = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedReference || !query.trim()) return;
    await runComparison({
      category,
      query: query.trim(),
      reference_place_id: selectedReference.id,
      radius_km: Number(radius),
      max_results: Number(maxResults),
      idempotency_key: crypto.randomUUID(),
    });
  };

  const saveCandidate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!result || !candidateDraft || candidateDraft.candidate.latitude === null
      || candidateDraft.candidate.longitude === null) return;
    setSavingCandidate(true);
    setError(null);
    const saved = await onSaveCandidate(result.comparison_id, candidateDraft.candidate.candidate_id, {
      name: candidateDraft.name.trim(),
      address: candidateDraft.address.trim() || null,
      category: candidateDraft.category.trim() || null,
      note: candidateDraft.note.trim() || null,
      trip_revision: result.trip_revision,
      reference_place_id: result.reference_place_id,
      reference_place_revision: result.reference_place_revision,
    });
    if (saved) {
      setSavedCandidateName(candidateDraft.name.trim());
      setSavedCandidateIds((current) => [...new Set([...current, candidateDraft.candidate.candidate_id])]);
      setCandidateDraft(null);
    } else {
      setError("The candidate was not confirmed as saved. Review the trip message before retrying.");
    }
    setSavingCandidate(false);
  };

  if (!comparisonsEnabled) {
    return (
      <section className="comparisonSection" id="compare" aria-labelledby="comparison-heading">
        <div className="sectionHeading">
          <div><p className="eyebrow">OPTIONAL EVIDENCE COMPARISON</p><h2 id="comparison-heading">Compare nearby places</h2></div>
        </div>
        <p className="muted">Travel comparison is disabled in this web build. The travel and personal AI service gates must be enabled separately.</p>
      </section>
    );
  }

  return (
    <section className="comparisonSection" id="compare" aria-labelledby="comparison-heading">
      <div className="sectionHeading">
        <div>
          <p className="eyebrow">OPTIONAL EVIDENCE COMPARISON</p>
          <h2 id="comparison-heading">Compare nearby places</h2>
          <p className="muted">Compare sourced place leads for one category. This does not check bookings or availability.</p>
        </div>
      </div>

      <p className="comparisonDisclosure">
        The category, search text, and selected place coordinates go to the configured AI service and its
        place provider. Trip IDs, titles, itinerary content, reservation details, and notes are excluded.
        Memory preferences are not retrieved. Results can lack hours, prices, accessibility, availability,
        or date suitability; confirm those details with each source.
      </p>

      {referencePlaces.length === 0 ? (
        <p className="emptyText">Add a place with coordinates to this trip before comparing a distance-scoped category.</p>
      ) : (
        <form className="comparisonForm" onSubmit={submitComparison}>
          <fieldset disabled={requestPending || pending || savingCandidate}>
            <div className="formGrid">
              <label>
                Category
                <select value={category} onChange={(event) => { setCategory(event.target.value as TravelComparisonCategory); resetResult(); }}>
                  {(Object.keys(CATEGORY_LABELS) as TravelComparisonCategory[]).map((value) => (
                    <option key={value} value={value}>{CATEGORY_LABELS[value]}</option>
                  ))}
                </select>
              </label>
              <label>
                Center on a trip place
                <select value={selectedReference?.id ?? ""} onChange={(event) => { setReferencePlaceId(event.target.value); resetResult(); }} required>
                  {referencePlaces.map((place) => <option key={place.id} value={place.id}>{place.name}</option>)}
                </select>
              </label>
            </div>
            <div className="formGrid">
              <label>
                Search area or terms
                <input value={query} onChange={(event) => { setQuery(event.target.value); resetResult(); }} maxLength={180} minLength={1} placeholder="sushi, parks, historic district" required />
              </label>
              <label>
                Maximum distance (km)
                <input type="number" value={radius} onChange={(event) => { setRadius(event.target.value); resetResult(); }} min="0.1" max="20" step="0.5" required />
              </label>
            </div>
            <label className="comparisonLimit">
              Maximum candidates
              <select value={maxResults} onChange={(event) => { setMaxResults(event.target.value); resetResult(); }}>
                {[4, 6, 8, 10].map((value) => <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
            <div className="formActions">
              <button className="primary" type="submit" disabled={!selectedReference || !query.trim()}>
                {requestPending ? "Comparing…" : "Compare places"}
              </button>
              {requestPending ? <span role="status">Checking bounded place evidence…</span> : null}
              {retryRequest ? <button className="secondary" type="button" onClick={() => void runComparison(retryRequest)} disabled={pending || requestPending}>Retry same comparison</button> : null}
            </div>
          </fieldset>
        </form>
      )}

      {error ? <p className="formError locationError" role="alert">{error}</p> : null}
      {result && contextStale ? <p className="comparisonState" role="status">Trip or reference-place details changed. Run a new comparison to refresh the result.</p> : null}
      {result && resultExpired ? <p className="comparisonState" role="status">The earliest source observation expired. Run a new comparison to refresh the result.</p> : null}
      {savedCandidateName ? (
        <div className="comparisonSavedActions" role="status">
          <span>{savedCandidateName} was saved to this trip’s candidates.</span>
          <button className="secondary" type="button" onClick={() => onArrangeCandidate(savedCandidateName)} disabled={pending}>
            Draft itinerary proposal
          </button>
        </div>
      ) : null}
      {result && !contextStale && !resultExpired ? (
        <div className="comparisonResults" aria-live="polite">
          <p className="comparisonState" role="status">{stateLabel(result)}</p>
          <p className="comparisonMeta">Centered on {result.reference_place_name} · maximum {result.radius_km} km · compared {timestamp(result.generated_at)}{result.expires_at ? ` · evidence expires ${timestamp(result.expires_at)}` : ""}</p>
          <p className="formHint">Candidates are leads, not confirmed hours, prices, access, availability, or reservations. Unknown details remain unknown.</p>
          {result.candidates.length === 0 ? <p className="emptyText">No source-backed candidates are available for this request.</p> : (
            <ol className="comparisonCandidateList">
              {result.candidates.map((candidate) => {
                const osmSource = candidate.sources.find((source) => source.provider === "osm_nominatim");
                const mapUrl = osmSource ? openMapUrl(candidate) : null;
                const saved = savedCandidateIds.includes(candidate.candidate_id);
                return (
                  <li className="comparisonCandidate" key={candidate.candidate_id}>
                    <div className="comparisonCandidateHeading">
                      <div>
                        <p className="eyebrow">{candidate.eligible ? `Candidate${candidate.rank ? ` · rank ${candidate.rank}` : ""}` : "Does not meet all requirements"}</p>
                        <h3>{candidate.name}</h3>
                        {candidate.address ? <p className="muted">{candidate.address}</p> : null}
                      </div>
                      <span className={candidate.eligible ? "comparisonPill" : "comparisonPill comparisonPill-warning"}>
                        {candidate.place_type ?? "Place type unknown"}
                      </span>
                    </div>
                    <ul className="comparisonChecks">
                      {candidate.constraints.map((constraint) => (
                        <li key={constraint.name} data-outcome={constraint.outcome}>
                          <strong>{constraint.label}:</strong> {constraint.detail}
                        </li>
                      ))}
                    </ul>
                    {candidate.exclusion_reasons.length > 0 ? (
                      <ul className="comparisonReasons">{candidate.exclusion_reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason.replaceAll("_", " ")}</li>)}</ul>
                    ) : null}
                    {candidate.sources.length > 0 ? (
                      <ul className="comparisonSources">
                        {candidate.sources.map((source) => {
                          const href = safeHttpUrl(source.url);
                          const policy = safeHttpUrl(source.policy_url);
                          return (
                            <li key={source.evidence_id}>
                              <div>
                                <strong>{source.provider === "osm_nominatim" ? "OpenStreetMap / Nominatim" : "Synthetic provider fixture"}</strong>
                                {href ? <a href={href} target="_blank" rel="noopener noreferrer">{source.title || "Open source"}</a> : <span>Source link unavailable</span>}
                                <span>{source.attribution}{policy ? <> · <a href={policy} target="_blank" rel="noopener noreferrer">Source policy</a></> : null}</span>
                                <small>Observed {timestamp(source.observed_at)} · expires {timestamp(source.expires_at)}</small>
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    ) : null}
                    {mapUrl ? <a className="comparisonMapLink" href={mapUrl} target="_blank" rel="noopener noreferrer">View location on OpenStreetMap</a> : null}
                    {saved ? (
                      <div className="comparisonSavedActions" role="status">
                        <span>Saved to this trip’s candidates.</span>
                        <button className="secondary" type="button" onClick={() => onArrangeCandidate(candidate.name)} disabled={pending}>
                          Draft itinerary proposal
                        </button>
                      </div>
                    ) : candidate.eligible && osmSource ? (
                      <button
                        className="secondary"
                        type="button"
                        onClick={() => setCandidateDraft({
                          candidate,
                          name: candidate.name,
                          address: candidate.address ?? "",
                          category: candidate.place_type ?? "",
                          note: "",
                        })}
                        disabled={pending || savingCandidate || resultExpired}
                      >Review and save candidate</button>
                    ) : candidate.eligible ? (
                      <p className="formHint">Synthetic results support interface checks but cannot be saved as real places.</p>
                    ) : null}
                    {candidateDraft?.candidate.candidate_id === candidate.candidate_id ? (
                      <form className="comparisonSaveForm" onSubmit={saveCandidate}>
                        <h4>Review permanent place fields</h4>
                        <p className="formHint">Saving is a separate action. OpenStreetMap attribution and source identity will be retained; current hours and availability will not.</p>
                        <fieldset disabled={savingCandidate || pending}>
                          <label>Name<input value={candidateDraft.name} onChange={(event) => setCandidateDraft({ ...candidateDraft, name: event.target.value })} maxLength={240} required /></label>
                          <label>Address<input value={candidateDraft.address} onChange={(event) => setCandidateDraft({ ...candidateDraft, address: event.target.value })} maxLength={500} /></label>
                          <label>Place type<input value={candidateDraft.category} onChange={(event) => setCandidateDraft({ ...candidateDraft, category: event.target.value })} maxLength={120} /></label>
                          <label>Trip note<textarea value={candidateDraft.note} onChange={(event) => setCandidateDraft({ ...candidateDraft, note: event.target.value })} maxLength={1000} rows={2} /></label>
                          <div className="formActions">
                            <button className="primary" type="submit" disabled={!candidateDraft.name.trim()}>{savingCandidate ? "Saving…" : "Save reviewed candidate"}</button>
                            <button className="secondary" type="button" onClick={() => setCandidateDraft(null)} disabled={savingCandidate}>Cancel</button>
                          </div>
                        </fieldset>
                      </form>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      ) : null}
    </section>
  );
}
