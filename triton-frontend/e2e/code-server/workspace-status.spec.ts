import { createServer, type RequestListener, type Server } from "node:http";
import {
  test,
  expect,
  request as playwrightRequest,
  type APIRequestContext,
} from "@playwright/test";
import { workspaceStatus } from "./workspace-status";

test.describe("workspace status requests", () => {
  let server: Server;
  let client: APIRequestContext;

  async function serve(handler: RequestListener) {
    server = createServer(handler);
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("Missing test server address");
    client = await playwrightRequest.newContext({ baseURL: `http://127.0.0.1:${address.port}` });
  }

  test.afterEach(async () => {
    await client?.dispose();
    if (server) {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      );
    }
  });

  test("recovers from a real connection reset without changing the request", async () => {
    const paths: (string | undefined)[] = [];
    await serve((request, response) => {
      paths.push(request.url);
      if (paths.length === 1) request.socket.destroy();
      else response.end(JSON.stringify({ status: "ready" }));
    });
    await expect(workspaceStatus(client, 42)).resolves.toBe("ready");
    expect(paths).toEqual(["/api/development/42", "/api/development/42"]);
  });

  test("fails after the bounded retry budget on persistent connection resets", async () => {
    let attempts = 0;
    await serve((request) => {
      attempts++;
      request.socket.destroy();
    });
    await expect(workspaceStatus(client, 42)).rejects.toThrow();
    expect(attempts).toBe(3);
  });

  for (const status of [401, 403, 404, 500, 503]) {
    test(`does not retry or accept HTTP ${status}`, async () => {
      let attempts = 0;
      await serve((_request, response) => {
        attempts++;
        response.writeHead(status);
        response.end(JSON.stringify({ status: "ready" }));
      });
      await expect(workspaceStatus(client, 42)).rejects.toThrow(`HTTP ${status}`);
      expect(attempts).toBe(1);
    });
  }

  test("returns a pending state for the readiness poll", async () => {
    await serve((_request, response) => response.end(JSON.stringify({ status: "starting" })));
    await expect(workspaceStatus(client, 42)).resolves.toBe("starting");
  });

  test("rejects a malformed status response", async () => {
    await serve((_request, response) => response.end(JSON.stringify({ status: true })));
    await expect(workspaceStatus(client, 42)).rejects.toThrow("string status");
  });
});
