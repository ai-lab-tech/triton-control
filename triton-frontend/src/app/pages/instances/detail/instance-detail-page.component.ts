import { Component, computed, DestroyRef, inject, OnInit, signal } from "@angular/core";
import { takeUntilDestroyed, toSignal } from "@angular/core/rxjs-interop";
import { EMPTY, Subject, firstValueFrom, interval, switchMap, timer } from "rxjs";

import { DatePipe } from "@angular/common";
import { ActivatedRoute, Router, RouterLink } from "@angular/router";
import { FormsModule } from "@angular/forms";
import { MatCardModule } from "@angular/material/card";
import { MatButtonModule } from "@angular/material/button";
import { MatIconModule } from "@angular/material/icon";
import { MatChipsModule } from "@angular/material/chips";
import { MatTabsModule } from "@angular/material/tabs";
import { MatDividerModule } from "@angular/material/divider";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatTooltipModule } from "@angular/material/tooltip";
import { MatTabChangeEvent } from "@angular/material/tabs";
import { Store } from "@ngrx/store";
import { Actions, ofType } from "@ngrx/effects";

import { type Instance } from "../instances.data";
import {
  DeploymentsService,
  InstancesService,
  PerfAnalyzersService,
  UpdateInstanceS3Request,
  UpdateTritonInstanceRequest,
} from "../../../api/generated/index";
import { mapApiErrorMessage } from "../../../shared/api-error-message";
import { AuthStore } from "../../../shared/auth/auth.store";
import { environment } from "../../../../environments/environment";
import {
  instanceDetailPageOpened,
  instanceDetailRefreshRequested,
  s3ConfigDisableRequested,
  s3ConfigSaveRequested,
  s3ConfigSaveSucceeded,
  s3ConfigDisableSucceeded,
  tritonConnectionSaveRequested,
  tritonConnectionSaveSucceeded,
} from "../../../state/instances-detail/instances-detail.actions";
import {
  selectDetailInstance,
  selectDetailLoading,
  selectDetailS3Saving,
  selectDetailTritonSaving,
} from "../../../state/instances-detail/instances-detail.selectors";
import {
  selectActiveRunKey,
  selectProfileRunning,
} from "../../../state/instances-profile/instances-profile.selectors";
import { isSelfDeployedStarting } from "../../../state/instances.utils";

