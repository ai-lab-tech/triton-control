import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  test,
  expect,
  BrowserContext,
  Page,
  FrameLocator,
  Frame,
  APIResponse,
} from "@playwright/test";
import { workspaceStatus } from "./workspace-status";

const namespace = "plugin-smoke";
const phase = process.env.PLUGIN_SMOKE_PHASE || "baseline";
const repository = `smoke-repository-${phase}`;
const model = "smoke_model";
const prefix = "smoke";
const s3Script = resolve(__dirname, "s3.py");
const workspaceImage = "plugin-smoke-workspace:local";
const hfTests = resolve(__dirname, "../../../code-server-extensions/triton-deploy/test");
const hfSource = `hf-checkpoint-${phase}`;
const hfRepository = `hf-repository-${phase}`;
const hfModel = "hf_smoke_model";

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
  let taskWorkspaceId: number | undefined;
  let buildTaskId: string;

  function readFile(path: string): string {
    return kubectl("exec", pod, "--", "cat", path);
  }

  function workspacePython(script: string, ...args: string[]): string {
    return execFileSync(
      "kubectl",
      [
        "--context",
        required("PLUGIN_SMOKE_CONTEXT"),
        "-n",
        namespace,
        "exec",
        "-i",
        pod,
        "--",
        "python3",
        "-",
        ...args,
      ],
      { input: readFileSync(resolve(hfTests, script)), encoding: "utf8", timeout: 120_000 },
    );
  }

  async function buildHfRepository(target: string): Promise<Frame> {
    // A restored workbench can render Explorer before extension commands are ready.
    await command("Triton Control: Refresh Repositories");
    await explorer();
    const localRoot = workspaceRow();
    if ((await localRoot.getAttribute("aria-expanded")) !== "true") {
      await localRoot.locator(".monaco-tl-twistie").click();
    }
    const sourceFolder = workspaceRow(hfSource);
    await expect(sourceFolder).toBeVisible();
    const action = workbench.getByRole("menuitem", {
      name: "Triton Control: Build Repository from Hugging Face",
      exact: true,
    });
    const widget = workbench.locator(".quick-input-widget");
    const runtimePrompt = widget.filter({ hasText: "HF Builder: Serving runtime" });
    // Retry only opening the context menu during workbench restoration. Once
    // activated, inspect may take time: do not cancel/relaunch it every two seconds.
    await expect(async () => {
      await workbench.locator("body").press("Escape");
      await sourceFolder.click({ timeout: 2000 });
      await sourceFolder.click({ button: "right", timeout: 2000 });
      await expect(action).toBeVisible({ timeout: 2000 });
    }).toPass({ timeout: 30_000 });
    // Hover updates Monaco's active menu item; DOM focus alone does not update
    // the action that its Enter handler runs.
    await action.hover();
    await expect(action.locator("..")).toHaveClass(/\bfocused\b/);
    await page.keyboard.press("Enter");
    await expect(action).toBeHidden();
    await expect(runtimePrompt).toBeVisible({ timeout: 60_000 });
    await runtimePrompt.getByText("Transformers / Python", { exact: true }).click();
    await expect(widget).toContainText("HF Builder: Serving device");
    await widget.getByText("cpu", { exact: true }).click();
    await input(workspaceImage, "HF Builder: Target serving image");
    await input(hfModel, "HF Builder: Triton model name");
    await input(`/workspace/${target}`, "HF Builder: Repository destination");
    await expect
      .poll(() => probe(() => readFile(`/workspace/${target}/serving.json`)))
      .toContain('"generator": "triton-hf-builder"');
    await workbench.getByRole("button", { name: "Deploy repository", exact: true }).click();
    let form: Frame | undefined;
    await expect
      .poll(async () => {
        for (const frame of page.frames().reverse()) {
          if (await frame.locator("#deploy-form").count()) {
            form = frame;
            return true;
          }
        }
        return false;
      })
      .toBeTruthy();
    return form!;
  }

  async function command(title: string): Promise<void> {
    // Workspace restoration can dismiss the palette during extension activation.
    // Retry opening the palette if it closes before the command can be clicked.
    const palette = workbench.locator(".quick-input-widget:visible");
    const choice = palette.getByRole("option").filter({ hasText: title }).first();
    await expect(async () => {
      await workbench.locator(".part.editor").click({ position: { x: 50, y: 100 } });
      await workbench.locator("body").press("F1");
      await palette.locator("input").fill(`>${title}`, { timeout: 2000 });
      await choice.click({ timeout: 2000 });
    }).toPass({ timeout: 30_000 });
  }

  async function input(value: string, prompt: string): Promise<void> {
    const widget = workbench.locator(".quick-input-widget");
    await expect(widget).toContainText(prompt);
    await widget.locator("input").fill(value);
    await widget.locator("input").press("Enter");
  }

  async function explorer(): Promise<void> {
    await command("View: Show Explorer");
  }

  function row(name: string) {
    return workbench.locator('.monaco-list-row[role="treeitem"]').filter({
      has: workbench.getByText(name, { exact: true }),
    });
  }

  function workspaceRow(path = "") {
    // Explorer uses ~ for workspace descendants and can append decoration text.
    const labels = path ? [`~/${path}`, `/workspace/${path}`] : ["/workspace"];
    const pathSelector = labels
      .flatMap((label) => [
        `[aria-label=${JSON.stringify(label)}]`,
        `[aria-label^=${JSON.stringify(`${label} • `)}]`,
      ])
      .join(", ");
    return workbench.locator('.monaco-list-row[role="treeitem"]').filter({
      has: workbench.locator(`.explorer-item:is(${pathSelector})`),
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
          endpoint: "http://seaweedfs:9000",
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
          install_hf_builder: true,
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
    workspacePython("hf-builder-fixture.py", `/workspace/${hfSource}`);
    // Seed before Explorer mounts the remote filesystem so its initial listing
    // includes the fixture, even when the workbench restores a cached tree.
    s3("put", `${prefix}/remote-${phase}.txt`, "original smoke content");
    page = await context.newPage();
    // Angular rehydrates authentication from the real backend session cookie.
    await page.goto("/workspaces");
    workbench = page.frameLocator('iframe[title="Workspace"]');
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
      try {
        if (taskWorkspaceId !== undefined) {
          await jsonResponse(await context.request.delete(`/api/development/${taskWorkspaceId}`));
        }
      } finally {
        await context.close();
      }
    }
  });

  async function jsonResponse(response: APIResponse) {
    if (!response.ok())
      throw new Error(`Smoke setup failed: ${response.status()} ${await response.text()}`);
    return response.json();
  }

  for (const title of ["Build serving image", "Inspect serving builds"]) {
    test(`${title} directs personal workspaces to an assigned task`, async () => {
      // Arrange: reload so a previous command's notification cannot satisfy this check.
      await page.goto(`/workspaces?workspace=${workspaceId}`);
      await expect(workbench.locator(".monaco-workbench.vs-dark")).toBeVisible({ timeout: 90_000 });
      await expect(page.locator(".frame-loading")).toBeHidden();
      const notification = workbench.locator(".notifications-toasts");

      // Act
      await command(`Triton Control: ${title}`);

      // Assert
      await expect(notification).toContainText(
        "Open an assigned task workspace from Triton Control first.",
      );
      await expect(workbench.locator(".quick-input-widget")).toBeHidden();
    });
  }

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
    expect(s3("get", `${prefix}/${filename}`)).toBe("original smoke content");
    await command("S3: Choose Profile…");
    await workbench.getByText("Smoke S3", { exact: true }).click();
    const root = row("S3 · Smoke S3 · plugin-smoke");
    await expect(root).toBeVisible();
    // Selecting a profile refreshes Explorer asynchronously and can replace
    // or collapse its root. Re-expand until the actual object is visible.
    await expect(async () => {
      if ((await root.getAttribute("aria-expanded")) !== "true") {
        await root.locator(".monaco-tl-twistie").click();
      }
      await expect(row(filename)).toBeVisible({ timeout: 1000 });
    }).toPass({ timeout: 30_000 });
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
    // Showing Explorer does not expand the local workspace after editing S3 files.
    const localRoot = workspaceRow();
    await expect(localRoot).toBeVisible();
    if ((await localRoot.getAttribute("aria-expanded")) !== "true") {
      await localRoot.locator(".monaco-tl-twistie").click();
    }
    const localRepository = workspaceRow(repository);
    await expect(localRepository).toBeVisible();
    if ((await localRepository.getAttribute("aria-expanded")) === "true") {
      await localRepository.locator(".monaco-tl-twistie").click();
    }
    await expect(localRepository).toHaveAttribute("aria-expanded", "false");
    await localRepository.getByText(repository, { exact: true }).dragTo(root);
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
    await row(filename).click();
    await row(filename).getByText(filename, { exact: true }).dragTo(localRoot);
    await expect
      .poll(() => probe(() => readFile(`/workspace/${filename}`)))
      .toBe("edited smoke content");
    expect(s3("get", `${prefix}/${filename}`)).toBe("edited smoke content");
  });

  test("deployment webview uploads the repository and creates a deployment", async () => {
    await explorer();
    const localRoot = workspaceRow();
    if ((await localRoot.getAttribute("aria-expanded")) !== "true") {
      await localRoot.locator(".monaco-tl-twistie").click();
    }
    await workspaceRow(repository).click({ button: "right" });
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
    await form!.locator("#submit").click();
    // Both the managed extension host and the browser flow open the created instance.
    await expect(page).toHaveURL(/\/instances\/\d+$/, { timeout: 120_000 });
    const instanceId = new URL(page.url()).pathname.split("/").pop()!;
    try {
      await expect(page.getByRole("heading", { name: deploymentName, exact: true })).toBeVisible();
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
      // Managed deletion first asks the durable worker to remove owned resources.
      // Only the documented cleanup conflict is retryable; other errors fail immediately.
      const deadline = Date.now() + 60_000;
      while (true) {
        const deletion = await context.request.delete(`/api/deployments/${instanceId}`);
        if (deletion.status() === 200) break;
        expect(deletion.status(), await deletion.text()).toBe(409);
        expect((await deletion.json()).detail).toContain("cleanup requested");
        expect(Date.now(), "Timed out waiting for owned deployment cleanup").toBeLessThan(deadline);
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      expect(
        kubectl("get", "deployment", deploymentName, "--ignore-not-found", "-o", "name").trim(),
      ).toBe("");
      expect((await context.request.get(`/api/instances/${instanceId}`)).status()).toBe(404);
    }
  });

  test("HF wizard uses the published builder and passes serving metadata to deployment", async () => {
    // Arrange
    await page.goto("/workspaces");
    await expect(workbench.locator(".monaco-workbench.vs-dark")).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".frame-loading")).toBeHidden({ timeout: 30_000 });

    // Act
    const form = await buildHfRepository(hfRepository);
    const manifest = JSON.parse(readFile(`/workspace/${hfRepository}/serving.json`));
    const notebook = JSON.parse(
      workspacePython(
        "hf-builder-notebook.py",
        required("PLUGIN_SMOKE_HF_BUILDER_VERSION"),
        "triton-control",
      ),
    );

    // Assert
    expect(manifest.generator_version).toBe(required("PLUGIN_SMOKE_HF_BUILDER_VERSION"));
    expect(manifest.schema_version).toBe(1);
    expect(manifest.model_name).toBe(hfModel);
    expect(readFile(`/workspace/${hfRepository}/${hfModel}/config.pbtxt`)).toContain(
      'backend: "python"',
    );
    expect(
      readFile(`/workspace/${hfRepository}/${hfModel}/1/model/model.safetensors`).length,
    ).toBeGreaterThan(0);
    await expect(form.locator('[name="image"]')).toHaveValue(manifest.image);
    await expect(form.locator('[name="gpuCount"]')).toHaveValue(String(manifest.gpu_count));
    await expect(form.locator('[name="requirementsTxt"]')).toHaveValue(
      readFile(`/workspace/${hfRepository}/requirements.txt`),
    );
    expect(notebook).toEqual({
      version: required("PLUGIN_SMOKE_HF_BUILDER_VERSION"),
      python: true,
      notebook: true,
    });
  });

  test("workspace and extension remain usable after pod restart or upgrade", async () => {
    test.setTimeout(600_000);
    if (phase === "upgrade") {
      expect(readFile("/workspace/smoke-repository-baseline/smoke_model/1/model.py")).toContain(
        "TritonPythonModel",
      );
      expect(readFile("/workspace/remote-baseline.txt")).toBe("edited smoke content");
      expect(
        JSON.parse(readFile("/workspace/hf-repository-baseline/serving.json")).generator_version,
      ).toBe(required("PLUGIN_SMOKE_HF_BUILDER_VERSION"));
    }
    const oldUid = kubectl("get", "pod", pod, "-o", "jsonpath={.metadata.uid}");
    kubectl("delete", "pod", pod, "--wait=false");
    await expect
      .poll(
        async () => {
          const uid = probe(() => kubectl("get", "pod", pod, "-o", "jsonpath={.metadata.uid}"));
          if (!uid || uid === oldUid) return "restarting";
          return workspaceStatus(context.request, workspaceId);
        },
        { timeout: 540_000, intervals: [3000] },
      )
      .toBe("ready");
    await page.goto("/workspaces");
    expect(readFile(`/workspace/${repository}/${model}/1/model.py`)).toContain("TritonPythonModel");
    expect(readFile(`/workspace/remote-${phase}.txt`)).toBe("edited smoke content");
    await expect(workbench.locator(".monaco-workbench.vs-dark")).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".frame-loading")).toBeHidden({ timeout: 30_000 });
    await expect(
      workbench.getByText("S3 · Smoke S3 · plugin-smoke", { exact: true }),
    ).toBeVisible();
    await command("Triton Control: Refresh Repositories");
    await explorer();
    // Quick Open can still be indexing after restart. Open the persisted file
    // through its local Explorer path, even when the uploaded S3 copy is visible.
    for (const path of ["", repository, `${repository}/${model}`]) {
      const folder = workspaceRow(path);
      if ((await folder.getAttribute("aria-expanded")) !== "true") {
        await folder.locator(".monaco-tl-twistie").click();
      }
    }
    await workspaceRow(`${repository}/${model}/config.pbtxt`).dblclick();
    await expect(workbench.locator(".monaco-editor").first()).toContainText("python");
    expect(JSON.parse(readFile(`/workspace/${hfRepository}/serving.json`)).generator_version).toBe(
      required("PLUGIN_SMOKE_HF_BUILDER_VERSION"),
    );
    const form = await buildHfRepository(`${hfRepository}-restarted`);
    await expect(form.locator('[name="image"]')).toHaveValue(workspaceImage);
    expect(
      JSON.parse(
        workspacePython(
          "hf-builder-notebook.py",
          required("PLUGIN_SMOKE_HF_BUILDER_VERSION"),
          "triton-control",
        ),
      ).notebook,
    ).toBe(true);
  });

  test("task build history uses the managed workspace credential", async () => {
    test.setTimeout(600_000);
    // Arrange: a real version-pinned handoff and claimed task using the default development builder.
    const profiles = await jsonResponse(await context.request.get("/api/s3-profiles"));
    const profileId = profiles.find((profile: { name: string }) => profile.name === "Smoke S3").id;
    const users = await jsonResponse(await context.request.get("/api/auth/users"));
    const userId = users.find((user: { email: string }) => user.email === "plugins@example.com").id;
    const team = await jsonResponse(await context.request.put("/api/teams/engineering"));
    await jsonResponse(await context.request.put(`/api/teams/${team.id}/members/${userId}`));
    const artifact = (role: string) => ({
      profile_id: profileId,
      path: `build-${phase}-${role}.txt`,
    });
    for (const role of ["source", "dataset", "baseline"]) {
      s3("put", `${prefix}/${artifact(role).path}`, `Serving build smoke ${role}`);
    }
    const receipt = await jsonResponse(
      await context.request.post("/api/preparation/handoffs", {
        data: {
          origin: { installation_id: "plugin-smoke", record_id: `serving-build-${phase}` },
          revision: "1",
          title: "Serving build smoke",
          source: artifact("source"),
          dataset: artifact("dataset"),
          baseline: artifact("baseline"),
          expected_behavior: "Read task build history using managed workspace credentials.",
          acceptance_criteria:
            "The assignee can select a Dockerfile and cancel without submitting a build.",
        },
      }),
    );
    await jsonResponse(
      await context.request.post(`/api/preparation-tasks/${receipt.task.id}/claim`, {
        data: { expected_revision: receipt.task.revision },
      }),
    );
    const opened = await jsonResponse(
      await context.request.post(`/api/preparation/tasks/${receipt.task.id}/workspace`, {
        data: {
          workspace: {
            name: `build-smoke-${phase}`,
            image: workspaceImage,
            theme: "Default Dark+",
            // Omit storage_size so task inputs determine the required capacity.
            memory: "1Gi",
            cpu: "250m",
          },
        },
      }),
    );
    taskWorkspaceId = opened.workspace.id;
    buildTaskId = receipt.task.id;
    await expect
      .poll(
        async () => {
          const workspace = await jsonResponse(
            await context.request.get(`/api/development/${taskWorkspaceId}`),
          );
          return workspace.status;
        },
        { timeout: 480_000, intervals: [3000] },
      )
      .toBe("ready");
    await page.goto(`/workspaces?workspace=${taskWorkspaceId}`);
    await expect(workbench.locator(".monaco-workbench.vs-dark")).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".frame-loading")).toBeHidden();
    const notification = workbench.locator(".notifications-toasts");

    // Act
    await command("Triton Control: Inspect serving builds");

    // Assert: the installed extension host authenticated against the real backend.
    await expect(notification).toContainText("No serving builds for this task yet.");
    await expect(workbench.locator(".quick-input-widget")).toBeHidden();
  });

  test("an assigned task can select a Dockerfile and cancel before submitting a build", async () => {
    // Arrange
    const options = await jsonResponse(
      await context.request.get(`/api/builds/options?task_id=${buildTaskId}`),
    );
    expect(options.local_archive).toBe(true);
    const picker = workbench
      .locator(".quick-input-widget")
      .filter({ hasText: "Select Dockerfile" });

    // Act
    await command("Triton Control: Build serving image");
    await expect(picker).toBeVisible();
    await picker.locator("input:visible").press("Escape");

    // Assert
    await expect(picker).toBeHidden();
    expect(
      await jsonResponse(await context.request.get(`/api/builds?task_id=${buildTaskId}`)),
    ).toEqual([]);
  });
});
