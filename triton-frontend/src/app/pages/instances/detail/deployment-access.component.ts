import { Component, inject, input, OnInit, output, signal } from "@angular/core";
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
        margin-top: 1rem;
      }
      .fields {
        display: grid;
        gap: 0.75rem;
        margin-top: 1rem;
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
  private readonly api = inject(DeploymentsService);
  readonly access = signal<DeploymentAccessResponse | null>(null);
  readonly busy = signal(false);
  readonly editing = signal(false);
  readonly error = signal("");
  readonly message = signal("");
  mode: "internal" | "ingress" = "internal";
  host = "";
  scheme: "http" | "https" = "https";
  ingressClass = "";
  authEnabled = false;
  username = "";
  password = "";

  ngOnInit(): void {
    void this.load();
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
    this.ingressClass = access.ingress_class_name || "";
    this.authEnabled = !!access.basic_auth_enabled;
    this.username = "";
    this.password = "";
    this.message.set("");
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
    if (!this.host.trim()) return false;
    if (!this.authEnabled) return true;
    if (this.scheme !== "https") return false;
    return (
      (!!this.username && !!this.password) ||
      (!!this.access()?.basic_auth_enabled && !this.username && !this.password)
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
            ingress_class_name: ingress ? this.ingressClass.trim() || undefined : undefined,
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
      this.cancel();
      this.message.set(
        "Access settings saved. The ingress controller may take a moment to apply them.",
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
