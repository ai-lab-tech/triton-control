import { InstallationService } from "../../shared/installation/installation.service";
import { S3ProfileLabelComponent } from "../../shared/s3-profile-label.component";
import { Component, inject, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { HttpClient } from "@angular/common/http";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";
import { MatMenuModule } from "@angular/material/menu";

import { BASE_PATH } from "../../api/generated/index";
import { AuthStore } from "../../shared/auth/auth.store";
import { mapApiErrorMessage } from "../../shared/api-error-message";

type S3Profile = {
  used_by_mlflow?: boolean;
  id: number;
  name: string;
  endpoint: string;
  bucket: string;
  region: string;
  access_key: string;
  secret_key: string;
  prefix: string;
  force_path_style: boolean;
  ca_certificate: string;
};

@Component({
  selector: "app-s3-profiles-page",
  standalone: true,
  imports: [
    S3ProfileLabelComponent,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatMenuModule,
  ],
  styleUrl: "./s3-profiles-page.component.scss",
  templateUrl: "./s3-profiles-page.component.html",
})
export class S3ProfilesPageComponent {
  readonly installation = inject(InstallationService);
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthStore);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");

  readonly profiles = signal<S3Profile[]>([]);
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly message = signal("");
  readonly messageTone = signal<"success" | "error" | "info">("info");
  selectedId = "";

  creating = false;
  private previousId = "";

  draft = this.emptyDraft();

  constructor() {
    void this.loadProfiles();
  }

  canManage(): boolean {
    return this.auth.isAdmin() && this.installation.allows("credential_admin");
  }

  usesHttpsS3(): boolean {
    return this.isHttpsEndpoint(this.draft.endpoint);
  }

  async loadProfiles(): Promise<void> {
    if (!this.auth.canWriteInstances()) return;
    this.loading.set(true);
    try {
      const profiles = await firstValueFrom(
        this.http.get<S3Profile[]>(this.apiUrl("/api/s3-profiles")),
      );
      this.profiles.set(profiles);
      if (profiles.length && !this.selectedId) {
        this.selectProfile(String(profiles[0].id));
      }
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to load S3 profiles."), "error");
    } finally {
      this.loading.set(false);
    }
  }

  selectProfile(id: string): void {
    this.creating = false;
    this.selectedId = id;
    const profile = this.profiles().find((item) => String(item.id) === id);
    this.draft = profile ? { ...profile } : this.emptyDraft();
  }

  newProfile(): void {
    if (!this.installation.allows("trust_admin") || this.creating || !this.canLeave()) return;
    this.previousId = this.selectedId;
    this.creating = true;
    this.message.set("");
    this.selectedId = "";
    this.draft = this.emptyDraft();
  }

  canLeave(): boolean {
    const original =
      this.profiles().find((p) => String(p.id) === this.selectedId) ?? this.emptyDraft();
    return (
      !this.saving() &&
      (JSON.stringify(original) === JSON.stringify(this.draft) ||
        window.confirm("Discard unsaved profile changes?"))
    );
  }

  openProfile(id: string): void {
    if (id !== this.selectedId && this.canLeave()) {
      this.message.set("");
      this.selectProfile(id);
    }
  }

  cancelNew(): void {
    if (this.canLeave()) {
      this.selectProfile(this.previousId);
      this.message.set("");
    }
  }

  confirmDelete(): void {
    const profile = this.profiles().find((item) => String(item.id) === this.selectedId);
    if (profile && window.confirm('Delete S3 profile "' + profile.name + '"?'))
      void this.deleteProfile();
  }

  async saveProfile(): Promise<void> {
    if (
      !this.canManage() ||
      this.saving() ||
      (!this.selectedId && !this.installation.allows("trust_admin"))
    )
      return;
    this.saving.set(true);
    const payload = {
      name: this.draft.name.trim(),
      endpoint: this.draft.endpoint.trim(),
      bucket: this.draft.bucket.trim(),
      region: this.draft.region.trim() || "us-east-1",
      access_key: this.draft.access_key.trim(),
      secret_key: this.draft.secret_key,
      prefix: this.draft.prefix,
      force_path_style: this.draft.force_path_style,
      ca_certificate: this.usesHttpsS3() ? this.draft.ca_certificate.trim() : "",
    };
    try {
      const saved = this.selectedId
        ? await firstValueFrom(
            this.http.put<S3Profile>(
              this.apiUrl(`/api/s3-profiles/${this.selectedId}`),
              this.installation.snapshot()?.mode === "production"
                ? { access_key: payload.access_key, secret_key: payload.secret_key }
                : payload,
            ),
          )
        : await firstValueFrom(this.http.post<S3Profile>(this.apiUrl("/api/s3-profiles"), payload));
      this.setMessage("S3 profile saved.", "success");
      this.profiles.update((items) =>
        items.some((item) => item.id === saved.id)
          ? items.map((item) => (item.id === saved.id ? saved : item))
          : [...items, saved],
      );
      this.selectProfile(String(saved.id));
      await this.loadProfiles();
      this.selectProfile(String(saved.id));
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to save S3 profile."), "error");
    } finally {
      this.saving.set(false);
    }
  }

  async deleteProfile(): Promise<void> {
    if (!this.canManage() || !this.selectedId || this.saving()) return;
    this.saving.set(true);
    try {
      await firstValueFrom(this.http.delete(this.apiUrl(`/api/s3-profiles/${this.selectedId}`)));
      this.setMessage("S3 profile deleted.", "success");
      this.profiles.update((items) => items.filter((item) => String(item.id) !== this.selectedId));
      this.selectProfile("");
      await this.loadProfiles();
    } catch (error) {
      this.setMessage(mapApiErrorMessage(error, "Failed to delete S3 profile."), "error");
    } finally {
      this.saving.set(false);
    }
  }

  private emptyDraft(): S3Profile {
    return {
      id: 0,
      name: "",
      endpoint: "",
      bucket: "",
      region: "us-east-1",
      access_key: "",
      secret_key: "",
      prefix: "",
      force_path_style: true,
      ca_certificate: "",
    };
  }

  private setMessage(message: string, tone: "success" | "error" | "info"): void {
    this.message.set(message);
    this.messageTone.set(tone);
  }

  private apiUrl(path: string): string {
    return `${this.basePath}${path}`;
  }

  private isHttpsEndpoint(value: string): boolean {
    return value.trim().toLowerCase().startsWith("https://");
  }
}
