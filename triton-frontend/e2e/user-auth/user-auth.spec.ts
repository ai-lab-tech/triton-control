import { APIRequestContext, BrowserContext, Page, expect, test } from "@playwright/test";

const mode = process.env.AUTH_SMOKE_MODE!;
const password = process.env.AUTH_SMOKE_PASSWORD!;
const isOidc = mode === "oidc";
const invitations = mode === "smtp" || mode === "manual-link";
type User = {
  id: number;
  email: string;
  role: string;
  is_active: boolean;
  assigned_instances: string[];
  oidc_subject?: string;
};
type Mail = { ID: string; To: { Address: string }[] };

async function me(context: BrowserContext) {
  const response = await context.request.get("/api/auth/me");
  expect(response.status()).toBe(200);
  return response.json();
}

async function login(page: Page, name: string, secret = password) {
  await page.goto("/signin");
  if (isOidc) {
    await page.getByRole("button", { name: "Log in with OIDC" }).click();
    await expect(page).toHaveURL(new RegExp(`${process.env.AUTH_SMOKE_OIDC_URL}/realms/smoke/`));
    await page.locator("#username").fill(name);
    await page.locator("#password").fill(secret);
    await Promise.all([
      page.waitForURL(
        (url) => url.origin === process.env.AUTH_SMOKE_URL && url.pathname !== "/auth/callback",
      ),
      page.locator("#kc-login").click(),
    ]);
  } else {
    await page.getByRole("textbox", { name: "Email", exact: true }).fill(`${name}@example.test`);
    await page.getByLabel("Password", { exact: true }).fill(secret);
    await page.getByRole("button", { name: "Log in with password" }).click();
    await expect(page).toHaveURL(/\/dashboard/);
  }
  const identity = await me(page.context());
  expect(identity.user.email).toBe(`${name}@example.test`);
  return identity;
}

async function users(admin: APIRequestContext): Promise<User[]> {
  const response = await admin.get("/api/auth/users");
  expect(response.status()).toBe(200);
  return response.json();
}

async function mailboxLink(
  api: APIRequestContext,
  email: string,
  route: string,
  seen: Set<string>,
) {
  let link = "";
  await expect
    .poll(
      async () => {
        const response = await api.get(`${process.env.AUTH_SMOKE_MAIL_URL}/api/v1/messages`);
        expect(response.ok()).toBeTruthy();
        const { messages } = await response.json();
        for (const item of messages as Mail[]) {
          if (seen.has(item.ID) || !item.To.some((to) => to.Address === email)) continue;
          const detail = await api.get(
            `${process.env.AUTH_SMOKE_MAIL_URL}/api/v1/message/${item.ID}`,
          );
          expect(detail.ok()).toBeTruthy();
          const message = await detail.json();
          const found = (message.Text as string).match(
            new RegExp(`http://[^\\s<>]+/${route}\\?token=[A-Za-z0-9_-]+`),
          );
          if (found) {
            seen.add(item.ID);
            link = found[0];
            return true;
          }
        }
        return false;
      },
      { timeout: 30_000 },
    )
    .toBe(true);
  expect(new URL(link).origin).toBe(process.env.AUTH_SMOKE_URL);
  return link;
}

async function provision(admin: Page, name: string, role: string, seen: Set<string>) {
  const options = admin.waitForResponse((response) =>
    response.url().endsWith("/api/auth/email-settings"),
  );
  await admin.goto("/users");
  await options;
  await expect(admin.getByRole("row").filter({ hasText: "admin@example.test" })).toBeVisible();
  await admin.getByRole("button", { name: "Add user" }).click();
  const dialog = admin.getByRole("dialog");
  await dialog.getByLabel("Full name").fill(`${name} Smoke`);
  await dialog.getByLabel("Email", { exact: true }).fill(`${name}@example.test`);
  await dialog.locator("#dialog-user-role").click();
  await admin.getByRole("option", { name: role, exact: true }).click();
  await dialog.locator("#dialog-user-instances").click();
  await admin.getByRole("option", { name: "Assigned", exact: true }).click();
  await admin.keyboard.press("Escape");
  const endpoint = invitations ? "/api/auth/invitations" : "/api/auth/register";
  const responsePromise = admin.waitForResponse(
    (response) => response.url().endsWith(endpoint) && response.request().method() === "POST",
  );
  await dialog
    .getByRole("button", { name: invitations ? "Invite user" : "Add user", exact: true })
    .click();
  const response = await responsePromise;
  expect(response.ok(), await response.text()).toBeTruthy();
  const result = await response.json();
  let link = "";
  if (mode === "manual-link") {
    const field = dialog.getByRole("textbox", { name: "One-time activation link" });
    await expect(field).toHaveValue(result.manual_link);
    link = await field.inputValue();
    await dialog.getByRole("button", { name: "Cancel" }).click();
  } else if (mode === "smtp") {
    expect(result.delivered).toBe(true);
    expect(result.manual_link).toBeNull();
    link = await mailboxLink(admin.request, `${name}@example.test`, "activate-account", seen);
  }
  await expect(dialog).toBeHidden();
  const user = (await users(admin.request)).find(
    (entry) => entry.email === `${name}@example.test`,
  )!;
  expect(user).toBeDefined();
  expect(user.role).toBe(role);
  expect(user.is_active).toBe(isOidc);
  expect(user.assigned_instances).toEqual(["Assigned"]);
  return { user, link };
}

