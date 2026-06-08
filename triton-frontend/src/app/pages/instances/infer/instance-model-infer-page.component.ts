import { Component, computed, effect, inject, OnInit, signal } from "@angular/core";
import { toSignal } from "@angular/core/rxjs-interop";
import { firstValueFrom } from "rxjs";

import { ActivatedRoute, RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatIconModule } from "@angular/material/icon";
import { Store } from "@ngrx/store";

import { InstancesService, TritonInstanceDTO } from "../../../api/generated/index";
import { InstanceModelMonacoEditorComponent } from "./instance-model-monaco-editor.component";
import { toggleModelApiConfig } from "../shared/model-api-config";
import {
  type InferenceMetrics,
  inferRequestStarted,
  inferResultHydrated,
} from "../../../state/instances-infer/instances-infer.actions";
import {
  selectInferError,
  selectInferInferenceMetrics,
  selectInferProcessingResponse,
  selectInferRequestLatencyMs,
  selectInferResponseJson,
  selectInferSubmitting,
} from "../../../state/instances-infer/instances-infer.selectors";

@Component({
  selector: "app-instance-model-infer-page",
  standalone: true,
  imports: [
    RouterLink,
    MatButtonModule,
    MatCardModule,
    MatIconModule,
    InstanceModelMonacoEditorComponent,
  ],
  styleUrl: "./instance-model-infer-page.component.scss",
  templateUrl: "./instance-model-infer-page.component.html",
})
export class InstanceModelInferPageComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly instancesApi = inject(InstancesService);
  private readonly store = inject(Store);

  readonly instanceId = computed(() => this.route.snapshot.paramMap.get("id") ?? "");
  readonly modelName = computed(() => this.route.snapshot.paramMap.get("modelName") ?? "");
  readonly version = computed(() => this.route.snapshot.paramMap.get("version") ?? "");
  readonly hasValidRoute = computed(() => {
    const id = this.instanceId().trim();
    return (
      /^[0-9]+$/.test(id) && this.modelName().trim().length > 0 && this.version().trim().length > 0
    );
  });

  instanceName = "";
  instanceUrl = "";
  resolvingInstance = false;
  private readonly resolveError = signal("");
  readonly submitting = toSignal(this.store.select(selectInferSubmitting), { initialValue: false });
  readonly processingResponse = toSignal(this.store.select(selectInferProcessingResponse), {
    initialValue: false,
  });
  readonly inferError = toSignal(this.store.select(selectInferError), { initialValue: "" });
  readonly responseJson = toSignal(this.store.select(selectInferResponseJson), {
    initialValue: "",
  });
  readonly requestLatencyMs = toSignal(this.store.select(selectInferRequestLatencyMs), {
    initialValue: null as number | null,
  });
  readonly inferenceMetrics = toSignal(this.store.select(selectInferInferenceMetrics), {
    initialValue: null,
  });
  readonly inferenceMetricRows = computed(() => this.inferenceMetrics()?.models ?? []);
  readonly displayError = computed(() => this.resolveError() || this.inferError());
  private readonly persistResult = effect(() => {
    const responseJson = this.responseJson();
    if (!responseJson) {
      return;
    }
    this.saveInferResult(responseJson, this.requestLatencyMs(), this.inferenceMetrics());
  });
  readonly apiConfigOpen = signal(false);
  readonly apiConfigLoading = signal(false);
  readonly apiConfigJson = signal("");
  readonly apiConfigError = signal("");
  readonly sendLabel = computed(() =>
    this.submitting()
      ? "Sending..."
      : this.processingResponse()
        ? "Processing response..."
        : "Send / Infer",
  );
  inferUrlCopied = false;
  responseCopied = false;
  private inferUrlCopyResetTimer: ReturnType<typeof setTimeout> | null = null;
  private responseCopyResetTimer: ReturnType<typeof setTimeout> | null = null;
  readonly requestUrl = computed(() => {
    const baseUrl = this.instanceUrl.trim().replace(/\/$/, "");
    const modelName = encodeURIComponent(this.modelName().trim());
    const version = encodeURIComponent(this.version().trim());

    if (!baseUrl || !modelName || !version) {
      return "";
    }

    return `${baseUrl}/v2/models/${modelName}/versions/${version}/infer`;
  });
  private readonly inferBodyDefault = `{
  "inputs": []
}`;
  private _editorContent = this.inferBodyDefault;

  get editorContent(): string {
    return this._editorContent;
  }

  set editorContent(value: string) {
    this._editorContent = value;
    try {
      localStorage.setItem(this.inferBodyStorageKey(), value);
    } catch {
      // storage unavailable — ignore
    }
  }

  private inferBodyStorageKey(): string {
    return `triton-infer-body:${this.instanceId()}:${this.modelName()}:${this.version()}`;
  }

  private inferResultStorageKey(): string {
    return `triton-infer-result:${this.instanceId()}:${this.modelName()}:${this.version()}`;
  }

  constructor() {}

  async ngOnInit(): Promise<void> {
    if (!this.hasValidRoute()) {
      return;
    }

    try {
      const saved = localStorage.getItem(this.inferBodyStorageKey());
      if (saved) {
        this._editorContent = saved;
      }
      this.loadSavedInferResult();
    } catch {
      // storage unavailable — ignore
    }

    const navState = (history.state ?? {}) as { instanceName?: unknown; instanceUrl?: unknown };
    const stateName = typeof navState.instanceName === "string" ? navState.instanceName.trim() : "";
    const stateUrl = typeof navState.instanceUrl === "string" ? navState.instanceUrl.trim() : "";
    if (stateName) {
      this.instanceName = stateName;
    }
    if (stateUrl) {
      this.instanceUrl = stateUrl;
    }

    if (!this.instanceName || !this.instanceUrl) {
      this.resolvingInstance = true;
      this.resolveError.set("");
      try {
        const instance = (await firstValueFrom(
          this.instancesApi.getInstanceApiInstancesInstanceIdGet(this.instanceId()),
        )) as TritonInstanceDTO;

        this.instanceName = instance?.name ?? this.instanceName;
        this.instanceUrl = instance?.url ?? this.instanceUrl;
      } catch {
        this.resolveError.set("Failed to load instance details.");
      } finally {
        this.resolvingInstance = false;
      }
    }
  }

  async sendInference(): Promise<void> {
    if (!this.hasValidRoute() || this.submitting() || this.processingResponse()) {
      return;
    }

    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(this.editorContent);
    } catch (error) {
      this.resolveError.set(this.formatJsonError(error));
      return;
    }

    this.resolveError.set("");

    this.store.dispatch(
      inferRequestStarted({
        payload,
        instanceId: this.instanceId(),
        modelName: this.modelName(),
        version: this.version(),
      }),
    );
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

  async copyInferUrl(): Promise<void> {
    const value = this.requestUrl();
    if (!value) {
      return;
    }

    const copied = await this.copyText(value);
    if (!copied) {
      return;
    }

    this.inferUrlCopied = true;
    if (this.inferUrlCopyResetTimer) {
      clearTimeout(this.inferUrlCopyResetTimer);
    }
    this.inferUrlCopyResetTimer = setTimeout(() => {
      this.inferUrlCopied = false;
    }, 1500);
  }

  async copyResponse(): Promise<void> {
    const value = this.responseJson().trim();
    if (!value) {
      return;
    }

    const copied = await this.copyText(value);
    if (!copied) {
      return;
    }

    this.responseCopied = true;
    if (this.responseCopyResetTimer) {
      clearTimeout(this.responseCopyResetTimer);
    }
    this.responseCopyResetTimer = setTimeout(() => {
      this.responseCopied = false;
    }, 1500);
  }

  private async copyText(value: string): Promise<boolean> {
    if (!value) {
      return false;
    }

    try {
      if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch {
      // fallback below
    }

    if (typeof document === "undefined") {
      return false;
    }

    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return ok;
  }

  private formatJsonError(error: unknown): string {
    if (error instanceof Error && error.message.trim()) {
      return `Invalid JSON: ${error.message.trim()}`;
    }
    return "Invalid JSON body.";
  }

  private loadSavedInferResult(): void {
    const savedResult = localStorage.getItem(this.inferResultStorageKey());
    if (!savedResult) {
      return;
    }

    const parsed = JSON.parse(savedResult) as {
      responseJson?: unknown;
      requestLatencyMs?: unknown;
      inferenceMetrics?: unknown;
    };
    if (typeof parsed.responseJson !== "string" || !parsed.responseJson.trim()) {
      return;
    }

    this.store.dispatch(
      inferResultHydrated({
        responseJson: parsed.responseJson,
        requestLatencyMs:
          typeof parsed.requestLatencyMs === "number" ? parsed.requestLatencyMs : null,
        inferenceMetrics: this.isInferenceMetrics(parsed.inferenceMetrics)
          ? parsed.inferenceMetrics
          : null,
      }),
    );
  }

  private saveInferResult(
    responseJson: string,
    requestLatencyMs: number | null,
    inferenceMetrics: InferenceMetrics | null,
  ): void {
    try {
      localStorage.setItem(
        this.inferResultStorageKey(),
        JSON.stringify({ responseJson, requestLatencyMs, inferenceMetrics }),
      );
    } catch {
      // storage unavailable — ignore
    }
  }

  private isInferenceMetrics(value: unknown): value is InferenceMetrics {
    return (
      typeof value === "object" &&
      value !== null &&
      typeof (value as InferenceMetrics).available === "boolean" &&
      Array.isArray((value as InferenceMetrics).models)
    );
  }
}
