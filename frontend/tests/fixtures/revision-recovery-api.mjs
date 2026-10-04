import http from "node:http";

const port = Number(process.env.REVISION_RECOVERY_PORT ?? 8011);
let didConflict = false;

const item = (title) => ({
  id: "item-1",
  item_type: "activity",
  title,
  notes: null,
  start_time: "10:00",
  end_time: null,
  sort_order: 0,
  status: "planned",
  place: null,
  reservation: null,
});

function trip() {
  return {
    id: "trip-1",
    revision: didConflict ? 5 : 4,
    title: "Review trip",
    start_date: "2026-10-03",
    end_date: "2026-10-03",
    timezone: "UTC",
    created_at: "2026-10-03T12:00:00Z",
    updated_at: "2026-10-03T12:00:00Z",
    expires_at: "2027-10-03T12:00:00Z",
    days: [{
      id: "day-1",
      day_index: 1,
      date: "2026-10-03",
      title: null,
      items: [item(didConflict ? "Remote museum change" : "Old museum title")],
    }],
  };
}

function send(response, status, payload) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(payload));
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, `http://127.0.0.1:${port}`);

  if (request.method === "GET" && url.pathname === "/v1/trips/trip-1") {
    return send(response, 200, trip());
  }
  if (request.method === "GET" && [
    "/v1/places",
    "/v1/trips/trip-1/reservations",
    "/v1/trips/trip-1/saved-places",
  ].includes(url.pathname)) {
    return send(response, 200, []);
  }
  if (request.method === "PATCH" && url.pathname === "/v1/trips/trip-1/items/item-1") {
    didConflict = true;
    return send(response, 409, {
      error: {
        code: "stale_revision",
        message: "This trip changed after it was loaded.",
        details: { expected_revision: 4, current_revision: 5 },
      },
    });
  }
  if (request.method === "POST" && url.pathname === "/v1/places") {
    let body = "";
    for await (const chunk of request) body += chunk;
    const input = JSON.parse(body);
    return send(response, 201, {
      id: "place-new",
      revision: 0,
      name: input.name,
      address: input.address ?? null,
      category: input.category ?? null,
      phone: input.phone ?? null,
      website_url: input.website_url ?? null,
      latitude: input.latitude ?? null,
      longitude: input.longitude ?? null,
      provider: null,
      provider_place_id: null,
      provider_source_name: null,
      provider_source_attribution: null,
      provider_source_license: null,
      provider_source_url: null,
    });
  }

  return send(response, 404, {
    error: { code: "not_found", message: `${request.method} ${url.pathname}` },
  });
});

server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`Revision recovery mock API listening on 127.0.0.1:${port}\n`);
});
