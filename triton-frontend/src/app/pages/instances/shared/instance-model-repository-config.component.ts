import { Component, computed, inject, input, signal } from "@angular/core";
import { firstValueFrom } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatIconModule } from "@angular/material/icon";

import { InstancesService, S3FileContentResponse } from "../../../api/generated/index";
import { AuthStore } from "../../../shared/auth/auth.store";
import { mapApiErrorMessage } from "../../../shared/api-error-message";
import { InstanceModelMonacoEditorComponent } from "../infer/instance-model-monaco-editor.component";

type InstanceS3ConfigLike = {
  enabled?: unknown;
  endpoint?: unknown;
  bucket?: unknown;
  prefix?: unknown;
};

@Component({
  selector: "app-instance-model-repository-config",
  standalone: true,
  imports: [MatButtonModule, MatIconModule, InstanceModelMonacoEditorComponent],
  templateUrl: "./instance-model-repository-config.component.html",
  styleUrl: "./instance-model-repository-config.component.scss",
})
export class InstanceModelRepositoryConfigComponent {
  private readonly instancesApi = inject(InstancesService);
  private readonly auth = inject(AuthStore);

  readonly instanceId = input("");
  readonly modelName = input("");
  readonly version = input("");
  readonly s3 = input<InstanceS3ConfigLike | null>(null);

  readonly open = signal(false);
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly content = signal("");
  readonly error = signal("");
  readonly savedMessage = signal("");
  readonly loadedConfigPath = signal("");
  readonly canWriteInstances = this.auth.canWriteInstances;
  readonly title = computed(() => (this.hasS3Config() ? "config.pbtxt" : "Live API Config"));
  readonly editorLanguage = computed(() => (this.hasS3Config() ? "proto" : "json"));
  readonly closedHint = computed(() =>
    this.hasS3Config()
      ? "Open the model repository config beside this workflow."
      : "Open the live Triton model API config beside this workflow.",
  );

  readonly relativePath = computed(() => {
    const loadedPath = this.loadedConfigPath();
    if (loadedPath) {
      return loadedPath;
    }
    return this.configPathCandidates()[0] ?? "";
  });
  readonly configPathCandidates = computed(() => {
    const modelName = this.modelName()
      .replace(/^\/+|\/+$/g, "")
      .trim();
    if (!modelName) {
      return [];
    }

    const modelConfigPath = `${modelName}/config.pbtxt`;
    if (this.pathEndsWithSegment(this.s3Text("prefix"), modelName)) {
      return ["config.pbtxt", modelConfigPath];
    }
    return [modelConfigPath, "config.pbtxt"];
  });
  readonly effectivePath = computed(() => {
    if (!this.hasS3Config()) {
      return "";
    }
    const relative = this.relativePath();
    if (!relative) {
      return "";
    }
    const prefix = this.s3Text("prefix").replace(/^\/+|\/+$/g, "");
    return this.joinDisplayPath(prefix, relative);
  });
  readonly hasS3Config = computed(
    () =>
      this.s3Boolean("enabled") &&
      this.s3Text("endpoint").length > 0 &&
      this.s3Text("bucket").length > 0 &&
      this.relativePath().length > 0,
  );
  readonly canLoadApiConfig = computed(
    () =>
      !!this.s3() &&
      !this.hasS3Config() &&
      /^[0-9]+$/.test(this.instanceId().trim()) &&
      this.modelName().trim().length > 0 &&
      this.version().trim().length > 0,
  );
  readonly canLoadConfig = computed(() => this.hasS3Config() || this.canLoadApiConfig());

  async toggleConfig(): Promise<void> {
    if (!this.canLoadConfig()) {
      return;
    }
    if (this.open()) {
      this.open.set(false);
      return;
    }

    this.open.set(true);
    if (!this.content() && !this.loading()) {
      await this.loadConfig();
    }
  }

  async reloadConfig(): Promise<void> {
    if (!this.canLoadConfig() || this.loading()) {
      return;
    }
    await this.loadConfig();
  }

  async saveConfig(): Promise<void> {
    if (!this.hasS3Config() || !this.canWriteInstances() || this.saving()) {
      return;
    }

    this.saving.set(true);
    this.error.set("");
    this.savedMessage.set("");
    try {
      await firstValueFrom(
        this.instancesApi.putInstanceS3ContentApiInstancesInstanceIdS3ContentPut(
          this.content(),
          this.relativePath(),
          this.instanceId(),
          "text/plain; charset=utf-8",
        ),
      );
      this.savedMessage.set("Saved config.pbtxt.");
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Failed to save config.pbtxt."));
    } finally {
      this.saving.set(false);
    }
  }

  private async loadConfig(): Promise<void> {
    this.loading.set(true);
    this.error.set("");
    this.savedMessage.set("");
    try {
      if (this.hasS3Config()) {
        const response = await this.loadS3ConfigContent();
        this.content.set(response?.content ?? "");
      } else {
        const config = await firstValueFrom(
          this.instancesApi.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet(
            this.instanceId(),
            this.modelName(),
            this.version(),
          ),
        );
        this.content.set(JSON.stringify(config, null, 2));
      }
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, `Failed to load ${this.title()}.`));
      this.content.set("");
    } finally {
      this.loading.set(false);
    }
  }

  private async loadS3ConfigContent(): Promise<S3FileContentResponse> {
    const candidates = this.configPathCandidates();
    let lastError: unknown = null;
    for (const path of candidates) {
      try {
        const response = (await firstValueFrom(
          this.instancesApi.getInstanceS3ContentApiInstancesInstanceIdS3ContentGet(
            this.instanceId(),
            path,
          ),
        )) as S3FileContentResponse;
        this.loadedConfigPath.set(path);
        return response;
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError ?? new Error("config.pbtxt not found");
  }

  private s3Text(key: keyof InstanceS3ConfigLike): string {
    const value = this.s3()?.[key];
    return typeof value === "string" ? value.trim() : "";
  }

  private s3Boolean(key: keyof InstanceS3ConfigLike): boolean {
    return this.s3()?.[key] === true;
  }

  private joinDisplayPath(prefix: string, relative: string): string {
    const cleanPrefix = prefix.replace(/^\/+|\/+$/g, "");
    const cleanRelative = relative.replace(/^\/+|\/+$/g, "");
    if (!cleanPrefix) {
      return cleanRelative;
    }
    const firstRelativeSegment = cleanRelative.split("/").filter(Boolean)[0] ?? "";
    if (firstRelativeSegment && this.pathEndsWithSegment(cleanPrefix, firstRelativeSegment)) {
      const rest = cleanRelative.split("/").filter(Boolean).slice(1).join("/");
      return rest ? `${cleanPrefix}/${rest}` : cleanPrefix;
    }
    return cleanRelative ? `${cleanPrefix}/${cleanRelative}` : cleanPrefix;
  }

  private pathEndsWithSegment(path: string, segment: string): boolean {
    const last = path
      .replace(/^\/+|\/+$/g, "")
      .split("/")
      .filter(Boolean)
      .pop();
    return this.normalizePathSegment(last) === this.normalizePathSegment(segment);
  }

  private normalizePathSegment(value: string | undefined): string {
    try {
      return decodeURIComponent(value ?? "")
        .trim()
        .toLowerCase();
    } catch {
      return (value ?? "").trim().toLowerCase();
    }
  }
}
