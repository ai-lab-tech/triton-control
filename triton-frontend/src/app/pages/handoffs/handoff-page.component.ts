import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { Component, DestroyRef, inject, signal } from "@angular/core";
import { DOCUMENT, DatePipe, DecimalPipe, NgTemplateOutlet } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, Router, RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { combineLatest, distinctUntilChanged, firstValueFrom, forkJoin, map, of } from "rxjs";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { InstancesService } from "../../api/generated";
import { dtoToInstance } from "../../state/instances.utils";
import type { Instance } from "../instances/instances.data";
import { AuthStore } from "../../shared/auth/auth.store";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import {
  CaptureOperation,
  HandoffsApiService,
  HandoffReceipt,
  HandoffRequest,
  ResolvedInput,
  SourceInput,
  SourceProfile,
  TaskWorkspaceOptions,
  WorkspaceSizing,
} from "./handoffs-api.service";
import { TaskWorkspaceOptionsComponent } from "./task-workspace-options.component";

@Component({
  selector: "app-handoff-page",
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    DatePipe,
    DecimalPipe,
    NgTemplateOutlet,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    TaskWorkspaceOptionsComponent,
  ],
  templateUrl: "./handoff-page.component.html",
  styleUrl: "./handoff-page.component.scss",
})
export class HandoffPageComponent {
  private readonly api = inject(HandoffsApiService);
  private readonly instances = inject(InstancesService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly document = inject(DOCUMENT);
  readonly canSubmit = inject(AuthStore).canWriteInstances;
  readonly profiles = signal<SourceProfile[]>([]);
  readonly requireS3Versioning = signal(true);
  readonly receipt = signal<HandoffReceipt | null>(null);
  readonly busy = signal(false);
  readonly error = signal("");
  readonly workspaceSizing = signal<WorkspaceSizing | null>(null);
  readonly detail = signal(false);
  readonly draftLoading = signal(false);
  readonly draftLoadFailed = signal(false);
  readonly original = signal<HandoffReceipt | null>(null);
  originalId = "";
  instanceId = "";
  servingInstance: Instance | null = null;
  readyVersions: string[] = [];
  selectedVersion = "";

  private draftGeneration = 0;
  readonly capture = signal<CaptureOperation | null>(null);
  readonly evaluationHelp = {
    dataset: {
      label: "Evaluation dataset",
      text: "Enter the S3 path to a file containing representative inputs the model will be tested with.",
    },
    baseline: {
      label: "Baseline",
      text: "Enter the S3 path to a file containing reference outputs or measurements for comparison. These can come from an existing model or from reviewed expected results.",
    },
    behavior: {
      label: "Expected behavior",
      text: "Describe what the model should do for typical inputs and how it should handle important edge cases.",
    },
    criteria: {
      label: "Acceptance criteria",
      text: "Define what counts as passing. Include measurable thresholds or allowed differences where relevant.",
    },
  };
  readonly openHelp = signal<keyof HandoffPageComponent["evaluationHelp"] | null>(null);
  private captureTimer: ReturnType<typeof setTimeout> | undefined;
  private destroyed = false;
  private captureGeneration = 0;
  private submissionId = bytesToHex(crypto.getRandomValues(new Uint8Array(16)));
  title = "";
  profileId: number | null = null;
  sourcePath = "";
  sourceKind: "s3" | "s3_repository" = "s3";
  revision = "1";
  previousHandoffId = "";
  datasetPath = "";
  datasetProfileId: number | null = null;
  baselinePath = "";
  baselineProfileId: number | null = null;
  expectedBehavior = "";
  criteria = "";

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.destroyed = true;
      clearTimeout(this.captureTimer);
    });
    const captureId = this.route.snapshot.queryParamMap.get("capture");
    if (captureId && this.canSubmit()) void this.monitorCapture(captureId);
    const draftSource = this.route.queryParamMap.pipe(
      map((params) => ({
        previous: params.get("previous") ?? "",
        instance: params.get("instance") ?? "",
      })),
      distinctUntilChanged((a, b) => a.previous === b.previous && a.instance === b.instance),
    );
    combineLatest([this.route.paramMap, draftSource])
      .pipe(takeUntilDestroyed())
      .subscribe(([params, { previous, instance }]) => {
        if (previous !== this.originalId || instance !== this.instanceId) {
          this.originalId = previous;
          this.instanceId = instance;
          this.servingInstance = null;
          this.readyVersions = [];
          this.selectedVersion = "";
          this.original.set(null);
          this.title = this.sourcePath = this.datasetPath = this.baselinePath = "";
          this.expectedBehavior = this.criteria = this.previousHandoffId = "";
          this.profileId = this.datasetProfileId = this.baselineProfileId = null;
          this.sourceKind = "s3";
          this.revision = "1";
          this.openHelp.set(null);
          this.submissionId = bytesToHex(crypto.getRandomValues(new Uint8Array(16)));
        }
        const id = params.get("handoffId");
        this.detail.set(!!id);
        this.receipt.set(null);
        this.workspaceSizing.set(null);
        this.error.set("");
        if (id) void this.loadReceipt(id);
        else if (this.canSubmit()) void this.loadDraft();
      });
  }
  async loadDraft() {
    const generation = ++this.draftGeneration;
    this.draftLoading.set(true);
    this.draftLoadFailed.set(false);
    this.error.set("");
    try {
      const { profiles, policy, original, serving } = await firstValueFrom(
        forkJoin({
          profiles: this.api.profiles(),
          serving:
            this.instanceId && !this.originalId
              ? this.instances
                  .getInstanceApiInstancesInstanceIdGet(this.instanceId)
                  .pipe(map(dtoToInstance))
              : of(null),
          policy: this.api.submissionPolicy(),
          original: this.originalId ? this.api.get(this.originalId) : of(null),
        }),
      );
      if (this.destroyed || generation !== this.draftGeneration) return;
      this.requireS3Versioning.set(policy.require_s3_versioning);
      this.profiles.set(profiles);
      if (serving) {
        if (serving.preparationTaskId) {
          await this.router.navigate(["/preparation/tasks", serving.preparationTaskId], {
            replaceUrl: true,
          });
          return;
        }
        this.prefillServingDraft(serving, profiles);
      }
      if (original) {
        this.original.set(original);
        const request = original.request;
        this.title = request.title;
        this.sourceKind = request.source.kind;
        this.profileId = request.source.profile_id;
        this.sourcePath = request.source.path;
        this.datasetPath = request.dataset?.path ?? "";
        this.datasetProfileId = request.dataset?.profile_id ?? null;
        this.baselinePath = request.baseline?.path ?? "";
        this.baselineProfileId = request.baseline?.profile_id ?? null;
        this.expectedBehavior = request.expected_behavior ?? "";
        this.criteria = request.acceptance_criteria ?? "";
        this.revision = `revision-${this.submissionId}`;
      }
    } catch (error) {
      if (this.destroyed || generation !== this.draftGeneration) return;
      this.draftLoadFailed.set(true);
      this.error.set(
        mapApiErrorMessage(
          error,
          this.originalId
            ? "Could not load the original handoff or its source profiles."
            : "Could not load handoff settings or source profiles.",
        ),
      );
    } finally {
      if (!this.destroyed && generation === this.draftGeneration) this.draftLoading.set(false);
    }
  }

  private prefillServingDraft(serving: Instance, profiles: SourceProfile[]): void {
    this.servingInstance = serving;
    this.readyVersions = [
      ...new Set(
        serving.repositoryModels
          .filter((row) => row.state.toUpperCase() === "READY" && row.version)
          .map((row) => row.name + ":" + row.version),
      ),
    ];
    const repo = serving.s3;
    const prefix = (repo.prefix || "").replace(/^\/+|\/+$/g, "");
    const matches = profiles
      .map((profile) => ({ profile, base: (profile.prefix || "").replace(/^\/+|\/+$/g, "") }))
      .filter(
        ({ profile, base }) =>
          repo.enabled &&
          profile.bucket === repo.bucket &&
          profile.endpoint?.replace(/\/+$/, "") === repo.endpoint.replace(/\/+$/, "") &&
          (!base || base === prefix || prefix.startsWith(base + "/")),
      )
      .sort((a, b) => b.base.length - a.base.length || a.profile.id - b.profile.id);
    if (!matches.length || !this.readyVersions.length) {
      this.draftLoadFailed.set(true);
      this.error.set(
        !this.readyVersions.length
          ? "This instance has no ready model versions."
          : "No handoff S3 profile covers the serving repository. Configure a profile for its endpoint, bucket and prefix.",
      );
      return;
    }
    const { profile, base } = matches[0];
    this.profileId = profile.id;
    this.sourceKind = "s3_repository";
    this.sourcePath = prefix.slice(base.length).replace(/^\//, "") || ".";
    this.selectServingVersion(this.readyVersions.length === 1 ? this.readyVersions[0] : "");
  }

  selectServingVersion(value: string): void {
    this.selectedVersion = value;
    this.title = value ? `Prepare ${value} from ${this.servingInstance?.name}`.slice(0, 200) : "";
    this.revision = value ? `${this.servingInstance?.id}-${value}` : "";
  }

  completingHandoff(): boolean {
    return !!this.original()?.task.missing_inputs.length;
  }

  toggleHelp(field: keyof HandoffPageComponent["evaluationHelp"]): void {
    this.openHelp.update((current) => (current === field ? null : field));
  }

  unavailableRevisionProfile(): boolean {
    if (!this.original()) return false;
    const selected = [
      this.profileId,
      this.datasetPath.trim() ? this.datasetProfileId : null,
      this.baselinePath.trim() ? this.baselineProfileId : null,
    ];
    return selected.some(
      (id) => id !== null && !this.profiles().some((profile) => profile.id === id),
    );
  }

  private objectInput(
    path: string,
    profileId: number,
    previous: SourceInput | null | undefined,
    captured: ResolvedInput | null | undefined,
  ): SourceInput {
    const input: SourceInput = { kind: "s3", profile_id: profileId, path: path.trim() };
    if (previous?.profile_id === profileId && previous.path === input.path) {
      input.version_id = captured?.version_id ?? previous.version_id;
      input.expected_digest = captured?.file.digest ?? previous.expected_digest;
    }
    return input;
  }
  hasUnversionedInputs(receipt: HandoffReceipt): boolean {
    const source = "files" in receipt.source ? receipt.source.files : [receipt.source];
    return [...source, receipt.dataset, receipt.baseline].some(
      (input) => input?.version_id === null,
    );
  }
  private async loadReceipt(id: string) {
    this.busy.set(true);
    try {
      this.receipt.set(await firstValueFrom(this.api.get(id)));
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not load handoff."));
    } finally {
      this.busy.set(false);
    }
  }
  async claimTask() {
    const receipt = this.receipt();
    if (!receipt || receipt.task.revision === undefined || this.busy()) return;
    this.busy.set(true);
    this.error.set("");
    try {
      await firstValueFrom(this.api.claim(receipt.task.id, receipt.task.revision));
      await this.loadReceipt(receipt.id);
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not claim this task."));
    } finally {
      this.busy.set(false);
    }
  }
  async prepareWorkspace() {
    const receipt = this.receipt();
    if (!receipt || this.busy()) return;
    this.busy.set(true);
    this.error.set("");
    try {
      const sizing = await firstValueFrom(this.api.workspaceSizing(receipt.task.id));
      this.workspaceSizing.set(sizing);
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not calculate workspace storage."));
    } finally {
      this.busy.set(false);
    }
  }
  async openWorkspace(options?: TaskWorkspaceOptions) {
    const receipt = this.receipt();
    if (!receipt || this.busy()) return;
    this.busy.set(true);
    this.error.set("");
    try {
      const result = await firstValueFrom(this.api.openWorkspace(receipt.task.id, options));
      await this.router.navigate(["/workspaces"], {
        queryParams: { workspace: result.workspace.id },
      });
    } catch (error) {
      this.error.set(
        mapApiErrorMessage(
          error,
          "Could not open task workspace. Check your assignment and input access.",
        ),
      );
    } finally {
      this.busy.set(false);
    }
  }
  newSubmission(): void {
    ++this.captureGeneration;
    clearTimeout(this.captureTimer);
    this.submissionId = bytesToHex(crypto.getRandomValues(new Uint8Array(16)));
    this.capture.set(null);
    this.error.set("");
  }
  async submit() {
    if (
      this.busy() ||
      !this.canSubmit() ||
      (this.originalId && !this.original()) ||
      this.capture() !== null ||
      (this.instanceId && !this.originalId && !this.readyVersions.includes(this.selectedVersion)) ||
      this.profileId === null ||
      !this.title.trim() ||
      !this.sourcePath.trim() ||
      !this.revision.trim() ||
      this.unavailableRevisionProfile()
    )
      return;
    const original = this.original();
    const previous = original?.request;
    const profileId = this.profileId;
    const path = this.sourcePath.trim();
    const sameSource =
      previous?.source.kind === this.sourceKind &&
      previous.source.profile_id === profileId &&
      previous.source.path === path;
    const [name, version] = this.selectedVersion.split(":");
    const model = this.servingInstance
      ? { name, versions: [version] }
      : sameSource && previous?.source.kind === "s3_repository"
        ? previous.source.model
        : undefined;
    const source: HandoffRequest["source"] =
      this.sourceKind === "s3_repository"
        ? { kind: "s3_repository", profile_id: profileId, path, ...(model ? { model } : {}) }
        : this.objectInput(
            path,
            profileId,
            previous?.source.kind === "s3" ? previous.source : null,
            original && "version_id" in original.source ? original.source : null,
          );
    const draft: HandoffRequest = {
      schema_version: 1,
      origin:
        sameSource && previous
          ? previous.origin
          : {
              installation_id: previous?.origin.installation_id ?? this.document.location.origin,
              record_id: this.servingInstance
                ? `instance:${this.servingInstance.id}:${name}`
                : `s3-profile:${profileId}:${bytesToHex(sha256(new TextEncoder().encode(path)))}`,
            },
      previous_handoff_id: original?.id ?? (this.previousHandoffId.trim() || null),
      revision: this.revision.trim(),
      title: this.title.trim(),
      source,
      ...(sameSource && previous?.lineage ? { lineage: previous.lineage } : {}),
      dataset: this.datasetPath.trim()
        ? this.objectInput(
            this.datasetPath,
            this.datasetProfileId ?? profileId,
            previous?.dataset,
            original?.dataset,
          )
        : null,
      baseline: this.baselinePath.trim()
        ? this.objectInput(
            this.baselinePath,
            this.baselineProfileId ?? profileId,
            previous?.baseline,
            original?.baseline,
          )
        : null,
      expected_behavior: this.expectedBehavior.trim() || null,
      acceptance_criteria: this.criteria.trim() || null,
    };
    this.busy.set(true);
    this.error.set("");
    try {
      const operation = await firstValueFrom(this.api.submit(draft, this.submissionId));
      this.capture.set(operation);
      await this.router.navigate([], {
        relativeTo: this.route,
        queryParams: { capture: operation.id },
        queryParamsHandling: "merge",
        replaceUrl: true,
      });
      await this.acceptCapture(operation);
    } catch (error) {
      this.error.set(
        mapApiErrorMessage(error, "Could not submit handoff. Retry with the same revision."),
      );
    } finally {
      this.busy.set(false);
    }
  }
  private async acceptCapture(operation: CaptureOperation): Promise<void> {
    if (this.destroyed) return;
    this.capture.set(operation);
    if (operation.state === "succeeded" && operation.result.handoff_id) {
      await this.router.navigate(["/preparation/handoffs", operation.result.handoff_id]);
      this.capture.set(null);
    } else if (!["failed", "cancelled"].includes(operation.state)) {
      this.captureTimer = setTimeout(() => {
        void this.monitorCapture(operation.id);
      }, 3000);
    }
  }
  async monitorCapture(id: string): Promise<void> {
    clearTimeout(this.captureTimer);
    const generation = ++this.captureGeneration;
    try {
      const operation = await firstValueFrom(this.api.capture(id));
      if (generation === this.captureGeneration) await this.acceptCapture(operation);
    } catch (error) {
      if (!this.destroyed)
        this.error.set(
          mapApiErrorMessage(error, "Could not read capture progress. Reload to resume."),
        );
    }
  }
  async captureAction(action: "cancel" | "retry"): Promise<void> {
    const current = this.capture();
    if (!current || this.busy()) return;
    this.busy.set(true);
    ++this.captureGeneration;
    clearTimeout(this.captureTimer);
    try {
      await this.acceptCapture(await firstValueFrom(this.api.captureAction(current.id, action)));
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not update capture."));
    } finally {
      this.busy.set(false);
    }
  }

  verifiedInputs(item: HandoffReceipt) {
    const sources =
      "files" in item.source
        ? item.source.files.map((file) => ({ label: `Model · ${file.file.path}`, value: file }))
        : [{ label: "Model", value: item.source }];
    return [
      ...sources,
      { label: "Evaluation dataset", value: item.dataset },
      { label: "Baseline", value: item.baseline },
    ];
  }

  hasBlockingInputs(item: HandoffReceipt): boolean {
    return item.task.blocked_reasons.some((reason) => reason.startsWith("missing_input:"));
  }

  inputLabel(name: string) {
    return (
      (
        {
          evaluation_dataset: "Evaluation dataset",
          baseline: "Baseline",
          expected_behavior: "Expected behavior",
          acceptance_criteria: "Acceptance criteria",
        } as Record<string, string>
      )[name] ?? name
    );
  }
}
