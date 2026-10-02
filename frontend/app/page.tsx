const days = [
  {
    date: "Dec 9",
    location: "Chiang Mai",
    items: ["Morning coffee + Old City", "Flexible lunch", "Night market"]
  },
  {
    date: "Dec 10",
    location: "Chiang Mai",
    items: ["Open itinerary slot", "Saved places"]
  },
  {
    date: "Dec 11",
    location: "Chiang Mai",
    items: ["Day plan placeholder", "Travel notes"]
  }
];

export default function Home() {
  return (
    <main className="shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">PERSONAL TRAVEL</p>
          <h1>Thailand 2026</h1>
          <p className="muted">Scaffold preview — authoritative trip state will live here.</p>
        </div>

        <nav className="nav" aria-label="Trip navigation">
          <a className="active" href="#itinerary">Itinerary</a>
          <a href="#map">Map</a>
          <a href="#reservations">Reservations</a>
          <a href="#saved">Saved places</a>
          <a href="#documents">Documents</a>
        </nav>

        <div className="status"><span className="dot" />Local-first architecture</div>
      </aside>

      <section className="content" id="itinerary">
        <header className="topbar">
          <div>
            <p className="eyebrow">DEC 8–23 · THAILAND</p>
            <h2>Day-by-day itinerary</h2>
          </div>
          <button className="secondary" type="button">+ Add day item</button>
        </header>

        <div className="days">
          {days.map((day) => (
            <article className="day" key={day.date}>
              <div className="dayHeading">
                <div>
                  <p className="date">{day.date}</p>
                  <h3>{day.location}</h3>
                </div>
                <button className="iconButton" type="button" aria-label={`More options for ${day.date}`}>•••</button>
              </div>
              <div className="items">
                {day.items.map((item, index) => (
                  <div className="item" key={item}>
                    <span className="time">{index === 0 ? "09:00" : index === 1 ? "13:00" : "18:00"}</span>
                    <span>{item}</span>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>

      <aside className="aiPanel">
        <div>
          <p className="eyebrow">AI / RESEARCH</p>
          <h2>Trip assistant</h2>
          <p className="muted">This panel will call personal-ai-system. It does not own itinerary state.</p>
        </div>

        <div className="promptCard">
          <p>Try later:</p>
          <strong>“Find dinner near where I’ll be Tuesday evening.”</strong>
        </div>

        <div className="aiBoundary">
          <span>Planned boundary</span>
          <code>travel-api → personal-ai-system</code>
        </div>
      </aside>
    </main>
  );
}
