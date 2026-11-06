import { isEndpointHostname, resolvedEndpointHostname } from "../../shared/endpoint-hostname";
import { Component, inject, signal } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { FormsModule } from "@angular/forms";
import { Router, RouterLink } from "@angular/router";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatCheckboxModule } from "@angular/material/checkbox";
import { MatExpansionModule } from "@angular/material/expansion";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { MonacoEditorModule, NGX_MONACO_EDITOR_CONFIG } from "ngx-monaco-editor-v2";
import { firstValueFrom } from "rxjs";

import { BASE_PATH, CreateDeploymentRequest, DeploymentsService } from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";

type S3Profile = {
  id: number;
  name: string;
  endpoint: string;
  bucket: string;
  region: string;
  access_key: string;
  secret_key: string;
  prefix: string;
  force_path_style: boolean;
  ca_certificate: string;
};

const DEFAULT_CPU = "4";
const DEFAULT_MEMORY_GI = 10;
const DEFAULT_GPU_COUNT = 0;

@Component({
  selector: "app-new-deployment-page",
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatCheckboxModule,
    MatExpansionModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatSelectModule,
    MonacoEditorModule,
    RouterLink,
  ],
  providers: [
    {
      provide: NGX_MONACO_EDITOR_CONFIG,
      useValue: {
        baseUrl: "assets",
      },
    },
  ],
  styleUrl: "./new-deployment-page.component.scss",
  templateUrl: "./new-deployment-page.component.html",
})
export class NewDeploymentPageComponent {
  private readonly router = inject(Router);
  private readonly deploymentsApi = inject(DeploymentsService);
  private readonly http = inject(HttpClient);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");

  deploymentName = "";
  image = "nvcr.io/nvidia/tritonserver:26.06-py3";
  accessMode: "internal" | "ingress" = "internal";
  basicAuthEnabled = false;
  basicAuthUsername = "";
  basicAuthPassword = "";
  ingressHost = "";
  ingressScheme: "http" | "https" = "https";
  selectedS3ProfileId = "";
  s3Url = "";
  s3Bucket = "";
  s3Prefix = "";
  backend: "triton" | "vllm" = "triton";
  modelControlMode: "explicit" | "poll" = "poll";
  repositorySyncMode: "direct" | "init" | "sidecar" = "direct";
  repositoryPollSecs = 15;
  modelName = "";
  exampleModelName = "";
  exampleRequest = "";
  readonly exampleEditorOptions = {
    language: "json",
    theme: "vs-dark",
    automaticLayout: true,
    minimap: { enabled: false },
    wordWrap: "on" as const,
  };

  gpuCount: number | null = DEFAULT_GPU_COUNT;
  cpu = DEFAULT_CPU;
  memoryGi: number | null = DEFAULT_MEMORY_GI;
  readonly requirementsTxt = signal("");
  readonly s3Profiles = signal<S3Profile[]>([]);
  readonly s3ProfilesLoading = signal(false);
  readonly requirementsEditorOptions = {
    theme: "vs-dark",
    language: "plaintext",
    automaticLayout: true,
    minimap: { enabled: false },
    wordWrap: "on" as const,
  };

  private readonly _message = signal("");
  private readonly _messageTone = signal<"info" | "success" | "error">("info");
  readonly deploying = signal(false);
  readonly message = this._message.asReadonly();
  readonly messageTone = this._messageTone.asReadonly();

  readonly baseDomain = signal("");
  readonly allowHttp = signal(false);
  readonly ingressEnabled = signal(false);
  async loadEndpointPolicy(): Promise<void> {
    try {
      const policy = await firstValueFrom(
        this.deploymentsApi.getDeploymentPolicyApiDeploymentsPolicyGet(),
      );
      this.baseDomain.set(policy.base_domain || "");
      this.allowHttp.set(policy.allow_http);
      this.ingressEnabled.set(policy.ingress_enabled);
    } catch {
      this.allowHttp.set(false);
      this.ingressEnabled.set(false);
    }
  }

  constructor() {
    void this.loadEndpointPolicy();
    void this.loadS3Profiles();
  }

  endpointPreview(): string {
    return this.ingressHost.trim()
      ? this.ingressScheme + "://" + resolvedEndpointHostname(this.ingressHost, this.baseDomain())
      : "";
  }

