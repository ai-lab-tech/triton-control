import { Component, DestroyRef, inject, input, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, Router, RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { firstValueFrom } from "rxjs";
import { AuthStore } from "../../shared/auth/auth.store";
import { InstallationService } from "../../shared/installation/installation.service";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { Instance } from "../instances/instances.data";
import { SnapshotCaptureService } from "./snapshot-capture.service";
import {
  CaptureOperation,
  PreparationApiService,
  SnapshotRequest,
} from "./preparation-api.service";

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
  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.destroyed = true;
      ++this.generation;
      clearTimeout(this.timer);
    });
  }
  ngOnInit() {
    this.title = `Prepare ${this.instance().name}`;
    const ready = this.readyModels();
    this.models = ready.length === 1 ? ready[0] : "";
    const saved = this.captures.request(this.storageKey());
    if (saved) {
      this.title = saved.title;
      this.models = saved.models.flatMap((m) => m.versions.map((v) => m.name + ":" + v)).join("\n");
      this.pending.set(true);
    }
    const capture = this.route.snapshot.queryParamMap.get("snapshotCapture");
    if (capture && this.canAccess()) {
      this.expanded.set(true);
      void this.monitor(capture);
    }
  }
  begin() {
    if (!this.allowed()) return;
    const ready = this.readyModels();
    if (!this.pending() && ready.length === 1) this.models = ready[0];
    this.expanded.set(true);
  }
  readyModels(instance = this.instance()) {
    return [
      ...new Set(
        instance.repositoryModels
          .filter((m) => m.state?.toUpperCase() === "READY" && m.version)
          .map((m) => m.name + ":" + m.version),
      ),
    ];
  }
  allowed(instance = this.instance(), unavailable = this.unavailable()) {
    return (
      this.canAccess(unavailable) &&
      !instance.preparationTaskId &&
      !!instance.s3?.enabled &&
      !!instance.s3.bucket &&
      !!instance.s3.endpoint &&
      !!instance.s3.accessKey &&
      !!instance.s3.secretConfigured &&
      this.readyModels(instance).length > 0
    );
  }
  canAccess(unavailable = this.unavailable()) {
    return (
      !unavailable && this.auth.canWriteInstances() && this.installation.allows("development_jobs")
    );
  }
  terminal() {
    return ["succeeded", "failed", "cancelled"].includes(this.operation()?.state ?? "");
  }
  taskId() {
    const id = this.operation()?.result["task_id"];
    return typeof id === "string" ? id : null;
  }
  private draft(): SnapshotRequest | null {
    if (!this.readyModels().includes(this.models)) return null;
    const [name, version] = this.models.split(":");
    return {
      title: this.title.slice(0, 200),
      expected_behavior: null,
      acceptance_criteria: null,
      models: [{ name, versions: [version] }],
    };
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
    if (!this.canAccess() || this.destroyed) return;
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
    if (!operation || this.busy() || !this.canAccess()) return;
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
