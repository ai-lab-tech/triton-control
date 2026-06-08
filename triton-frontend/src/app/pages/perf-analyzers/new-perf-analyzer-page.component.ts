import { Component, DestroyRef, OnInit, inject, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { FormsModule } from "@angular/forms";
import { RouterLink } from "@angular/router";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatExpansionModule } from "@angular/material/expansion";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";
import { MonacoEditorModule, NGX_MONACO_EDITOR_CONFIG } from "ngx-monaco-editor-v2";
import { firstValueFrom, interval } from "rxjs";

import {
  InstallPerfAnalyzerRequest,
  PerfAnalyzerInstallResponse,
  PerfAnalyzersService,
} from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";

@Component({
  selector: "app-new-perf-analyzer-page",
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatExpansionModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
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
  styleUrl: "./new-perf-analyzer-page.component.scss",
  templateUrl: "./new-perf-analyzer-page.component.html",
})
export class NewPerfAnalyzerPageComponent implements OnInit {
  private readonly perfAnalyzersApi = inject(PerfAnalyzersService);
  private readonly destroyRef = inject(DestroyRef);

  installationName = "perf-analyzer";
  image = "nvcr.io/nvidia/tritonserver:25.02-py3-sdk";
  readonly dockerconfigjson = signal("");
  readonly dockerconfigjsonEditorOptions = {
    theme: "vs-dark",
    language: "json",
    automaticLayout: true,
    minimap: { enabled: false },
    wordWrap: "on" as const,
  };

  private readonly _message = signal("");
  private readonly _messageTone = signal<"info" | "success" | "error">("info");
  readonly installing = signal(false);
  readonly uninstalling = signal(false);
  readonly loading = signal(true);
  readonly installation = signal<PerfAnalyzerInstallResponse | null>(null);
  readonly installationState = signal("not_installed");
  readonly installationReady = signal(false);
  readonly installationStatusMessage = signal("");
  readonly message = this._message.asReadonly();
  readonly messageTone = this._messageTone.asReadonly();

  pullSecretStatus(): { label: string; tone: "neutral" | "ok" | "error"; detail: string } {
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

  ngOnInit(): void {
    void this.loadStatus();
    interval(5000)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        if (this.installation() && !this.installationReady()) {
          void this.loadStatus(true);
        }
      });
  }

  async loadStatus(silent = false): Promise<void> {
    if (!silent) {
      this.loading.set(true);
    }
    try {
      const status = await firstValueFrom(
        this.perfAnalyzersApi.getPerfAnalyzerStatusApiPerfAnalyzersGet(),
      );
      const statusView = status as {
        status?: string | null;
        ready?: boolean | null;
        status_message?: string | null;
      };
      this.installation.set(status.installation ?? null);
      this.installationState.set(String(statusView.status || "not_installed"));
      this.installationReady.set(Boolean(statusView.ready));
      this.installationStatusMessage.set(String(statusView.status_message || ""));
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to load Perf Analyzer status."), "error");
      this.installationState.set("not_installed");
      this.installationReady.set(false);
      this.installationStatusMessage.set("");
    } finally {
      if (!silent) {
        this.loading.set(false);
      }
    }
  }

  async install(): Promise<void> {
    if (!this.canInstall()) {
      this.setMessage("Required Perf Analyzer fields are missing.", "error");
      return;
    }

    this.installing.set(true);
    this.setMessage("Installing Perf Analyzer and waiting for the pod to reach Running.", "info");

    const payload: InstallPerfAnalyzerRequest = {
      installation_name: this.installationName.trim(),
      image: this.image.trim(),
      dockerconfigjson: this.dockerconfigjson().trim() || undefined,
    };

    try {
      const response = await firstValueFrom(
        this.perfAnalyzersApi.installPerfAnalyzerApiPerfAnalyzersPost(payload),
      );
      this.installation.set(response);
      this.installationState.set("creating");
      this.installationReady.set(false);
      this.installationStatusMessage.set(
        "Installation exists. Waiting for Perf Analyzer pod to reach Running state.",
      );
      this.setMessage(`Perf Analyzer installed in namespace "${response.namespace}".`, "success");
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to install Perf Analyzer."), "error");
    } finally {
      this.installing.set(false);
    }
  }

  canInstall(): boolean {
    return (
      this.installationName.trim().length > 0 &&
      this.image.trim().length > 0 &&
      !this.installing() &&
      !this.installation()
    );
  }

  async uninstall(): Promise<void> {
    const current = this.installation();
    if (!current || this.uninstalling()) {
      return;
    }
    const confirmed = window.confirm(
      `Uninstall Perf Analyzer and delete namespace "${current.namespace}"?`,
    );
    if (!confirmed) {
      return;
    }

    this.uninstalling.set(true);
    this.setMessage("Uninstalling Perf Analyzer.", "info");
    try {
      const response = await firstValueFrom(
        this.perfAnalyzersApi.uninstallPerfAnalyzerApiPerfAnalyzersDelete(),
      );
      this.installation.set(null);
      this.installationState.set("not_installed");
      this.installationReady.set(false);
      this.installationStatusMessage.set("");
      this.setMessage(response.message, "success");
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to uninstall Perf Analyzer."), "error");
    } finally {
      this.uninstalling.set(false);
    }
  }

  private setMessage(message: string, tone: "info" | "success" | "error"): void {
    this._message.set(message);
    this._messageTone.set(tone);
  }
}
