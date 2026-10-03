"use client";

import type { FormEvent } from "react";
import { useState } from "react";
import { errorMessage } from "../../lib/errors";
import type { CreatePlaceInput, PlaceSummary, UpdatePlaceInput } from "../../lib/api";

export default function PlaceForm({
  initial,
  pending,
  disabled,
  onSubmit,
  onCancel,
}: {
  initial?: PlaceSummary;
  pending: boolean;
  disabled: boolean;
  onSubmit: (input: CreatePlaceInput | UpdatePlaceInput) => Promise<boolean>;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [category, setCategory] = useState(initial?.category ?? "");
  const [address, setAddress] = useState(initial?.address ?? "");
  const [phone, setPhone] = useState(initial?.phone ?? "");
  const [websiteUrl, setWebsiteUrl] = useState(initial?.website_url ?? "");
  const [latitude, setLatitude] = useState(initial?.latitude?.toString() ?? "");
  const [longitude, setLongitude] = useState(initial?.longitude?.toString() ?? "");
  const [formError, setFormError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!name.trim()) {
      setFormError("Give the place a name.");
      return;
    }
    setFormError(null);
    try {
      const saved = await onSubmit({
        name: name.trim(),
        latitude: latitude.trim() ? Number(latitude) : null,
        longitude: longitude.trim() ? Number(longitude) : null,
        category: category.trim() || null,
        address: address.trim() || null,
        phone: phone.trim() || null,
        website_url: websiteUrl.trim() || null,
      });
      if (!saved) setFormError("The place was not saved. Review the error above and try again.");
    } catch (error) {
      setFormError(errorMessage(error));
    }
  };

  return (
    <form className="placeForm" onSubmit={submit}>
      <fieldset disabled={disabled || pending}>
        <label>Name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={240} required /></label>
        <div className="formGrid">
          <label>Category<input value={category} onChange={(event) => setCategory(event.target.value)} maxLength={120} placeholder="Museum, hotel, restaurant…" /></label>
          <label>Phone<input value={phone} onChange={(event) => setPhone(event.target.value)} maxLength={64} /></label>
        </div>
        <div className="formGrid">
          <label>Latitude<input type="number" step="any" min={-90} max={90} value={latitude} onChange={(event) => setLatitude(event.target.value)} /></label>
          <label>Longitude<input type="number" step="any" min={-180} max={180} value={longitude} onChange={(event) => setLongitude(event.target.value)} /></label>
        </div>
        <p className="formHint">Enter both coordinates to show this place on the map, or clear both.</p>
        <label>Address<input value={address} onChange={(event) => setAddress(event.target.value)} maxLength={500} /></label>
        <label>Website<input type="url" value={websiteUrl} onChange={(event) => setWebsiteUrl(event.target.value)} maxLength={500} placeholder="https://…" /></label>
        <div className="formActions">
          <button className="primary" type="submit">{pending ? "Saving…" : initial ? "Save place" : "Add place"}</button>
          {onCancel ? <button className="secondary" type="button" onClick={onCancel}>Cancel</button> : null}
        </div>
      </fieldset>
      {formError ? <p className="formError" role="alert">{formError}</p> : null}
    </form>
  );
}

