"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  type CreateManualSavedPlaceInput,
  type CreatePlaceInput,
  type CreateReservationInput,
  type LogisticsEstimate,
  type LogisticsMode,
  type PlaceSummary,
  type PlaceSearchResult,
  type Reservation,
  type SavedPlace,
  type SaveTravelComparisonCandidateInput,
  type TripDetail,
  type UpdatePlaceInput,
  type UpdateReservationInput,
  type UpdateTripInput,
  travelApi,
  tripAttachmentsEnabled,
} from "../lib/api";
import TripResearchPanel from "./trip-research-panel";
import TripComparisonPanel from "./trip-comparison-panel";
import ProposalPanel from "./trip-workspace/proposal-panel";
import BookingImportPanel from "./trip-workspace/booking-import-panel";
import { errorMessage } from "../lib/errors";
import { uncertainMutationError } from "../lib/mutation-outcome.mjs";
import { revisionConflictRecovery } from "../lib/revision-conflict.mjs";
import { safeHttpUrl } from "../lib/urls.mjs";
import ReservationForm from "./trip-workspace/reservation-form";
import PlaceForm from "./trip-workspace/place-form";
import SavedPlaceNoteForm from "./trip-workspace/saved-place-note-form";
import { TripAttachmentsPanel, TripExportPanel } from "./trip-workspace/phase-eight-panels";
import PlaceAttribution from "./trip-workspace/place-attribution";
import ItinerarySection from "./trip-workspace/itinerary-section";
import PlaceMapSection from "./trip-workspace/place-map-section";
import TripOverviewSection from "./trip-workspace/trip-overview-section";

function reservationSchedule(reservation: Reservation): string {
  if (!reservation.start_date || !reservation.start_time) return "No schedule yet";
  const start = `${reservation.start_date} ${reservation.start_time}`;
  if (!reservation.end_date || !reservation.end_time) return start;
  return `${start} → ${reservation.end_date} ${reservation.end_time}`;
}