async function completeLink(
  page: Page,
  link: string,
  action: "Activate account" | "Reset password",
  secret: string,
) {
  await page.goto(link);
  await page.getByLabel("New password", { exact: true }).fill(secret);
  await page.getByLabel("Confirm password", { exact: true }).fill(secret);
  await page.getByRole("button", { name: action, exact: true }).click();
  await expect(page.getByText(/You can now sign in\./)).toBeVisible();
  await page.goto(link);
  await expect(page.getByText("This link is invalid or expired.", { exact: true })).toBeVisible();
}

test(`${mode}: provision additional users, enforce roles, and complete account lifecycle`, async ({
  browser,
  baseURL,
}, testInfo) => {
  expect(password, "Use the disposable runner to supply credentials").toBeTruthy();
  const contexts: BrowserContext[] = [];
  async function session() {
    const context = await browser.newContext({ baseURL });
    contexts.push(context);
    return { context, page: await context.newPage() };
  }
  const seen = new Set<string>();
  try {
    const admin = await session();
    await test.step("prepare first admin only as setup", async () => {
      if (!isOidc) {
        expect(
          (
            await admin.context.request.post("/api/auth/bootstrap/register", {
              data: { email: "admin@example.test", password },
            })
          ).ok(),
        ).toBeTruthy();
      }
      expect((await login(admin.page, "admin")).user.role.toLowerCase()).toBe("admin");
    });
    const instances: { id: number; name: string }[] = [];
    await test.step("prepare assigned and unassigned instance records", async () => {
      for (const [name, host] of [
        ["Assigned", "127.0.0.1"],
        ["Hidden", "localhost"],
      ]) {
        const response = await admin.context.request.post("/api/instances", {
          data: { name, url: `http://${host}:19000`, verify_ssl: false },
        });
        expect(response.ok(), await response.text()).toBeTruthy();
        instances.push(await response.json());
      }
    });
    const accounts: { context: BrowserContext; page: Page; user: User; name: string }[] = [];
    for (const role of ["member", "viewer"]) {
      await test.step(`admin adds a new ${role}; recipient activates and signs in`, async () => {
        const { user, link } = await provision(admin.page, role, role, seen);
        const recipient = await session();
        if (!isOidc) {
          expect(
            (
              await recipient.context.request.post("/api/auth/login", {
                data: { email: user.email, password },
              })
            ).status(),
          ).toBe(403);
          if (invitations) {
            // Invited identities cannot bypass activation through public registration.
            expect(
              (
                await recipient.context.request.post("/api/auth/self-register", {
                  data: { email: user.email, password },
                })
              ).status(),
            ).toBe(409);
            await completeLink(recipient.page, link, "Activate account", password);
          } else {
            await recipient.page.goto("/signin");
            await recipient.page.getByRole("button", { name: "Register with password" }).click();
            await recipient.page.getByLabel("Email", { exact: true }).fill(user.email);
            await recipient.page.getByLabel("Password", { exact: true }).fill(password);
            await recipient.page.getByLabel("Confirm password", { exact: true }).fill(password);
            const registered = recipient.page.waitForResponse(
              (r) => r.url().endsWith("/api/auth/self-register") && r.request().method() === "POST",
            );
            await recipient.page.getByRole("button", { name: "Create account" }).click();
            expect((await registered).ok()).toBeTruthy();
          }
        }
        const identity = await login(recipient.page, role);
        expect(identity.user.role).toBe(role);
        expect(identity.access_allowed).toBe(true);
        // A second session refresh catches credential-version loss after activation.
        expect((await me(recipient.context)).access_allowed).toBe(true);
        const stored = (await users(admin.context.request)).find((entry) => entry.id === user.id)!;
        expect(stored.is_active).toBe(true);
        expect(stored.assigned_instances).toEqual(["Assigned"]);
        if (isOidc) expect(stored.oidc_subject).toBeTruthy();
        accounts.push({ ...recipient, user: stored, name: role });
      });
    }
    await test.step("new users cannot administer accounts or access unassigned instances", async () => {
      for (const account of accounts) {
        const api = account.context.request;
        expect((await api.get("/api/auth/users")).status()).toBe(403);
        expect(
          (
            await api.post("/api/auth/register", {
              data: {
                name: "Escalation",
                email: "escalation@example.test",
                role: "admin",
                auth_provider: isOidc ? "oidc" : "local",
              },
            })
          ).status(),
        ).toBe(403);
        expect(
          (
            await api.put(`/api/auth/users/${account.user.id}/role`, { data: { role: "admin" } })
          ).status(),
        ).toBe(403);
        const listed = await api.get("/api/instances");
        expect(listed.ok()).toBeTruthy();
        expect((await listed.json()).map((item: { name: string }) => item.name)).toEqual([
          "Assigned",
        ]);
        expect((await api.get(`/api/instances/${instances[0].id}`)).status()).toBe(200);
        expect((await api.get(`/api/instances/${instances[1].id}`)).status()).toBe(403);
        expect((await api.delete(`/api/instances/${instances[0].id}`)).status()).toBe(403);
      }
      const update = { data: { url: "http://127.0.0.1:19000", verify_ssl: false } };
      expect(
        (
          await accounts[0].context.request.put(`/api/instances/${instances[0].id}`, update)
        ).status(),
      ).toBe(200);
      expect(
        (
          await accounts[1].context.request.put(`/api/instances/${instances[0].id}`, update)
        ).status(),
      ).toBe(403);
      const changed = await admin.context.request.put(
        `/api/auth/users/${accounts[1].user.id}/instances`,
        { data: { assigned_instances: [] } },
      );
      expect(changed.ok()).toBeTruthy();
      expect(
        (await accounts[1].context.request.get(`/api/instances/${instances[0].id}`)).status(),
      ).toBe(403);
    });

    if (invitations) {
      await test.step("reissued invitations invalidate old links and cancelled invitations cannot activate", async () => {
        const pending = await provision(admin.page, "cancelled", "viewer", seen);
        const reissued = await admin.context.request.post(
          `/api/auth/invitations/${pending.user.id}/reissue`,
        );
        expect(reissued.ok()).toBeTruthy();
        const data = await reissued.json();
        const replacement =
          mode === "smtp"
            ? await mailboxLink(admin.context.request, pending.user.email, "activate-account", seen)
            : data.manual_link;
        const anonymous = await session();
        await anonymous.page.goto(pending.link);
        await expect(
          anonymous.page.getByText("This link is invalid or expired.", { exact: true }),
        ).toBeVisible();
        expect(
          (await admin.context.request.delete(`/api/auth/invitations/${pending.user.id}`)).ok(),
        ).toBeTruthy();
        await anonymous.page.goto(replacement);
        await expect(
          anonymous.page.getByText("This link is invalid or expired.", { exact: true }),
        ).toBeVisible();
        expect(
          (
            await anonymous.context.request.post("/api/auth/invitations/activate", {
              data: { token: new URL(replacement).searchParams.get("token"), password },
            })
          ).status(),
        ).toBe(400);
      });
      await test.step("reset an invited user's password and revoke their old session and token", async () => {
        const member = accounts[0];
        const stale = await session();
        const beforeReset = await stale.context.request.post("/api/auth/login", {
          data: { email: member.user.email, password },
        });
        expect(beforeReset.ok()).toBeTruthy();
        const oldToken = (await beforeReset.json()).access_token;
        const reset = await session();
        let link: string;
        if (mode === "smtp") {
          await reset.page.goto("/forgot-password");
          await reset.page.getByLabel("Email", { exact: true }).fill(member.user.email);
          await reset.page.getByRole("button", { name: "Send reset link" }).click();
          await expect(
            reset.page.getByText(
              "If the account is eligible, password reset instructions will be sent.",
            ),
          ).toBeVisible();
          link = await mailboxLink(
            admin.context.request,
            member.user.email,
            "reset-password",
            seen,
          );
        } else {
          await admin.page.goto("/users");
          await admin.page
            .getByRole("row")
            .filter({ hasText: member.user.email })
            .getByRole("button", { name: "Reset password", exact: true })
            .click();
          const field = admin.page.getByRole("textbox", { name: "One-time account link" });
          await expect(field).toBeVisible();
          link = await field.inputValue();
        }
        const changedPassword = `${password}New2!`;
        await completeLink(reset.page, link, "Reset password", changedPassword);
        expect((await stale.context.request.get("/api/auth/me")).status()).toBe(401);
        expect(
          (
            await stale.context.request.get("/api/auth/me", {
              headers: { Authorization: `Bearer ${oldToken}` },
            })
          ).status(),
        ).toBe(401);
        expect(
          (
            await reset.context.request.post("/api/auth/login", {
              data: { email: member.user.email, password },
            })
          ).status(),
        ).toBe(401);
        expect((await login(reset.page, "member", changedPassword)).access_allowed).toBe(true);
      });
    } else if (mode === "disabled") {
      await test.step("no-delivery mode disables invitations and resets; unknown registrations need approval", async () => {
        expect(
          (
            await admin.context.request.post("/api/auth/invitations", {
              data: { name: "Blocked", email: "blocked@example.test" },
            })
          ).status(),
        ).toBe(400);
        expect(
          (
            await admin.context.request.post("/api/auth/password-resets", {
              data: { user_id: accounts[0].user.id },
            })
          ).status(),
        ).toBe(400);
        const pending = await session();
        const response = await pending.context.request.post("/api/auth/self-register", {
          data: { email: "pending@example.test", password },
        });
        expect(response.ok()).toBeTruthy();
        const user: User = await response.json();
        expect(user.is_active).toBe(false);
        expect(
          (
            await pending.context.request.post("/api/auth/login", {
              data: { email: user.email, password },
            })
          ).status(),
        ).toBe(403);
        expect(
          (
            await admin.context.request.put(`/api/auth/users/${user.id}/role`, {
              data: { role: "viewer" },
            })
          ).ok(),
        ).toBeTruthy();
        expect((await login(pending.page, "pending")).access_allowed).toBe(true);
      });
    } else {
      await test.step("OIDC rejects local authentication and keeps unknown identities pending approval", async () => {
        const pending = await session();
        expect(
          (
            await pending.context.request.post("/api/auth/login", {
              data: { email: "member@example.test", password },
            })
          ).status(),
        ).toBe(400);
        expect(
          (
            await admin.context.request.post("/api/auth/invitations", {
              data: { name: "Blocked", email: "blocked@example.test" },
            })
          ).status(),
        ).toBe(400);
        const identity = await login(pending.page, "pending");
        expect(identity.access_allowed).toBe(false);
        expect((await pending.context.request.get("/api/instances")).status()).toBe(403);
        const user = (await users(admin.context.request)).find(
          (entry) => entry.email === "pending@example.test",
        )!;
        expect(
          (
            await admin.context.request.put(`/api/auth/users/${user.id}/role`, {
              data: { role: "viewer" },
            })
          ).ok(),
        ).toBeTruthy();
        expect((await me(pending.context)).access_allowed).toBe(true);
      });
    }
    await test.step("logout removes application session access; administrator can delete a new user", async () => {
      const viewer = accounts[1];
      expect((await viewer.context.request.post("/logout")).ok()).toBeTruthy();
      expect((await viewer.context.request.get("/api/auth/me")).status()).toBe(401);
      expect(
        (await admin.context.request.delete(`/api/auth/users/${viewer.user.id}`)).status(),
      ).toBe(204);
      expect((await users(admin.context.request)).some((user) => user.id === viewer.user.id)).toBe(
        false,
      );
      if (mode !== "smtp") {
        const mail = await admin.context.request.get(
          `${process.env.AUTH_SMOKE_MAIL_URL}/api/v1/messages`,
        );
        expect((await mail.json()).total).toBe(0);
      }
    });
    testInfo.annotations.push({
      type: "authentication",
      description: `${mode}: additional member and viewer provisioned through the Users UI`,
    });
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
