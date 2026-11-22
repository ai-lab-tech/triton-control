import { HttpClient } from "@angular/common/http";
import { Component, inject, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { firstValueFrom } from "rxjs";
import { BASE_PATH } from "../../api/generated";
import { mapApiErrorMessage } from "../../shared/api-error-message";

const textFields = {
  namespace: "Dedicated build namespace",
  staging_prefix: "Staging prefix (ending in /)",
  executor_image: "Executor image (pinned by digest)",
  release: "Executor release",
  provisioning_revision: "Provisioning revision",
  base_pull_secret: "Base image pull secret name",
  push_secret: "Build push secret name",
  runtime_pull_secret: "Runtime pull secret name",
} as const;

interface Settings {
  source: "admin" | "deployment" | "unconfigured";
  policy: Record<string, unknown> | null;
}

interface Profile {
  name: string;
  bucket: string;
  prefix: string;
}

@Component({
  selector: "app-build-settings",
  standalone: true,
  imports: [FormsModule, MatButtonModule],
  styleUrl: "./settings-form.scss",
  template: `
    <h2>Serving image builds</h2>
    <p>
      Configure the Kubernetes builder for task workspaces. Provision the build namespace, network
      policy and registry secrets before enabling builds.
    </p>
    @if (loaded()) {
      <p>Policy source: {{ source() }}</p>
      <form #form="ngForm" (ngSubmit)="save()">
        <fieldset [disabled]="busy()">
          <label for="build-profile">Build input storage profile</label>
          <select required id="build-profile" name="profile" [(ngModel)]="profile">
            <option value="">Choose a versioned S3 or MinIO profile</option>
            @for (item of profiles; track $index) {
              <option [value]="item.name">
                {{ item.name }} — {{ item.bucket }}/{{ item.prefix }}
              </option>
            }
          </select>
          @for (field of fields; track field) {
            <label [for]="'build-' + field">{{ labels[field] }}</label>
            <input required [id]="'build-' + field" [name]="field" [(ngModel)]="values[field]" />
          }
          <label for="build-repos">Allowed registry repositories (one per line)</label>
          <textarea required id="build-repos" name="repos" [(ngModel)]="repositories"></textarea>
          <label for="build-networks">Allowed egress CIDRs (one per line)</label>
          <textarea required id="build-networks" name="networks" [(ngModel)]="networks"></textarea>
          <label for="build-advanced">Executor limits, platform and network ports (JSON)</label>
          <textarea
            required
            id="build-advanced"
            rows="6"
            name="advanced"
            [(ngModel)]="advanced"
          ></textarea>
          <label
            ><input type="checkbox" name="enabled" [(ngModel)]="enabled" /> Enable qualified
            builds</label
          >
        </fieldset>
        <button mat-flat-button type="submit" [disabled]="busy() || form.invalid">
          Save policy
        </button>
        <button
          mat-stroked-button
          type="button"
          [disabled]="busy() || source() !== 'admin'"
          (click)="reset()"
        >
          Reset to installation defaults
        </button>
      </form>
    } @else if (!busy()) {
      <button mat-stroked-button (click)="load()">Retry loading settings</button>
    }
    @if (message()) {
      <p role="status">{{ message() }}</p>
    }
  `,
  styles: [
    `
      fieldset {
        display: grid;
        gap: 0.5rem;
        border: 0;
        padding: 0;
        margin-bottom: 1rem;
      }
      input,
      textarea,
      select {
        font: inherit;
      }
      label {
        margin-top: 0.5rem;
      }
    `,
  ],
})
export class BuildSettingsComponent {
  private readonly http = inject(HttpClient);
  private readonly base = (inject(BASE_PATH, { optional: true }) ?? "").replace(/\/$/, "");
  private readonly url = this.base + "/api/settings/serving-image-builds";
  readonly busy = signal(false);
  readonly loaded = signal(false);
  readonly message = signal("");
  readonly source = signal<Settings["source"]>("unconfigured");
  readonly labels = textFields;
  readonly fields = Object.keys(textFields) as (keyof typeof textFields)[];
  values: Record<string, string> = {};
  profile = "";
  profiles: Profile[] = [];
  repositories = "";
  networks = "";
  advanced = "{}";
  enabled = false;

  constructor() {
    void this.load();
  }
  private accept(settings: Settings): void {
    const policy = { ...settings.policy };
    this.source.set(settings.source);
    for (const key of this.fields) {
      this.values[key] = String(policy[key] ?? "");
      delete policy[key];
    }
    this.profile = String(policy["s3_profile_name"] ?? "");
    this.enabled = policy["enabled"] === true;
    this.repositories = (policy["allowed_repositories"] as string[] | undefined)?.join("\n") ?? "";
    this.networks = (policy["egress_cidrs"] as string[] | undefined)?.join("\n") ?? "";
    for (const key of ["s3_profile_name", "enabled", "allowed_repositories", "egress_cidrs"])
      delete policy[key];
    this.advanced = JSON.stringify(
      { platform: "linux/amd64", egress_ports: [443], ...policy },
      null,
      2,
    );
  }
  async load(): Promise<void> {
    this.busy.set(true);
    this.message.set("");
    try {
      const [settings, profiles] = await Promise.all([
        firstValueFrom(this.http.get<Settings>(this.url)),
        firstValueFrom(this.http.get<Profile[]>(this.base + "/api/s3-profiles")),
      ]);
      this.profiles = profiles;
      this.accept(settings);
      this.loaded.set(true);
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Could not load build settings."));
    } finally {
      this.busy.set(false);
    }
  }
  async save(): Promise<void> {
    let options: unknown;
    try {
      options = JSON.parse(this.advanced);
    } catch {
      options = null;
    }
    if (!options || typeof options !== "object" || Array.isArray(options)) {
      this.message.set("Enter a JSON object for the executor options.");
      return;
    }
    const lines = (value: string) =>
      value
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    await this.write({
      ...options,
      ...this.values,
      s3_profile_name: this.profile,
      enabled: this.enabled,
      allowed_repositories: lines(this.repositories),
      egress_cidrs: lines(this.networks),
    });
  }
  async reset(): Promise<void> {
    await this.write(null);
  }
  private async write(policy: Record<string, unknown> | null): Promise<void> {
    if (this.busy() || !this.loaded()) return;
    this.busy.set(true);
    try {
      this.accept(
        await firstValueFrom(
          policy ? this.http.put<Settings>(this.url, policy) : this.http.delete<Settings>(this.url),
        ),
      );
      this.message.set(policy ? "Build policy saved." : "Using installation defaults.");
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, "Could not update the build policy."));
    } finally {
      this.busy.set(false);
    }
  }
}
