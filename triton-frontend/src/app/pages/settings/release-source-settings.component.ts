import { Component, computed, inject, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatCheckboxModule } from "@angular/material/checkbox";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { firstValueFrom } from "rxjs";
import { ReleaseSourcesService } from "../../api/generated";
import { mapApiErrorMessage } from "../../shared/api-error-message";
import { InstallationService } from "../../shared/installation/installation.service";

type Location =
  | { type: "git"; repository: string; ref: string; path: string }
  | { type: "s3"; bucket: string; key: string };
type ApprovalMode = "manual" | "independently-approved";

interface Registration {
  source: {
    metadata: { name: string };
    spec: {
      location: Location;
      readCredential: string;
      allowedTargets: string[];
      intervalSeconds: number;
      approval: { mode: ApprovalMode };
      enabled: boolean;
    };
  };
  provenance: "database" | "environment" | "file";
  revision: number;
  editable: boolean;
}

class SourceDraft {
  name = "";
  type: Location["type"] = "git";
  repository = "";
  ref = "refs/heads/main";
  bucket = "";
  path = "";
  binding = "";
  targets = "";
  interval = 86400;
  approval: ApprovalMode = "manual";
  enabled = false;

  constructor(readonly original?: Registration) {
    if (!original) return;

    const { metadata, spec } = original.source;
    this.name = metadata.name;
    this.type = spec.location.type;
    this.binding = spec.readCredential;
    this.targets = spec.allowedTargets.join(", ");
    this.interval = spec.intervalSeconds;
    this.approval = spec.approval.mode;
    this.enabled = spec.enabled;
    if (spec.location.type === "git") {
      this.repository = spec.location.repository;
      this.ref = spec.location.ref;
      this.path = spec.location.path;
    } else {
      this.bucket = spec.location.bucket;
      this.path = spec.location.key;
    }
  }

  document() {
    const path = this.path.trim();
    const location: Location =
      this.type === "git"
        ? { type: "git", repository: this.repository.trim(), ref: this.ref.trim(), path }
        : { type: "s3", bucket: this.bucket.trim(), key: path };
    return {
      apiVersion: "serving.triton-control/v1alpha1",
      kind: "MetadataSource",
      metadata: { name: this.name.trim() },
      spec: {
        location,
        readCredential: this.binding.trim(),
        allowedTargets: this.targets
          .split(",")
          .map((target) => target.trim())
          .filter(Boolean),
        intervalSeconds: this.interval,
        approval: { mode: this.approval },
        enabled: this.enabled,
      },
    };
  }
}

@Component({
  selector: "app-release-source-settings",
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  styleUrl: "./settings-form.scss",
  templateUrl: "./release-source-settings.component.html",
})
export class ReleaseSourceSettingsComponent {
  private readonly api = inject(ReleaseSourcesService);
  private readonly installation = inject(InstallationService);
  readonly canWrite = computed(() => this.installation.allows("trust_admin"));
  readonly sources = signal<Registration[] | null>(null);
  readonly busy = signal(false);
  readonly message = signal("");
  readonly draft = signal<SourceDraft | null>(null);
  readonly removal = signal<Registration | null>(null);
  readonly origins = {
    database: "Admin settings",
    environment: "Helm / environment JSON",
    file: "Mounted configuration file",
  };

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    await this.run(async () => {
      this.sources.set(await firstValueFrom(this.api.listReleaseSourcesApiReleaseSourcesGet()));
      this.draft.set(null);
      this.removal.set(null);
    }, "Could not load release sources.");
  }

  edit(source?: Registration): void {
    if (!this.canWrite() || this.busy() || (source && !source.editable)) return;
    this.removal.set(null);
    this.draft.set(new SourceDraft(source));
  }

  async save(draft: SourceDraft): Promise<void> {
    if (!this.canWrite() || this.draft() !== draft) return;
    await this.run(async () => {
      const document = draft.document();
      const original = draft.original;
      const request = original
        ? this.api.updateReleaseSourceApiReleaseSourcesNamePut(
            { source: document, expected_revision: original.revision },
            original.source.metadata.name,
          )
        : this.api.createReleaseSourceApiReleaseSourcesPost(document);
      const saved = (await firstValueFrom(request)) as Registration;
      this.sources.update((sources) =>
        [
          ...(sources ?? []).filter(
            (item) => item.source.metadata.name !== saved.source.metadata.name,
          ),
          saved,
        ].sort((left, right) =>
          left.source.metadata.name.localeCompare(right.source.metadata.name),
        ),
      );
      this.draft.set(null);
      this.message.set("Release source saved.");
    }, "Could not save the release source.");
  }

  requestRemoval(source: Registration): void {
    if (!this.canWrite() || !source.editable || this.busy()) return;
    this.draft.set(null);
    this.removal.set(source);
  }

  async remove(source: Registration): Promise<void> {
    if (!this.canWrite() || !source.editable || this.removal() !== source) return;
    await this.run(async () => {
      const name = source.source.metadata.name;
      await firstValueFrom(
        this.api.deleteReleaseSourceApiReleaseSourcesNameDelete(name, source.revision),
      );
      this.sources.update((sources) =>
        (sources ?? []).filter((item) => item.source.metadata.name !== name),
      );
      this.removal.set(null);
      this.message.set("Release source removed.");
    }, "Could not remove the release source.");
  }

  private async run(action: () => Promise<void>, fallback: string): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    this.message.set("");
    try {
      await action();
    } catch (error) {
      this.message.set(mapApiErrorMessage(error, fallback));
    } finally {
      this.busy.set(false);
    }
  }
}
