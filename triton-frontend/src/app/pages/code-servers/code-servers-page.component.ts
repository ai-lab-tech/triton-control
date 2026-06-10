import { Component, inject, signal } from "@angular/core";
import { DomSanitizer, SafeResourceUrl } from "@angular/platform-browser";
import { FormsModule } from "@angular/forms";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatExpansionModule } from "@angular/material/expansion";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";

import {
  BASE_PATH,
  CodeServerDTO,
  CodeServersService,
  CreateCodeServerRequest,
} from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";

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
  ],
  styleUrl: "./code-servers-page.component.scss",
  templateUrl: "./code-servers-page.component.html",
})
export class CodeServersPageComponent {
  private readonly codeServersApi = inject(CodeServersService);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`.trim().replace(/\/$/, "");

  name = "workspace";
  image = "nvcr.io/nvidia/tritonserver:25.02-py3";
  ingressHost = "";
  ingressClassName = "";
  storageSize = "20Gi";
  cpu = "";
  memory = "";
  dockerconfigjson = "";

  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly deletingId = signal<number | null>(null);
  readonly workspaces = signal<CodeServer[]>([]);
  readonly selectedWorkspaceId = signal<number | null>(null);
  readonly embeddedWorkspaceUrl = signal<SafeResourceUrl | null>(null);
  readonly message = signal("");
  readonly messageTone = signal<"info" | "success" | "error">("info");

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const workspaces = (await firstValueFrom(
        this.codeServersApi.listCodeServersApiCodeServersGet(),
      )) as CodeServer[];
      this.workspaces.set(workspaces);
      this.ensureSelectedWorkspace();
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to load code servers."), "error");
    } finally {
      this.loading.set(false);
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
        ingress_host: this.ingressHost.trim() || undefined,
        ingress_class_name: this.ingressClassName.trim() || undefined,
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
      this.selectWorkspace(workspace);
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
    return (
      this.name.trim().length > 0 &&
      this.image.trim().length > 0 &&
      !this.saving()
    );
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
    const workspaces = this.workspaces();
    const selected = workspaces.find((workspace) => workspace.id === selectedId);
    if (selected) {
      this.setEmbeddedWorkspace(selected);
      return;
    }
    const firstReady = workspaces.find((workspace) => workspace.status === "ready" && workspace.url);
    if (firstReady) {
      this.selectWorkspace(firstReady);
      return;
    }
    this.selectedWorkspaceId.set(null);
    this.embeddedWorkspaceUrl.set(null);
  }

  private setEmbeddedWorkspace(workspace: CodeServer): void {
    if (workspace.status === "ready" && workspace.url) {
      this.embeddedWorkspaceUrl.set(
        this.sanitizer.bypassSecurityTrustResourceUrl(this.proxyUrl(workspace.url)),
      );
      return;
    }
    this.embeddedWorkspaceUrl.set(null);
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
}
