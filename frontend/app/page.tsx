"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useCallback, useEffect, useState } from "react";

import { type CreateTripInput, type TripSummary, travelApi } from "../lib/api";

import { errorMessage } from "../lib/errors";

export default function Home() {
  const router = useRouter();
  const [trips, setTrips] = useState<TripSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<CreateTripInput>({ title: "", start_date: "", end_date: "", timezone: "UTC" });

  const loadTrips = useCallback(async () => {
    setError(null);
    try {
      setTrips(await travelApi.listTrips());
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(loadTrips);
  }, [loadTrips]);

  const createTrip = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const trip = await travelApi.createTrip(form);
      router.push(`/trips/${trip.id}`);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setCreating(false);
    }
  };

  return (
    <main className="shell homeShell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">PERSONAL TRAVEL</p>
          <h1>Trip workspace</h1>
          <p className="muted">A local-first planner for authoritative, editable travel state.</p>
        </div>
        <div className="status"><span className="dot" />AI optional · PostgreSQL first</div>
      </aside>
      <section className="content homeContent">
        <header className="topbar">
          <div>
            <p className="eyebrow">YOUR TRIPS</p>
            <h2>Plan the trip itself</h2>
            <p className="muted">Create a trip, shape each day, and keep the itinerary useful even when AI is offline.</p>
          </div>
          <button className="primary" type="button" onClick={() => setShowForm((current) => !current)}>{showForm ? "Close" : "+ New trip"}</button>
        </header>
        {error ? <><p className="errorBanner" role="alert">{error}</p><button className="secondary" type="button" onClick={() => void loadTrips()}>Reload trips</button></> : null}
        {showForm ? (
          <form className="panel createTripForm" onSubmit={createTrip}>
            <fieldset disabled={creating}><div className="formGrid">
              <label>Trip title<input maxLength={200} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder="Japan in spring" required /></label>
              <label>Timezone<input maxLength={64} value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} placeholder="Asia/Tokyo" required /></label>
              <label>Start date<input type="date" value={form.start_date} onChange={(event) => setForm({ ...form, start_date: event.target.value })} required /></label>
              <label>End date<input type="date" value={form.end_date} onChange={(event) => setForm({ ...form, end_date: event.target.value })} required /></label>
            </div>
            <button className="primary" type="submit" disabled={creating}>{creating ? "Creating…" : "Create trip"}</button></fieldset>
          </form>
        ) : null}
        {loading ? <div className="centerState"><p>Loading trips…</p></div> : trips.length === 0 ? (
          <div className="emptyPanel"><h3>No trips yet</h3><p className="muted">Start with a date range. Days are generated automatically and can be titled as your plan takes shape.</p><button className="secondary" type="button" onClick={() => setShowForm(true)}>Create your first trip</button></div>
        ) : (
          <div className="tripGrid">
            {trips.map((trip) => <Link className="tripCard" key={trip.id} href={`/trips/${trip.id}`}><div className="tripCardTop"><p className="date">{trip.start_date} → {trip.end_date}</p><span className="arrow">→</span></div><h3>{trip.title}</h3><p className="muted">{trip.day_count} {trip.day_count === 1 ? "day" : "days"} · {trip.item_count} {trip.item_count === 1 ? "item" : "items"}</p><span className="tripCardAction">Open itinerary</span></Link>)}
          </div>
        )}
      </section>
      <aside className="aiPanel"><div><p className="eyebrow">YOUR WORKSPACE</p><h2>Manual first</h2><p className="muted">The planner owns trips, days, items, places, ordering, and validation. Shared AI remains a typed external boundary.</p></div><div className="aiBoundary"><span>Optional research</span><code>travel-api → personal-ai-system</code></div></aside>
    </main>
  );
}
