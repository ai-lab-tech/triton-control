import { HttpClient } from "@angular/common/http";
import { Component, inject, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { firstValueFrom } from "rxjs";
import { BASE_PATH } from "../../api/generated";
import { mapApiErrorMessage } from "../../shared/api-error-message";

interface Destination {
  reuse_pull_credentials?: boolean;
  dockerconfigjson?: string;
  namespace: string;
  allowed_repositories: string[];
}

interface Settings {
  builder: { executor_image: string; release: string };
  destination: Destination | null;
  status: string;
  has_override: boolean;
  push_credentials_saved: boolean;
  reuse_pull_credentials: boolean;
}

@Component({
  selector: "app-build-settings",
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatFormFieldModule, MatInputModule],
  styleUrl: "./settings-form.scss",
  template: `
    <h2 class="card-title">Push images from builds</h2>
    <p>
      Choose where to publish images built in code-server. Leave credentials empty for anonymous
      access.
    </p>
    @if (settings(); as current) {
      <p>{{ current.status }}</p>
      <form #form="ngForm" (ngSubmit)="save()">
        <div class="limits-grid">
          <mat-form-field appearance="outline" subscriptSizing="dynamic">
            <mat-label>Destination repositories (optional)</mat-label>
            <textarea
              matInput
              name="repositories"
              rows="2"
              placeholder="ghcr.io/my-team/models"
              [(ngModel)]="repositories"
              [disabled]="busy()"
            ></textarea>
            <mat-hint
              >Leave blank to save image.tar in the cluster. To push images, enter one repository per line.</mat-hint
            >
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Build namespace</mat-label>
            <input matInput required name="namespace" [(ngModel)]="namespace" [disabled]="busy()" />
          </mat-form-field>
        </div>
        <details>
          <summary class="app-disclosure">Push credentials (optional)</summary>
          <label>
            <input type="checkbox" name="reusePull" [(ngModel)]="reusePull" [disabled]="busy()" />
            Use the saved pull credentials for pushing
          </label>
          @if (reusePull) {
            <p>The registry account must have write access to the destination.</p>
          } @else {
            <mat-form-field appearance="outline" subscriptSizing="dynamic">
              <mat-label>Push credentials (Docker config JSON)</mat-label>
              <textarea
                matInput
                rows="3"
                name="pushCredentials"
                [(ngModel)]="pushCredentials"
                autocomplete="off"
                spellcheck="false"
                [disabled]="busy()"
              ></textarea>
              <mat-hint>
                @if (current.push_credentials_saved) {
                  Push credentials saved. Paste new JSON to replace them.
                } @else {
                  For a private registry, paste Docker config JSON with write access. Triton Control
                  creates the build secret.
                }
              </mat-hint>
            </mat-form-field>
          }
        </details>
        @if (current.builder; as builder) {
          <details>
            <summary class="app-disclosure">Advanced</summary>
            <p>
              Build executor:
              <strong>{{ builder.executor_image.split("@")[0] }} {{ builder.release }}</strong>
            </p>
            <p>
              Image digest: <code>{{ builder.executor_image.split("@")[1] }}</code>
            </p>
          </details>
        }
        <div class="actions form-actions">
          @if (current.push_credentials_saved && !reusePull) {
            <button
              mat-stroked-button
              type="button"
              [disabled]="busy() || form.invalid"
              (click)="save(true)"
            >
              Remove saved push credentials
            </button>
          }
          @if (current.has_override) {
            <button mat-stroked-button type="button" [disabled]="busy()" (click)="reset()">
              Use installation defaults
            </button>
          }
          <button mat-flat-button type="submit" [disabled]="busy() || form.invalid">
            Save destination
          </button>
        </div>
      </form>
    } @else if (!busy()) {
      <button mat-stroked-button (click)="load()">Retry loading destination</button>
    }
    @if (message()) {
      <p role="status">{{ message() }}</p>
    }
  `,
})
export class BuildSettingsComponent {
  private readonly http = inject(HttpClient);
  private readonly url =
    (inject(BASE_PATH, { optional: true }) ?? "").replace(/\/$/, "") +
    "/api/settings/serving-image-builds/destination";
  readonly settings = signal<Settings | null>(null);
  readonly busy = signal(false);
  readonly message = signal("");
  namespace = "triton-builds";
  repositories = "";
  pushCredentials = "";
  reusePull = false;

  constructor() {
    void this.load();
  }

  load(): Promise<void> {
    return this.request("GET");
  }
  reset(): Promise<void> {
    return this.request("DELETE");
  }
  save(removeCredentials = false): Promise<void> {
    return this.request("PUT", {
      namespace: this.namespace.trim(),
      reuse_pull_credentials: this.reusePull,
      dockerconfigjson: this.reusePull
        ? undefined
        : removeCredentials
          ? ""
          : this.pushCredentials.trim() || undefined,
      allowed_repositories: this.repositories
        .split("\n")
        .map((value) => value.trim())
        .filter(Boolean),
    });
  }

  private async request(method: "GET" | "PUT" | "DELETE", body?: Destination): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    this.message.set("");
    try {
      const result = await firstValueFrom(this.http.request<Settings>(method, this.url, { body }));
      this.settings.set(result);
      this.reusePull = result.reuse_pull_credentials ?? false;
      this.pushCredentials = "";
      this.namespace = result.destination?.namespace ?? "triton-builds";
      this.repositories = result.destination?.allowed_repositories.join("\n") ?? "";
      if (method === "PUT") this.message.set("Build destination saved.");
      if (method === "DELETE") this.message.set("Using installation defaults.");
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Could not access the build destination."));
    } finally {
      this.busy.set(false);
    }
  }
}
