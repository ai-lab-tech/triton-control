import { APIRequestContext, expect, test } from "@playwright/test";

type LoginResult = {
  access_token: string;
  user: { id: number };
};

const adminEmail = process.env.SMOKE_ADMIN_EMAIL ?? "admin@example.com";
const adminPassword = process.env.SMOKE_ADMIN_PASSWORD ?? "ChangeMe123!";
const tritonUrl = process.env.SMOKE_TRITON_URL ?? "http://127.0.0.1:9000";
const frontendUrl = process.env.SMOKE_FRONTEND_URL ?? "http://127.0.0.1:4200";

type CreatedResources = {
  instanceIds: number[];
  userIds: number[];
  s3ProfileIds: number[];
};

async function ensureBootstrap(request: APIRequestContext) {
  const statusResp = await request.get("/api/auth/bootstrap-status");
  expect(statusResp.ok()).toBeTruthy();
  const statusJson = await statusResp.json();
  if (statusJson.needs_setup) {
    const bootstrapResp = await request.post("/api/auth/bootstrap/register", {
      data: { email: adminEmail, password: adminPassword },
    });
    expect(bootstrapResp.ok()).toBeTruthy();
  }
}

async function loginAdmin(request: APIRequestContext): Promise<LoginResult> {
  const loginResp = await request.post("/api/auth/login", {
    data: { email: adminEmail, password: adminPassword },
  });
  expect(loginResp.ok()).toBeTruthy();
  return (await loginResp.json()) as LoginResult;
}

async function createInstance(
  request: APIRequestContext,
  authHeaders: Record<string, string>,
  name: string,
) {
  const createInstanceResp = await request.post("/api/instances", {
    headers: authHeaders,
    data: {
      name,
      url: tritonUrl,
      verify_ssl: false,
      ca_certificate: "",
      metrics_url: `${tritonUrl}/metrics`,
    },
  });
  expect(createInstanceResp.ok()).toBeTruthy();
  return createInstanceResp.json();
}

async function deleteInstanceIfExists(
  request: APIRequestContext,
  authHeaders: Record<string, string>,
  instanceId: number,
) {
  const resp = await request.delete(`/api/instances/${instanceId}`, { headers: authHeaders });
  expect([204, 404]).toContain(resp.status());
}

async function deleteUserIfExists(
  request: APIRequestContext,
  authHeaders: Record<string, string>,
  userId: number,
) {
  const resp = await request.delete(`/api/auth/users/${userId}`, { headers: authHeaders });
  expect([204, 404]).toContain(resp.status());
}

