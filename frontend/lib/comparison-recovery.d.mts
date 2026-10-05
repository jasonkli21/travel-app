import type { TravelComparisonRequest } from "./api";

export function comparisonRetryKey(tripId: string): string;
export function isAmbiguousComparisonFailure(error: unknown): boolean;
export function shouldKeepComparisonRequest(
  error: unknown,
  reconcilingUnknownOutcome: boolean,
): boolean;
export function parseComparisonRetry(value: unknown): TravelComparisonRequest | null;
export function readComparisonRetry(
  storage: Pick<Storage, "getItem">,
  tripId: string,
): TravelComparisonRequest | null;
export function getComparisonRetrySnapshot(
  storage: Pick<Storage, "getItem">,
  tripId: string,
): string | null;
export function subscribeComparisonRetry(tripId: string, listener: () => void): () => void;
export function writeComparisonRetry(
  storage: Pick<Storage, "setItem" | "removeItem">,
  tripId: string,
  request: TravelComparisonRequest | null,
): void;
