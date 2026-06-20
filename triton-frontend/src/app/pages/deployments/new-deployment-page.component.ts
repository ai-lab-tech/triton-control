import { Component, inject, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { Router, RouterLink } from "@angular/router";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatExpansionModule } from "@angular/material/expansion";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { MonacoEditorModule, NGX_MONACO_EDITOR_CONFIG } from "ngx-monaco-editor-v2";
import { firstValueFrom } from "rxjs";

import { CreateDeploymentRequest, DeploymentsService } from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";

@Component({
  selector: "app-new-deployment-page",
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
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

  deploymentName = "";
  image = "nvcr.io/nvidia/tritonserver:25.02-py3";
  ingressHost = "";
  ingressClassName = "";
  s3Url = "";
  s3Prefix = "";
  s3AccessKey = "";
  s3SecretKey = "";
  s3Region = "us-east-1";
  s3CaCertificate = "";
  modelControlMode: "explicit" | "poll" = "explicit";
  repositoryPollSecs = 15;
  modelName = "";

  gpuCount: number | null = 0;
  cpu = "";
  memory = "";
  readonly dockerconfigjson = signal("");
  readonly requirementsTxt = signal("");
  readonly requirementsEditorOptions = {
    theme: "vs-dark",
    language: "plaintext",
    automaticLayout: true,
    minimap: { enabled: false },
    wordWrap: "on" as const,
  };
  readonly dockerconfigjsonEditorOptions = {
    theme: "vs-dark",
    language: "json",
    automaticLayout: true,
    minimap: { enabled: false },
    wordWrap: "on" as const,
  };

  private readonly _message = signal("");
  private readonly _messageTone = signal<"info" | "success" | "error">("info");
  readonly deploying = signal(false);
  readonly message = this._message.asReadonly();
  readonly messageTone = this._messageTone.asReadonly();

  ingressStatus(): { label: string; tone: "neutral" | "ok" | "warn" | "error"; detail: string } {
    const hasHost = this.ingressHost.trim().length > 0;
    const hasClass = this.ingressClassName.trim().length > 0;
    if (!hasHost && !hasClass) {
      return { label: "Not configured", tone: "neutral", detail: "No ingress host/class set" };
    }
    if (hasHost && hasClass) {
      return { label: "Configured", tone: "ok", detail: "Host and class configured" };
    }
    return { label: "Warning", tone: "warn", detail: "Set both ingress host and class" };
  }

  pullSecretStatus(): { label: string; tone: "neutral" | "ok" | "warn" | "error"; detail: string } {
    const raw = this.dockerconfigjson().trim();
    if (!raw) {
      return { label: "Not configured", tone: "neutral", detail: "No pull secret provided" };
    }
    try {
      const parsed = JSON.parse(raw) as { auths?: Record<string, unknown> };
      const auths = parsed && typeof parsed === "object" ? parsed.auths : undefined;
      if (auths && typeof auths === "object" && Object.keys(auths).length > 0) {
        return { label: "Configured", tone: "ok", detail: "Registry auth configured" };
      }
      return { label: "Invalid", tone: "error", detail: "Missing auths entries" };
    } catch {
      return { label: "Invalid", tone: "error", detail: "Invalid JSON format" };
    }
  }

  requirementsStatus(): {
    label: string;
    tone: "neutral" | "ok" | "warn" | "error";
    detail: string;
  } {
    const count = this.requirementsPackageCount();
    if (count <= 0) {
      return { label: "Not configured", tone: "neutral", detail: "No packages configured" };
    }
    const packageLabel = count === 1 ? "package" : "packages";
    return { label: "Configured", tone: "ok", detail: `${count} ${packageLabel} configured` };
  }

  resourcesStatus(): { label: string; tone: "neutral" | "ok" | "warn" | "error"; detail: string } {
    const gpu = this.gpuCount ?? 0;
    if (gpu < 0) {
      return { label: "Invalid", tone: "error", detail: "GPU count cannot be negative" };
    }
    const hasCpu = this.cpu.trim().length > 0;
    const hasMemory = this.memory.trim().length > 0;
    if (gpu > 0) {
      return { label: "Configured", tone: "ok", detail: "GPU enabled" };
    }
    if (hasCpu || hasMemory) {
      return { label: "Configured", tone: "ok", detail: "Custom CPU/memory set" };
    }
    return { label: "Not configured", tone: "neutral", detail: "Default resource settings" };
  }

  usesHttpsS3(): boolean {
    const value = this.s3Url.trim().toLowerCase();
    return value.startsWith("https://") || value.startsWith("s3://https://");
  }

  async deploy(): Promise<void> {
    if (!this.canDeploy()) {
      this.setMessage("Required deployment fields are missing.", "error");
      return;
    }

    this.deploying.set(true);
    this.setMessage("", "info");

    const payload: CreateDeploymentRequest = {
      deployment_name: this.deploymentName.trim(),
      image: this.image.trim(),
      ingress_host: this.ingressHost.trim() || undefined,
      ingress_class_name: this.ingressClassName.trim() || undefined,
      s3_url: this.buildS3RepositoryUrl(this.s3Url, this.s3Prefix),
      s3_access_key: this.s3AccessKey.trim(),
      s3_secret_key: this.s3SecretKey,
      s3_region: this.s3Region.trim() || "us-east-1",
      dockerconfigjson: this.dockerconfigjson().trim() || undefined,
      model_control_mode: this.modelControlMode,
      repository_poll_secs: this.repositoryPollSecs,
      model_name:
        this.modelControlMode === "explicit" ? this.modelName.trim() || undefined : undefined,
      allow_metrics: true,
      requirements_txt: this.requirementsTxt().trim() || undefined,
      gpu_count: this.gpuCount ?? undefined,
      cpu: this.cpu.trim() || undefined,
      cpu_limit: this.cpu.trim() || undefined,
      memory: this.memory.trim() || undefined,
      memory_limit: this.memory.trim() || undefined,
    };
    const caCertificate = this.s3CaCertificate.trim();
    if (caCertificate) {
      // Keep runtime payload compatible if generated TS models lag behind backend schema.
      (payload as unknown as Record<string, unknown>)["s3_ca_certificate"] = caCertificate;
    }

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
      this.deploying.set(false);
    }
  }

  canDeploy(): boolean {
    return (
      this.s3Url.trim().length > 0 &&
      this.deploymentName.trim().length > 0 &&
      this.image.trim().length > 0 &&
      this.s3AccessKey.trim().length > 0 &&
      this.s3SecretKey.trim().length > 0 &&
      !this.deploying()
    );
  }

  private setMessage(message: string, tone: "info" | "success" | "error"): void {
    this._message.set(message);
    this._messageTone.set(tone);
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

  private buildS3RepositoryUrl(endpoint: string, prefix: string): string {
    const normalizedEndpoint = this.normalizeS3Url(endpoint).replace(/\/+$/, "");
    const normalizedPrefix = prefix.trim().replace(/^\/+|\/+$/g, "");
    return normalizedPrefix ? `${normalizedEndpoint}/${normalizedPrefix}` : normalizedEndpoint;
  }

  private requirementsPackageCount(): number {
    return this.requirementsTxt()
      .split(/\r?\n/)
      .map((line) => line.split("#", 1)[0].trim())
      .filter((line) => line.length > 0).length;
  }
}
