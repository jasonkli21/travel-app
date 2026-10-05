import type { TravelComparisonRequest } from "./api";

export function browserComparisonStorage(): Storage | null;
export function clearComparisonRetries(storage: Storage | null): void;

export function comparisonRetryKey(tripId: string): string;
export function isAmbiguousComparisonFailure(error: unknown): boolean;
export function shouldKeepComparisonRequest(
  error: unknown,
  reconcilingUnknownOutcome: boolean,
): boolean;
export function parseComparisonRetry(value: unknown): TravelComparisonRequest | null;
export function readComparisonRetry(
  storage: Pick<Storage, "getItem"> | null,
  tripId: string,
): TravelComparisonRequest | null;
export function getComparisonRetrySnapshot(
  storage: Pick<Storage, "getItem"> | null,
  tripId: string,
): string | null;
export function subscribeComparisonRetry(tripId: string, listener: () => void): () => void;
export function writeComparisonRetry(
  storage: Pick<Storage, "setItem" | "removeItem"> | null,
  tripId: string,
  request: TravelComparisonRequest | null,
): void;
