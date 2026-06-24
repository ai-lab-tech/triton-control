import { Component, inject, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { HttpClient } from "@angular/common/http";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";

import { BASE_PATH } from "../../api/generated/index";
import { AuthStore } from "../../shared/auth/auth.store";
import { mapApiErrorMessage } from "../../shared/api-error-message";

type S3Profile = {
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
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatSelectModule,
  ],
  styleUrl: "./s3-profiles-page.component.scss",
  templateUrl: "./s3-profiles-page.component.html",
})
export class S3ProfilesPageComponent {
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

  draft = this.emptyDraft();

  constructor() {
    void this.loadProfiles();
  }

  canManage(): boolean {
    return this.auth.canWriteInstances();
  }

  usesHttpsS3(): boolean {
    return this.isHttpsEndpoint(this.draft.endpoint);
  }

  async loadProfiles(): Promise<void> {
    if (!this.canManage()) return;
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
    this.selectedId = id;
    const profile = this.profiles().find((item) => String(item.id) === id);
    this.draft = profile ? { ...profile } : this.emptyDraft();
  }

  newProfile(): void {
    this.selectedId = "";
    this.draft = this.emptyDraft();
  }

  async saveProfile(): Promise<void> {
    if (!this.canManage() || this.saving()) return;
    this.saving.set(true);
    const payload = {
      name: this.draft.name.trim(),
      endpoint: this.draft.endpoint.trim(),
      bucket: this.draft.bucket.trim(),
      region: this.draft.region.trim() || "us-east-1",
      access_key: this.draft.access_key.trim(),
      secret_key: this.draft.secret_key,
      prefix: "",
      force_path_style: this.draft.force_path_style,
      ca_certificate: this.usesHttpsS3() ? this.draft.ca_certificate.trim() : "",
    };
    try {
      const saved = this.selectedId
        ? await firstValueFrom(
            this.http.put<S3Profile>(this.apiUrl(`/api/s3-profiles/${this.selectedId}`), payload),
          )
        : await firstValueFrom(this.http.post<S3Profile>(this.apiUrl("/api/s3-profiles"), payload));
      this.setMessage("S3 profile saved.", "success");
      this.selectedId = String(saved.id);
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
      this.newProfile();
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
