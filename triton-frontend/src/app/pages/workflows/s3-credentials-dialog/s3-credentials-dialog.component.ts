import { Component, inject, signal } from "@angular/core";
import { DatePipe } from "@angular/common";
import { MatSelectModule } from "@angular/material/select";
import { HttpClient } from "@angular/common/http";
import { FormsModule } from "@angular/forms";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatDialogModule, MatDialogRef } from "@angular/material/dialog";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";

import { BASE_PATH } from "../../../api/generated/index";
import { mapApiErrorMessage } from "../../../shared/api-error-message";

type WorkflowS3CredentialDTO = {
  id: number;
  name: string;
  namespace: string;
  secret_name: string;
  artifact_repository_config_map?: string | null;
  artifact_repository_key?: string | null;
  access_key_id: string;
  created_at: string;
  updated_at: string;
  s3_profile_id?: number | null;
  s3_profile_name?: string;
  sync_error?: string;
  last_synced_at?: string | null;
};

type ProfileChoice = { id: number; name: string; endpoint: string; bucket: string; region: string };

@Component({
  selector: "app-s3-credentials-dialog",
  standalone: true,
  imports: [
    FormsModule,
    MatSelectModule,
    DatePipe,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatIconModule,
  ],
  templateUrl: "./s3-credentials-dialog.component.html",
  styleUrl: "./s3-credentials-dialog.component.scss",
})
export class S3CredentialsDialogComponent {
  private readonly dialogRef = inject(MatDialogRef<S3CredentialsDialogComponent>);
  private readonly http = inject(HttpClient);
  private readonly basePath = `${inject(BASE_PATH, { optional: true }) ?? ""}`
    .trim()
    .replace(/\/$/, "");

  readonly credentials = signal<WorkflowS3CredentialDTO[]>([]);
  readonly credentialsLoading = signal(false);
  readonly credentialsSaving = signal(false);
  readonly deletingCredentialId = signal<number | null>(null);
  readonly message = signal("");
  readonly messageIsError = signal(false);
  readonly profilesLoading = signal(true);
  readonly profiles = signal<ProfileChoice[]>([]);
  readonly syncingCredentialId = signal<number | null>(null);
  selectedProfileId: number | null = null;
  constructor() {
    void this.loadCredentials();
    void this.loadProfiles();
  }

  async refresh(): Promise<void> {
    await Promise.all([this.loadCredentials(), this.loadProfiles()]);
  }

  close(): void {
    this.dialogRef.close();
  }

  selectedProfile(): ProfileChoice | undefined {
    return this.profiles().find((p) => p.id === this.selectedProfileId);
  }

  isLinked(profileId: number): boolean {
    return this.credentials().some((c) => c.s3_profile_id === profileId);
  }

  canCreate(): boolean {
    return (
      !this.credentialsSaving() &&
      !this.credentialsLoading() &&
      !!this.selectedProfile() &&
      !this.isLinked(this.selectedProfileId!)
    );
  }

  async createCredential(): Promise<void> {
    if (!this.canCreate()) {
      this.messageIsError.set(true);
      this.message.set("Select an unlinked S3 profile.");
      return;
    }

    this.credentialsSaving.set(true);
    this.messageIsError.set(false);
    this.message.set("");
    try {
      const profile = this.selectedProfile()!;
      const payload = { name: `${profile.name} (${profile.id})`, s3_profile_id: profile.id };
      const result = await firstValueFrom(
        this.http.post<WorkflowS3CredentialDTO>(
          `${this.basePath}/api/workflows/s3-credentials`,
          payload,
        ),
      );
      this.messageIsError.set(!!result.sync_error);
      this.message.set(result.sync_error || "S3 profile linked.");
      this.resetForm();
      await this.loadCredentials();
    } catch (error) {
      this.messageIsError.set(true);
      this.message.set(mapApiErrorMessage(error, "Failed to create workflow S3 credential."));
    } finally {
      this.credentialsSaving.set(false);
    }
  }

  async deleteCredential(credential: WorkflowS3CredentialDTO): Promise<void> {
    this.deletingCredentialId.set(Number(credential.id));
    this.messageIsError.set(false);
    this.message.set("");
    try {
      await firstValueFrom(
        this.http.delete(`${this.basePath}/api/workflows/s3-credentials/${credential.id}`),
      );
      this.message.set("Workflow S3 credential deleted.");
      await this.loadCredentials();
    } catch (error) {
      this.messageIsError.set(true);
      this.message.set(mapApiErrorMessage(error, "Failed to delete workflow S3 credential."));
    } finally {
      this.deletingCredentialId.set(null);
    }
  }

  canSync(credential: WorkflowS3CredentialDTO): boolean {
    return this.profiles().some((p) => p.id === credential.s3_profile_id);
  }

  async syncCredential(credential: WorkflowS3CredentialDTO): Promise<void> {
    this.syncingCredentialId.set(credential.id);
    try {
      const result = await firstValueFrom(
        this.http.post<WorkflowS3CredentialDTO>(
          `${this.basePath}/api/workflows/s3-credentials/${credential.id}/sync`,
          {},
        ),
      );
      this.messageIsError.set(!!result.sync_error);
      this.message.set(result.sync_error || "Workflow S3 configuration synchronized.");
      await this.loadCredentials();
    } catch (error) {
      this.messageIsError.set(true);
      this.message.set(mapApiErrorMessage(error, "Failed to synchronize workflow Secret."));
    } finally {
      this.syncingCredentialId.set(null);
    }
  }

  private async loadProfiles(): Promise<void> {
    this.profilesLoading.set(true);
    try {
      this.profiles.set(
        await firstValueFrom(
          this.http.get<ProfileChoice[]>(`${this.basePath}/api/workflows/s3-profile-choices`),
        ),
      );
    } catch {
      this.messageIsError.set(true);
      this.message.set("Could not load S3 profiles. Please refresh.");
    } finally {
      this.profilesLoading.set(false);
    }
  }

  private async loadCredentials(): Promise<void> {
    this.credentialsLoading.set(true);
    try {
      const rows = await firstValueFrom(
        this.http.get<WorkflowS3CredentialDTO[]>(`${this.basePath}/api/workflows/s3-credentials`),
      );
      this.credentials.set(rows || []);
    } catch (error) {
      this.credentials.set([]);
      this.message.set(mapApiErrorMessage(error, "Failed to load workflow S3 credentials."));
    } finally {
      this.credentialsLoading.set(false);
    }
  }

  private resetForm(): void {
    this.selectedProfileId = null;
  }
}
