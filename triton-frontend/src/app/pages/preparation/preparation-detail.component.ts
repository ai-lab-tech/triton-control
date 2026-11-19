import { Component, DestroyRef, inject, signal } from "@angular/core";
import { DatePipe, JsonPipe } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, Router, RouterLink } from "@angular/router";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { MatButtonModule } from "@angular/material/button";
import { firstValueFrom } from "rxjs";
import {
  PreparationApiService,
  Overview,
  Attempt,
  TaskEvent,
  SnapshotReceipt,
} from "./preparation-api.service";
import { HandoffsApiService, WorkspaceSizing } from "../handoffs/handoffs-api.service";
import { BuildsApiService, TaskBuild, buildLabel } from "../builds/builds-api.service";
import { mapApiErrorMessage } from "../../shared/api-error-message";

@Component({
  selector: "app-preparation-detail",
  standalone: true,
  imports: [DatePipe, JsonPipe, FormsModule, RouterLink, MatButtonModule],
  templateUrl: "./preparation-detail.component.html",
  styleUrl: "./preparation.scss",
})
export class PreparationDetailComponent {
  private readonly api = inject(PreparationApiService);
  private readonly workspaces = inject(HandoffsApiService);
  private readonly buildsApi = inject(BuildsApiService);
  private readonly router = inject(Router);
  private generation = 0;
  private id = "";
  readonly entry = signal<Overview | null>(null);
  readonly attempts = signal<Attempt[]>([]);
  readonly events = signal<TaskEvent[]>([]);
  readonly snapshot = signal<SnapshotReceipt | null>(null);
  readonly builds = signal<TaskBuild[]>([]);
  readonly buildsError = signal("");
  readonly moreBuilds = signal(false);
  readonly buildsBusy = signal(false);
  readonly moreAttempts = signal(false);
  readonly moreEvents = signal(false);
  readonly sizing = signal<WorkspaceSizing | null>(null);
  storageGiB: number | null = null;
  reason = "";
  missing = "";
  readonly busy = signal(false);
  readonly error = signal("");
  readonly sourceError = signal("");
  readonly buildLabel = buildLabel;
  constructor() {
    inject(DestroyRef).onDestroy(() => ++this.generation);
    inject(ActivatedRoute)
      .paramMap.pipe(takeUntilDestroyed())
      .subscribe((params) => {
        this.id = params.get("taskId") ?? "";
        void this.load();
      });
  }
  async load() {
    const generation = ++this.generation;
    const id = this.id;
    this.entry.set(null);
    this.snapshot.set(null);
    this.sizing.set(null);
    this.attempts.set([]);
    this.events.set([]);
    this.builds.set([]);
    this.moreBuilds.set(false);
    this.buildsError.set("");
    this.busy.set(true);
    this.error.set("");
    this.sourceError.set("");
    try {
      const entry = await firstValueFrom(this.api.overview(id));
      if (generation !== this.generation) return;
      this.entry.set(entry);
      const [attempts, events] = await Promise.all([
        firstValueFrom(this.api.attempts(id)),
        firstValueFrom(this.api.events(id)),
      ]);
      if (generation !== this.generation) return;
      this.attempts.set(attempts);
      this.events.set(events);
      this.moreAttempts.set(attempts.length === 50);
      this.moreEvents.set(events.length === 50);
      if (entry.source.snapshot_id) {
        try {
          const snapshot = await firstValueFrom(this.api.snapshot(entry.source.snapshot_id));
          if (generation === this.generation) this.snapshot.set(snapshot);
        } catch (error) {
          if (generation === this.generation)
            this.sourceError.set(mapApiErrorMessage(error, "Could not inspect snapshot."));
        }
      }
    } catch (error) {
      if (generation === this.generation)
        this.error.set(mapApiErrorMessage(error, "Could not load preparation task."));
    } finally {
      if (generation === this.generation) this.busy.set(false);
    }
  }
  validInformation() {
    const names = this.missing.split(",").map((name) => name.trim());
    return (
      !!this.reason.trim() &&
      this.reason.trim().length <= 2000 &&
      names.length <= 50 &&
      names.every((name) => /^[a-z][a-z0-9_]{0,79}$/.test(name))
    );
  }
  async action(action: "claim" | "release-claim" | "request-information") {
    const entry = this.entry();
    const allowed =
      entry &&
      (action === "claim"
        ? entry.actions.claim
        : action === "release-claim"
          ? entry.actions.release
          : entry.actions.request_information);
    if (
      !entry ||
      !allowed ||
      this.busy() ||
      (action === "request-information" && !this.validInformation())
    )
      return;
    const generation = this.generation;
    this.busy.set(true);
    this.error.set("");
    let message = "";
    try {
      await firstValueFrom(
        this.api.action(
          entry.task,
          action,
          action === "request-information"
            ? {
                reason: this.reason.trim(),
                missing_inputs: [...new Set(this.missing.split(",").map((name) => name.trim()))],
              }
            : {},
        ),
      );
    } catch (error) {
      message = mapApiErrorMessage(
        error,
        "Task changed or access was revoked. Reload before retrying.",
      );
    }
    if (generation !== this.generation) return;
    await this.load();
    if (message) this.error.set(message);
  }
  async more(kind: "attempts" | "events") {
    if (this.busy()) return;
    const generation = this.generation;
    this.busy.set(true);
    try {
      if (kind === "attempts") {
        const rows = await firstValueFrom(this.api.attempts(this.id, this.attempts().length));
        if (generation === this.generation) {
          this.attempts.update((old) => [...old, ...rows]);
          this.moreAttempts.set(rows.length === 50);
        }
      } else {
        const rows = await firstValueFrom(this.api.events(this.id, this.events().length));
        if (generation === this.generation) {
          this.events.update((old) => [...old, ...rows]);
          this.moreEvents.set(rows.length === 50);
        }
      }
    } catch (error) {
      if (generation === this.generation)
        this.error.set(mapApiErrorMessage(error, "Could not load history."));
    } finally {
      if (generation === this.generation) this.busy.set(false);
    }
  }
  async loadBuilds() {
    if (this.buildsBusy()) return;
    const generation = this.generation;
    this.buildsBusy.set(true);
    this.buildsError.set("");
    try {
      const rows = await firstValueFrom(this.buildsApi.list(this.id, this.builds().length));
      if (generation === this.generation) {
        this.builds.update((old) => [...old, ...rows]);
        this.moreBuilds.set(rows.length === 50);
      }
    } catch (error) {
      if (generation === this.generation)
        this.buildsError.set(
          mapApiErrorMessage(error, "Build history is unavailable for this account."),
        );
    } finally {
      this.buildsBusy.set(false);
    }
  }
  async prepareWorkspace() {
    if (!this.entry()?.actions.open_workspace || this.busy()) return;
    const generation = this.generation;
    this.busy.set(true);
    this.error.set("");
    try {
      const sizing = await firstValueFrom(this.workspaces.workspaceSizing(this.id));
      if (generation === this.generation) {
        this.sizing.set(sizing);
        this.storageGiB = sizing.recommended_gib;
      }
    } catch (error) {
      if (generation === this.generation)
        this.error.set(mapApiErrorMessage(error, "Could not size task workspace."));
    } finally {
      if (generation === this.generation) this.busy.set(false);
    }
  }
  validStorage() {
    const sizing = this.sizing();
    return (
      !!sizing &&
      (!!sizing.existing_storage_size ||
        (this.storageGiB !== null &&
          Number.isInteger(this.storageGiB) &&
          this.storageGiB >= sizing.recommended_gib &&
          this.storageGiB <= sizing.maximum_gib))
    );
  }
  async openWorkspace() {
    if (!this.entry()?.actions.open_workspace || this.busy() || !this.validStorage()) return;
    const generation = this.generation;
    this.busy.set(true);
    try {
      const result = await firstValueFrom(
        this.workspaces.openWorkspace(
          this.id,
          this.sizing()?.existing_storage_size ? undefined : this.storageGiB!,
        ),
      );
      if (generation === this.generation)
        await this.router.navigate(["/workspaces"], {
          queryParams: { workspace: result.workspace.id },
        });
    } catch (error) {
      if (generation === this.generation)
        this.error.set(mapApiErrorMessage(error, "Could not open task workspace."));
    } finally {
      if (generation === this.generation) this.busy.set(false);
    }
  }
}
