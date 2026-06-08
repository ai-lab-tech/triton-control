# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: smoke.spec.ts >> smoke: create and delete user + instance
- Location: e2e\tests\smoke.spec.ts:32:5

# Error details

```
Error: expect(received).toBeTruthy()

Received: false
```

# Test source

```ts
  1  | import { APIRequestContext, expect, test } from "@playwright/test";
  2  |
  3  | type LoginResult = {
  4  |   access_token: string;
  5  |   user: { id: number };
  6  | };
  7  |
  8  | const adminEmail = process.env.SMOKE_ADMIN_EMAIL ?? "admin@example.com";
  9  | const adminPassword = process.env.SMOKE_ADMIN_PASSWORD ?? "ChangeMe123!";
  10 | const tritonUrl = process.env.SMOKE_TRITON_URL ?? "http://127.0.0.1:9000";
  11 |
  12 | async function ensureBootstrap(request: APIRequestContext) {
  13 |   const statusResp = await request.get("/api/auth/bootstrap-status");
  14 |   expect(statusResp.ok()).toBeTruthy();
  15 |   const statusJson = await statusResp.json();
  16 |   if (statusJson.needs_setup) {
  17 |     const bootstrapResp = await request.post("/api/auth/bootstrap/register", {
  18 |       data: { email: adminEmail, password: adminPassword },
  19 |     });
  20 |     expect(bootstrapResp.ok()).toBeTruthy();
  21 |   }
  22 | }
  23 |
  24 | async function loginAdmin(request: APIRequestContext): Promise<LoginResult> {
  25 |   const loginResp = await request.post("/api/auth/login", {
  26 |     data: { email: adminEmail, password: adminPassword },
  27 |   });
> 28 |   expect(loginResp.ok()).toBeTruthy();
     |                          ^ Error: expect(received).toBeTruthy()
  29 |   return (await loginResp.json()) as LoginResult;
  30 | }
  31 |
  32 | test("smoke: create and delete user + instance", async ({ request, page, baseURL }) => {
  33 |   await ensureBootstrap(request);
  34 |   const login = await loginAdmin(request);
  35 |   const authHeaders = { Authorization: `Bearer ${login.access_token}` };
  36 |
  37 |   await page.goto(process.env.SMOKE_FRONTEND_URL ?? "http://127.0.0.1:4200", {
  38 |     waitUntil: "domcontentloaded",
  39 |   });
  40 |   await expect(page).toHaveTitle(/Triton/i);
  41 |
  42 |   const suffix = Date.now();
  43 |   const userEmail = `smoke-user-${suffix}@example.com`;
  44 |   const userName = `Smoke User ${suffix}`;
  45 |
  46 |   const createUserResp = await request.post("/api/auth/register", {
  47 |     headers: authHeaders,
  48 |     data: {
  49 |       name: userName,
  50 |       email: userEmail,
  51 |       role: "viewer",
  52 |       auth_provider: "local",
  53 |       password: "UserPass123!",
  54 |       assigned_instances: [],
  55 |     },
  56 |   });
  57 |   expect(createUserResp.ok()).toBeTruthy();
  58 |   const createdUser = await createUserResp.json();
  59 |   expect(createdUser.email).toBe(userEmail);
  60 |
  61 |   const instanceName = `smoke-instance-${suffix}`;
  62 |   const createInstanceResp = await request.post("/api/instances", {
  63 |     headers: authHeaders,
  64 |     data: {
  65 |       name: instanceName,
  66 |       url: tritonUrl,
  67 |       verify_ssl: false,
  68 |       ca_certificate: "",
  69 |     },
  70 |   });
  71 |   expect(createInstanceResp.ok()).toBeTruthy();
  72 |   const createdInstance = await createInstanceResp.json();
  73 |   expect(createdInstance.name).toBe(instanceName);
  74 |
  75 |   const deleteInstanceResp = await request.delete(`/api/instances/${createdInstance.id}`, {
  76 |     headers: authHeaders,
  77 |   });
  78 |   expect(deleteInstanceResp.status()).toBe(204);
  79 |
  80 |   const deleteUserResp = await request.delete(`/api/auth/users/${createdUser.id}`, {
  81 |     headers: authHeaders,
  82 |   });
  83 |   expect(deleteUserResp.status()).toBe(204);
  84 |
  85 |   const healthResp = await request.get(`${baseURL}/health`);
  86 |   expect(healthResp.ok()).toBeTruthy();
  87 | });
  88 |
```