export default function TripWorkspace({ tripId }: { tripId: string }) {
  const router = useRouter();
  const [trip, setTrip] = useState<TripDetail | null>(null);
  const [places, setPlaces] = useState<PlaceSummary[]>([]);
  const [reservations, setReservations] = useState<Reservation[]>([]);
  const [savedPlaces, setSavedPlaces] = useState<SavedPlace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mutationInFlight = useRef(false);
  const [stale, setStale] = useState(false);
  const [editorGeneration, setEditorGeneration] = useState(0);
  const [workspaceSnapshotVersion, setWorkspaceSnapshotVersion] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  const [editingReservation, setEditingReservation] = useState<string | null>(null);
  const [addingReservation, setAddingReservation] = useState(false);
  const [editingPlace, setEditingPlace] = useState<string | null>(null);
  const [showPlaceForm, setShowPlaceForm] = useState(false);
  const [proposalSuggestion, setProposalSuggestion] = useState<{ key: string; instruction: string } | null>(null);

  const refresh = useCallback(async (
    { resetDrafts = false }: { resetDrafts?: boolean } = {},
  ): Promise<boolean> => {
    setError(null);
    let nextTrip: TripDetail;
    try {
      nextTrip = await travelApi.getTrip(tripId);
    } catch (nextError) {
      setError(`${errorMessage(nextError)} Reloading clears open editors.`);
      setLoading(false);
      setStale(true);
      return false;
    }

    try {
      const [nextPlaces, nextReservations, nextSavedPlaces] = await Promise.all([
        travelApi.listPlaces(),
        travelApi.listReservations(tripId),
        travelApi.listSavedPlaces(tripId),
      ]);
      setTrip(nextTrip);
      setWorkspaceSnapshotVersion((current) => current + 1);
      setPlaces(nextPlaces);
      setReservations(nextReservations);
      setSavedPlaces(nextSavedPlaces);
      if (resetDrafts) {
        // The user chose recovery after a stale or ambiguous write. Discard
        // every open editor draft before re-enabling writes against this snapshot.
        setEditingReservation(null);
        setAddingReservation(false);
        setEditingPlace(null);
        setShowPlaceForm(false);
        setEditorGeneration((current) => current + 1);
      }
    } catch (nextError) {
      setStale(true);
      setError(`The workspace could not be refreshed: ${errorMessage(nextError)} Reloading clears open editors.`);
      setLoading(false);
      return false;
    }
    setStale(false);
    setLoading(false);
    return true;
  }, [tripId]);

  useEffect(() => {
    void Promise.resolve().then(() => refresh());
  }, [refresh]);

  const reloadWorkspace = () => {
    void refresh({ resetDrafts: true });
  };

  const setProposalPending = (isPending: boolean) => {
    mutationInFlight.current = isPending;
    setPending(isPending ? "proposal" : null);
  };

  const run = async (
    key: string,
    operation: () => Promise<void>,
    options: { refreshAfter?: boolean } = {},
  ): Promise<boolean> => {
    if (mutationInFlight.current || stale) return false;
    mutationInFlight.current = true;
    setPending(key);
    setError(null);
    try {
      await operation();
      if (options.refreshAfter !== false && !await refresh()) {
        setError("The change was saved, but the workspace could not be refreshed. Reload before editing again. Reloading clears open editors.");
      }
      // A committed write succeeded even if its follow-up read failed. Keeping
      // the create form open invites duplicate reservations/items on retry.
      return true;
    } catch (nextError) {
      mutationError(nextError);
      return false;
    } finally {
      mutationInFlight.current = false;
      setPending(null);
    }
  };

  const mutationError = (nextError: unknown) => {
    const revisionRecovery = revisionConflictRecovery(nextError);
    if (revisionRecovery?.requiresReload) {
      setStale(true);
      setError(revisionRecovery.message);
      return;
    }
    if (uncertainMutationError(nextError)) {
      setStale(true);
      setError("The change could not be confirmed. Reload and check the workspace before submitting again. Reloading clears open editors.");
    } else {
      setError(errorMessage(nextError));
    }
  };

  const createPlace = async (name: string) => {
    if (mutationInFlight.current || stale) throw new Error("A workspace change is already pending.");
    mutationInFlight.current = true;
    setPending("quick-place");
    try {
      const place = await travelApi.createPlace({ name });
      setPlaces((current) => [...current, place].sort((left, right) => left.name.localeCompare(right.name)));
      return place;
    } catch (nextError) {
      mutationError(nextError);
      throw nextError;
    } finally {
      mutationInFlight.current = false;
      setPending(null);
    }
  };

  const importSearchResult = async (result: PlaceSearchResult, onCommitted: () => void) => {
    if (mutationInFlight.current || stale || !trip) return;
    mutationInFlight.current = true;
    setPending(`provider-import-${result.provider_place_id}`);
    try {
      await travelApi.importPlace(tripId, {
        provider_place_id: result.provider_place_id,
        name: result.name,
        address: result.address,
        category: result.category,
        latitude: result.latitude,
        longitude: result.longitude,
        provider_source_name: result.provider_source_name,
        provider_source_attribution: result.provider_source_attribution,
        provider_source_license: result.provider_source_license,
        provider_source_url: result.provider_source_url,
        note: null,
      }, trip.revision);
      onCommitted();
      await refresh();
    } catch (nextError) {
      mutationError(nextError);
      throw nextError;
    } finally {
      mutationInFlight.current = false;
      setPending(null);
    }
  };

  const estimateDayLogistics = async (
    input: { day_id: string; mode: LogisticsMode; buffer_minutes: number },
  ): Promise<LogisticsEstimate | null> => {
    if (mutationInFlight.current || stale) return null;
    mutationInFlight.current = true;
    setPending("logistics-estimate");
    try {
      return await travelApi.estimateLogistics(tripId, input);
    } finally {
      mutationInFlight.current = false;
      setPending(null);
    }
  };

  const deleteTrip = () => {
    if (!trip) return;
    if (!window.confirm("Permanently delete this trip, reservations, candidates, and itinerary items?")) return;
    void run("trip-delete", async () => {
      await travelApi.deleteTrip(tripId, trip.revision);
      router.push("/");
    }, { refreshAfter: false });
  };

  if (loading) return <main className="centerState"><p>Loading trip workspace…</p></main>;
  if (!trip) return <main className="centerState"><p className="formError" role="alert">{error ?? "Trip not found."}</p><Link className="primary linkButton" href="/">Back to trips</Link></main>;

  const saveTrip = (input: UpdateTripInput, onCommitted: () => void) => {
    void run("trip", async () => {
      await travelApi.updateTrip(tripId, input, trip.revision);
      onCommitted();
    });
  };

  const confirmedReservations = reservations.filter((reservation) => reservation.status === "confirmed").length;
  const tentativeReservations = reservations.filter((reservation) => reservation.status === "tentative").length;
  const conflictCount = reservations.reduce((total, reservation) => total + reservation.conflicts.length, 0);
  const savedPlaceIds = new Set(savedPlaces.map((savedPlace) => savedPlace.place.id));

  const saveReservation = async (input: CreateReservationInput | UpdateReservationInput, reservationId?: string) => {
    const saved = await run(`reservation-${reservationId ?? "new"}`, async () => {
      if (reservationId) {
        await travelApi.updateReservation(tripId, reservationId, input, trip.revision);
      } else {
        await travelApi.createReservation(tripId, input as CreateReservationInput, trip.revision);
      }
    });
    if (saved) {
      setAddingReservation(false);
      setEditingReservation(null);
    }
    return saved;
  };

  const savePlace = async (input: CreatePlaceInput | UpdatePlaceInput, placeId?: string) => {
    const saved = await run(`place-${placeId ?? "new"}`, async () => {
      if (placeId) {
        const place = places.find((candidate) => candidate.id === placeId);
        if (!place) throw new Error("The place is no longer available in this workspace.");
        await travelApi.updatePlace(placeId, input as UpdatePlaceInput, place.revision);
      }
      else await travelApi.createPlace(input as CreatePlaceInput);
    });
    if (saved) {
      setShowPlaceForm(false);
      setEditingPlace(null);
    }
    return saved;
  };

  const saveResearchCandidate = async (input: CreateManualSavedPlaceInput) => {
    const saved = await run(
      "research-candidate",
      async () => {
        await travelApi.createManualSavedPlace(trip.id, input, trip.revision);
      },
    );
    return saved;
  };

  const saveComparedCandidate = async (
    comparisonId: string,
    candidateId: string,
    input: SaveTravelComparisonCandidateInput,
  ) => run("comparison-candidate", async () => {
    await travelApi.saveTravelComparisonCandidate(
      trip.id,
      comparisonId,
      candidateId,
      input,
      trip.revision,
    );
  });

  const arrangeComparedCandidate = (name: string) => {
    const instruction = `Add ${name} as an itinerary item on a suitable free day. Keep confirmed or booked anchors in place.`;
    setProposalSuggestion({ key: crypto.randomUUID(), instruction });
    document.getElementById("proposals")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const workspacePlaceFootprint = places
    .map((place) => `${place.id}:${place.revision}`)
    .sort()
    .join("|");

  return (
    <main className="shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">PERSONAL TRAVEL</p>
          <Link className="backLink" href="/">← All trips</Link>
          <h1>{trip.title}</h1>
          <p className="muted">{trip.start_date} → {trip.end_date}</p>
        </div>
        <nav className="nav" aria-label="Trip navigation">
          <a href="#overview">Overview</a>
          <a className="active" href="#itinerary">Itinerary</a>
          <a href="#map">Map</a>
          <a href="#research">Research</a>
          <a href="#compare">Compare places</a>
          <a href="#proposals">Proposals</a>
          <a href="#reservations">Reservations</a>
          <a href="#saved-places">Saved places</a>
          {tripAttachmentsEnabled ? <a href="#attachments">Documents</a> : null}
          <a href="#exports">Travel exports</a>
          <Link href={`/trips/${trip.id}/travel`}>Travel mode</Link>
        </nav>
        <div className="status"><span className="dot" />Local PostgreSQL planner</div>
      </aside>

      <section className="content" id="overview">
        <TripOverviewSection
          key={editorGeneration}
          trip={trip}
          pending={pending !== null || stale}
          saving={pending === "trip"}
          error={error}
          stale={stale}
          confirmedReservations={confirmedReservations}
          tentativeReservations={tentativeReservations}
          savedPlaceCount={savedPlaces.length}
          conflictCount={conflictCount}
          onUpdateTrip={saveTrip}
          onDeleteTrip={deleteTrip}
          onReload={reloadWorkspace}
        />

        <PlaceMapSection
          trip={trip}
          places={places}
          reservations={reservations}
          savedPlaces={savedPlaces}
          pending={pending}
          stale={stale}
          snapshotVersion={workspaceSnapshotVersion}
          onImportSearchResult={importSearchResult}
          onEstimateLogistics={estimateDayLogistics}
        />

        <TripResearchPanel
          key={editorGeneration}
          trip={trip}
          pending={(pending !== null || stale)}
          onSaveCandidate={saveResearchCandidate}
        />

        <TripComparisonPanel
          key={`${editorGeneration}-${trip.id}`}
          trip={trip}
          savedPlaces={savedPlaces}
          reservations={reservations}
          pending={(pending !== null || stale)}
          onSaveCandidate={saveComparedCandidate}
          onArrangeCandidate={arrangeComparedCandidate}
        />

        <ProposalPanel
          trip={trip}
          workspacePlaceFootprint={workspacePlaceFootprint}
          busy={pending !== null}
          disabled={stale}
          onPendingChange={setProposalPending}
          onCommitted={() => refresh({ resetDrafts: true })}
          suggestedInstruction={proposalSuggestion}
        />

        <BookingImportPanel
          trip={trip}
          reservations={reservations}
          savedPlaces={savedPlaces}
          disabled={pending !== null || stale}
          onTripChanged={() => refresh({ resetDrafts: true })}
        />

        {tripAttachmentsEnabled ? (
          <TripAttachmentsPanel
            trip={trip}
            reservations={reservations}
            disabled={pending !== null || stale}
            onChanged={() => refresh()}
            onRecoveryRequired={(message) => {
              setStale(true);
              setError(`${message} Reload the workspace before continuing. Reloading clears open editors.`);
            }}
          />
        ) : null}
        <TripExportPanel trip={trip} />

        <ItinerarySection
          key={editorGeneration}
          trip={trip}
          places={places}
          reservations={reservations}
          pending={pending}
          stale={stale}
          editorGeneration={editorGeneration}
          runMutation={run}
          onCreatePlace={createPlace}
        />

        <section className="reservationSection" id="reservations">
          <div className="sectionHeading">
            <div><p className="eyebrow">BOOKED ANCHORS</p><h2>Reservations</h2><p className="muted">Tentative and confirmed bookings stay separate from optional saved-place candidates.</p></div>
            <button className="primary" type="button" onClick={() => { setAddingReservation((current) => !current); setEditingReservation(null); }} disabled={(pending !== null || stale)}>{addingReservation ? "Close form" : "+ Add reservation"}</button>
          </div>
          {addingReservation ? <ReservationForm key={`${editorGeneration}-new`} places={places} pending={pending === "reservation-new"} disabled={(pending !== null || stale)} onSubmit={(input) => saveReservation(input)} onCancel={() => setAddingReservation(false)} /> : null}
          <div className="reservationList">
            {reservations.length === 0 ? <div className="emptyPanel"><h3>No reservations yet</h3><p className="muted">Add a confirmed anchor or a tentative booking when you have one.</p></div> : reservations.map((reservation) => (
              <article className="reservationCard" key={reservation.id}>
                <div className="reservationHeader">
                  <div><p className="date">{reservation.reservation_type.replace("_", " ")}</p><h3>{reservation.provider_name}</h3></div>
                  <div className="reservationBadges"><span className={`badge badge-${reservation.status}`}>{reservation.status}</span>{reservation.confirmation_code ? <span className="confirmationBadge">{reservation.confirmation_code}</span> : null}</div>
                </div>
                <p className="reservationSchedule">{reservationSchedule(reservation)}</p>
                <div className="reservationDetails">
                  {reservation.place ? <span>Place: {reservation.place.name}</span> : null}
                  {reservation.place ? <PlaceAttribution place={reservation.place} /> : null}
                  {reservation.source_reference ? (safeHttpUrl(reservation.source_reference) ? <a href={safeHttpUrl(reservation.source_reference)!} target="_blank" rel="noreferrer">Open source ↗</a> : <span>{reservation.source_reference}</span>) : null}
                </div>
                {reservation.notes ? <p className="itemNotes">{reservation.notes}</p> : null}
                {reservation.linked_items.length > 0 ? <p className="reservationContext"><strong>Linked itinerary:</strong> {reservation.linked_items.map((item) => `Day ${item.day_index} · ${item.title}`).join("; ")}</p> : <p className="reservationContext muted">Not linked to an itinerary item yet.</p>}
                {reservation.conflicts.length > 0 ? <div className="conflictBox" role="alert"><strong>{reservation.conflicts.length} scheduling {reservation.conflicts.length === 1 ? "conflict" : "conflicts"}</strong><ul>{reservation.conflicts.map((conflict) => <li key={conflict.item_id}>Day {conflict.day_index} · {conflict.title}{conflict.start_time ? ` (${conflict.start_time}${conflict.end_time ? `–${conflict.end_time}` : ""})` : ""}</li>)}</ul><p>{reservation.conflicts[0].reason} Review the plan before applying any change.</p></div> : null}
                <div className="formActions">
                  <button className="secondary compact" type="button" onClick={() => { setEditingReservation((current) => current === reservation.id ? null : reservation.id); setAddingReservation(false); }} disabled={(pending !== null || stale)}>{editingReservation === reservation.id ? "Close editor" : "Edit reservation"}</button>
                  <button className="iconButton dangerText" type="button" onClick={() => { if (window.confirm(`Permanently delete the ${reservation.provider_name} reservation?`)) void run(`delete-reservation-${reservation.id}`, async () => { await travelApi.deleteReservation(trip.id, reservation.id, trip.revision); }); }} disabled={(pending !== null || stale)}>Delete</button>
                </div>
                {editingReservation === reservation.id ? <ReservationForm key={`${editorGeneration}-${reservation.id}-${reservation.updated_at}`} places={places} initial={reservation} pending={pending === `reservation-${reservation.id}`} disabled={(pending !== null || stale)} onSubmit={(input) => saveReservation(input, reservation.id)} onCancel={() => setEditingReservation(null)} /> : null}
              </article>
            ))}
          </div>
        </section>
      </section>

      <aside className="aiPanel" id="saved-places">
        <div>
          <p className="eyebrow">OPTIONAL CANDIDATES</p>
          <h2>Saved places</h2>
          <p className="muted">Save a manual place to this trip without turning it into a booking or itinerary item.</p>
          <button className="secondary" type="button" onClick={() => { setShowPlaceForm((current) => !current); setEditingPlace(null); }} disabled={(pending !== null || stale)}>{showPlaceForm ? "Close place form" : "+ Add place"}</button>
        </div>
        {showPlaceForm ? <PlaceForm key={`${editorGeneration}-new`} pending={pending === "place-new"} disabled={(pending !== null || stale)} onSubmit={(input) => savePlace(input)} onCancel={() => setShowPlaceForm(false)} /> : null}
        {editingPlace ? <PlaceForm key={`${editorGeneration}-${editingPlace}`} initial={places.find((place) => place.id === editingPlace)} pending={pending === `place-${editingPlace}`} disabled={(pending !== null || stale)} onSubmit={(input) => savePlace(input, editingPlace)} onCancel={() => setEditingPlace(null)} /> : null}
        <div className="placeGroup">
          <p className="sectionLabel">All places</p>
          <div className="placeList">
            {places.length === 0 ? <p className="emptyText">No places yet.</p> : places.map((place) => {
              const saved = savedPlaceIds.has(place.id);
              return <div className="placeRow" key={place.id}><strong>{place.name}</strong>{place.category || place.address ? <span>{[place.category, place.address].filter(Boolean).join(" · ")}</span> : null}<PlaceAttribution place={place} />{place.phone || place.website_url ? <span>{[place.phone, place.website_url].filter(Boolean).join(" · ")}</span> : null}<div className="placeActions"><button className="iconButton" type="button" onClick={() => { setEditingPlace((current) => current === place.id ? null : place.id); setShowPlaceForm(false); }} disabled={(pending !== null || stale)}>{editingPlace === place.id ? "Close" : "Edit"}</button><button className="iconButton" type="button" onClick={() => void run(`save-place-${place.id}`, async () => { if (!saved) await travelApi.createSavedPlace(trip.id, { place_id: place.id, note: null }, trip.revision); })} disabled={(pending !== null || stale) || saved}>{saved ? "Saved" : "Save"}</button></div></div>;
            })}
          </div>
        </div>
        <div className="placeGroup">
          <p className="sectionLabel">This trip’s candidates</p>
          <div className="placeList">
            {savedPlaces.length === 0 ? <p className="emptyText">No saved candidates yet.</p> : savedPlaces.map((savedPlace) => <div className="placeRow" key={savedPlace.id}><strong>{savedPlace.place.name}</strong>{savedPlace.place.category ? <span>{savedPlace.place.category}</span> : null}<PlaceAttribution place={savedPlace.place} /><SavedPlaceNoteForm key={`${editorGeneration}-${savedPlace.id}-${savedPlace.updated_at}`} savedPlace={savedPlace} pending={pending === `saved-place-${savedPlace.id}`} disabled={(pending !== null || stale)} onSave={(note) => run(`saved-place-${savedPlace.id}`, async () => { await travelApi.updateSavedPlace(trip.id, savedPlace.id, { note }, trip.revision); })} /><button className="iconButton dangerText" type="button" onClick={() => void run(`remove-saved-place-${savedPlace.id}`, async () => { await travelApi.deleteSavedPlace(trip.id, savedPlace.id, trip.revision); })} disabled={(pending !== null || stale)}>Remove candidate</button></div>)}
          </div>
        </div>
        <div className="aiBoundary"><span>Optional research</span><code>travel-api → personal-ai-system</code><p>Research and provider evidence remain separate from these authoritative manual records.</p></div>
      </aside>
    </main>
  );
}