test.describe.serial("smoke", () => {
  const created: CreatedResources = {
    instanceIds: [],
    userIds: [],
    s3ProfileIds: [],
  };

  test.afterEach(async ({ request }) => {
    const login = await loginAdmin(request);
    const authHeaders = { Authorization: `Bearer ${login.access_token}` };

    while (created.instanceIds.length) {
      const id = created.instanceIds.pop();
      if (id) {
        await deleteInstanceIfExists(request, authHeaders, id);
      }
    }
    while (created.s3ProfileIds.length) {
      const id = created.s3ProfileIds.pop();
      if (id) {
        const response = await request.delete(`/api/s3-profiles/${id}`, { headers: authHeaders });
        expect([200, 404]).toContain(response.status());
      }
    }
    while (created.userIds.length) {
      const id = created.userIds.pop();
      if (id) {
        await deleteUserIfExists(request, authHeaders, id);
      }
    }
  });

  test("auth and frontend health", async ({ request, page, baseURL }) => {
    await ensureBootstrap(request);
    const login = await loginAdmin(request);
    expect(login.access_token).toBeTruthy();

    await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
    await expect(page).toHaveTitle(/Triton/i);

    const healthResp = await request.get(`${baseURL}/health`);
    expect(healthResp.ok()).toBeTruthy();
  });

  test("public local-account lifecycle routes render", async ({ page }) => {
    await page.goto(`${frontendUrl}/forgot-password`, { waitUntil: "domcontentloaded" });
    await expect(page.getByText("Forgot password", { exact: true })).toBeVisible();

    await page.goto(`${frontendUrl}/activate-account`, { waitUntil: "domcontentloaded" });
    await expect(page.getByText("This link is invalid or expired.")).toBeVisible();

    await page.goto(`${frontendUrl}/reset-password`, { waitUntil: "domcontentloaded" });
    await expect(page.getByText("This link is invalid or expired.")).toBeVisible();
  });

  test("user create and delete", async ({ request }) => {
    await ensureBootstrap(request);
    const login = await loginAdmin(request);
    const authHeaders = { Authorization: `Bearer ${login.access_token}` };

    const suffix = Date.now();
    const userEmail = `smoke-user-${suffix}@example.com`;
    const userName = `Smoke User ${suffix}`;

    const createUserResp = await request.post("/api/auth/register", {
      headers: authHeaders,
      data: {
        name: userName,
        email: userEmail,
        role: "viewer",
        auth_provider: "local",
        password: "UserPass123!",
        assigned_instances: [],
      },
    });
    expect(createUserResp.ok()).toBeTruthy();
    const createdUser = await createUserResp.json();
    expect(createdUser.email).toBe(userEmail);
    created.userIds.push(createdUser.id);
  });

  test("instance update and s3 lifecycle", async ({ request }) => {
    await ensureBootstrap(request);
    const login = await loginAdmin(request);
    const authHeaders = { Authorization: `Bearer ${login.access_token}` };
    const suffix = Date.now();
    const instanceName = `smoke-instance-${suffix}`;

    const createdInstance = await createInstance(request, authHeaders, instanceName);
    created.instanceIds.push(createdInstance.id);
    expect(createdInstance.metrics_url).toContain("/metrics");

    // Arrange: administrators manage storage credentials in reusable profiles.
    const createProfileResp = await request.post("/api/s3-profiles", {
      headers: authHeaders,
      data: {
        name: `smoke-s3-${suffix}`,
        endpoint: tritonUrl,
        bucket: "smoke-bucket",
        region: "us-east-1",
        access_key: "smoke-access",
        secret_key: "smoke-secret",
        force_path_style: true,
      },
    });
    expect(createProfileResp.ok(), await createProfileResp.text()).toBeTruthy();
    const profile = await createProfileResp.json();
    created.s3ProfileIds.push(profile.id);

    // Act: the instance selects a profile rather than supplying connection credentials.
    const updateS3Resp = await request.put(`/api/instances/${createdInstance.id}/s3`, {
      headers: authHeaders,
      data: { enabled: true, s3_profile_id: profile.id, prefix: "models/" },
    });

    // Assert
    expect(profile.secret_key).toBe("");
    expect(updateS3Resp.ok(), await updateS3Resp.text()).toBeTruthy();
    const s3Config = await updateS3Resp.json();
    expect(s3Config.bucket).toBe("smoke-bucket");
    expect(s3Config.secret_configured).toBeTruthy();

    // Arrange: update the central profile, then reapply it to the instance.
    const editProfileResp = await request.put(`/api/s3-profiles/${profile.id}`, {
      headers: authHeaders,
      data: {
        bucket: "smoke-bucket-updated",
        access_key: "smoke-access-updated",
        secret_key: "smoke-secret-updated",
      },
    });
    expect(editProfileResp.ok(), await editProfileResp.text()).toBeTruthy();

    // Act
    const editS3Resp = await request.put(`/api/instances/${createdInstance.id}/s3`, {
      headers: authHeaders,
      data: { enabled: true, s3_profile_id: profile.id, prefix: "models-updated/" },
    });

    // Assert
    expect(editS3Resp.ok(), await editS3Resp.text()).toBeTruthy();
    const s3ConfigEdited = await editS3Resp.json();
    expect(s3ConfigEdited.bucket).toBe("smoke-bucket-updated");
    expect(s3ConfigEdited.access_key).toBe("smoke-access-updated");
    expect(s3ConfigEdited.prefix).toBe("models-updated/");
    expect(s3ConfigEdited.secret_configured).toBeTruthy();

    // Act: manual connection overrides are rejected even when a profile is selected.
    const manualS3Resp = await request.put(`/api/instances/${createdInstance.id}/s3`, {
      headers: authHeaders,
      data: { enabled: true, s3_profile_id: profile.id, secret_key: "manual-override" },
    });

    // Assert
    expect(manualS3Resp.status()).toBe(400);

    const deleteS3Resp = await request.put(`/api/instances/${createdInstance.id}/s3`, {
      headers: authHeaders,
      data: { enabled: false },
    });
    expect(deleteS3Resp.ok()).toBeTruthy();
    const s3ConfigDeleted = await deleteS3Resp.json();
    expect(s3ConfigDeleted.enabled).toBeFalsy();

    const updateInstanceResp = await request.put(`/api/instances/${createdInstance.id}`, {
      headers: authHeaders,
      data: {
        url: tritonUrl,
        verify_ssl: false,
        ca_certificate: "",
        metrics_url: `${tritonUrl}/metrics`,
      },
    });
    expect(updateInstanceResp.ok()).toBeTruthy();
    const updatedInstance = await updateInstanceResp.json();
    expect(updatedInstance.id).toBe(createdInstance.id);

    const getInstanceResp = await request.get(`/api/instances/${createdInstance.id}`, {
      headers: authHeaders,
    });
    expect(getInstanceResp.ok()).toBeTruthy();
    const instanceWithMetrics = await getInstanceResp.json();
    expect(typeof instanceWithMetrics.metrics_cpu).toBe("number");
  });

  test("model load infer unload with metrics header", async ({ request }) => {
    await ensureBootstrap(request);
    const login = await loginAdmin(request);
    const authHeaders = { Authorization: `Bearer ${login.access_token}` };
    const suffix = Date.now();
    const instanceName = `smoke-model-instance-${suffix}`;

    const createdInstance = await createInstance(request, authHeaders, instanceName);
    created.instanceIds.push(createdInstance.id);

    const modelsResp = await request.get(`/api/instances/${createdInstance.id}/models`, {
      headers: authHeaders,
    });
    expect(modelsResp.ok()).toBeTruthy();
    const models = await modelsResp.json();
    expect(Array.isArray(models)).toBeTruthy();
    expect(models.length).toBeGreaterThan(0);

    const modelName = models[0].name;
    const modelVersion = models[0].version ?? "1";

    const loadModelResp = await request.post(
      `/api/instances/${createdInstance.id}/models/${modelName}/load`,
      { headers: authHeaders },
    );
    expect(loadModelResp.ok()).toBeTruthy();

    const inferResp = await request.post(
      `/api/instances/${createdInstance.id}/models/${modelName}/versions/${modelVersion}/infer`,
      {
        headers: {
          ...authHeaders,
          "content-type": "application/json",
        },
        data: {
          inputs: [
            {
              name: "INPUT0",
              shape: [1, 1],
              datatype: "FP32",
              data: [1.0],
            },
          ],
          outputs: [{ name: "OUTPUT0" }],
        },
      },
    );
    expect(inferResp.ok()).toBeTruthy();
    const inferenceMetricsHeader = inferResp.headers()["x-triton-inference-metrics"];
    expect(inferenceMetricsHeader).toBeTruthy();

    const unloadModelResp = await request.post(
      `/api/instances/${createdInstance.id}/models/${modelName}/unload`,
      { headers: authHeaders },
    );
    expect(unloadModelResp.ok()).toBeTruthy();
  });
});
