import type { PlaceSummary } from "../../lib/api";
import { safeHttpUrl } from "../../lib/urls.mjs";

export default function PlaceAttribution({ place }: { place: PlaceSummary | null }) {
  if (place?.provider === "osm_nominatim") {
    return (
      <span className="providerAttribution">
        {place.provider_source_attribution ?? "© OpenStreetMap contributors"}
        {place.provider_source_license ? ` · ${place.provider_source_license}` : " · ODbL 1.0"}
        {safeHttpUrl(place.provider_source_url) ? <> · <a href={safeHttpUrl(place.provider_source_url)!} target="_blank" rel="noreferrer">OpenStreetMap source</a></> : null}
      </span>
    );
  }
  if (place?.provider !== "geoapify") return null;
  return (
    <span className="providerAttribution">
      {place.provider_source_attribution ?? "Source attribution unavailable"}
      {place.provider_source_license ? ` · ${place.provider_source_license}` : ""}
      {safeHttpUrl(place.provider_source_url) ? <> · <a href={safeHttpUrl(place.provider_source_url)!} target="_blank" rel="noreferrer">Source</a></> : null}
      {" · "}<a href="https://www.geoapify.com/" target="_blank" rel="noreferrer">Powered by Geoapify</a>
    </span>
  );
}
