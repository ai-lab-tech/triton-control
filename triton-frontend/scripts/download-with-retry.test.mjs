import assert from "node:assert/strict";
import { test } from "node:test";
import { downloadWithRetry } from "./download-with-retry.mjs";

for (const [header, expected] of [
  ["3", 3000],
  ["Thu, 01 Jan 1970 00:00:05 GMT", 5000],
  ["9999", 60000],
  ["invalid", 2000],
]) {
  test(`retries throttling with Retry-After ${header}`, async () => {
    // Arrange
    const delays = [];
    const responses = [
      new Response(null, { status: 429, headers: { "Retry-After": header } }),
      new Response("jar"),
    ];
    // Act
    const result = await downloadWithRetry(["primary"], {
      fetchImpl: async () => responses.shift(),
      sleep: async (delay) => {
        delays.push(delay);
      },
      now: () => 0,
    });
    // Assert
    assert.equal(result.toString(), "jar");
    assert.deepEqual(delays, [expected]);
    assert.equal(responses.length, 0);
  });
}

test("falls back after bounded server-error retries", async () => {
  // Arrange
  const calls = [];
  const delays = [];
  // Act
  const result = await downloadWithRetry(["primary", "mirror"], {
    fetchImpl: async (url) => {
      calls.push(url);
      return new Response(url === "mirror" ? "jar" : null, {
        status: url === "mirror" ? 200 : 503,
      });
    },
    sleep: async (delay) => {
      delays.push(delay);
    },
  });
  // Assert
  assert.equal(result.toString(), "jar");
  assert.deepEqual(calls, ["primary", "primary", "primary", "primary", "mirror"]);
  assert.deepEqual(delays, [2000, 4000, 8000]);
});

test("does not retry permanent HTTP errors", async () => {
  // Arrange
  const calls = [];
  // Act
  const result = await downloadWithRetry(["primary", "mirror"], {
    fetchImpl: async (url) => {
      calls.push(url);
      return new Response("jar", { status: url === "primary" ? 404 : 200 });
    },
    sleep: async () => {
      assert.fail("Unexpected retry");
    },
  });
  // Assert
  assert.equal(result.toString(), "jar");
  assert.deepEqual(calls, ["primary", "mirror"]);
});

test("reports exhausted network failures without waiting after the last attempt", async () => {
  // Arrange
  const delays = [];
  // Act
  const result = downloadWithRetry(["primary"], {
    fetchImpl: async () => {
      throw new Error("connection reset");
    },
    sleep: async (delay) => {
      delays.push(delay);
    },
  });
  // Assert
  await assert.rejects(result, /primary -> connection reset/);
  assert.deepEqual(delays, [2000, 4000, 8000]);
});
