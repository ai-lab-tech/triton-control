import { Component, ElementRef, OnDestroy, ViewChild, effect, inject, signal } from "@angular/core";
import { DomSanitizer, SafeResourceUrl } from "@angular/platform-browser";
import { FormsModule } from "@angular/forms";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatExpansionModule } from "@angular/material/expansion";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";

import {
  BASE_PATH,
  CodeServerDTO,
  CodeServersService,
  CreateCodeServerRequest,
} from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { ChromeService } from "../../shared/chrome.service";

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
  ],
  styleUrl: "./code-servers-page.component.scss",
  templateUrl: "./code-servers-page.component.html",
})
export class CodeServersPageComponent implements OnDestroy {
  private static readonly statusPollIntervalMs = 3000;

  private readonly codeServersApi = inject(CodeServersService);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly chrome = inject(ChromeService);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");
  private statusPollId: ReturnType<typeof setInterval> | null = null;
  private statusPollInFlight = false;
  private frameReloadNonce = 0;
  @ViewChild("codeServerFrame") private codeServerFrame?: ElementRef<HTMLIFrameElement>;

  name = "workspace";
  image = "nvcr.io/nvidia/tritonserver:25.02-py3";
  theme: CreateCodeServerRequest["theme"] = "Default Dark+";
  storageSize = "20Gi";
  cpu = "";
  memory = "";
  dockerconfigjson = "";

  readonly loading = signal(false);
  readonly initialLoaded = signal(false);
  readonly saving = signal(false);
  readonly deletingId = signal<number | null>(null);
  readonly workspaces = signal<CodeServer[]>([]);
  readonly selectedWorkspaceId = signal<number | null>(null);
  readonly embeddedWorkspaceUrl = signal<SafeResourceUrl | null>(null);
  readonly workspacePanelCollapsed = signal(false);
  readonly showCreate = signal(false);
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
    void this.load();
  }

  ngOnDestroy(): void {
    this.chrome.showTopbar();
    this.stopStatusPolling();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const workspaces = (await firstValueFrom(
        this.codeServersApi.listCodeServersApiCodeServersGet(),
      )) as CodeServer[];
      this.workspaces.set(workspaces);
      this.ensureSelectedWorkspace();
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
      const payload: CreateCodeServerRequest = {
        name: this.name.trim(),
        image: this.image.trim(),
        theme: this.theme,
        storage_size: this.storageSize.trim() || "20Gi",
        cpu: this.cpu.trim() || undefined,
        cpu_limit: this.cpu.trim() || undefined,
        memory: this.memory.trim() || undefined,
        memory_limit: this.memory.trim() || undefined,
        dockerconfigjson: this.dockerconfigjson.trim() || undefined,
      };
      const workspace = await firstValueFrom(
        this.codeServersApi.createCodeServerApiCodeServersPost(payload),
      );
      this.upsertWorkspace(workspace);
      this.showCreate.set(false);
      this.updateStatusPolling();
      void this.pollPendingWorkspaces();
      this.setMessage("Code server created.", "success");
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to create code server."), "error");
    } finally {
      this.saving.set(false);
    }
  }

  async refresh(workspace: CodeServer): Promise<void> {
    try {
      const updated = await firstValueFrom(
        this.codeServersApi.getCodeServerApiCodeServersCodeServerIdGet(workspace.id),
      );
      this.upsertWorkspace(updated);
      if (this.selectedWorkspaceId() === updated.id) {
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
        this.ensureSelectedWorkspace();
      }
      this.updateStatusPolling();
      this.setMessage("Code server deleted.", "success");
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to delete code server."), "error");
    } finally {
      this.deletingId.set(null);
    }
  }

  open(workspace: CodeServer): void {
    this.selectWorkspace(workspace);
  }

  backToList(): void {
    this.selectedWorkspaceId.set(null);
    this.embeddedWorkspaceUrl.set(null);
  }

  startCreate(): void {
    this.setMessage("", "info");
    this.showCreate.set(true);
  }

  cancelCreate(): void {
    this.showCreate.set(false);
  }

  selectWorkspace(workspace: CodeServer): void {
    this.selectedWorkspaceId.set(workspace.id);
    this.setEmbeddedWorkspace(workspace);
  }

  selectWorkspaceFromKeyboard(event: KeyboardEvent, workspace: CodeServer): void {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    this.selectWorkspace(workspace);
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

  private ensureSelectedWorkspace(): void {
    const selectedId = this.selectedWorkspaceId();
    if (selectedId === null) {
      return;
    }
    const selected = this.workspaces().find((workspace) => workspace.id === selectedId);
    if (selected) {
      this.setEmbeddedWorkspace(selected);
      return;
    }
    this.selectedWorkspaceId.set(null);
    this.embeddedWorkspaceUrl.set(null);
  }

  private setEmbeddedWorkspace(workspace: CodeServer): void {
    if (workspace.status === "ready" && workspace.url) {
      this.workspacePanelCollapsed.set(true);
      this.frameReloadNonce += 1;
      this.embeddedWorkspaceUrl.set(
        this.sanitizer.bypassSecurityTrustResourceUrl(
          this.withFrameReloadNonce(this.proxyUrl(workspace.url)),
        ),
      );
      this.scheduleFrameResize();
      return;
    }
    this.workspacePanelCollapsed.set(false);
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
      this.ensureSelectedWorkspace();
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