@Component({
  selector: "app-instance-detail-page",
  standalone: true,
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    MatCardModule,
    MatButtonModule,
    MatIconModule,
    MatChipsModule,
    MatTabsModule,
    MatDividerModule,
    MatFormFieldModule,
    MatInputModule,
    MatTooltipModule,
  ],
  styleUrl: "./instance-detail-page.component.scss",
  templateUrl: "./instance-detail-page.component.html",
})
export class InstanceDetailPageComponent implements OnInit {
  readonly defaultS3Region = "us-east-1";
  private readonly instancesApi = inject(InstancesService);
  private readonly deploymentsApi = inject(DeploymentsService);
  private readonly perfAnalyzersApi = inject(PerfAnalyzersService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly store = inject(Store);
  private readonly auth = inject(AuthStore);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly actions$ = inject(Actions);
  private readonly pollingIntervalMs = environment.instancePollingIntervalMs ?? 10000;
  private readonly deploymentLogPollingIntervalMs =
    environment.deploymentLogPollingIntervalMs ?? 5000;
  private readonly logsTabActive$ = new Subject<boolean>();

  s3AccessKey = "";
  s3SecretKey = "";
  s3Bucket = "";
  s3Region = this.defaultS3Region;
  s3Endpoint = "";
  s3Prefix = "";
  s3VerifySsl = false;
  s3CaCertificate = "";
  s3SecretConfigured = false;
  tritonEndpoint = "";
  metricsEndpoint = "";
  tritonVerifySsl = false;
  tritonCaCertificate = "";
  copyBaseUrlMessage = "";
  readonly showRawMetadata = signal(false);
  readonly showS3Dialog = signal(false);
  readonly showTritonDialog = signal(false);
  readonly openModelConfigs = signal<Record<string, boolean>>({});
  readonly modelConfigLoading = signal<Record<string, boolean>>({});
  readonly modelConfigJson = signal<Record<string, string>>({});
  readonly modelConfigError = signal<Record<string, string>>({});
  readonly modelActionLoading = signal<Record<string, boolean>>({});
  readonly modelActionMessage = signal<Record<string, string>>({});
  readonly modelActionError = signal<Record<string, string>>({});
  readonly deploymentLogs = signal("");
  readonly deploymentLogsLoading = signal(false);
  readonly deploymentLogsError = signal("");
  readonly selectedDetailTabIndex = signal(0);
  readonly deploymentDeleting = signal(false);
  readonly deploymentDeleteError = signal("");
  readonly perfAnalyzerInstalled = signal(false);
  readonly perfProfileRunning = toSignal(this.store.select(selectProfileRunning), {
    initialValue: false,
  });
  readonly activeProfileRunKey = toSignal(this.store.select(selectActiveRunKey), {
    initialValue: "",
  });
  private dialogPointerStartedOnBackdrop = false;
  private openLogsOnce = false;

  readonly instanceId = computed(() => this.route.snapshot.paramMap.get("id"));
  readonly instance = toSignal(this.store.select(selectDetailInstance), { initialValue: null });
  readonly loading = toSignal(this.store.select(selectDetailLoading), { initialValue: false });
  readonly loadRequested = signal(false);
  readonly hasValidInstanceId = computed(() => {
    const id = (this.instanceId() ?? "").trim();
    return !!id && /^[0-9]+$/.test(id);
  });
  readonly showInitialLoading = computed(
    () =>
      this.hasValidInstanceId() && !this.instance() && (!this.loadRequested() || this.loading()),
  );
  readonly showNotFound = computed(
    () =>
      !this.hasValidInstanceId() || (this.loadRequested() && !this.loading() && !this.instance()),
  );
  readonly s3Saving = toSignal(this.store.select(selectDetailS3Saving), { initialValue: false });
  readonly tritonSaving = toSignal(this.store.select(selectDetailTritonSaving), {
    initialValue: false,
  });
  readonly canWriteInstances = this.auth.canWriteInstances;

  readonly metadataJson = computed(() => {
    const metadata = this.instance()?.serverMetadata;
    if (!metadata) {
      return "{}";
    }
    return JSON.stringify(metadata, null, 2);
  });
  readonly metadataSummary = computed(() => {
    const metadata = this.instance()?.serverMetadata ?? {};
    const serverName = this.readMetadataString(metadata, "name");
    const version = this.readMetadataString(metadata, "version");
    const extensions = this.readMetadataStringArray(metadata, "extensions");

    return {
      serverName: serverName || "Unavailable",
      version: version || "Unavailable",
      extensions,
    };
  });
  readonly runtimeEnvironmentLabel = computed(() => {
    const instance = this.instance();
    if (!instance) {
      return "";
    }
    if (instance.isSelfDeployed) {
      return `Kubernetes self-deployed (${instance.deploymentNamespace || "namespace pending"})`;
    }
    return this.resolveRuntimeEnvironment(instance.url, instance.serverMetadata);
  });
  readonly deploymentInProgress = computed(() => {
    const instance = this.instance();
    return !!instance && isSelfDeployedStarting(instance);
  });
  readonly activeProfileRunLabel = computed(() => {
    if (!this.perfProfileRunning() || !this.activeProfileRunKey()) {
      return "";
    }
    const parts = this.activeProfileRunKey().split(":");
    if (parts.length < 3) {
      return "Perf Analyzer run in progress";
    }
    const runInstanceId = parts[0];
    const runVersion = parts[parts.length - 1];
    const runModel = parts.slice(1, -1).join(":");
    const currentInstanceId = this.instanceId() ?? "";
    if (runInstanceId === currentInstanceId) {
      return `Perf Analyzer running for ${runModel}:${runVersion}`;
    }
    return "Perf Analyzer run in progress on another instance";
  });

  constructor() {
    this.openLogsOnce =
      !!this.router.getCurrentNavigation()?.extras.state?.["openLogsOnce"] ||
      !!window.history.state?.["openLogsOnce"];
    if (this.openLogsOnce) {
      this.clearOpenLogsOnceState();
    }

    this.actions$
      .pipe(ofType(s3ConfigSaveSucceeded, s3ConfigDisableSucceeded), takeUntilDestroyed())
      .subscribe(() => {
        this.showS3Dialog.set(false);
        this.s3AccessKey = "";
        this.s3SecretKey = "";
        this.s3Bucket = "";
        this.s3Region = this.defaultS3Region;
        this.s3Endpoint = "";
        this.s3Prefix = "";
        this.s3VerifySsl = false;
        this.s3CaCertificate = "";
        this.s3SecretConfigured = false;
      });
    this.actions$
      .pipe(ofType(tritonConnectionSaveSucceeded), takeUntilDestroyed())
      .subscribe(() => {
        this.showTritonDialog.set(false);
        this.tritonEndpoint = "";
        this.tritonVerifySsl = false;
        this.tritonCaCertificate = "";
      });

    this.logsTabActive$
      .pipe(
        switchMap((active) => (active ? timer(0, this.deploymentLogPollingIntervalMs) : EMPTY)),
        takeUntilDestroyed(),
      )
      .subscribe((tick) => {
        void this.loadDeploymentLogs({ showLoading: tick === 0 });
      });
  }

  ngOnInit(): void {
    const id = (this.instanceId() ?? "").trim();
    if (!id || !/^[0-9]+$/.test(id)) {
      return;
    }

    this.loadRequested.set(true);
    this.store.dispatch(instanceDetailPageOpened({ instanceId: id }));
    if (this.openLogsOnce) {
      this.selectedDetailTabIndex.set(3);
      this.logsTabActive$.next(true);
      this.openLogsOnce = false;
    }
    this.startRuntimePolling(id);
    void this.loadPerfAnalyzerStatus();
  }

  async loadPerfAnalyzerStatus(): Promise<void> {
    try {
      const status = await firstValueFrom(
        this.perfAnalyzersApi.getPerfAnalyzerStatusApiPerfAnalyzersGet(),
      );
      this.perfAnalyzerInstalled.set(status.installed);
    } catch {
      this.perfAnalyzerInstalled.set(false);
    }
  }

  saveS3Config(): void {
    const instance = this.instance();
    if (!instance || !this.canWriteInstances() || !this.canSaveS3Config()) {
      return;
    }

    const payload: UpdateInstanceS3Request = {
      enabled: true,
      endpoint: this.s3Endpoint?.trim(),
      bucket: this.s3Bucket?.trim(),
      region: this.s3Region?.trim() || this.defaultS3Region,
      prefix: this.s3Prefix?.trim(),
      access_key: this.s3AccessKey?.trim() || undefined,
      secret_key: this.s3SecretKey?.trim() || undefined,
      verify_ssl: this.shouldShowS3CaCertificate(),
      ca_certificate: this.shouldShowS3CaCertificate() ? this.s3CaCertificate.trim() : "",
      address_style: "path",
    };

    this.store.dispatch(s3ConfigSaveRequested({ instanceId: instance.id, payload }));
  }

  openS3Dialog(): void {
    const instance = this.instance();
    if (!instance || !this.canWriteInstances()) {
      return;
    }
    this.s3Bucket = instance.s3.bucket;
    this.s3Region = instance.s3.region || this.defaultS3Region;
    this.s3Endpoint = instance.s3.endpoint;
    this.s3Prefix = instance.s3.prefix;
    this.s3VerifySsl = instance.s3.verifySsl;
    this.s3CaCertificate = instance.s3.caCertificate;
    this.s3AccessKey = instance.s3.accessKey;
    this.s3SecretKey = "";
    this.s3SecretConfigured = instance.s3.secretConfigured;
    this.showS3Dialog.set(true);
  }

  onS3CertificateFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      this.s3CaCertificate = `${reader.result ?? ""}`;
      input.value = "";
    };
    reader.readAsText(file);
  }

  shouldShowS3CaCertificate(): boolean {
    return this.s3VerifySsl && this.s3Endpoint.trim().toLowerCase().startsWith("https://");
  }

  closeS3Dialog(): void {
    this.showS3Dialog.set(false);
  }

  onDialogBackdropPointerDown(event: PointerEvent): void {
    this.dialogPointerStartedOnBackdrop = event.target === event.currentTarget;
  }

  closeS3DialogFromBackdrop(event: MouseEvent): void {
    if (this.shouldCloseFromBackdrop(event)) {
      this.closeS3Dialog();
    }
  }

  openTritonDialog(): void {
    const instance = this.instance();
    if (!instance || !this.canWriteInstances()) {
      return;
    }
    this.tritonEndpoint = instance.url;
    this.metricsEndpoint = instance.metricsUrl;
    this.tritonVerifySsl = instance.tritonVerifySsl;
    this.tritonCaCertificate = instance.tritonCaCertificate;
    this.showTritonDialog.set(true);
  }

  closeTritonDialog(): void {
    this.showTritonDialog.set(false);
  }

  closeTritonDialogFromBackdrop(event: MouseEvent): void {
    if (this.shouldCloseFromBackdrop(event)) {
      this.closeTritonDialog();
    }
  }

  saveTritonConnection(): void {
    const instance = this.instance();
    if (!instance || !this.canWriteInstances()) {
      return;
    }

    const payload: UpdateTritonInstanceRequest = {
      url: this.tritonEndpoint.trim(),
      verify_ssl: this.tritonVerifySsl,
      ca_certificate: this.tritonVerifySsl ? this.tritonCaCertificate.trim() : "",
      metrics_url: this.metricsEndpoint.trim() || undefined,
    };

    this.store.dispatch(tritonConnectionSaveRequested({ instanceId: instance.id, payload }));
  }

  onTritonCertificateFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      this.tritonCaCertificate = `${reader.result ?? ""}`;
      input.value = "";
    };
    reader.readAsText(file);
  }

  disableS3Config(): void {
    const instance = this.instance();
    if (!instance || !this.canWriteInstances()) {
      return;
    }
    this.store.dispatch(
      s3ConfigDisableRequested({ instanceId: instance.id, currentS3: instance.s3 }),
    );
  }

  onDetailTabChanged(event: MatTabChangeEvent): void {
    this.selectedDetailTabIndex.set(event.index);
    this.logsTabActive$.next(event.tab.textLabel === "Logs");
  }

  async loadDeploymentLogs(options: { showLoading?: boolean } = {}): Promise<void> {
    const showLoading = options.showLoading ?? true;
    const instance = this.instance();
    if (!instance?.isSelfDeployed || this.deploymentLogsLoading()) {
      return;
    }

    if (showLoading) {
      this.deploymentLogsLoading.set(true);
      this.deploymentLogsError.set("");
    }
    try {
      const response = await firstValueFrom(
        this.deploymentsApi.getDeploymentLogsApiDeploymentsInstanceIdLogsGet(instance.id),
      );
      this.deploymentLogs.set(response.logs || instance.deploymentLog || "");
      if (!showLoading) {
        this.deploymentLogsError.set("");
      }
    } catch (error) {
      this.deploymentLogsError.set(mapApiErrorMessage(error, "Failed to load deployment logs."));
      this.deploymentLogs.set(instance.deploymentLog || "");
    } finally {
      if (showLoading) {
        this.deploymentLogsLoading.set(false);
      }
    }
  }

  async deleteDeployment(): Promise<void> {
    const instance = this.instance();
    if (!instance?.isSelfDeployed || this.deploymentDeleting()) {
      return;
    }

    const confirmed = window.confirm(
      `Delete Kubernetes deployment namespace "${instance.deploymentNamespace}" and remove this instance?`,
    );
    if (!confirmed) {
      return;
    }

    this.deploymentDeleting.set(true);
    this.deploymentDeleteError.set("");
    try {
      await firstValueFrom(
        this.deploymentsApi.deleteDeploymentApiDeploymentsInstanceIdDelete(instance.id),
      );
      void this.router.navigateByUrl("/instances");
    } catch (error) {
      this.deploymentDeleteError.set(mapApiErrorMessage(error, "Failed to delete deployment."));
    } finally {
      this.deploymentDeleting.set(false);
    }
  }

  toggleMetadataView(): void {
    this.showRawMetadata.update((value) => !value);
  }

  async copyBaseUrl(url: string): Promise<void> {
    if (!url) {
      return;
    }

    try {
      await navigator.clipboard.writeText(url);
      this.copyBaseUrlMessage = "Copied";
    } catch {
      this.copyBaseUrlMessage = "Copy failed";
    }
  }

  async toggleModelConfig(modelName: string, version: string): Promise<void> {
    const key = this.getModelConfigKey(modelName, version);
    const isOpen = !!this.openModelConfigs()[key];

    if (isOpen) {
      this.openModelConfigs.update((state) => ({ ...state, [key]: false }));
      return;
    }

    this.openModelConfigs.update((state) => ({ ...state, [key]: true }));

    if (this.modelConfigJson()[key] || this.modelConfigLoading()[key]) {
      return;
    }

    if (!version.trim()) {
      this.modelConfigError.update((state) => ({
        ...state,
        [key]: "No version available for this model.",
      }));
      return;
    }

    this.modelConfigLoading.update((state) => ({ ...state, [key]: true }));
    this.modelConfigError.update((state) => ({ ...state, [key]: "" }));

    try {
      const config = await firstValueFrom(
        this.instancesApi.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet(
          this.instanceId(),
          modelName,
          version,
        ),
      );
      this.modelConfigJson.update((state) => ({
        ...state,
        [key]: JSON.stringify(config, null, 2),
      }));
    } catch {
      this.modelConfigError.update((state) => ({
        ...state,
        [key]: "Failed to load model config.",
      }));
    } finally {
      this.modelConfigLoading.update((state) => ({ ...state, [key]: false }));
    }
  }

  isModelConfigOpen(modelName: string, version: string): boolean {
    return !!this.openModelConfigs()[this.getModelConfigKey(modelName, version)];
  }

  getModelConfigContent(modelName: string, version: string): string {
    return this.modelConfigJson()[this.getModelConfigKey(modelName, version)] ?? "";
  }

  getModelConfigError(modelName: string, version: string): string {
    return this.modelConfigError()[this.getModelConfigKey(modelName, version)] ?? "";
  }

  isModelConfigLoading(modelName: string, version: string): boolean {
    return !!this.modelConfigLoading()[this.getModelConfigKey(modelName, version)];
  }

  async loadModel(modelName: string, version: string): Promise<void> {
    await this.runModelAction("load", modelName, version);
  }

  async unloadModel(modelName: string, version: string): Promise<void> {
    await this.runModelAction("unload", modelName, version);
  }

  isModelActionLoading(modelName: string, version: string, action: "load" | "unload"): boolean {
    return !!this.modelActionLoading()[this.getModelActionKey(modelName, version, action)];
  }

  loadModelLabel(modelName: string, version: string, state: string): string {
    return this.isModelActionLoading(modelName, version, "load")
      ? "Loading..."
      : state === "READY"
        ? "Reload"
        : "Load";
  }

  unloadModelLabel(modelName: string, version: string): string {
    return this.isModelActionLoading(modelName, version, "unload") ? "Unloading..." : "Unload";
  }

  getModelActionMessage(modelName: string, version: string): string {
    return this.modelActionMessage()[this.getModelConfigKey(modelName, version)] ?? "";
  }

  getModelActionError(modelName: string, version: string): string {
    return this.modelActionError()[this.getModelConfigKey(modelName, version)] ?? "";
  }

  hasS3Connection(instance: Instance): boolean {
    return Boolean(
      instance.s3.enabled ||
      (instance.s3.endpoint ?? "").trim() ||
      (instance.s3.bucket ?? "").trim() ||
      (instance.s3.accessKey ?? "").trim() ||
      instance.s3.secretConfigured,
    );
  }

  getS3SecretPlaceholder(): string {
    return this.s3SecretConfigured ? "Stored secret configured" : "Secret key";
  }

  canSaveS3Config(): boolean {
    const instance = this.instance();
    const hasExistingConnection = instance ? this.hasS3Connection(instance) : false;
    return Boolean(
      this.s3Endpoint.trim() &&
      this.s3Bucket.trim() &&
      this.s3AccessKey.trim() &&
      (this.s3SecretKey.trim() || hasExistingConnection),
    );
  }

  private getModelConfigKey(modelName: string, version: string): string {
    return `${modelName}|${version}`;
  }

  private shouldCloseFromBackdrop(event: MouseEvent): boolean {
    const shouldClose = this.dialogPointerStartedOnBackdrop && event.target === event.currentTarget;
    this.dialogPointerStartedOnBackdrop = false;
    return shouldClose;
  }

  private clearOpenLogsOnceState(): void {
    const state = { ...window.history.state };
    delete state["openLogsOnce"];
    window.history.replaceState(state, document.title, window.location.href);
  }

  private getModelActionKey(modelName: string, version: string, action: "load" | "unload"): string {
    return `${this.getModelConfigKey(modelName, version)}|${action}`;
  }

  private startRuntimePolling(instanceId: string): void {
    interval(this.pollingIntervalMs)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        this.store.dispatch(instanceDetailRefreshRequested({ instanceId }));
      });
  }

  private async runModelAction(
    action: "load" | "unload",
    modelName: string,
    version: string,
  ): Promise<void> {
    const instanceId = this.instanceId();
    if (!instanceId || !this.canWriteInstances()) {
      return;
    }

    const actionKey = this.getModelActionKey(modelName, version, action);
    const modelKey = this.getModelConfigKey(modelName, version);

    this.modelActionLoading.update((state) => ({ ...state, [actionKey]: true }));
    this.modelActionMessage.update((state) => ({ ...state, [modelKey]: "" }));
    this.modelActionError.update((state) => ({ ...state, [modelKey]: "" }));

    try {
      if (action === "load") {
        await firstValueFrom(
          this.instancesApi.loadInstanceModelApiInstancesInstanceIdModelsModelNameLoadPost(
            instanceId,
            modelName,
          ),
        );
        this.modelActionMessage.update((state) => ({
          ...state,
          [modelKey]: "Model load requested.",
        }));
      } else {
        await firstValueFrom(
          this.instancesApi.unloadInstanceModelApiInstancesInstanceIdModelsModelNameUnloadPost(
            instanceId,
            modelName,
          ),
        );
        this.modelActionMessage.update((state) => ({
          ...state,
          [modelKey]: "Model unload requested.",
        }));
      }

      this.store.dispatch(instanceDetailRefreshRequested({ instanceId }));
    } catch (error) {
      this.modelActionError.update((state) => ({
        ...state,
        [modelKey]: this.buildModelActionErrorMessage(error),
      }));
    } finally {
      this.modelActionLoading.update((state) => ({ ...state, [actionKey]: false }));
    }
  }

  private buildModelActionErrorMessage(error: unknown): string {
    const detail = mapApiErrorMessage(error, "Failed to update model state.");
    const explicitModeError = "explicit model load / unload is not allowed if polling is enabled";
    if (detail.toLowerCase().includes(explicitModeError)) {
      return `${detail} This is only available if models are loaded in explicit mode in Triton.`;
    }
    return detail;
  }

  modelStateTone(state: string): "ok" | "warn" | "down" | "neutral" {
    const value = (state ?? "").trim().toUpperCase();
    if (value === "READY") {
      return "ok";
    }
    if (value === "LOADING" || value === "UNLOADING") {
      return "warn";
    }
    if (value === "UNAVAILABLE" || value === "FAILED") {
      return "down";
    }
    return "neutral";
  }

  modelStateLabel(state: string): string {
    const value = (state ?? "").trim().toUpperCase();
    if (!value) {
      return "Unknown";
    }
    return value.charAt(0) + value.slice(1).toLowerCase();
  }

  canInferModel(state: string, version: string): boolean {
    const normalizedState = (state ?? "").trim().toUpperCase();
    const hasVersion = (version ?? "").trim().length > 0;
    if (!hasVersion) {
      return false;
    }
    return normalizedState === "READY" || normalizedState === "ACTIVE";
  }

  canOpenProfile(modelName: string, state: string, version: string): boolean {
    if (!this.canInferModel(state, version)) {
      return false;
    }
    if (!this.perfProfileRunning()) {
      return true;
    }

    const instance = this.instance();
    if (!instance) {
      return false;
    }

    const targetKey = `${instance.id}:${modelName}:${version}`;
    return this.activeProfileRunKey() === targetKey;
  }

  canUnloadModel(state: string): boolean {
    const normalizedState = (state ?? "").trim().toUpperCase();
    return normalizedState !== "UNAVAILABLE";
  }

  canShowModelConfig(state: string): boolean {
    const normalizedState = (state ?? "").trim().toUpperCase();
    return normalizedState !== "UNAVAILABLE";
  }

  private readMetadataString(metadata: Record<string, unknown>, key: string): string {
    const value = metadata[key];
    return typeof value === "string" ? value : "";
  }

  private readMetadataStringArray(metadata: Record<string, unknown>, key: string): string[] {
    const value = metadata[key];
    if (!Array.isArray(value)) {
      return [];
    }
    return value.filter(
      (entry): entry is string => typeof entry === "string" && entry.trim().length > 0,
    );
  }

  private resolveRuntimeEnvironment(url: string, metadata: Record<string, unknown> | null): string {
    const platform = this.readMetadataString(metadata ?? {}, "platform");
    if (platform) {
      return platform;
    }

    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      if (host === "localhost" || host === "127.0.0.1") {
        return "Local runtime";
      }
      return `Runtime on ${parsed.hostname}`;
    } catch {
      return "Connected runtime";
    }
  }
}
