import { Component, inject, input, OnInit, output, signal } from "@angular/core";
import { ClipboardModule } from "@angular/cdk/clipboard";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatCheckboxModule } from "@angular/material/checkbox";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { firstValueFrom } from "rxjs";
import { DeploymentAccessResponse, DeploymentsService } from "../../../api/generated";
import { mapApiErrorMessage } from "../../../shared/api-error-message";

@Component({
  selector: "app-deployment-access",
  standalone: true,
  imports: [
    ClipboardModule,
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  templateUrl: "./deployment-access.component.html",
  styles: [
    `
      :host {
        display: block;
      }
      h2 {
        font-size: 1.2rem;
        margin: 0;
      }
      h3 {
        font-size: 1rem;
        margin: 0 0 0.5rem;
      }
      .hint {
        margin: 0.5rem 0 0;
        opacity: 0.75;
      }
      .endpoint-header,
      .endpoint-url {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        flex-wrap: wrap;
      }
      .endpoint-url {
        margin: 1.25rem 0;
        padding: 1rem;
        border-radius: 8px;
        background: var(--mat-sys-surface, #fff);
      }
      .endpoint-url code {
        flex: 1;
        min-width: 0;
      }
      .authentication {
        padding: 1rem 0;
      }
      button {
        flex-shrink: 0;
      }
      .fields {
        display: grid;
        gap: 0.75rem;
        border: 0;
        padding: 1rem 0;
        max-width: 640px;
      }
      .actions {
        display: flex;
        gap: 0.5rem;
      }
      .error {
        color: var(--mat-sys-error, #b3261e);
      }
      code {
        overflow-wrap: anywhere;
      }
    `,
  ],
})
export class DeploymentAccessComponent implements OnInit {
  readonly instanceId = input.required<string>();
  readonly canWrite = input(false);
  readonly saved = output<void>();
  readonly accessChanged = output<DeploymentAccessResponse>();
  readonly changingCredentials = signal(false);
  readonly copyMessage = signal("");
  private readonly api = inject(DeploymentsService);
  readonly access = signal<DeploymentAccessResponse | null>(null);
  readonly busy = signal(false);
  readonly allowHttp = signal(false);
  readonly ingressEnabled = signal(false);

  async loadPolicy(): Promise<void> {
    try {
      const policy = await firstValueFrom(this.api.getDeploymentPolicyApiDeploymentsPolicyGet());
      this.allowHttp.set(policy.allow_http);
      this.ingressEnabled.set(policy.ingress_enabled);
    } catch {
      this.allowHttp.set(false);
      this.ingressEnabled.set(false);
    }
  }

  readonly editing = signal(false);
  readonly error = signal("");
  readonly message = signal("");
  mode: "internal" | "ingress" = "internal";
  host = "";
  scheme: "http" | "https" = "https";
  authEnabled = false;
  username = "";
  password = "";

  ngOnInit(): void {
    void this.load();
    void this.loadPolicy();
  }

  async load(): Promise<void> {
    this.busy.set(true);
    this.error.set("");
    try {
      this.access.set(
        await firstValueFrom(
          this.api.getDeploymentAccessApiDeploymentsInstanceIdAccessGet(this.instanceId()),
        ),
      );
      const current = this.access();
      if (current) this.accessChanged.emit(current);
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not load access settings."));
    } finally {
      this.busy.set(false);
    }
  }

  edit(): void {
    const access = this.access();
    if (!access || !this.canWrite() || this.busy()) return;
    this.mode = access.access_mode;
    this.host = access.ingress_host || "";
    this.scheme = access.ingress_scheme || "https";
    this.authEnabled = !!access.basic_auth_enabled;
    this.username = "";
    this.password = "";
    this.message.set("");
    this.changingCredentials.set(false);
    this.editing.set(true);
  }

  cancel(): void {
    this.username = "";
    this.password = "";
    this.editing.set(false);
    this.error.set("");
  }

  valid(): boolean {
    if (this.mode === "internal") return true;
    if (!this.ingressEnabled() || !this.host.trim()) return false;
    if (
      this.scheme === "http" &&
      !this.allowHttp() &&
      !(
        this.access()?.ingress_scheme === "http" &&
        this.host.trim().toLowerCase().replace(/\.$/, "") === this.access()?.ingress_host
      )
    )
      return false;
    if (!this.authEnabled) return true;
    if (this.scheme !== "https") return false;
    return (
      (!!this.username && !!this.password) ||
      (!!this.access()?.basic_auth_enabled &&
        !this.changingCredentials() &&
        !this.username &&
        !this.password)
    );
  }

  async save(): Promise<void> {
    if (!this.canWrite() || this.busy() || !this.valid()) return;
    this.busy.set(true);
    this.error.set("");
    const ingress = this.mode === "ingress";
    try {
      const response = await firstValueFrom(
        this.api.updateDeploymentAccessApiDeploymentsInstanceIdAccessPut(
          {
            access_mode: this.mode,
            ingress_host: ingress ? this.host.trim() : undefined,
            ingress_scheme: ingress ? this.scheme : undefined,
            basic_auth_enabled: ingress && this.authEnabled,
            basic_auth_username:
              ingress && this.authEnabled ? this.username || undefined : undefined,
            basic_auth_password:
              ingress && this.authEnabled ? this.password || undefined : undefined,
          },
          this.instanceId(),
        ),
      );
      this.access.set(response);
      this.accessChanged.emit(response);
      this.cancel();
      this.message.set(
        "Endpoint saved. The ingress controller may take a moment to apply the changes.",
      );
      this.saved.emit();
    } catch (error) {
      this.error.set(
        mapApiErrorMessage(error, "Could not update access settings. Reload before retrying."),
      );
    } finally {
      this.password = "";
      this.busy.set(false);
    }
  }
}
