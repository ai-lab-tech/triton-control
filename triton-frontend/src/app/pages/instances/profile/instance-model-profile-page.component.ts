import { Component, computed, effect, inject, OnInit, signal } from "@angular/core";
import { toSignal } from "@angular/core/rxjs-interop";
import { firstValueFrom } from "rxjs";

import { ActivatedRoute, RouterLink } from "@angular/router";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";
import { Store } from "@ngrx/store";

import {
  InstancesService,
  PerfAnalyzersService,
  TritonInstanceDTO,
} from "../../../api/generated/index";
import { mapApiErrorMessage } from "../../../shared/api-error-message";
import { toggleModelApiConfig } from "../shared/model-api-config";
import {
  profileLastResultLoadStarted,
  profilePageOpened,
  profileRunStarted,
} from "../../../state/instances-profile/instances-profile.actions";
import {
  selectActiveProfileEntry,
  selectActiveKey,
  selectActiveRunKey,
  selectProfileError,
  selectProfileOutput,
  selectProfileRunning,
} from "../../../state/instances-profile/instances-profile.selectors";
import { InstanceModelMonacoEditorComponent } from "../infer/instance-model-monaco-editor.component";

@Component({
  selector: "app-instance-model-profile-page",
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    InstanceModelMonacoEditorComponent,
  ],
  styleUrl: "./instance-model-profile-page.component.scss",
  templateUrl: "./instance-model-profile-page.component.html",
})
export class InstanceModelProfilePageComponent implements OnInit {
  private static readonly LEGACY_TEMP_INPUT_PATH = "/tmp/pa_input.json";
  private static readonly LEGACY_SHM_INPUT_PATH = "/dev/shm/pa_input.json";
  private readonly route = inject(ActivatedRoute);
  private readonly instancesApi = inject(InstancesService);
  private readonly perfAnalyzersApi = inject(PerfAnalyzersService);
  private readonly store = inject(Store);

  readonly instanceId = computed(() => this.route.snapshot.paramMap.get("id") ?? "");
  readonly modelName = computed(() => this.route.snapshot.paramMap.get("modelName") ?? "");
  readonly version = computed(() => this.route.snapshot.paramMap.get("version") ?? "");
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

  readonly loadingStatus = signal(true);
  readonly installed = signal(false);
  readonly resolvingInstance = signal(false);
  readonly statusError = signal("");
  readonly running = toSignal(this.store.select(selectProfileRunning), { initialValue: false });
  readonly activeProfileKey = toSignal(this.store.select(selectActiveKey), { initialValue: "" });
  readonly activeRunKey = toSignal(this.store.select(selectActiveRunKey), { initialValue: "" });
  readonly profileEntry = toSignal(this.store.select(selectActiveProfileEntry), {
    initialValue: { error: "", output: "", command: [] },
  });
  readonly output = toSignal(this.store.select(selectProfileOutput), { initialValue: "" });
  readonly profileError = toSignal(this.store.select(selectProfileError), { initialValue: "" });
  readonly error = computed(() => this.statusError() || this.profileError());
  readonly otherRunInProgress = computed(
    () => this.running() && !!this.activeRunKey() && this.activeRunKey() !== this.profileKey(),
  );
  readonly apiConfigOpen = signal(false);
  readonly apiConfigLoading = signal(false);
  readonly apiConfigJson = signal("");
  readonly apiConfigError = signal("");
  private readonly applyLoadedResult = effect(() => {
    const entry = this.profileEntry();
    if (!entry.output || this.activeProfileKey() !== this.profileKey()) {
      return;
    }
    if (entry.batchSize != null) {
      this.batchSize = entry.batchSize;
    }
    if (entry.concurrencyRange != null) {
      this.concurrencyRange = entry.concurrencyRange;
    }
    if (entry.measurementRequestCount != null) {
      this.measurementRequestCount = entry.measurementRequestCount;
    }
    if (entry.inputData != null) {
      this.inputData = this.normalizeLegacyInputData(entry.inputData);
    }
  });

  canRun(): boolean {
    return (
      this.hasValidRoute() &&
      this.installed() &&
      !this.loadingStatus() &&
      !this.running() &&
      Number.isFinite(Number(this.batchSize)) &&
      Number(this.batchSize) >= 1 &&
      Number.isFinite(Number(this.measurementRequestCount)) &&
      Number(this.measurementRequestCount) >= 1 &&
      this.concurrencyRange.trim().length > 0
    );
  }

  async ngOnInit(): Promise<void> {
    if (!this.hasValidRoute()) {
      this.loadingStatus.set(false);
      return;
    }

    this.store.dispatch(profilePageOpened({ key: this.profileKey() }));
    this.store.dispatch(
      profileLastResultLoadStarted({
        key: this.profileKey(),
        instanceId: this.instanceId(),
        modelName: this.modelName(),
        version: this.version(),
      }),
    );
    this.loadInstanceFromNavigation();

    await Promise.all([this.loadStatus(), this.resolveInstance()]);
  }

  async runProfiler(): Promise<void> {
    const instanceId = Number(this.instanceId());
    if (!this.canRun() || !Number.isInteger(instanceId) || instanceId <= 0) {
      return;
    }

    this.statusError.set("");
    const normalizedInputData = this.normalizeLegacyInputData(this.inputData);
    this.inputData = normalizedInputData;
    this.store.dispatch(
      profileRunStarted({
        key: this.profileKey(),
        instanceId: this.instanceId(),
        modelName: this.modelName(),
        version: this.version(),
        batchSize: Number(this.batchSize),
        concurrencyRange: this.concurrencyRange.trim() || "1",
        measurementRequestCount: Number(this.measurementRequestCount),
        inputData: normalizedInputData || undefined,
      }),
    );
  }

  saveInputData(value: string): void {
    this.inputData = this.normalizeLegacyInputData(value);
  }

  async toggleApiConfig(): Promise<void> {
    await toggleModelApiConfig({
      hasValidRoute: this.hasValidRoute(),
      state: {
        open: this.apiConfigOpen,
        loading: this.apiConfigLoading,
        json: this.apiConfigJson,
        error: this.apiConfigError,
      },
      loadConfig: () =>
        firstValueFrom(
          this.instancesApi.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet(
            this.instanceId(),
            this.modelName(),
            this.version(),
          ),
        ),
    });
  }

  private async loadStatus(): Promise<void> {
    this.loadingStatus.set(true);
    try {
      const status = await firstValueFrom(
        this.perfAnalyzersApi.getPerfAnalyzerStatusApiPerfAnalyzersGet(),
      );
      this.installed.set(Boolean(status.installed));
    } catch (error) {
      this.installed.set(false);
      this.statusError.set(mapApiErrorMessage(error, "Failed to load Perf Analyzer status."));
    } finally {
      this.loadingStatus.set(false);
    }
  }

  private async resolveInstance(): Promise<void> {
    if (this.instanceName && this.instanceUrl) {
      return;
    }

    this.resolvingInstance.set(true);
    try {
      const instance = (await firstValueFrom(
        this.instancesApi.getInstanceApiInstancesInstanceIdGet(this.instanceId()),
      )) as TritonInstanceDTO;

      this.instanceName = instance?.name ?? this.instanceName;
      this.instanceUrl = instance?.url ?? this.instanceUrl;
    } catch {
      this.statusError.set("Failed to load instance details.");
    } finally {
      this.resolvingInstance.set(false);
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
