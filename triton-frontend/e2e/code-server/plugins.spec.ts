import { execFileSync } from "node:child_process";
import { basename, resolve } from "node:path";
import { test, expect, BrowserContext, Page, FrameLocator, Frame } from "@playwright/test";

const namespace = "plugin-smoke";
const phase = process.env.PLUGIN_SMOKE_PHASE || "baseline";
const repository = `smoke-repository-${phase}`;
const model = "smoke_model";
const prefix = "smoke";
const s3Script = resolve(__dirname, "s3.py");
const workspaceImage = "plugin-smoke-workspace:local";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required. Use e2e/code-server/run.sh.`);
  return value;
}

function kubectl(...args: string[]): string {
  return execFileSync(
    "kubectl",
    ["--context", required("PLUGIN_SMOKE_CONTEXT"), "-n", namespace, ...args],
    {
      encoding: "utf8",
      stdio: "pipe",
      timeout: 60_000,
    },
  );
}

function s3(operation: string, ...args: string[]): string {
  return execFileSync(
    process.env.PLUGIN_SMOKE_PYTHON || "python3",
    [s3Script, operation, ...args],
    {
      encoding: "utf8",
      stdio: "pipe",
      timeout: 30_000,
    },
  );
}

// Pods and objects may not exist yet while a real transfer/restart is in progress.
function probe<T>(read: () => T): T | null {
  try {
    return read();
  } catch {
    return null;
  }
}

test.describe.serial("code-server plugin workflows", () => {
  let context: BrowserContext;
  let page: Page;
  let workbench: FrameLocator;
  let pod: string;
  let workspaceId: number;

  function readFile(path: string): string {
    return kubectl("exec", pod, "--", "cat", path);
  }

  async function command(title: string): Promise<void> {
    // Workspace restoration can dismiss the palette during extension activation.
    // Wait for the command to remain available before executing it once.
    const choice = workbench
      .locator(".quick-input-list .monaco-list-row")
      .filter({ hasText: title })
      .first();
    await expect(async () => {
      await workbench.locator(".part.editor").click({ position: { x: 50, y: 100 } });
      await workbench.locator("body").press("F1");
      const input = workbench.locator(".quick-input-widget input");
      await expect(input).toBeVisible({ timeout: 2000 });
      await input.fill(`>${title}`);
      await expect(choice).toBeVisible({ timeout: 2000 });
    }).toPass({ timeout: 30_000 });
    await choice.click();
  }

  async function input(value: string, prompt: string): Promise<void> {
    const widget = workbench.locator(".quick-input-widget");
    await expect(widget).toContainText(prompt);
    await widget.locator("input").fill(value);
    await widget.locator("input").press("Enter");
  }

  async function openFile(path: string): Promise<void> {
    await page.keyboard.press("Control+p");
    const quick = workbench.locator(".quick-input-widget input");
    await expect(quick).toBeVisible();
    await quick.fill(path);
    await workbench
      .locator(".quick-input-list .monaco-list-row")
      .filter({ hasText: basename(path) })
      .first()
      .click();
    await expect(quick).toBeHidden();
  }

  async function explorer(): Promise<void> {
    await command("View: Show Explorer");
  }

  function row(name: string) {
    return workbench.locator('.monaco-list-row[role="treeitem"]').filter({
      has: workbench.getByText(name, { exact: true }),
    });
  }

  test.beforeAll(async ({ browser, baseURL }) => {
    test.setTimeout(600_000);
    context = await browser.newContext({
      baseURL,
      viewport: { width: 1600, height: 1100 },
      recordVideo: { dir: "test-results/code-server/videos" },
    });
    const credentials = {
      email: "plugins@example.com",
      password: required("PLUGIN_SMOKE_PASSWORD"),
    };
    const bootstrap = await context.request.get("/api/auth/bootstrap-status");
    expect(bootstrap.ok()).toBeTruthy();
    if ((await bootstrap.json()).needs_setup) {
      expect(
        (await context.request.post("/api/auth/bootstrap/register", { data: credentials })).ok(),
      ).toBeTruthy();
    }
    expect(
      (await context.request.post("/api/auth/login", { data: credentials })).ok(),
    ).toBeTruthy();
    const profiles = await context.request.get("/api/s3-profiles");
    expect(profiles.ok()).toBeTruthy();
    if (!(await profiles.json()).some((p: { name: string }) => p.name === "Smoke S3")) {
      const created = await context.request.post("/api/s3-profiles", {
        data: {
          name: "Smoke S3",
          endpoint: "http://minio:9000",
          bucket: "plugin-smoke",
          prefix,
          access_key: required("PLUGIN_SMOKE_S3_KEY"),
          secret_key: required("PLUGIN_SMOKE_S3_SECRET"),
          region: "us-east-1",
          force_path_style: true,
        },
      });
      expect(created.ok()).toBeTruthy();
    }
    const workspaces = await context.request.get("/api/development");
    expect(workspaces.ok()).toBeTruthy();
    let workspace = (await workspaces.json())[0];
    if (!workspace) {
      const response = await context.request.post("/api/development", {
        data: {
          name: "plugin-smoke",
          theme: "Default Dark+",
          image: workspaceImage,
          storage_size: "1Gi",
          memory: "1Gi",
          cpu: "250m",
        },
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      workspace = await response.json();
    }
    workspaceId = workspace.id;
    pod = `${workspace.statefulset_name}-0`;
    await expect
      .poll(
        async () => {
          const response = await context.request.get(`/api/development/${workspaceId}`);
          expect(response.ok()).toBeTruthy();
          return (await response.json()).status;
        },
        { timeout: 540_000, intervals: [3000] },
      )
      .toBe("ready");
    expect(
      kubectl("exec", pod, "--", "/tmp/triton-control-code-server/bin/code-server", "--version"),
    ).toContain(required("PLUGIN_SMOKE_VERSION"));
    page = await context.newPage();
    // Angular rehydrates authentication from the real backend session cookie.
    await page.goto("/development");
    workbench = page.frameLocator('iframe[title="Development"]');
    await expect(workbench.locator(".monaco-workbench.vs-dark")).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".frame-loading")).toBeHidden({ timeout: 30_000 });
    await expect(
      workbench.getByText("S3 · Smoke S3 · plugin-smoke", { exact: true }),
    ).toBeVisible();
  });

  test.afterEach(async () => {
    const testInfo = test.info();
    if (testInfo.status !== testInfo.expectedStatus && page) {
      await testInfo.attach("workspace", {
        body: await page.screenshot(),
        contentType: "image/png",
      });
    }
  });

  test.afterAll(async () => {
    if (context) {
      await context.close();
    }
  });

  test("repository creator writes a Python model through the real wizard", async () => {
    await command("Triton Control: New Model Repository");
    await workbench.getByText("Single model", { exact: true }).click();
    await input(repository, "Repository target folder name");
    await input(model, "Model name");
    await workbench.getByText("Python backend", { exact: true }).click();
    await expect
      .poll(() => probe(() => readFile(`/workspace/${repository}/${model}/config.pbtxt`)))
      .toContain('backend: "python"');
    expect(readFile(`/workspace/${repository}/${model}/1/model.py`)).toContain("TritonPythonModel");
    await command("View: Show Triton Control");
    await expect(workbench.getByText(repository, { exact: true })).toBeVisible();
  });

  test("S3 Explorer edits objects and copies a local folder using drag and drop", async () => {
    const filename = `remote-${phase}.txt`;
    s3("put", `${prefix}/${filename}`, "original smoke content");
    await command("S3: Choose Profile…");
    await workbench.getByText("Smoke S3", { exact: true }).click();
    const root = row("S3 · Smoke S3 · plugin-smoke");
    await expect(root).toBeVisible();
    if ((await root.getAttribute("aria-expanded")) !== "true") {
      await root.locator(".monaco-tl-twistie").click();
    }
    await row(filename).dblclick();
    const editor = workbench.locator(".part.editor .monaco-editor .view-lines");
    await expect(editor).toContainText("original smoke content");
    await editor.click();
    await page.keyboard.press("Control+a");
    await page.keyboard.type("edited smoke content");
    await page.keyboard.press("Control+s");
    await expect
      .poll(() => probe(() => s3("get", `${prefix}/${filename}`)))
      .toBe("edited smoke content");
    await explorer();
    await expect(row(repository)).toBeVisible();
    if ((await row(repository).getAttribute("aria-expanded")) === "true") {
      await row(repository).locator(".monaco-tl-twistie").click();
    }
    await expect(row(repository)).toHaveAttribute("aria-expanded", "false");
    await row(repository).getByText(repository, { exact: true }).dragTo(root);
    await expect
      .poll(() => probe(() => s3("get", `${prefix}/${repository}/${model}/config.pbtxt`)), {
        timeout: 60_000,
      })
      .toBe(readFile(`/workspace/${repository}/${model}/config.pbtxt`));
    await expect
      .poll(() => probe(() => s3("get", `${prefix}/${repository}/${model}/1/model.py`)))
      .toBe(readFile(`/workspace/${repository}/${model}/1/model.py`));
    expect(readFile(`/workspace/${repository}/${model}/1/model.py`)).toContain("TritonPythonModel");
    // Reverse direction exercises S3 -> workspace copy, including patched drop handling.
    const localRoot = row("workspace");
    await row(filename).click();
    await row(filename).getByText(filename, { exact: true }).dragTo(localRoot);
    await expect
      .poll(() => probe(() => readFile(`/workspace/${filename}`)))
      .toBe("edited smoke content");
    expect(s3("get", `${prefix}/${filename}`)).toBe("edited smoke content");
    // Hide the remote repository copy before selecting the local repository for deployment.
    await root.click();
    await page.keyboard.press("ArrowLeft");
  });

  test("deployment webview uploads the repository and creates a deployment", async () => {
    await explorer();
    const localRoot = row("workspace");
    if ((await localRoot.getAttribute("aria-expanded")) !== "true") {
      await localRoot.locator(".monaco-tl-twistie").click();
    }
    const remoteRoot = row("S3 · Smoke S3 · plugin-smoke");
    if ((await remoteRoot.getAttribute("aria-expanded")) === "true") {
      await remoteRoot.locator(".monaco-tl-twistie").click();
    }
    await row(repository).click({ button: "right" });
    await workbench
      .getByRole("menuitem", { name: "Triton Control: Deploy Model Repository", exact: true })
      .hover();
    await page.keyboard.press("Enter");
    let form: Frame | undefined;
    await expect
      .poll(async () => {
        for (const frame of page.frames()) {
          if (await frame.locator("#deploy-form").count()) {
            form = frame;
            return true;
          }
        }
        return false;
      })
      .toBeTruthy();
    const deploymentName = `plugin-smoke-${phase}`;
    await form!.locator('[name="deploymentName"]').fill(deploymentName);
    await form!.locator('[name="profileId"]').selectOption({ label: "Smoke S3" });
    await form!.locator('[name="prefix"]').fill(prefix);
    // Small image: this smoke test verifies upload and deployment creation, not GPU inference.
    await form!.locator('[name="image"]').fill(workspaceImage);
    const responsePromise = page.waitForResponse(
      (r) => r.url().endsWith("/api/deployments") && r.request().method() === "POST",
    );
    await form!.locator("#submit").click();
    const response = await responsePromise;
    expect(response.ok(), await response.text()).toBeTruthy();
    const deployment = await response.json();
    try {
      await expect
        .poll(() => probe(() => s3("get", `${prefix}/${deploymentName}/${model}/config.pbtxt`)))
        .toBe(readFile(`/workspace/${repository}/${model}/config.pbtxt`));
      expect(s3("get", `${prefix}/${deploymentName}/${model}/1/model.py`)).toBe(
        readFile(`/workspace/${repository}/${model}/1/model.py`),
      );
      await expect
        .poll(() =>
          probe(() =>
            kubectl("get", "deployment", deploymentName, "-o", "jsonpath={.metadata.name}"),
          ),
        )
        .toBe(deploymentName);
    } finally {
      expect(
        (await context.request.delete(`/api/deployments/${deployment.instance_id}`)).ok(),
      ).toBeTruthy();
    }
  });

  test("workspace and extension remain usable after pod restart or upgrade", async () => {
    test.setTimeout(600_000);
    if (phase === "upgrade") {
      expect(readFile("/workspace/smoke-repository-baseline/smoke_model/1/model.py")).toContain(
        "TritonPythonModel",
      );
      expect(readFile("/workspace/remote-baseline.txt")).toBe("edited smoke content");
    }
    const oldUid = kubectl("get", "pod", pod, "-o", "jsonpath={.metadata.uid}");
    kubectl("delete", "pod", pod, "--wait=false");
    await expect
      .poll(
        async () => {
          const uid = probe(() => kubectl("get", "pod", pod, "-o", "jsonpath={.metadata.uid}"));
          if (!uid || uid === oldUid) return "restarting";
          const response = await context.request.get(`/api/development/${workspaceId}`);
          return (await response.json()).status;
        },
        { timeout: 540_000, intervals: [3000] },
      )
      .toBe("ready");
    await page.goto("/development");
    expect(readFile(`/workspace/${repository}/${model}/1/model.py`)).toContain("TritonPythonModel");
    expect(readFile(`/workspace/remote-${phase}.txt`)).toBe("edited smoke content");
    await expect(workbench.locator(".monaco-workbench.vs-dark")).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".frame-loading")).toBeHidden({ timeout: 30_000 });
    await expect(
      workbench.getByText("S3 · Smoke S3 · plugin-smoke", { exact: true }),
    ).toBeVisible();
    await command("Triton Control: Refresh Repositories");
    await openFile(`/workspace/${repository}/${model}/config.pbtxt`);
    await expect(workbench.locator(".monaco-editor").first()).toContainText("python");
  });
});
