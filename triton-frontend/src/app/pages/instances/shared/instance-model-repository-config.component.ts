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
  template: `
    <aside class="repository-config-pane">
      <div class="editor-head">
        <div>
          <div class="section-title">{{ title() }}</div>
          @if (effectivePath()) {
            <code class="config-path">{{ effectivePath() }}</code>
          }
        </div>
        <button
          mat-stroked-button
          type="button"
          class="config-btn"
          (click)="toggleConfig()"
          [disabled]="loading() || !canLoadConfig()"
        >
          <span class="material-icons">
            {{ open() ? "expand_less" : "article" }}
          </span>
          {{ open() ? "Hide Config" : "Show Config" }}
        </button>
      </div>

      @if (!s3()) {
        <div class="panel-hint">Loading instance repository settings...</div>
      } @else if (open()) {
        @if (loading()) {
          <div class="panel-hint">Loading {{ title() }}...</div>
        } @else {
          @if (error()) {
            <div class="panel-hint health-error">{{ error() }}</div>
          }
          <app-instance-model-monaco-editor
            class="repository-config-editor"
            [value]="content()"
            (valueChange)="content.set($event)"
            [language]="editorLanguage()"
            [readOnly]="saving() || !hasS3Config()"
          ></app-instance-model-monaco-editor>
          @if (hasS3Config()) {
            <div class="config-actions">
              <button
                mat-stroked-button
                type="button"
                (click)="reloadConfig()"
                [disabled]="loading() || saving()"
              >
                <span class="material-icons">sync</span>
                Reload
              </button>
              <button
                mat-flat-button
                color="primary"
                type="button"
                (click)="saveConfig()"
                [disabled]="saving() || loading() || !canWriteInstances()"
              >
                <span class="material-icons">save</span>
                {{ saving() ? "Saving..." : "Save" }}
              </button>
            </div>
          } @else {
            <button
              mat-stroked-button
              type="button"
              (click)="reloadConfig()"
              [disabled]="loading() || saving()"
            >
              <span class="material-icons">sync</span>
              Reload
            </button>
          }
          @if (hasS3Config() && !canWriteInstances()) {
            <div class="panel-hint">Your role can view this config but cannot save changes.</div>
          }
          @if (!hasS3Config()) {
            <div class="panel-hint">
              Read-only Triton runtime config. Raw config.pbtxt editing requires S3 access.
            </div>
          }
          @if (savedMessage()) {
            <div class="panel-hint save-message">{{ savedMessage() }}</div>
          }
        }
      } @else {
        <div class="panel-hint">{{ closedHint() }}</div>
      }
    </aside>
  `,
  styles: [
    `
      .repository-config-pane {
        display: grid;
        gap: 0.55rem;
        min-width: 0;
      }

      .editor-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        flex-wrap: wrap;
      }

      .section-title {
        font-size: 0.8rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #64748b;
      }

      .config-path {
        display: block;
        margin-top: 0.25rem;
        color: #475569;
        font-size: 0.78rem;
        word-break: break-all;
      }

      .config-btn,
      .config-actions button {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
      }

      .config-btn .material-icons,
      .config-actions .material-icons {
        font-size: 1rem;
      }

      .panel-hint {
        font-size: 0.85rem;
        color: #64748b;
      }

      .health-error {
        color: #b91c1c;
      }

      .config-actions {
        display: flex;
        justify-content: flex-start;
        gap: 0.6rem;
        flex-wrap: wrap;
      }

      .save-message {
        color: #047857;
        font-weight: 600;
      }

      :host ::ng-deep app-instance-model-monaco-editor.repository-config-editor .editor {
        height: clamp(320px, 48vh, 560px);
      }
    `,
  ],
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
  readonly canWriteInstances = this.auth.canWriteInstances;
  readonly title = computed(() => (this.hasS3Config() ? "config.pbtxt" : "Live API Config"));
  readonly editorLanguage = computed(() => (this.hasS3Config() ? "proto" : "json"));
  readonly closedHint = computed(() =>
    this.hasS3Config()
      ? "Open the model repository config beside this workflow."
      : "Open the live Triton model API config beside this workflow.",
  );

  readonly relativePath = computed(() => {
    const modelName = this.modelName()
      .replace(/^\/+|\/+$/g, "")
      .trim();
    return modelName ? `${modelName}/config.pbtxt` : "";
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
    return prefix ? `${prefix}/${relative}` : relative;
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
        const response = (await firstValueFrom(
          this.instancesApi.getInstanceS3ContentApiInstancesInstanceIdS3ContentGet(
            this.instanceId(),
            this.relativePath(),
          ),
        )) as S3FileContentResponse;
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

  private s3Text(key: keyof InstanceS3ConfigLike): string {
    const value = this.s3()?.[key];
    return typeof value === "string" ? value.trim() : "";
  }

  private s3Boolean(key: keyof InstanceS3ConfigLike): boolean {
    return this.s3()?.[key] === true;
  }
}
