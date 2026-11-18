import { setTimeout } from "node:timers/promises";

const attempts = 4;
const maxDelayMs = 60_000;

function retryDelay(value, attempt, now) {
  const seconds = value === null ? NaN : Number(value);
  const date = Date.parse(value);
  const requested = Number.isFinite(seconds)
    ? seconds * 1000
    : Number.isFinite(date)
      ? date - now()
      : NaN;
  return Math.min(
    maxDelayMs,
    Math.max(0, Number.isFinite(requested) ? requested : 2000 * 2 ** attempt),
  );
}

/** Download with bounded retries for throttling, server errors and network failures. */
export async function downloadWithRetry(
  urls,
  { fetchImpl = fetch, sleep = setTimeout, now = Date.now, retryNotFound = false } = {},
) {
  const errors = [];
  for (const url of urls) {
    for (let attempt = 0; attempt < attempts; attempt++) {
      let delay = retryDelay(null, attempt, now);
      try {
        const response = await fetchImpl(url, { signal: AbortSignal.timeout(60_000) });
        if (response.ok) return Buffer.from(await response.arrayBuffer());
        errors.push(`${url} -> ${response.status} ${response.statusText}`);
        await response.body?.cancel();
        if (
          response.status !== 429 &&
          response.status < 500 &&
          !(retryNotFound && response.status === 404)
        )
          break;
        delay = retryDelay(response.headers.get("retry-after"), attempt, now);
      } catch (error) {
        errors.push(`${url} -> ${error instanceof Error ? error.message : String(error)}`);
      }
      if (attempt + 1 < attempts) await sleep(delay);
    }
  }
  throw new Error(
    `Failed to download swagger-codegen-cli.jar from all sources:\n${errors.join("\n")}`,
  );
}
