import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { Component, inject, signal } from "@angular/core";
import { DOCUMENT, DatePipe, DecimalPipe } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, Router, RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { firstValueFrom } from "rxjs";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { AuthStore } from "../../shared/auth/auth.store";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import {
  HandoffsApiService,
  HandoffReceipt,
  SourceInput,
  SourceProfile,
} from "./handoffs-api.service";

@Component({
  selector: "app-handoff-page",
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    DatePipe,
    DecimalPipe,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  templateUrl: "./handoff-page.component.html",
  styleUrl: "./handoff-page.component.scss",
})
export class HandoffPageComponent {
  private readonly api = inject(HandoffsApiService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly document = inject(DOCUMENT);
  readonly canSubmit = inject(AuthStore).canWriteInstances;
  readonly profiles = signal<SourceProfile[]>([]);
  readonly receipt = signal<HandoffReceipt | null>(null);
  readonly busy = signal(false);
  readonly error = signal("");
  readonly detail = signal(false);
  title = "";
  profileId: number | null = null;
  sourcePath = "";
  sourceKind: "s3" | "s3_repository" = "s3";
  revision = "1";
  previousHandoffId = "";
  datasetPath = "";
  baselinePath = "";
  expectedBehavior = "";
  criteria = "";

  constructor() {
    this.previousHandoffId = this.route.snapshot.queryParamMap.get("previous") ?? "";
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((params) => {
      const id = params.get("handoffId");
      this.detail.set(!!id);
      this.receipt.set(null);
      this.error.set("");
      if (id) void this.loadReceipt(id);
      else if (this.canSubmit()) void this.loadProfiles();
    });
  }
  private async loadProfiles() {
    try {
      this.profiles.set(await firstValueFrom(this.api.profiles()));
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not load source profiles."));
    }
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
  async openWorkspace() {
    const receipt = this.receipt();
    if (!receipt || this.busy()) return;
    this.busy.set(true);
    this.error.set("");
    try {
      const result = await firstValueFrom(this.api.openWorkspace(receipt.task.id));
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
  async submit() {
    if (
      this.busy() ||
      !this.canSubmit() ||
      this.profileId === null ||
      !this.title.trim() ||
      !this.sourcePath.trim() ||
      !this.revision.trim()
    )
      return;
    const source = (path: string): SourceInput => ({
      kind: "s3",
      profile_id: this.profileId!,
      path: path.trim(),
    });
    this.busy.set(true);
    this.error.set("");
    try {
      const receipt = await firstValueFrom(
        this.api.submit({
          schema_version: 1,
          origin: {
            installation_id: this.document.location.origin,
            record_id: `s3-profile:${this.profileId}:${bytesToHex(sha256(new TextEncoder().encode(this.sourcePath.trim())))}`,
          },
          previous_handoff_id: this.previousHandoffId.trim() || null,
          revision: this.revision.trim(),
          title: this.title.trim(),
          source:
            this.sourceKind === "s3_repository"
              ? { kind: "s3_repository", profile_id: this.profileId!, path: this.sourcePath.trim() }
              : source(this.sourcePath),
          dataset: this.datasetPath.trim() ? source(this.datasetPath) : null,
          baseline: this.baselinePath.trim() ? source(this.baselinePath) : null,
          expected_behavior: this.expectedBehavior.trim() || null,
          acceptance_criteria: this.criteria.trim() || null,
        }),
      );
      await this.router.navigate(["/preparation/handoffs", receipt.id]);
    } catch (error) {
      this.error.set(
        mapApiErrorMessage(error, "Could not submit handoff. Retry with the same revision."),
      );
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
