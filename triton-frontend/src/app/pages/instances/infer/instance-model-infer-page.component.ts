import { Component, computed, effect, inject, OnInit, signal } from "@angular/core";
import { toSignal } from "@angular/core/rxjs-interop";
import { firstValueFrom, of } from "rxjs";

import { ActivatedRoute, RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatIconModule } from "@angular/material/icon";
import { Store } from "@ngrx/store";

import {
  InstanceS3ConfigDTO,
  InstancesService,
  TritonInstanceDTO,
} from "../../../api/generated/index";
import { InstanceModelMonacoEditorComponent } from "./instance-model-monaco-editor.component";
import { InstanceModelRepositoryConfigComponent } from "../shared/instance-model-repository-config.component";
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
    InstanceModelRepositoryConfigComponent,
  ],
  styleUrl: "./instance-model-infer-page.component.scss",
  templateUrl: "./instance-model-infer-page.component.html",
})
export class InstanceModelInferPageComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly instancesApi = inject(InstancesService);
  private readonly store = inject(Store);

  private readonly routeParamMap = toSignal(
    this.route.paramMap ?? of(this.route.snapshot.paramMap),
    {
      initialValue: this.route.snapshot.paramMap,
    },
  );
  readonly instanceId = computed(() => this.routeParamMap().get("id") ?? "");
  readonly modelName = computed(() => this.routeParamMap().get("modelName") ?? "");
  readonly version = computed(() => this.routeParamMap().get("version") ?? "");
  readonly hasValidRoute = computed(() => {
    const id = this.instanceId().trim();
    return (
      /^[0-9]+$/.test(id) && this.modelName().trim().length > 0 && this.version().trim().length > 0
    );
  });

  instanceName = "";
  instanceUrl = "";
  resolvingInstance = false;
  readonly instanceS3 = signal<InstanceS3ConfigDTO | null>(null);
  readonly usesGenerateEndpoint = signal(false);
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
  private readonly canPersistResult = signal(false);
  private readonly persistResult = effect(() => {
    if (!this.canPersistResult()) {
      return;
    }
    const responseJson = this.responseJson();
    if (!responseJson) {
      return;
    }
    this.saveInferResult(responseJson, this.requestLatencyMs(), this.inferenceMetrics());
  });
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

    if (this.usesGenerateEndpoint()) {
      return `${baseUrl}/v2/models/${modelName}/generate`;
    }

    return `${baseUrl}/v2/models/${modelName}/versions/${version}/infer`;
  });
  private readonly inferBodyDefault = `{
  "inputs": []
}`;
  private readonly generateBodyDefault = `{
  "text_input": "What is Triton Inference Server?",
  "parameters": {
    "stream": false,
    "temperature": 0
  }
}`;
  private _editorContent = this.inferBodyDefault;
  private initializedRouteKey = "";

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

  constructor() {
    effect(() => {
      const routeKey = this.inferResultStorageKey();
      if (!this.hasValidRoute() || this.initializedRouteKey === routeKey) {
        return;
      }
      this.initializedRouteKey = routeKey;
      this.loadRouteState();
    });
  }

  async ngOnInit(): Promise<void> {
    if (!this.hasValidRoute()) {
      return;
    }

    if (this.initializedRouteKey !== this.inferResultStorageKey()) {
      this.initializedRouteKey = this.inferResultStorageKey();
      this.loadRouteState();
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

    await this.resolveInstance();
  }

  private loadRouteState(): void {
    this.canPersistResult.set(false);
    this.clearInferResult();

    try {
      const saved = localStorage.getItem(this.inferBodyStorageKey());
      this._editorContent = saved || this.inferBodyDefault;
      this.loadSavedInferResult();
    } catch {
      this._editorContent = this.inferBodyDefault;
    }

    this.canPersistResult.set(true);
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

  private clearInferResult(): void {
    this.store.dispatch(
      inferResultHydrated({
        responseJson: "",
        requestLatencyMs: null,
        inferenceMetrics: null,
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

  private async resolveInstance(): Promise<void> {
    this.resolvingInstance = true;
    this.resolveError.set("");
    try {
      const instance = (await firstValueFrom(
        this.instancesApi.getInstanceApiInstancesInstanceIdGet(this.instanceId()),
      )) as TritonInstanceDTO;

      this.instanceName = instance?.name ?? this.instanceName;
      this.instanceUrl = instance?.url ?? this.instanceUrl;
      this.instanceS3.set((instance?.s3 ?? null) as InstanceS3ConfigDTO | null);
      const usesGenerate = this.usesGenerateEndpointBackend(instance);
      this.usesGenerateEndpoint.set(usesGenerate);
      if (usesGenerate && this.editorContent.trim() === this.inferBodyDefault.trim()) {
        this._editorContent = this.generateBodyDefault;
      }
    } catch {
      this.resolveError.set("Failed to load instance details.");
    } finally {
      this.resolvingInstance = false;
    }
  }

  private usesGenerateEndpointBackend(instance: TritonInstanceDTO | null | undefined): boolean {
    if (!instance) {
      return false;
    }
    const metadata = instance.server_metadata as Record<string, unknown> | null | undefined;
    const values: string[] = [];
    if (metadata && typeof metadata === "object") {
      values.push(...Object.values(metadata).map((value) => String(value ?? "")));
    }
    values.push(instance.deployment_log ?? "");
    for (const model of instance.repository_models ?? []) {
      values.push(String(model.name ?? ""), String(model.reason ?? ""), String(model.state ?? ""));
    }
    const haystack = values.join("\n").toLowerCase();
    return ["vllm", "tensorrtllm", "tensorrt_llm", "trtllm"].some((token) =>
      haystack.includes(token),
    );
  }
}
