import { Component, inject, signal } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { firstValueFrom } from "rxjs";
import { BASE_PATH } from "../../api/generated";
import { mapApiErrorMessage } from "../../shared/api-error-message";

@Component({
  selector: "app-registry-settings",
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatFormFieldModule, MatInputModule],
  template: `
    <h2>Image pull secret</h2>
    <p>
      Used for new deployments, development workspaces, MLflow installations and profiling jobs.
    </p>
    <p>
      {{ configured() ? "A registry secret is configured." : "No registry secret configured." }}
    </p>
    <mat-form-field appearance="outline" style="width:100%">
      <mat-label>Docker config JSON</mat-label>
      <textarea
        matInput
        rows="8"
        [(ngModel)]="draft"
        autocomplete="off"
        spellcheck="false"
      ></textarea>
      <mat-hint>Paste a new .dockerconfigjson to replace the stored credentials.</mat-hint>
    </mat-form-field>
    <button mat-flat-button (click)="save(false)" [disabled]="busy() || !draft.trim()">
      Save secret
    </button>
    <button mat-stroked-button (click)="save(true)" [disabled]="busy() || !configured()">
      Remove secret
    </button>
    <p role="status">{{ message() }}</p>
  `,
})
export class RegistrySettingsComponent {
  private readonly http = inject(HttpClient);
  private readonly url =
    `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "") + "/api/settings/registry";
  readonly configured = signal(false);
  readonly busy = signal(false);
  readonly message = signal("");
  draft = "";
  constructor() {
    void this.load();
  }
  private async load(): Promise<void> {
    this.busy.set(true);
    try {
      const result = await firstValueFrom(this.http.get<{ configured: boolean }>(this.url));
      this.configured.set(result.configured);
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Failed to load registry settings."));
    } finally {
      this.busy.set(false);
    }
  }
  async save(remove: boolean): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      const result = await firstValueFrom(
        this.http.put<{ configured: boolean }>(this.url, {
          dockerconfigjson: remove ? "" : this.draft,
        }),
      );
      this.configured.set(result.configured);
      this.draft = "";
      this.message.set("Registry settings saved. Existing workloads are unchanged.");
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Failed to save registry settings."));
    } finally {
      this.busy.set(false);
    }
  }
}
