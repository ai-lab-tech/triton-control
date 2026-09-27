import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { APIRequestContext, BrowserContext, expect, test } from "@playwright/test";

type Workspace = { id: number; namespace: string; statefulset_name: string };
type Pod = {
  metadata: { uid: string };
  spec: {
    containers: { name: string; volumeMounts: { name: string; mountPath: string }[] }[];
    volumes: { name: string; persistentVolumeClaim?: { claimName: string } }[];
  };
};
type Claim = { metadata: { uid: string }; spec: { volumeName: string }; status: { phase: string } };

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required. Use e2e/workspace-isolation/run.sh.`);
  return value;
}

function kubectl(...args: string[]): string {
  return execFileSync(
    "kubectl",
    ["--context", required("ISOLATION_SMOKE_CONTEXT"), "-n", "workspace-isolation", ...args],
    {
      encoding: "utf8",
      timeout: 60_000,
      stdio: "pipe",
    },
  ).trim();
}

function storage(workspace: Workspace) {
  const podName = `${workspace.statefulset_name}-0`;
  const pod: Pod = JSON.parse(kubectl("get", "pod", podName, "-o", "json"));
  const container = pod.spec.containers.find((item) => item.name === "code-server");
  const mount = container?.volumeMounts.find((item) => item.mountPath === "/workspace");
  expect(mount, "code-server must mount /workspace").toBeDefined();
  const claimName = pod.spec.volumes.find((item) => item.name === mount!.name)
    ?.persistentVolumeClaim?.claimName;
  expect(claimName, "/workspace must use a PVC").toBeTruthy();
  const claim: Claim = JSON.parse(kubectl("get", "pvc", claimName!, "-o", "json"));
  expect(claim.status.phase).toBe("Bound");
  expect(claim.spec.volumeName).toBeTruthy();
  return {
    podName,
    podUid: pod.metadata.uid,
    claimName,
    claimUid: claim.metadata.uid,
    volumeName: claim.spec.volumeName,
  };
}

async function ready(api: APIRequestContext, workspace: Workspace) {
  await expect
    .poll(
      async () => {
        const response = await api.get(`/api/development/${workspace.id}`);
        expect(response.ok()).toBeTruthy();
        return (await response.json()).status;
      },
      { timeout: 540_000, intervals: [3000] },
    )
    .toBe("ready");
}

test("members have separate pods and PVCs, isolated access, and persistent files", async ({
  browser,
  baseURL,
}, testInfo) => {
  const contexts: BrowserContext[] = [];
  const password = required("ISOLATION_SMOKE_PASSWORD");
  const version = required("ISOLATION_SMOKE_VERSION");
  testInfo.annotations.push({ type: "code-server", description: version });
  try {
    const admin = await browser.newContext({ baseURL });
    contexts.push(admin);
    const credentials = { email: "isolation-admin@example.com", password };
    const bootstrap = await admin.request.post("/api/auth/bootstrap/register", {
      data: credentials,
    });
    expect(bootstrap.ok()).toBeTruthy();
    expect((await admin.request.post("/api/auth/login", { data: credentials })).ok()).toBeTruthy();

    const members: { context: BrowserContext; workspace: Workspace }[] = [];
    await test.step("invite and activate two members with separate sessions and same workspace name", async () => {
      for (const name of ["alice", "bob"]) {
        const email = `${name}@example.com`;
        const invite = await admin.request.post("/api/auth/invitations", {
          data: { email, name, role: "member" },
        });
        expect(invite.ok()).toBeTruthy();
        const { manual_link: link } = await invite.json();
        expect(link).toBeTruthy();
        const context = await browser.newContext({ baseURL });
        contexts.push(context);
        expect(
          (
            await context.request.post("/api/auth/invitations/activate", {
              data: { token: new URL(link).searchParams.get("token"), password },
            })
          ).ok(),
        ).toBeTruthy();
        expect(
          (await context.request.post("/api/auth/login", { data: { email, password } })).ok(),
        ).toBeTruthy();
        const response = await context.request.post("/api/development", {
          data: {
            name: "isolation",
            image: "workspace-isolation-workspace:local",
            storage_size: "1Gi",
            memory: "1Gi",
            cpu: "250m",
          },
        });
        expect(response.ok(), await response.text()).toBeTruthy();
        const workspace: Workspace = await response.json();
        expect(workspace.namespace).toBe("workspace-isolation");
        members.push({ context, workspace });
      }
      await Promise.all(members.map(({ context, workspace }) => ready(context.request, workspace)));
    });

    const [alice, bob] = members;
    const resources = members.map(({ workspace }) => storage(workspace));
    await test.step("verify distinct pods, mounted PVCs, and backing volumes", async () => {
      expect(alice.workspace.id).not.toBe(bob.workspace.id);
      expect(alice.workspace.statefulset_name).not.toBe(bob.workspace.statefulset_name);
      for (const key of ["podName", "podUid", "claimName", "claimUid", "volumeName"] as const) {
        expect(resources[0][key], key).not.toBe(resources[1][key]);
      }
      for (const resource of resources) {
        expect(
          kubectl(
            "exec",
            resource.podName,
            "--",
            "/tmp/triton-control-code-server/bin/code-server",
            "--version",
          ),
        ).toContain(version);
      }
      await testInfo.attach("workspace-storage", {
        body: JSON.stringify({ version, resources }, null, 2),
        contentType: "application/json",
      });
    });

    await test.step("each member sees only their workspace and cannot read, proxy, or delete the other", async () => {
      for (const [owner, other] of [
        [alice, bob],
        [bob, alice],
      ]) {
        const response = await owner.context.request.get("/api/development");
        expect(response.ok()).toBeTruthy();
        expect((await response.json()).map((item: Workspace) => item.id)).toEqual([
          owner.workspace.id,
        ]);
        const foreign = `/api/development/${other.workspace.id}`;
        expect((await owner.context.request.get(foreign)).status()).toBe(403);
        expect((await owner.context.request.get(`${foreign}/proxy/`)).status()).toBe(403);
        expect((await owner.context.request.delete(foreign)).status()).toBe(403);
        const page = await owner.context.newPage();
        await page.goto("/development");
        await expect(
          page.frameLocator('iframe[title="Development"]').locator(".monaco-workbench"),
        ).toBeVisible({ timeout: 90_000 });
        await page.close();
      }
    });

    const markers = [randomUUID(), randomUUID()];
    const file = "/workspace/isolation-owner.txt";
    await test.step("same file path contains different private content in each workspace", async () => {
      for (let i = 0; i < resources.length; i++) {
        kubectl(
          "exec",
          resources[i].podName,
          "--",
          "python3",
          "-c",
          "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])",
          file,
          markers[i],
        );
      }
      for (let i = 0; i < resources.length; i++) {
        expect(kubectl("exec", resources[i].podName, "--", "cat", file)).toBe(markers[i]);
      }
    });

    await test.step("restarted pod keeps its PVC and files without changing the other user", async () => {
      kubectl("delete", "pod", resources[0].podName, "--wait=false");
      await expect
        .poll(
          () => {
            try {
              return JSON.parse(kubectl("get", "pod", resources[0].podName, "-o", "json")).metadata
                .uid;
            } catch {
              return resources[0].podUid;
            }
          },
          { timeout: 180_000, intervals: [3000] },
        )
        .not.toBe(resources[0].podUid);
      await ready(alice.context.request, alice.workspace);
      const restarted = storage(alice.workspace);
      expect(restarted.claimUid).toBe(resources[0].claimUid);
      expect(restarted.volumeName).toBe(resources[0].volumeName);
      expect(storage(bob.workspace)).toEqual(resources[1]);
      for (let i = 0; i < resources.length; i++) {
        expect(kubectl("exec", resources[i].podName, "--", "cat", file)).toBe(markers[i]);
      }
    });
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
