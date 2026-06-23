import { Component, OnDestroy, inject, signal } from "@angular/core";
import { DomSanitizer, SafeResourceUrl } from "@angular/platform-browser";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatDialog, MatDialogModule } from "@angular/material/dialog";
import { MatIconModule } from "@angular/material/icon";

import {
  ArgoWorkflowsStatusResponse,
  BASE_PATH,
  WorkflowsService,
} from "../../api/generated/index";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { AuthService } from "../../shared/auth/auth.service";
import { ChromeService } from "../../shared/chrome.service";
import { S3CredentialsDialogComponent } from "./s3-credentials-dialog/s3-credentials-dialog.component";

@Component({
  selector: "app-workflows-page",
  standalone: true,
  imports: [MatButtonModule, MatCardModule, MatDialogModule, MatIconModule],
  templateUrl: "./workflows-page.component.html",
  styleUrl: "./workflows-page.component.scss",
})
export class WorkflowsPageComponent implements OnDestroy {
  private readonly workflowsApi = inject(WorkflowsService);
  private readonly dialog = inject(MatDialog);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly auth = inject(AuthService);
  private readonly chrome = inject(ChromeService);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");

  readonly loading = signal(true);
  readonly frameLoading = signal(false);
  readonly status = signal<ArgoWorkflowsStatusResponse | null>(null);
  readonly frameUrl = signal<SafeResourceUrl | null>(null);
  readonly message = signal("");
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
    this.message.set("");
    try {
      await this.auth.refreshSession().catch(() => undefined);
      const status = await firstValueFrom(
        this.workflowsApi.getArgoWorkflowsStatusApiWorkflowsGet(),
      );
      this.status.set(status);
      if (status.enabled && status.ready) {
        this.openFrame(String(status.base_path));
      } else {
        this.frameUrl.set(null);
      }
    } catch (error) {
      this.status.set(null);
      this.frameUrl.set(null);
      this.message.set(mapApiErrorMessage(error, "Failed to load Argo Workflows status."));
    } finally {
      this.loading.set(false);
    }
  }

  reload(): void {
    const current = this.status();
    if (current?.enabled && current.ready) {
      this.openFrame(current.base_path);
    } else {
      void this.load();
    }
  }

  onFrameLoaded(): void {
    this.frameLoading.set(false);
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

  openCredentialsDialog(): void {
    this.dialog.open(S3CredentialsDialogComponent, {
      width: "900px",
      maxWidth: "95vw",
      panelClass: "custom-dialog",
    });
  }
}
