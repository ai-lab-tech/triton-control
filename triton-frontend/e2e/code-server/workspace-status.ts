import type { APIRequestContext } from "@playwright/test";

export async function workspaceStatus(
  request: APIRequestContext,
  workspaceId: number,
): Promise<string> {
  // A reused connection can reset during a pod restart. Retry only ECONNRESET,
  // at most twice; HTTP errors and persistent transport failures must still fail.
  const response = await request.get(`/api/development/${workspaceId}`, {
    maxRetries: 2,
    timeout: 10_000,
  });
  try {
    if (!response.ok()) {
      throw new Error(`Workspace status request failed: HTTP ${response.status()}`);
    }
    const body: unknown = await response.json();
    if (
      !body ||
      typeof body !== "object" ||
      !("status" in body) ||
      typeof body.status !== "string"
    ) {
      throw new Error("Workspace status response must contain a string status");
    }
    return body.status;
  } finally {
    await response.dispose();
  }
}
