const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const { apiUrl, requestJson } = require("../profile-api");

async function server(t, handler) {
  const app = http.createServer(handler);
  await new Promise((resolve) => app.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => { app.close(resolve); app.closeAllConnections(); }));
  return `http://127.0.0.1:${app.address().port}`;
}

test("native API login and profile requests use JSON and a bearer token", async (t) => {
  const seen = [];
  const endpoint = await server(t, async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    seen.push({ url: req.url, method: req.method, authorization: req.headers.authorization, body });
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(req.url.endsWith("login") ? { access_token: "session-token" } : [{ id: 2 }]));
  });
  assert.deepEqual(await requestJson(endpoint, "/api/auth/login", {
    body: { email: "member@example.com", password: "test-password" },
  }), { access_token: "session-token" });
  assert.deepEqual(await requestJson(endpoint, "/api/s3-profiles", { token: "session-token" }), [{ id: 2 }]);
  assert.equal(seen[0].method, "POST");
  assert.equal(JSON.parse(seen[0].body).password, "test-password");
  assert.equal(seen[0].authorization, undefined);
  assert.equal(seen[1].authorization, "Bearer session-token");
  assert.equal(seen[1].body, "");
});

test("API failures do not echo response bodies or follow credential redirects", async (t) => {
  let requests = 0;
  const endpoint = await server(t, (req, res) => {
    requests++;
    const status = Number(req.url.slice(1));
    res.writeHead(status, { location: "/200" });
    res.end("sensitive-provider-response");
  });
  for (const status of [401, 403, 302, 500]) {
    await assert.rejects(requestJson(endpoint, `/${status}`, { token: "private-token" }), (error) => {
      assert.equal(error.status, status);
      assert.ok(!error.message.includes("sensitive-provider-response"));
      assert.ok(!error.message.includes("private-token"));
      return true;
    });
  }
  assert.equal(requests, 4);
  await assert.rejects(requestJson(endpoint, "/200"), /invalid response/);
});

test("API URLs reject embedded credentials and unsupported protocols", () => {
  for (const url of ["bad-url", "file:///tmp/test", "https://user:secret@host", "https://host?token=secret", "https://host/#fragment"])
    assert.throws(() => apiUrl(url));
  assert.equal(apiUrl("https://control.example/base/").pathname, "/base/");
});
