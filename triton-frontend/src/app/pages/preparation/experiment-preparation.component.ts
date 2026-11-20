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
} from "./preparation-api.service";

@Component({
  selector: "app-experiment-preparation",
  standalone: true,
  imports: [FormsModule, RouterLink, MatButtonModule],
  styleUrls: ["./preparation.scss", "./experiment-preparation.component.scss"],
  template: `
    @if (allowed()) {
      @if (expanded()) {
        <section class="card" aria-labelledby="capture-title">
          <h2 class="card-title" id="capture-title">Prepare for production</h2>
          <p>
            Capture the selected model versions in an immutable snapshot for an engineering
            preparation task.
          </p>
          @if (error()) {
            <p role="alert">{{ error() }}</p>
          }
          @if (operation(); as op) {
            <p role="status">Snapshot capture: {{ op.state }} · {{ op.message }}</p>
            <a [routerLink]="['/instances', instance().id]">Original development experiment</a>
            @if (taskId()) {
              <a [routerLink]="['/preparation/tasks', taskId()]"
                >Open preparation task and snapshot</a
              >
            }
            @if (op.state === "failed") {
              <button mat-button (click)="act('retry')" [disabled]="busy()">Retry snapshot</button>
            }
            @if (!terminal()) {
              <button mat-button (click)="act('cancel')" [disabled]="busy()">
                Cancel snapshot
              </button>
            }
            @if (terminal()) {
              <button mat-button (click)="newCapture()" [disabled]="busy()">New capture</button>
            }
            <button mat-stroked-button (click)="monitor(op.id)" [disabled]="busy()">
              <span class="material-icons action-glyph" aria-hidden="true">refresh</span> Refresh
              snapshot status
            </button>
          } @else {
            <form (ngSubmit)="submit()">
              @if (pending()) {
                <p>Retry the saved selection to recover the original capture request.</p>
              }
              <fieldset [disabled]="busy() || pending()">
                <label
                  >Task title<input name="title" [(ngModel)]="title" maxlength="200" required
                /></label>
                <label
                  >Models and versions (one model:version per line)<textarea
                    rows="4"
                    name="models"
                    [(ngModel)]="models"
                    required
                    placeholder="iris:1"
                  ></textarea>
                </label>
                <p>
                  Leave repository fields empty for a managed Kubernetes experiment. External
                  endpoints require an explicit S3 profile and repository prefix.
                </p>
                <label
                  >Source repository profile
                  <select name="profile" [(ngModel)]="profileId">
                    <option [ngValue]="null">Use managed experiment repository</option>
                    @for (profile of profiles(); track profile.id) {
                      <option [ngValue]="profile.id">
                        {{ profile.name }} · {{ profile.bucket }}
                      </option>
                    }
                  </select>
                </label>
                <label>Repository prefix<input name="prefix" [(ngModel)]="prefix" /></label>
                <label
                  >Expected behavior<textarea
                    name="behavior"
                    [(ngModel)]="behavior"
                    maxlength="8000"
                  ></textarea>
                </label>
                <label
                  >Acceptance criteria<textarea
                    name="criteria"
                    [(ngModel)]="criteria"
                    maxlength="8000"
                  ></textarea>
                </label>
                <p>
                  Missing evaluation evidence and unknown runtime changes remain visible as task
                  blockers.
                </p>
              </fieldset>
              <div class="form-actions">
                <button
                  mat-flat-button
                  type="submit"
                  [disabled]="busy() || !valid()"
                  color="primary"
                >
                  {{ pending() ? "Retry capture request" : "Capture snapshot" }}
                </button>
              </div>
            </form>
          }
        </section>
      }
    }
  `,
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
      const [name, version] = line.trim().split(":");
      const versions = selected.get(name) ?? new Set<string>();
      versions.add(version);
      selected.set(name, versions);
    }
    return [...selected].map(([name, versions]) => ({ name, versions: [...versions] }));
  }
  valid() {
    if (this.pending()) return true;
    const models = this.selectedModels();
    const repositoryValid =
      this.profileId !== null &&
      Number.isInteger(this.profileId) &&
      this.profileId > 0 &&
      !!this.prefix.trim();
    const managed =
      this.instance().isSelfDeployed && this.instance().deploymentRuntime === "kubernetes";
    return (
      !!this.title.trim() &&
      this.title.trim().length <= 200 &&
      models.length <= 100 &&
      this.models
        .trim()
        .split(/\n/)
        .every((line) => /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}:[1-9][0-9]{0,18}$/.test(line.trim())) &&
      models.every((model) => model.versions.length <= 100) &&
      (repositoryValid || (managed && this.profileId === null && !this.prefix.trim()))
    );
  }
  private storageKey() {
    return `preparation-capture:${this.auth.userEmail()}:${this.instance().id}`;
  }
  async submit() {
    if (!this.allowed() || !this.valid() || this.busy() || this.operation()) return;
    this.busy.set(true);
    this.error.set("");
    try {
      const instance = this.instance();
      const request: SnapshotRequest = {
        title: this.title.trim(),
        models: this.selectedModels(),
        expected_behavior: this.behavior.trim() || null,
        acceptance_criteria: this.criteria.trim() || null,
      };
      if (this.profileId !== null)
        request.repository = { profile_id: this.profileId, prefix: this.prefix.trim() };
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
