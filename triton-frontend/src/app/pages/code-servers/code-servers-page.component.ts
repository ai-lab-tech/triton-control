import { Component, ElementRef, OnDestroy, ViewChild, effect, inject, signal } from "@angular/core";
import { HttpErrorResponse } from "@angular/common/http";
import { DomSanitizer, SafeResourceUrl } from "@angular/platform-browser";
import { FormsModule } from "@angular/forms";
import { Router } from "@angular/router";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatExpansionModule } from "@angular/material/expansion";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { MatCheckboxModule } from "@angular/material/checkbox";

import {
  BASE_PATH,
  CodeServerDTO,
  CodeServersService,
  CreateCodeServerRequest,
} from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { ChromeService } from "../../shared/chrome.service";
import { AuthService } from "../../shared/auth/auth.service";

type CodeServer = CodeServerDTO;

@Component({
  selector: "app-code-servers-page",
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatExpansionModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatSelectModule,
    MatCheckboxModule,
  ],
  styleUrl: "./code-servers-page.component.scss",
  templateUrl: "./code-servers-page.component.html",
})
export class CodeServersPageComponent implements OnDestroy {
  private static readonly statusPollIntervalMs = 3000;
  private static readonly deploymentNavigationPollIntervalMs = 1500;
  private static readonly handledDeploymentNavigationKey =
    "triton-control-handled-deployment-navigation-instance-ids";

