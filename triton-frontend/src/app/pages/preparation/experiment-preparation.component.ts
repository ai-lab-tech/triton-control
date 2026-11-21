import { Component, DestroyRef, inject, input, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, Router, RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { firstValueFrom } from "rxjs";
import { AuthStore } from "../../shared/auth/auth.store";
import { InstallationService } from "../../shared/installation/installation.service";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { HandoffsApiService, SourceProfile } from "../handoffs/handoffs-api.service";
import { Instance } from "../instances/instances.data";
import { SnapshotCaptureService } from "./snapshot-capture.service";
import {
  CaptureOperation,
  PreparationApiService,
  SnapshotRequest,
  SnapshotSource,
} from "./preparation-api.service";

type TextField = Exclude<
  keyof SnapshotSource,
  "kind" | "ai_assisted" | "checkpoint_files" | "source_documents"
>;
const sourceFields: Record<SnapshotSource["kind"], { key: TextField; label: string }[]> = {
  hf: [
    { key: "repository", label: "HF repository (owner/model)" },
    { key: "revision", label: "Revision" },
    { key: "resolved_commit", label: "Resolved commit" },
    { key: "serving_manifest", label: "Workspace serving.json path" },
    { key: "acquisition_record", label: "Workspace acquisition record path" },
  ],
  example: [
    { key: "repository", label: "Git repository (host/owner/repo)" },
    { key: "commit", label: "Full commit hash" },
    { key: "project_path", label: "Project path in repository" },
  ],
  manual: [{ key: "author", label: "Author" }],
};
function paths(text: string): string[] {
  return [
    ...new Set(
      text
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ];
}
@Component({
  selector: "app-experiment-preparation",
  standalone: true,
  imports: [FormsModule, RouterLink, MatButtonModule],
  styleUrls: ["./preparation.scss", "./experiment-preparation.component.scss"],
  templateUrl: "./experiment-preparation.component.html",
})
export class ExperimentPreparationComponent implements OnInit {
  readonly instance = input.required<Instance>();
  readonly unavailable = input(false);
  private readonly api = inject(PreparationApiService);
  private readonly handoffs = inject(HandoffsApiService);
  readonly profiles = signal<SourceProfile[]>([]);
  private readonly captures = inject(SnapshotCaptureService);
  private readonly auth = inject(AuthStore);
  private readonly installation = inject(InstallationService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private timer: ReturnType<typeof setTimeout> | undefined;
  private generation = 0;
  private destroyed = false;
  readonly pending = signal(false);
  readonly expanded = signal(false);
  readonly busy = signal(false);
  readonly error = signal("");
  readonly operation = signal<CaptureOperation | null>(null);
  title = "";
  models = "";
  prefix = "";
  profileId: number | null = null;
  readonly sourceFields = sourceFields;
  kind: SnapshotSource["kind"] | "" = "";
  workspaceId: number | null = null;
  workspaceFiles = "";
  values: Partial<Record<TextField, string>> = {};
  documents = "";
  checkpoints = "";
  aiAssisted: boolean | null = null;
  behavior = "";
  criteria = "";
  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.destroyed = true;
      ++this.generation;
      clearTimeout(this.timer);
    });
  }
  ngOnInit() {
    this.title = `Prepare ${this.instance().name}`;
    this.models = [
      ...new Set(
        this.instance()
          .repositoryModels.filter((model) => model.version)
          .map((model) => `${model.name}:${model.version}`),
      ),
    ].join("\n");
    const saved = this.captures.request(this.storageKey());
    if (saved) {
      this.title = saved.title;
      this.models = saved.models
        .flatMap((model) => model.versions.map((version) => `${model.name}:${version}`))
        .join("\n");
      this.profileId = saved.repository?.profile_id ?? null;
      this.prefix = saved.repository?.prefix ?? "";
      this.behavior = saved.expected_behavior ?? "";
      this.criteria = saved.acceptance_criteria ?? "";
      if (saved.source) {
        const { kind, source_documents, checkpoint_files, ai_assisted, ...values } = saved.source;
        this.kind = kind;
        this.values = values;
        this.documents = source_documents?.join("\n") ?? "";
        this.checkpoints = checkpoint_files?.join("\n") ?? "";
        this.aiAssisted = ai_assisted ?? null;
      }
      this.workspaceId = saved.workspace?.workspace_id ?? null;
      this.workspaceFiles = saved.workspace?.files.join("\n") ?? "";
      this.pending.set(true);
    }
    const capture = this.route.snapshot.queryParamMap.get("snapshotCapture");
    if (capture && this.allowed()) {
      this.expanded.set(true);
      void this.monitor(capture);
    }
  }
  async begin() {
    if (!this.allowed()) return;
    this.expanded.set(true);
    try {
      this.profiles.set(await firstValueFrom(this.handoffs.profiles()));
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not load repository profiles."));
    }
  }
  allowed() {
    return (
      !this.unavailable() &&
      this.auth.canWriteInstances() &&
      this.installation.allows("development_jobs")
    );
  }
  terminal() {
    return ["succeeded", "failed", "cancelled"].includes(this.operation()?.state ?? "");
  }
  taskId() {
    const id = this.operation()?.result["task_id"];
    return typeof id === "string" ? id : null;
  }
  private selectedModels() {
    const selected = new Map<string, Set<string>>();
    for (const line of this.models.trim().split(/\n/)) {
      const match = /^([A-Za-z0-9][A-Za-z0-9_-]{0,127}):([1-9][0-9]{0,18})$/.exec(line.trim());
      if (!match) return [];
      const [, name, version] = match;
      const versions = selected.get(name) ?? new Set<string>();
      versions.add(version);
      selected.set(name, versions);
    }
    return [...selected].map(([name, versions]) => ({ name, versions: [...versions] }));
  }
  private selection(): Pick<SnapshotRequest, "source" | "workspace"> | null {
    if (!this.kind) return {};
    const source: SnapshotSource = { kind: this.kind };
    const keys: TextField[] = [
      "workspace_model_root",
      "model_card",
      "license_file",
      ...sourceFields[this.kind].map((field) => field.key),
    ];
    for (const key of keys) {
      const value = this.values[key]?.trim();
      if (value) source[key] = value;
    }
    if (this.kind === "manual" && this.aiAssisted !== null) source.ai_assisted = this.aiAssisted;
    if (this.kind === "hf") source.checkpoint_files = paths(this.checkpoints);
    source.source_documents = paths(this.documents);
    const files = paths(this.workspaceFiles);
    const requiredFiles = [
      source.model_card,
      source.license_file,
      source.serving_manifest,
      source.acquisition_record,
      ...source.source_documents,
    ];
    if (requiredFiles.some((path) => path && !files.includes(path))) return null;
    if (
      !files.length &&
      this.workspaceId === null &&
      this.kind !== "manual" &&
      !source.workspace_model_root
    )
      return { source };
    if (
      !files.length ||
      !this.workspaceId ||
      !Number.isInteger(this.workspaceId) ||
      this.workspaceId < 1
    )
      return null;
    return { source, workspace: { workspace_id: this.workspaceId, files } };
  }
  private draft(): SnapshotRequest | null {
    const models = this.selectedModels();
    const selection = this.selection();
    if (
      !selection ||
      !this.title.trim() ||
      this.title.trim().length > 200 ||
      !models.length ||
      models.length > 100 ||
      models.some((model) => model.versions.length > 100)
    )
      return null;
    const request: SnapshotRequest = {
      ...selection,
      models,
      title: this.title.trim(),
      expected_behavior: this.behavior.trim() || null,
      acceptance_criteria: this.criteria.trim() || null,
    };
    if (this.profileId !== null || this.prefix.trim()) {
      if (
        !this.profileId ||
        !Number.isInteger(this.profileId) ||
        this.profileId < 1 ||
        !this.prefix.trim()
      )
        return null;
      request.repository = { profile_id: this.profileId, prefix: this.prefix.trim() };
    } else if (
      !(this.instance().isSelfDeployed && this.instance().deploymentRuntime === "kubernetes") &&
      !selection.source?.workspace_model_root
    )
      return null;
    return request;
  }
  valid() {
    return this.pending() || this.draft() !== null;
  }
  private storageKey() {
    return `preparation-capture:${this.auth.userEmail()}:${this.instance().id}`;
  }
  async submit() {
    if (!this.allowed() || this.busy() || this.operation()) return;
    const request = this.captures.request(this.storageKey()) ?? this.draft();
    if (!request) return;
    this.busy.set(true);
    this.error.set("");
    try {
      const instance = this.instance();
      const accepted = await this.captures.submit(this.storageKey(), instance, request);
      if (this.destroyed) return;
      await this.router.navigate([], {
        relativeTo: this.route,
        queryParams: { snapshotCapture: accepted.operation.id },
        queryParamsHandling: "merge",
        replaceUrl: true,
      });
      this.accept(accepted.operation);
    } catch (error) {
      if (!this.destroyed)
        this.error.set(
          error instanceof Error && !("status" in error)
            ? error.message
            : mapApiErrorMessage(
                error,
                "Could not capture experiment. Retry to recover the same request.",
              ),
        );
    } finally {
      this.pending.set(this.captures.request(this.storageKey()) !== null);
      this.busy.set(false);
    }
  }
  private accept(operation: CaptureOperation) {
    if (this.destroyed) return;
    if (operation.kind !== "experiment.snapshot" || operation.target !== this.instance().id) {
      this.error.set("This capture does not belong to this experiment.");
      return;
    }
    this.operation.set(operation);
    if (!this.terminal()) this.timer = setTimeout(() => void this.monitor(operation.id), 3000);
  }
  async monitor(id: string) {
    if (!this.allowed() || this.destroyed) return;
    clearTimeout(this.timer);
    const generation = ++this.generation;
    this.busy.set(true);
    this.error.set("");
    try {
      const operation = await firstValueFrom(this.api.operation(id));
      if (generation === this.generation) this.accept(operation);
    } catch (error) {
      if (generation === this.generation && !this.destroyed)
        this.error.set(
          mapApiErrorMessage(error, "Could not read snapshot status. Refresh to resume."),
        );
    } finally {
      if (generation === this.generation && !this.destroyed) this.busy.set(false);
    }
  }
  async act(action: "cancel" | "retry") {
    const operation = this.operation();
    if (!operation || this.busy() || !this.allowed()) return;
    ++this.generation;
    clearTimeout(this.timer);
    this.busy.set(true);
    this.error.set("");
    try {
      this.accept(await firstValueFrom(this.api.operationAction(operation.id, action)));
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not update snapshot capture."));
    } finally {
      this.busy.set(false);
    }
  }
  newCapture() {
    if (!this.terminal() || this.busy()) return;
    ++this.generation;
    clearTimeout(this.timer);
    this.operation.set(null);
    this.captures.clear(this.storageKey());
    this.pending.set(false);
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { snapshotCapture: null },
      queryParamsHandling: "merge",
      replaceUrl: true,
    });
  }
}
