import { Component, OnDestroy, inject, signal } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { DomSanitizer, SafeResourceUrl } from "@angular/platform-browser";
import { FormsModule } from "@angular/forms";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";

import { BASE_PATH } from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { AuthService } from "../../shared/auth/auth.service";
import { ChromeService } from "../../shared/chrome.service";

type MlflowInstallResponse = {
  namespace: string;
  deployment_name: string;
  service_name: string;
  image: string;
  applied_resources: string[];
};

type MlflowStatusResponse = {
  installed: boolean;
  status: string;
  ready: boolean;
  status_message: string;
  base_path: string;
  installation: MlflowInstallResponse | null;
};

type InstallMlflowRequest = {
  installation_name: string;
  image: string;
  dockerconfigjson?: string;
};

@Component({
  selector: "app-mlflow-page",
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
  ],
  templateUrl: "./mlflow-page.component.html",
  styleUrl: "./mlflow-page.component.scss",
})
export class MlflowPageComponent implements OnDestroy {
  private readonly http = inject(HttpClient);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly auth = inject(AuthService);
  private readonly chrome = inject(ChromeService);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");

  installationName = "mlflow";
  image = "ghcr.io/mlflow/mlflow:v2.15.1";
  dockerconfigjson = "";

  readonly loading = signal(true);
  readonly installing = signal(false);
  readonly uninstalling = signal(false);
  readonly frameLoading = signal(false);
  readonly status = signal<MlflowStatusResponse | null>(null);
  readonly frameUrl = signal<SafeResourceUrl | null>(null);
  readonly message = signal("");
  readonly messageTone = signal<"info" | "success" | "error">("info");
  private reloadNonce = 0;

  constructor() {
    this.chrome.hideTopbar();
    void this.load();
  }

  ngOnDestroy(): void {
    this.chrome.showTopbar();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      await this.auth.refreshSession().catch(() => undefined);
      const status = await firstValueFrom(
        this.http.get<MlflowStatusResponse>(`${this.basePath}/api/mlflow`),
      );
      this.status.set(status);
      if (status.installed && status.ready) {
        this.openFrame(status.base_path || "/api/mlflow/proxy/");
      } else {
        this.frameUrl.set(null);
        this.frameLoading.set(false);
      }
    } catch (error) {
      this.status.set(null);
      this.frameUrl.set(null);
      this.frameLoading.set(false);
      this.setMessage(mapApiErrorMessage(error, "Failed to load MLflow status."), "error");
    } finally {
      this.loading.set(false);
    }
  }

  reload(): void {
    const current = this.status();
    if (current?.installed && current.ready) {
      this.openFrame(current.base_path || "/api/mlflow/proxy/");
      return;
    }
    void this.load();
  }

  onFrameLoaded(): void {
    this.frameLoading.set(false);
  }

  canInstall(): boolean {
    return (
      !this.installing() &&
      !this.status()?.installed &&
      this.installationName.trim().length > 0 &&
      this.image.trim().length > 0
    );
  }

  async install(): Promise<void> {
    if (!this.canInstall()) {
      return;
    }

    this.installing.set(true);
    this.setMessage("Installing MLflow and waiting for pod readiness.", "info");
    try {
      const payload: InstallMlflowRequest = {
        installation_name: this.installationName.trim(),
        image: this.image.trim(),
        dockerconfigjson: this.dockerconfigjson.trim() || undefined,
      };
      await firstValueFrom(
        this.http.post<MlflowInstallResponse>(`${this.basePath}/api/mlflow`, payload),
      );
      await this.load();
      this.setMessage("MLflow installation started.", "success");
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to install MLflow."), "error");
    } finally {
      this.installing.set(false);
    }
  }

  async uninstall(): Promise<void> {
    const current = this.status();
    if (!current?.installed || this.uninstalling()) {
      return;
    }
    const namespace = current.installation?.namespace || "mlflow";
    const confirmed = window.confirm(
      `Uninstall MLflow and delete resources in namespace "${namespace}"?`,
    );
    if (!confirmed) {
      return;
    }

    this.uninstalling.set(true);
    this.setMessage("Uninstalling MLflow.", "info");
    try {
      const response = await firstValueFrom(
        this.http.delete<{ status: string; message: string; namespace: string }>(
          `${this.basePath}/api/mlflow`,
        ),
      );
      this.status.set({
        installed: false,
        status: "not_installed",
        ready: false,
        status_message: "",
        base_path: "/api/mlflow/proxy/",
        installation: null,
      });
      this.frameUrl.set(null);
      this.frameLoading.set(false);
      this.setMessage(response.message, "success");
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to uninstall MLflow."), "error");
    } finally {
      this.uninstalling.set(false);
    }
  }

  private openFrame(path: string): void {
    this.reloadNonce += 1;
    const normalized = path.startsWith("/") ? path : `/${path}`;
    const separator = normalized.includes("?") ? "&" : "?";
    this.frameLoading.set(true);
    this.frameUrl.set(
      this.sanitizer.bypassSecurityTrustResourceUrl(
        `${this.basePath}${normalized}${separator}_tc_reload=${this.reloadNonce}`,
      ),
    );
  }

  private setMessage(message: string, tone: "info" | "success" | "error"): void {
    this.message.set(message);
    this.messageTone.set(tone);
  }
}
