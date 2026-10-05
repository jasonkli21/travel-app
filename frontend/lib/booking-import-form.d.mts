import type {
  BookingCandidate,
  CandidateEdit,
  ConfirmationEntry,
  ReservationStatus,
} from "./booking-imports";
import type { ReservationType } from "./api";

export type BookingImportDraft = CandidateEdit & {
  place_id: string;
  itinerary_item_id: string;
  reservation_status: ReservationStatus | "";
  acknowledgedUncertainty: boolean;
};

export function currentValue<T>(
  candidate: BookingCandidate,
  key: keyof CandidateEdit,
  fallback: T,
): T | CandidateEdit[keyof CandidateEdit];

export function emptyCandidateDraft(candidate: BookingCandidate): BookingImportDraft;
export function normalizeReservationType(value: string | null | undefined): ReservationType | null;
export function isDefinitiveUploadFailure(status: number | null): boolean;
export function createEntryError(
  entry: ConfirmationEntry,
  candidate: BookingCandidate,
  draft: BookingImportDraft,
): string | null;