  ingressStatus(): { label: string; tone: "neutral" | "ok" | "warn" | "error"; detail: string } {
    if (this.accessMode === "internal")
      return { label: "Internal", tone: "neutral", detail: "Cluster Service" };
    const hasHost = this.ingressHost.trim().length > 0;
    if (hasHost) {
      return {
        label: "On",
        tone: "ok",
        detail: "Administrator-managed ingress",
      };
    }
    return { label: "Check", tone: "warn", detail: "Set an Ingress hostname" };
  }

  requirementsStatus(): {
    label: string;
    tone: "neutral" | "ok" | "warn" | "error";
    detail: string;
  } {
    const count = this.requirementsPackageCount();
    if (count <= 0) {
      return { label: "Off", tone: "neutral", detail: "No packages" };
    }
    const packageLabel = count === 1 ? "Package" : "Packages";
    return { label: "On", tone: "ok", detail: `${count} ${packageLabel}` };
  }

  resourcesStatus(): { label: string; tone: "neutral" | "ok" | "warn" | "error"; detail: string } {
    const gpu = this.gpuCount ?? 0;
    if (gpu < 0) {
      return { label: "Invalid", tone: "error", detail: "GPU count cannot be negative" };
    }
    if (!this.hasValidMemoryGi()) {
      return { label: "Invalid", tone: "error", detail: "Memory must be a whole Gi number" };
    }
    const cpu = this.cpu.trim();
    const memory = this.memoryQuantity();
    const hasCpu = cpu.length > 0;
    const hasMemory = memory.length > 0;
    if (gpu > 0) {
      return { label: "On", tone: "ok", detail: "GPU enabled" };
    }
    if (cpu === DEFAULT_CPU && this.memoryGi === DEFAULT_MEMORY_GI) {
      return {
        label: "Default",
        tone: "ok",
        detail: `CPU ${DEFAULT_CPU}, memory ${DEFAULT_MEMORY_GI}Gi`,
      };
    }
    if (hasCpu || hasMemory) {
      return { label: "On", tone: "ok", detail: "CPU/memory set" };
    }
    return { label: "Off", tone: "neutral", detail: "Default resources" };
  }

  modelControlStatus(): { label: string; tone: "neutral" | "ok"; detail: string } {
    if (this.modelControlMode === "poll") {
      return {
        label: "Poll",
        tone: "ok",
        detail: `Every ${this.repositoryPollSecs || 15}s`,
      };
    }
    return { label: "Explicit", tone: "neutral", detail: "Manual loading" };
  }

  s3Destination(): string {
    return this.buildS3RepositoryUrl(this.s3Url, this.s3Bucket, this.s3Prefix);
  }

  backendChanged(): void {
    this.repositorySyncMode = this.usesSyncedRepository() ? "sidecar" : "direct";
    if (this.backend === "vllm" && !this.gpuCount) {
      this.gpuCount = 1;
    }
  }

  setSyncedLlmBackend(enabled: boolean): void {
    this.backend = enabled ? "vllm" : "triton";
    this.backendChanged();
  }

  async loadS3Profiles(): Promise<void> {
    this.s3ProfilesLoading.set(true);
    try {
      this.s3Profiles.set(
        await firstValueFrom(this.http.get<S3Profile[]>(this.apiUrl("/api/s3-profiles"))),
      );
    } catch {
      this.s3Profiles.set([]);
    } finally {
      this.s3ProfilesLoading.set(false);
    }
  }

  s3ProfileChanged(): void {
    const profile = this.s3Profiles().find((item) => String(item.id) === this.selectedS3ProfileId);
    if (!profile) {
      return;
    }
    this.s3Url = profile.endpoint;
    this.s3Bucket = profile.bucket;
  }

