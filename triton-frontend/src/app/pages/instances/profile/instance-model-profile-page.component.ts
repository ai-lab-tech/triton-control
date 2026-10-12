import { Component, computed, inject, OnInit, OnDestroy, signal } from "@angular/core";
import { toSignal, takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { firstValueFrom } from "rxjs";

import { ActivatedRoute, RouterLink } from "@angular/router";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatExpansionModule } from "@angular/material/expansion";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";

import {
  InstanceS3ConfigDTO,
  InstancesService,
  PerfAnalyzersService,
  ModelPerfRunResponse,
  ModelPerfStatusResponse,
  TritonInstanceDTO,
} from "../../../api/generated/index";
import { mapApiErrorMessage } from "../../../shared/api-error-message";
import { InstanceModelRepositoryConfigComponent } from "../shared/instance-model-repository-config.component";
import { InstanceModelMonacoEditorComponent } from "../infer/instance-model-monaco-editor.component";

@Component({
  selector: "app-instance-model-profile-page",
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatCardModule,
    MatExpansionModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    InstanceModelMonacoEditorComponent,
    InstanceModelRepositoryConfigComponent,
  ],
  styleUrl: "./instance-model-profile-page.component.scss",
  templateUrl: "./instance-model-profile-page.component.html",
})
export class InstanceModelProfilePageComponent implements OnInit, OnDestroy {
  private static readonly LEGACY_TEMP_INPUT_PATH = "/tmp/pa_input.json";
  private static readonly LEGACY_SHM_INPUT_PATH = "/dev/shm/pa_input.json";
  private readonly route = inject(ActivatedRoute);
  private readonly instancesApi = inject(InstancesService);
  private readonly perfAnalyzersApi = inject(PerfAnalyzersService);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });

  readonly instanceId = computed(() => this.params().get("id") ?? "");
  readonly modelName = computed(() => this.params().get("modelName") ?? "");
  readonly version = computed(() => this.params().get("version") ?? "");
  readonly profileKey = computed(
    () => `${this.instanceId()}:${this.modelName()}:${this.version()}`,
  );
  readonly hasValidRoute = computed(() => {
    const id = this.instanceId().trim();
    return (
      /^[0-9]+$/.test(id) && this.modelName().trim().length > 0 && this.version().trim().length > 0
    );
  });

  instanceName = "";
  instanceUrl = "";
  batchSize = 1;
  concurrencyRange = "1";
  measurementRequestCount = 50;
  inputData = `{
  "data": []
}`;

  image = "";
  dockerconfigjson = "";
  readonly loadingStatus = signal(true);
  readonly resolvingInstance = signal(false);
  readonly instanceS3 = signal<InstanceS3ConfigDTO | null>(null);
  readonly statusError = signal("");
  readonly actionError = signal("");
  readonly busy = signal(false);
  readonly activeRun = signal<ModelPerfRunResponse | null>(null);
  readonly latestRun = signal<ModelPerfRunResponse | null>(null);
  readonly output = signal("");
  readonly hasActiveRun = computed(() => !!this.activeRun());
  readonly error = computed(() => this.actionError() || this.statusError());
  private timer?: ReturnType<typeof setTimeout>;
  private generation = 0;
  private statusRequest = 0;
  private destroyed = false;
  private restoredKey = "";

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe(() => {
      this.generation++;
      clearTimeout(this.timer);
      this.activeRun.set(null);
      this.latestRun.set(null);
      this.output.set("");
      this.loadingStatus.set(true);
      this.statusError.set("");
      this.actionError.set("");
      this.busy.set(false);
      this.image = "";
      this.dockerconfigjson = "";
      this.batchSize = 1;
      this.concurrencyRange = "1";
      this.measurementRequestCount = 50;
      this.inputData = "";
      this.restoredKey = "";
      // Defer until the route signal receives the new parameters as well.
      queueMicrotask(() => {
        if (!this.destroyed && this.hasValidRoute()) {
          void this.loadStatus();
          void this.resolveInstance();
        }
      });
    });
  }

  canRun(): boolean {
    return (
      this.hasValidRoute() &&
      !this.loadingStatus() &&
      !this.statusError() &&
      !this.hasActiveRun() &&
      !this.busy() &&
      !!this.image.trim() &&
      Number.isInteger(Number(this.batchSize)) &&
      Number(this.batchSize) >= 1 &&
      Number.isInteger(Number(this.measurementRequestCount)) &&
      Number(this.measurementRequestCount) >= 1 &&
      /^[1-9][0-9]*(?::[0-9]+(?::[1-9][0-9]*)?)?$/.test(this.concurrencyRange.trim())
    );
  }

  ngOnInit(): void {
    this.loadInstanceFromNavigation();
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.generation++;
    clearTimeout(this.timer);
  }

  async runProfiler(): Promise<void> {
    if (!this.canRun()) return;
    const generation = this.generation;
    this.statusRequest++;
    clearTimeout(this.timer);
    this.busy.set(true);
    this.actionError.set("");
    try {
      const run = await firstValueFrom(
        this.perfAnalyzersApi.startModelPerf(
          {
            model_version: this.version(),
            image: this.image.trim(),
            dockerconfigjson: this.dockerconfigjson.trim() || undefined,
            batch_size: Number(this.batchSize),
            concurrency_range: this.concurrencyRange.trim(),
            measurement_request_count: Number(this.measurementRequestCount),
            input_data: this.normalizeLegacyInputData(this.inputData) || undefined,
          },
          Number(this.instanceId()),
          this.modelName(),
        ),
      );
      if (generation !== this.generation) return;
      this.dockerconfigjson = "";
      this.applyRun(run);
    } catch (error) {
      if (generation !== this.generation) return;
      this.actionError.set(mapApiErrorMessage(error, "Could not start benchmark."));
    } finally {
      if (generation === this.generation) {
        this.busy.set(false);
        await this.loadStatus();
      }
    }
  }

  async stopProfiler(): Promise<void> {
    const run = this.activeRun();
    if (!run || this.busy() || run.state === "stopping") return;
    const generation = this.generation;
    this.statusRequest++;
    clearTimeout(this.timer);
    this.busy.set(true);
    this.actionError.set("");
    try {
      const stopped = await firstValueFrom(
        this.perfAnalyzersApi.stopModelPerf(Number(this.instanceId()), this.modelName(), run.id),
      );
      if (generation === this.generation) this.applyRun(stopped);
    } catch (error) {
      if (generation === this.generation) {
        this.actionError.set(mapApiErrorMessage(error, "Could not stop benchmark."));
      }
    } finally {
      if (generation === this.generation) {
        this.busy.set(false);
        await this.loadStatus();
      }
    }
  }

  saveInputData(value: string): void {
    this.inputData = this.normalizeLegacyInputData(value);
  }

  private applyRun(run: ModelPerfRunResponse): void {
    const active = ["creating", "pending", "running", "stopping"].includes(run.state);
    this.activeRun.set(active ? run : null);
    this.latestRun.set(run);
    this.output.set(run.output || "");
  }

  async loadStatus(): Promise<void> {
    const generation = this.generation;
    const key = this.profileKey();
    const requestId = ++this.statusRequest;
    clearTimeout(this.timer);
    try {
      const status: ModelPerfStatusResponse = await firstValueFrom(
        this.perfAnalyzersApi.getModelPerfStatus(
          Number(this.instanceId()),
          this.modelName(),
          this.version(),
        ),
      );
      if (generation !== this.generation || requestId !== this.statusRequest || this.busy()) return;
      this.statusError.set("");
      if (!this.image) this.image = status.default_image;
      const active = (status.active_run ?? null) as ModelPerfRunResponse | null;
      const latest = (status.latest_run ?? null) as ModelPerfRunResponse | null;
      this.activeRun.set(active);
      this.latestRun.set(active || latest);
      this.output.set(
        active || latest ? (active || latest)!.output || "" : status.latest_result.output || "",
      );
      if (this.restoredKey !== key) {
        const selectedRun = [active, latest].find((run) => run?.model_version === this.version());
        const saved = selectedRun || (status.latest_result.found ? status.latest_result : null);
        if (saved) {
          this.batchSize = saved.batch_size ?? 1;
          this.concurrencyRange = saved.concurrency_range ?? "1";
          this.measurementRequestCount = saved.measurement_request_count ?? 50;
          this.inputData = this.normalizeLegacyInputData(saved.input_data);
          if (selectedRun) this.image = selectedRun.image;
        }
        this.restoredKey = key;
      }
    } catch (error) {
      if (generation !== this.generation || requestId !== this.statusRequest) return;
      this.statusError.set(mapApiErrorMessage(error, "Failed to load benchmark status; retrying."));
    } finally {
      if (
        generation === this.generation &&
        requestId === this.statusRequest &&
        !this.destroyed &&
        !this.busy()
      ) {
        this.loadingStatus.set(false);
        // Poll idle tabs too, so runs started in another browser become visible.
        this.timer = setTimeout(() => void this.loadStatus(), this.activeRun() ? 3000 : 5000);
      }
    }
  }

  private async resolveInstance(): Promise<void> {
    const generation = this.generation;
    this.resolvingInstance.set(true);
    try {
      const instance = (await firstValueFrom(
        this.instancesApi.getInstanceApiInstancesInstanceIdGet(this.instanceId()),
      )) as TritonInstanceDTO;

      if (generation !== this.generation) return;
      this.instanceName = instance?.name ?? this.instanceName;
      this.instanceUrl = instance?.url ?? this.instanceUrl;
      this.instanceS3.set((instance?.s3 ?? null) as InstanceS3ConfigDTO | null);
    } catch {
      if (generation === this.generation) this.actionError.set("Failed to load instance details.");
    } finally {
      if (generation === this.generation) this.resolvingInstance.set(false);
    }
  }

  private loadInstanceFromNavigation(): void {
    const navState = (history.state ?? {}) as { instanceName?: unknown; instanceUrl?: unknown };
    const stateName = typeof navState.instanceName === "string" ? navState.instanceName.trim() : "";
    const stateUrl = typeof navState.instanceUrl === "string" ? navState.instanceUrl.trim() : "";
    if (stateName) {
      this.instanceName = stateName;
    }
    if (stateUrl) {
      this.instanceUrl = stateUrl;
    }
  }

  private normalizeLegacyInputData(value: string | undefined | null): string {
    const raw = `${value ?? ""}`;
    const trimmed = raw.trim();
    if (!trimmed) {
      return raw;
    }
    if (
      trimmed === InstanceModelProfilePageComponent.LEGACY_TEMP_INPUT_PATH ||
      trimmed === InstanceModelProfilePageComponent.LEGACY_SHM_INPUT_PATH ||
      trimmed === `"${InstanceModelProfilePageComponent.LEGACY_TEMP_INPUT_PATH}"` ||
      trimmed === `"${InstanceModelProfilePageComponent.LEGACY_SHM_INPUT_PATH}"`
    ) {
      return `{
  "data": []
}`;
    }
    return raw;
  }
}
