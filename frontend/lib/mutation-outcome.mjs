// A transport/5xx failure cannot prove whether the server committed a write.
export function uncertainMutationError(error) {
  return !error || typeof error.status !== "number" || error.status === 0 || error.status >= 500;
}
