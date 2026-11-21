import { MatButtonModule } from "@angular/material/button";
import { Component, DestroyRef, inject, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute } from "@angular/router";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { firstValueFrom } from "rxjs";
import {
  BuildsApiService,
  BuildOptions,
  TaskBuild,
  BuildLogs,
  buildLabel,
  imageReference,
} from "./builds-api.service";
import { mapApiErrorMessage } from "../../shared/api-error-message";

@Component({
  selector: "app-task-builds-page",
  standalone: true,
  imports: [FormsModule, MatButtonModule],
  templateUrl: "./task-builds-page.component.html",
  styles: [
    `
      :host {
        display: block;
        max-width: 1100px;
        margin: auto;
        padding: var(--card-padding);
      }
      article,
      form {
        padding: var(--card-padding);
        margin: 16px 0;
        background: white;
        box-shadow: var(--shadow-card);
        border-radius: var(--radius-lg);
      }
      label {
        display: block;
        margin: 12px 0;
      }
      input,
      select {
        display: block;
        width: min(100%, 32rem);
        min-height: var(--control-height);
        border: 1px solid var(--mist-200);
        border-radius: 10px;
        background: white;
        color: inherit;
        font: inherit;
        max-width: 100%;
        padding: 8px;
      }
      button {
        margin: 4px;
        padding: 8px 12px;
      }
      code {
        overflow-wrap: anywhere;
      }
      pre {
        white-space: pre-wrap;
        max-height: 400px;
        overflow: auto;
      }
      .error {
        color: #b91c1c;
      }
    `,
  ],
})
export class TaskBuildsPageComponent {
  private readonly api = inject(BuildsApiService);
  readonly taskId = signal("");
  readonly builds = signal<TaskBuild[]>([]);
  readonly options = signal<BuildOptions | null>(null);
  readonly error = signal("");
  readonly accessMessage = signal("");
  readonly busy = signal(false);
  readonly log = signal<BuildLogs | null>(null);
  readonly logTitle = signal("");
  readonly offset = signal(0);
  readonly label = buildLabel;
  readonly image = imageReference;
  contextId = "";
  repository = "";
  private generation = 0;
  private logVersion = 0;
  private refreshVersion = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private submission?: { payload: string; key: string };

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.generation++;
      clearTimeout(this.timer);
    });
    inject(ActivatedRoute)
      .paramMap.pipe(takeUntilDestroyed())
      .subscribe((params) => {
        this.generation++;
        clearTimeout(this.timer);
        this.taskId.set(params.get("taskId") ?? "");
        this.builds.set([]);
        this.options.set(null);
        this.log.set(null);
        this.offset.set(0);
        this.contextId = "";
        this.repository = "";
        this.submission = undefined;
        this.busy.set(false);
        this.error.set("");
        this.accessMessage.set("");
        void this.loadOptions();
        void this.refresh();
      });
  }
  private async loadOptions() {
    const generation = this.generation;
    try {
      const options = await firstValueFrom(this.api.options(this.taskId()));
      if (generation !== this.generation) return;
      this.options.set(options);
      this.repository = options.repositories[0] ?? "";
    } catch (error) {
      if (generation === this.generation)
        this.accessMessage.set(
          mapApiErrorMessage(
            error,
            "Builds require development mode, an eligible engineer and a qualified build environment.",
          ),
        );
    }
  }
  async refresh() {
    const version = ++this.refreshVersion;
    clearTimeout(this.timer);
    const generation = this.generation;
    try {
      const rows = await firstValueFrom(this.api.list(this.taskId(), this.offset()));
      if (generation !== this.generation || version !== this.refreshVersion) return;
      this.builds.set(rows);
      this.error.set("");
      if (rows.some((row) => !["succeeded", "failed", "cancelled"].includes(row.operation.state)))
        this.timer = setTimeout(() => void this.refresh(), 5000);
    } catch (error) {
      if (generation !== this.generation || version !== this.refreshVersion) return;
      this.builds.set([]);
      this.logVersion++;
      this.log.set(null);
      this.options.set(null);
      this.error.set(
        mapApiErrorMessage(error, "Could not read builds. Your task access may have changed."),
      );
    }
  }
  async page(delta: number) {
    this.generation++;
    clearTimeout(this.timer);
    this.log.set(null);
    this.offset.set(Math.max(0, this.offset() + delta));
    await this.refresh();
  }
  async submit() {
    if (
      this.busy() ||
      !this.options()?.repositories.includes(this.repository) ||
      !/^[a-f0-9]{32}$/.test(this.contextId)
    )
      return;
    const payload = JSON.stringify([this.taskId(), this.contextId, this.repository]);
    if (this.submission?.payload !== payload)
      this.submission = { payload, key: crypto.randomUUID() };
    await this.mutate(() =>
      firstValueFrom(
        this.api.submit(this.taskId(), this.contextId, this.repository, this.submission!.key),
      ),
    );
  }
  async action(build: TaskBuild, action: "cancel" | "retry") {
    if (this.busy() || build.task_id !== this.taskId()) return;
    await this.mutate(() => firstValueFrom(this.api.action(build.id, action)));
  }
  private async mutate(action: () => Promise<TaskBuild>) {
    const generation = this.generation;
    this.busy.set(true);
    this.error.set("");
    try {
      await action();
      if (generation === this.generation) {
        this.offset.set(0);
        await this.refresh();
      }
    } catch (error) {
      if (generation === this.generation)
        this.error.set(mapApiErrorMessage(error, "Build request failed. Refresh before retrying."));
    } finally {
      if (generation === this.generation) this.busy.set(false);
    }
  }
  async showLogs(build: TaskBuild, attempt: number) {
    const generation = this.generation;
    const version = ++this.logVersion;
    this.log.set(null);
    this.logTitle.set(`${build.id} · attempt ${attempt}`);
    try {
      const log = await firstValueFrom(this.api.logs(build.id, attempt));
      if (generation === this.generation && version === this.logVersion) this.log.set(log);
    } catch (error) {
      if (generation === this.generation && version === this.logVersion)
        this.error.set(mapApiErrorMessage(error, "Logs unavailable or access expired."));
    }
  }
}
