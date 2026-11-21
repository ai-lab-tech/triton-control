import { Component, inject, signal } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { firstValueFrom } from "rxjs";
import { BASE_PATH } from "../../api/generated";
import { mapApiErrorMessage } from "../../shared/api-error-message";

interface Limits {
  max_input_gib: number;
  max_workspace_storage_gib: number;
  transfer_timeout_seconds: number;
}

interface Settings {
  limits: Limits;
  defaults: Limits;
  source: "deployment" | "admin";
}

@Component({
  selector: "app-preparation-settings",
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatFormFieldModule, MatInputModule],
  styleUrl: "./settings-form.scss",
  template: `
    <h2 class="card-title">Model preparation</h2>
    <p>
      Limits apply to model submission and verified task inputs. Large files are verified in a
      background job.
    </p>
    <p>
      Current source:
      {{ source() === "admin" ? "Saved admin settings" : "Deployment / Helm defaults" }}.
    </p>
    <p>Saved settings override Helm defaults. Reset to use the deployment values again.</p>
    @if (draft; as values) {
      <form #form="ngForm" (ngSubmit)="save()">
        <div class="limits-grid">
          @for (field of fields; track field.key) {
            <div>
              <label [for]="field.key">{{ field.label }}</label
              ><mat-form-field appearance="outline" subscriptSizing="dynamic">
                <input
                  [id]="field.key"
                  matInput
                  type="number"
                  step="1"
                  required
                  [min]="field.min"
                  [max]="field.max"
                  [name]="field.key"
                  [(ngModel)]="values[field.key]"
                  [disabled]="busy()"
                />
              </mat-form-field>
            </div>
          }
        </div>
        <details>
          <summary class="app-disclosure">How preparation limits apply</summary>
          <p>
            Writable workspace storage is calculated from actual task inputs plus the larger of the
            minimum extra space or percentage allowance, rounded up to GiB. The storage maximum is a
            cap, not space allocated to every task.
          </p>
          <p>
            The total input limit covers the model, evaluation dataset and baseline together. The
            transfer timeout applies to capture/verification and each file download.
          </p>
          <p>
            Allow sufficient backend temporary disk, Kubernetes node disk and writable workspace
            storage for these sizes. Increasing a limit does not add physical disk capacity.
            Lowering limits can block access to larger existing inputs.
          </p>
        </details>
        <div class="actions form-actions">
          <button
            mat-stroked-button
            type="button"
            (click)="reset()"
            [disabled]="busy() || source() !== 'admin'"
          >
            Reset to deployment defaults
          </button>
          <button
            mat-flat-button
            type="submit"
            [disabled]="busy() || form.invalid || !valid()"
            color="primary"
          >
            Save limits
          </button>
        </div>
      </form>
    }
    @if (message()) {
      <p role="status">{{ message() }}</p>
    }
  `,
})
export class PreparationSettingsComponent {
  private readonly http = inject(HttpClient);
  private readonly url =
    `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "") +
    "/api/settings/model-preparation";
  readonly busy = signal(false);
  readonly message = signal("");
  readonly source = signal<"deployment" | "admin">("deployment");
  draft: Limits | null = null;
  readonly fields: { key: keyof Limits; label: string; min: number; max: number }[] = [
    { key: "max_input_gib", label: "Maximum total task inputs (GiB)", min: 1, max: 8192 },
    {
      key: "max_workspace_storage_gib",
      label: "Maximum writable workspace storage (GiB)",
      min: 6,
      max: 16384,
    },
    {
      key: "transfer_timeout_seconds",
      label: "Transfer time limit (seconds)",
      min: 60,
      max: 604800,
    },
  ];
  constructor() {
    void this.load();
  }
  valid(): boolean {
    const d = this.draft;
    return (
      !!d &&
      this.fields.every((f) => Number.isInteger(d[f.key]) && d[f.key] >= f.min && d[f.key] <= f.max)
    );
  }
  private accept(settings: Settings): void {
    this.draft = { ...settings.limits };
    this.source.set(settings.source);
  }
  async load(): Promise<void> {
    this.busy.set(true);
    try {
      this.accept(await firstValueFrom(this.http.get<Settings>(this.url)));
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Could not load preparation settings."));
    } finally {
      this.busy.set(false);
    }
  }
  async save(): Promise<void> {
    if (this.busy() || !this.valid()) return;
    this.busy.set(true);
    try {
      this.accept(await firstValueFrom(this.http.put<Settings>(this.url, this.draft)));
      this.message.set("Preparation limits saved.");
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Could not save preparation settings."));
    } finally {
      this.busy.set(false);
    }
  }
  async reset(): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      this.accept(await firstValueFrom(this.http.delete<Settings>(this.url)));
      this.message.set("Using deployment defaults.");
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Could not reset preparation settings."));
    } finally {
      this.busy.set(false);
    }
  }
}
