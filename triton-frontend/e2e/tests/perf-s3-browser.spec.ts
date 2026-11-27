import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test.use({ extraHTTPHeaders: {} });

const profilePath = process.env.SMOKE_S3_PERF_PATH ?? "";
const frontend = new URL(process.env.SMOKE_FRONTEND_URL ?? "http://127.0.0.1:4200");

test("uploads and edits repository files without losing benchmark inputs", async ({
  page,
  request,
}) => {
  test.skip(!profilePath, "Set SMOKE_S3_PERF_PATH to a writable test model profile path.");

  // Arrange
  const login = await request.post("/api/auth/login", {
    data: {
      email: process.env.SMOKE_ADMIN_EMAIL ?? "admin@example.com",
      password: process.env.SMOKE_ADMIN_PASSWORD ?? "ChangeMe123!",
    },
  });
  expect(login.ok()).toBeTruthy();
  const { access_token } = await login.json();
  const target = /^\/instances\/(\d+)\/models\/[^/]+\/versions\/[^/]+\/profile$/.exec(profilePath);
  if (!target) throw new Error("SMOKE_S3_PERF_PATH must be a relative model profile path.");
  const profileUrl = new URL(profilePath, frontend).href;
  await page.addInitScript(
    ({ origin, token }) => {
      if (location.origin === origin) localStorage.setItem("triton_access_token", token);
    },
    { origin: frontend.origin, token: access_token },
  );
  await page.goto(profileUrl);
  const batchSize = page.getByLabel("Batch size");
  await batchSize.fill("7");
  const fileName = `perf-smoke-${randomUUID()}.py`;
  const contentUrl = `/api/instances/${target[1]}/s3/content`;
  const fileRequest = {
    headers: { Authorization: `Bearer ${access_token}` },
    params: { path: "/" + fileName },
  };

  try {
    // Act
    await page.getByRole("button", { name: "S3 Browser" }).click();
    await page.locator("input[type=file]:not([multiple])").setInputFiles({
      name: fileName,
      mimeType: "text/plain",
      buffer: Buffer.from("initial = 1"),
    });
    await page.getByRole("button", { name: "Edit " + fileName }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.locator(".view-lines")).toContainText("initial");
    await dialog.locator(".view-lines").click();
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.insertText("updated = 2");
    await dialog.getByRole("button", { name: /save/i }).click();

    // Assert
    await expect(dialog).toBeHidden();
    const saved = await request.get(contentUrl, fileRequest);
    expect(saved.ok()).toBeTruthy();
    expect((await saved.json()).content).toBe("updated = 2");
    await expect(page).toHaveURL(profileUrl);
    await expect(batchSize).toHaveValue("7");
  } finally {
    const deleted = await request.delete(contentUrl, fileRequest);
    expect([200, 204, 404]).toContain(deleted.status());
  }
});
