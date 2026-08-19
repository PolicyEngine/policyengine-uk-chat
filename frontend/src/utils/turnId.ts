/** Create one identifier per user submission and reuse it for HTTP retries. */
export function createTurnId(excludedTurnId?: string): string {
  let turnId = crypto.randomUUID();
  while (turnId === excludedTurnId) turnId = crypto.randomUUID();
  return turnId;
}

const RETRYABLE_CHAT_STATUSES = new Set([502, 503, 504]);

type FetchImplementation = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

/**
 * Submit one already-serialized chat turn, retrying one transient failure with
 * the identical request body and therefore the identical turn identifier.
 */
export async function postChatTurnWithRetry(
  url: string,
  init: RequestInit,
  fetchImplementation: FetchImplementation = fetch,
): Promise<Response> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetchImplementation(url, init);
      if (attempt === 0 && RETRYABLE_CHAT_STATUSES.has(response.status)) {
        continue;
      }
      return response;
    } catch (error) {
      if (attempt === 1 || isAbortError(error)) throw error;
    }
  }
  throw new Error("Chat request retry ended without a response");
}