  async deploy(): Promise<void> {
    if (!this.canDeploy()) {
      this.setMessage("Required fields are missing.", "error");
      return;
    }

    let exampleRequest: Record<string, unknown> | undefined;
    if (this.exampleRequest.trim()) {
      try {
        const parsed: unknown = JSON.parse(this.exampleRequest);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("Expected a JSON object.");
        }
        exampleRequest = parsed as Record<string, unknown>;
        if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(this.exampleModelName.trim())) {
          throw new Error("Enter the model name for the example request.");
        }
      } catch (error) {
        this.setMessage(
          `Invalid example request: ${error instanceof Error ? error.message : "Invalid JSON."}`,
          "error",
        );
        return;
      }
    }

    this.deploying.set(true);
    this.setMessage("", "info");

    const payload: CreateDeploymentRequest = {
      deployment_name: this.deploymentName.trim(),
      image: this.image.trim(),
      access_mode: this.accessMode,
      ingress_scheme: this.accessMode === "ingress" ? this.ingressScheme : undefined,
      ingress_host: this.accessMode === "ingress" ? this.ingressHost.trim() : undefined,
      basic_auth_enabled: this.accessMode === "ingress" && this.basicAuthEnabled,
      basic_auth_username:
        this.accessMode === "ingress" && this.basicAuthEnabled ? this.basicAuthUsername : undefined,
      basic_auth_password:
        this.accessMode === "ingress" && this.basicAuthEnabled ? this.basicAuthPassword : undefined,
      s3_profile_id: Number(this.selectedS3ProfileId),
      repository_prefix: this.s3Prefix.trim().replace(/^\/+|\/+$/g, ""),
      model_control_mode: this.modelControlMode,
      repository_poll_secs: this.repositoryPollSecs,
      repository_sync_mode: this.usesSyncedRepository() ? "sidecar" : this.repositorySyncMode,
      model_name: this.modelName.trim() || undefined,
      example_model_name: exampleRequest ? this.exampleModelName.trim() : undefined,
      example_request: exampleRequest,
      allow_metrics: true,
      requirements_txt: this.requirementsTxt().trim() || undefined,
      gpu_count: this.gpuCount ?? undefined,
      cpu: this.cpu.trim() || undefined,
      cpu_limit: this.cpu.trim() || undefined,
      memory: this.memoryQuantity() || undefined,
      memory_limit: this.memoryQuantity() || undefined,
    };

    try {
      const response = await firstValueFrom(
        this.deploymentsApi.createDeploymentApiDeploymentsPost(payload),
      );
      this.setMessage("Deployment created.", "success");
      if (response.instance_id) {
        void this.router.navigateByUrl(`/instances/${response.instance_id}`, {
          state: { openLogsOnce: true },
        });
      } else {
        void this.router.navigateByUrl("/instances");
      }
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to create deployment."), "error");
    } finally {
      this.basicAuthPassword = "";
      this.deploying.set(false);
    }
  }

  usesSyncedRepository(): boolean {
    const image = this.image.toLowerCase();
    return this.backend === "vllm" || image.includes("trtllm") || image.includes("tensorrtllm");
  }

  canDeploy(): boolean {
    return (
      this.s3Profiles().some((profile) => String(profile.id) === this.selectedS3ProfileId) &&
      this.deploymentName.trim().length > 0 &&
      this.image.trim().length > 0 &&
      (this.accessMode === "internal" ||
        (this.ingressEnabled() &&
          isEndpointHostname(this.ingressHost) &&
          (this.allowHttp() || this.ingressScheme === "https") &&
          (!this.basicAuthEnabled ||
            (!!this.basicAuthUsername &&
              !!this.basicAuthPassword &&
              this.ingressScheme === "https")))) &&
      this.hasValidMemoryGi() &&
      !this.deploying()
    );
  }

  private setMessage(message: string, tone: "info" | "success" | "error"): void {
    this._message.set(message);
    this._messageTone.set(tone);
  }

  private memoryQuantity(): string {
    const value = this.memoryGi ?? 0;
    return value > 0 && Number.isInteger(value) ? `${value}Gi` : "";
  }

  private hasValidMemoryGi(): boolean {
    const value = this.memoryGi ?? 0;
    return value >= 0 && Number.isInteger(value);
  }

  private apiUrl(path: string): string {
    return `${this.basePath}${path}`;
  }

  private normalizeS3Url(value: string): string {
    const raw = value.trim();
    if (!raw) {
      return raw;
    }
    if (raw.startsWith("s3://")) {
      return raw;
    }
    if (raw.startsWith("http://") || raw.startsWith("https://")) {
      return `s3://${raw}`;
    }
    return raw;
  }

  private buildS3RepositoryUrl(endpoint: string, bucket: string, prefix: string): string {
    if (!endpoint.trim()) {
      return "";
    }
    const normalizedEndpoint = this.normalizeS3Url(endpoint).replace(/\/+$/, "");
    const normalizedBucket = bucket.trim().replace(/^\/+|\/+$/g, "");
    const normalizedPrefix = prefix.trim().replace(/^\/+|\/+$/g, "");
    const path = [normalizedBucket, normalizedPrefix].filter(Boolean).join("/");
    return path ? `${normalizedEndpoint}/${path}` : normalizedEndpoint;
  }

  private requirementsPackageCount(): number {
    return this.requirementsTxt()
      .split(/\r?\n/)
      .map((line) => line.split("#", 1)[0].trim())
      .filter((line) => line.length > 0).length;
  }
}