  private readonly codeServersApi = inject(CodeServersService);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly chrome = inject(ChromeService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");
  private statusPollId: ReturnType<typeof setInterval> | null = null;
  private statusPollInFlight = false;
  private deploymentNavigationPollId: ReturnType<typeof setInterval> | null = null;
  private deploymentNavigationPollInFlight = false;
  private frameReloadNonce = 0;
  private frameLoadStartedAt = 0;
  private frameLoaderHideTimer: ReturnType<typeof setTimeout> | null = null;
  private static readonly minFrameLoaderMs = 3500;
  private static readonly postLoadGraceMs = 2500;
  @ViewChild("codeServerFrame") private codeServerFrame?: ElementRef<HTMLIFrameElement>;

  name = "workspace";
  image = "nvcr.io/nvidia/tritonserver:25.02-py3";
  theme: CreateCodeServerRequest["theme"] = "Default Dark+";
  storageSize = "20Gi";
  cpu = "";
  memory = "";
  gpuCount: string | number = "";
  dockerconfigjson = "";
  imageHasCodeServer = false;

  readonly loading = signal(false);
  readonly initialLoaded = signal(false);
  readonly saving = signal(false);
  readonly deletingId = signal<number | null>(null);
  readonly workspaces = signal<CodeServer[]>([]);
  readonly selectedWorkspaceId = signal<number | null>(null);
  readonly embeddedWorkspaceUrl = signal<SafeResourceUrl | null>(null);
  readonly frameLoading = signal(false);
  readonly workspacePanelCollapsed = signal(false);
  readonly message = signal("");
  readonly messageTone = signal<"info" | "success" | "error">("info");

  constructor() {
    effect(() => {
      if (this.selectedWorkspaceId() !== null) {
        this.chrome.hideTopbar();
      } else {
        this.chrome.showTopbar();
      }
    });
    window.addEventListener("message", this.handleCodeServerMessage);
    this.startDeploymentNavigationPolling();
    void this.load();
  }

  ngOnDestroy(): void {
    window.removeEventListener("message", this.handleCodeServerMessage);
    this.chrome.showTopbar();
    this.stopStatusPolling();
    this.stopDeploymentNavigationPolling();
    if (this.frameLoaderHideTimer !== null) {
      clearTimeout(this.frameLoaderHideTimer);
      this.frameLoaderHideTimer = null;
    }
  }

  private readonly handleCodeServerMessage = (event: MessageEvent): void => {
    void this.handleCodeServerMessageAsync(event);
  };

  private async handleCodeServerMessageAsync(event: MessageEvent): Promise<void> {
    if (event.origin && event.origin !== "null" && event.origin !== window.location.origin) {
      return;
    }
    const data = event.data as Record<string, unknown> | null;
    if (
      !data ||
      data["source"] !== "triton-control-deploy" ||
      data["type"] !== "deploymentCreated"
    ) {
      return;
    }
    const instanceId = Number(data["instanceId"]);
    if (!Number.isInteger(instanceId) || instanceId <= 0) {
      return;
    }
    if (!this.router.url.startsWith("/code-servers")) {
      return;
    }
    if (this.hasHandledDeploymentNavigation(instanceId)) {
      await this.consumeDeploymentNavigationTarget();
      return;
    }
    await this.consumeDeploymentNavigationTarget();
    this.navigateToDeploymentInstance(instanceId);
  }

  private async pollDeploymentNavigationTarget(): Promise<void> {
    if (this.deploymentNavigationPollInFlight || !this.router.url.startsWith("/code-servers")) {
      return;
    }
    this.deploymentNavigationPollInFlight = true;
    try {
      const instanceId = await this.consumeDeploymentNavigationTarget();
      if (instanceId !== null) {
        this.navigateToDeploymentInstance(instanceId);
      }
    } finally {
      this.deploymentNavigationPollInFlight = false;
    }
  }

  private navigateToDeploymentInstance(instanceId: number): void {
    if (this.hasHandledDeploymentNavigation(instanceId)) {
      return;
    }
    this.markDeploymentNavigationHandled(instanceId);
    void this.router.navigateByUrl(`/instances/${instanceId}`, {
      state: { openLogsOnce: true },
    });
  }

  private async consumeDeploymentNavigationTarget(): Promise<number | null> {
    try {
      const payload = await firstValueFrom(
        this.codeServersApi.consumeCodeServerDeploymentNavigationApiCodeServersDeploymentNavigationGet(),
      );
      const instanceId = Number(payload.instance_id);
      return Number.isInteger(instanceId) && instanceId > 0 ? instanceId : null;
    } catch {
      return null;
    }
  }

  private hasHandledDeploymentNavigation(instanceId: number): boolean {
    try {
      return this.readHandledDeploymentNavigationIds().includes(instanceId);
    } catch {
      return false;
    }
  }

  private markDeploymentNavigationHandled(instanceId: number): void {
    try {
      const ids = this.readHandledDeploymentNavigationIds().filter((id) => id !== instanceId);
      ids.push(instanceId);
      window.sessionStorage.setItem(
        CodeServersPageComponent.handledDeploymentNavigationKey,
        JSON.stringify(ids.slice(-25)),
      );
    } catch {
      // Session storage can be unavailable in constrained browser contexts.
    }
  }

  private readHandledDeploymentNavigationIds(): number[] {
    const raw = window.sessionStorage.getItem(
      CodeServersPageComponent.handledDeploymentNavigationKey,
    );
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value > 0);
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const workspaces = (await firstValueFrom(
        this.codeServersApi.listCodeServersApiCodeServersGet(),
      )) as CodeServer[];
      this.workspaces.set(workspaces);
      await this.ensureSelectedWorkspace();
      this.updateStatusPolling();
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to load code servers."), "error");
    } finally {
      this.loading.set(false);
      this.initialLoaded.set(true);
    }
  }

  async create(): Promise<void> {
    if (!this.canCreate()) {
      this.setMessage("Workspace name and image are required.", "error");
      return;
    }
    this.saving.set(true);
    this.setMessage("", "info");
    try {
      // Best effort: do not block workspace creation when refresh probes fail.
      await this.auth.refreshSession().catch(() => undefined);
      const payload: CreateCodeServerRequest = {
        name: this.name.trim(),
        image: this.image.trim(),
        theme: this.theme,
        storage_size: this.storageSize.trim() || "20Gi",
        cpu: this.cpu.trim() || undefined,
        cpu_limit: this.cpu.trim() || undefined,
        memory: this.memory.trim() || undefined,
        memory_limit: this.memory.trim() || undefined,
        gpu_count: this.parseGpuCount(),
        dockerconfigjson: this.dockerconfigjson.trim() || undefined,
        image_has_code_server: this.imageHasCodeServer,
      };
      const workspace = await firstValueFrom(
        this.codeServersApi.createCodeServerApiCodeServersPost(payload),
      );
      this.upsertWorkspace(workspace);
      this.selectWorkspace(workspace);
      this.updateStatusPolling();
      await this.pollPendingWorkspaces();
      this.setMessage("Code server created.", "success");
    } catch (error) {
      console.error("Code server create failed", error);
      if (this.isCanceledError(error)) {
        this.setMessage(
          "Request was canceled in the browser. Please retry and check Network tab for POST /api/code-servers.",
          "error",
        );
        return;
      }
      if (!(error instanceof HttpErrorResponse) && error instanceof Error && error.message.trim()) {
        this.setMessage(`Create failed before API request: ${error.message.trim()}`, "error");
        return;
      }
      this.setMessage(mapApiErrorMessage(error, "Failed to create code server."), "error");
    } finally {
      this.saving.set(false);
    }
  }

  private isCanceledError(error: unknown): boolean {
    if (!error || typeof error !== "object") {
      return false;
    }
    const maybe = error as { name?: unknown; message?: unknown };
    const name = typeof maybe.name === "string" ? maybe.name.toLowerCase() : "";
    const message = typeof maybe.message === "string" ? maybe.message.toLowerCase() : "";
    return name.includes("canceled") || message.includes("canceled");
  }

  private parseGpuCount(): number | undefined {
    const raw = typeof this.gpuCount === "number" ? String(this.gpuCount) : this.gpuCount;
    const parsed = Number.parseInt((raw || "").trim(), 10);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
  }

  async refresh(workspace: CodeServer): Promise<void> {
    try {
      const updated = await firstValueFrom(
        this.codeServersApi.getCodeServerApiCodeServersCodeServerIdGet(workspace.id),
      );
      this.upsertWorkspace(updated);
      if (this.selectedWorkspaceId() === updated.id) {
        await this.auth.refreshSession().catch(() => undefined);
        this.setEmbeddedWorkspace(updated);
      }
      this.updateStatusPolling();
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to refresh code server."), "error");
    }
  }

  async delete(workspace: CodeServer): Promise<void> {
    this.deletingId.set(workspace.id);
    try {
      await firstValueFrom(
        this.codeServersApi.deleteCodeServerApiCodeServersCodeServerIdDelete(workspace.id),
      );
      this.workspaces.update((items) => items.filter((item) => item.id !== workspace.id));
      if (this.selectedWorkspaceId() === workspace.id) {
        this.selectedWorkspaceId.set(null);
        this.embeddedWorkspaceUrl.set(null);
        await this.ensureSelectedWorkspace();
      }
      this.updateStatusPolling();
      this.setMessage("Code server deleted.", "success");
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to delete code server."), "error");
    } finally {
      this.deletingId.set(null);
    }
  }

  selectWorkspace(workspace: CodeServer): void {
    void this.selectWorkspaceAsync(workspace);
  }

  canCreate(): boolean {
    return this.name.trim().length > 0 && this.image.trim().length > 0 && !this.saving();
  }

  toggleWorkspacePanel(): void {
    this.workspacePanelCollapsed.update((collapsed) => !collapsed);
    this.scheduleFrameResize();
  }

  selectedWorkspace(): CodeServer | null {
    const selectedId = this.selectedWorkspaceId();
    return this.workspaces().find((workspace) => workspace.id === selectedId) ?? null;
  }

  imageLabel(image: string): string {
    const trimmed = image.trim();
    if (trimmed === "nvcr.io/nvidia/tritonserver:25.02-py3") {
      return "Triton SDK 25.02";
    }
    return trimmed.split("/").pop() || trimmed;
  }

  private upsertWorkspace(workspace: CodeServer): void {
    this.workspaces.update((items) => {
      const index = items.findIndex((item) => item.id === workspace.id);
      if (index === -1) {
        return [workspace, ...items];
      }
      const next = [...items];
      next[index] = workspace;
      return next;
    });
  }

  private async ensureSelectedWorkspace(): Promise<void> {
    const workspaces = this.workspaces();
    const selectedId = this.selectedWorkspaceId();
    const selected = workspaces.find((workspace) => workspace.id === selectedId);
    if (selected) {
      await this.selectWorkspaceAsync(selected);
      return;
    }
    const first = workspaces[0];
    if (first) {
      await this.selectWorkspaceAsync(first);
      return;
    }
    this.selectedWorkspaceId.set(null);
    this.embeddedWorkspaceUrl.set(null);
  }

  private async selectWorkspaceAsync(workspace: CodeServer): Promise<void> {
    this.selectedWorkspaceId.set(workspace.id);
    if (workspace.status === "ready" && workspace.url) {
      this.embeddedWorkspaceUrl.set(null);
      this.frameLoading.set(true);
      await this.auth.refreshSession().catch(() => undefined);
    }
    if (this.selectedWorkspaceId() !== workspace.id) {
      return;
    }
    this.setEmbeddedWorkspace(workspace);
  }

  onFrameLoaded(): void {
    const elapsed = Date.now() - this.frameLoadStartedAt;
    const remainingMin = CodeServersPageComponent.minFrameLoaderMs - elapsed;
    const delay = Math.max(remainingMin, CodeServersPageComponent.postLoadGraceMs);
    if (this.frameLoaderHideTimer !== null) {
      clearTimeout(this.frameLoaderHideTimer);
      this.frameLoaderHideTimer = null;
    }
    this.frameLoaderHideTimer = setTimeout(() => {
      this.frameLoading.set(false);
      this.frameLoaderHideTimer = null;
    }, delay);
  }

  private setEmbeddedWorkspace(workspace: CodeServer): void {
    if (workspace.status === "ready" && workspace.url) {
      this.workspacePanelCollapsed.set(true);
      this.frameReloadNonce += 1;
      if (this.frameLoaderHideTimer !== null) {
        clearTimeout(this.frameLoaderHideTimer);
        this.frameLoaderHideTimer = null;
      }
      this.frameLoadStartedAt = Date.now();
      this.frameLoading.set(true);
      this.embeddedWorkspaceUrl.set(
        this.sanitizer.bypassSecurityTrustResourceUrl(
          this.withFrameReloadNonce(this.proxyUrl(workspace.url)),
        ),
      );
      this.scheduleFrameResize();
      return;
    }
    this.workspacePanelCollapsed.set(false);
    this.frameLoading.set(false);
    this.embeddedWorkspaceUrl.set(null);
  }

  private scheduleFrameResize(): void {
    const notify = () => {
      try {
        this.codeServerFrame?.nativeElement.contentWindow?.dispatchEvent(new Event("resize"));
      } catch {
        // The iframe can briefly be unavailable while Angular swaps the src.
      }
    };
    window.setTimeout(notify, 80);
    window.setTimeout(notify, 260);
  }

  private proxyUrl(path: string): string {
    if (/^https?:\/\//i.test(path)) {
      return path;
    }
    return `${this.basePath}${path.startsWith("/") ? path : `/${path}`}`;
  }

  private setMessage(message: string, tone: "info" | "success" | "error"): void {
    this.message.set(message);
    this.messageTone.set(tone);
  }

  private updateStatusPolling(): void {
    if (!this.shouldKeepPolling()) {
      this.stopStatusPolling();
      return;
    }
    if (this.statusPollId !== null) {
      return;
    }
    this.statusPollId = setInterval(() => {
      void this.pollPendingWorkspaces();
    }, CodeServersPageComponent.statusPollIntervalMs);
  }

  private stopStatusPolling(): void {
    if (this.statusPollId === null) {
      return;
    }
    clearInterval(this.statusPollId);
    this.statusPollId = null;
  }

  private startDeploymentNavigationPolling(): void {
    if (this.deploymentNavigationPollId !== null) {
      return;
    }
    this.deploymentNavigationPollId = setInterval(() => {
      void this.pollDeploymentNavigationTarget();
    }, CodeServersPageComponent.deploymentNavigationPollIntervalMs);
  }

  private stopDeploymentNavigationPolling(): void {
    if (this.deploymentNavigationPollId === null) {
      return;
    }
    clearInterval(this.deploymentNavigationPollId);
    this.deploymentNavigationPollId = null;
  }

  private shouldKeepPolling(): boolean {
    return this.workspaces().some((workspace) => this.shouldPollWorkspace(workspace));
  }

  private shouldPollWorkspace(workspace: CodeServer): boolean {
    if (workspace.status === "missing") {
      return false;
    }
    if (workspace.status !== "ready") {
      return true;
    }
    return this.selectedWorkspaceId() === workspace.id && this.embeddedWorkspaceUrl() === null;
  }

  private async pollPendingWorkspaces(): Promise<void> {
    if (this.statusPollInFlight) {
      return;
    }
    const pending = this.workspaces().filter((workspace) => this.shouldPollWorkspace(workspace));
    if (pending.length === 0) {
      this.stopStatusPolling();
      return;
    }
    this.statusPollInFlight = true;
    try {
      const updates = await Promise.all(
        pending.map((workspace) =>
          firstValueFrom(
            this.codeServersApi.getCodeServerApiCodeServersCodeServerIdGet(workspace.id),
          ),
        ),
      );
      updates.forEach((workspace) => this.upsertWorkspace(workspace));
      await this.ensureSelectedWorkspace();
    } catch {
      // Keep manual refresh as the visible error path; transient startup polls can fail briefly.
    } finally {
      this.statusPollInFlight = false;
      this.updateStatusPolling();
    }
  }

  private withFrameReloadNonce(url: string): string {
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}_tc_reload=${this.frameReloadNonce}`;
  }
}
