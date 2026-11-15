import { DatePipe } from "@angular/common";
import { HttpClient, HttpErrorResponse, HttpParams } from "@angular/common/http";
import {
  Component,
  DestroyRef,
  ElementRef,
  ViewChild,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  computed,
  inject,
  signal,
} from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { FormsModule } from "@angular/forms";
import { MatButtonModule } from "@angular/material/button";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatTooltipModule } from "@angular/material/tooltip";
import { MatInputModule } from "@angular/material/input";
import { MatSlideToggleModule } from "@angular/material/slide-toggle";
import { MatSelectModule } from "@angular/material/select";
import { firstValueFrom, interval } from "rxjs";
import { BASE_PATH } from "../../../api/generated";
import { InstancePods, PodLogs, Pod } from "./workload.types";
import { mapApiErrorMessage } from "../../../shared/api-error-message";

@Component({
  selector: "app-workload-panel",
  standalone: true,
  imports: [
    DatePipe,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatIconModule,
    MatTooltipModule,
    MatSelectModule,
    MatSlideToggleModule,
  ],
  templateUrl: "./workload-panel.component.html",
  styleUrl: "./workload-panel.component.scss",
})
export class WorkloadPanelComponent implements OnChanges {
  @Input({ required: true }) instanceId = "";
  @Input() view: "pods" | "logs" = "pods";
  @Input() active = true;
  @Input() initialPodUid = "";
  @Input() selectionRevision = 0;
  @Input() initialContainer = "";
  @Input() initialPrevious = false;
  @Output() viewContainerLogs = new EventEmitter<{
    uid: string;
    container: string;
    previous?: boolean;
  }>();
  @ViewChild("logViewport") logViewport?: ElementRef<HTMLElement>;
  @ViewChild("workspace") workspace?: ElementRef<HTMLElement>;
  readonly podSearch = signal("");
  readonly pendingRead = signal(false);
  readonly selectedPod = computed(() =>
    this.state()?.pods.find((p) => p.uid === this.selectedUid()),
  );
  readonly selectedContainerState = computed(() =>
    this.selectedPod()?.containers.find((c) => c.name === this.selectedContainer()),
  );
  readonly orderedPods = computed(() => {
    const priority = (pod: Pod) =>
      pod.terminating
        ? 3
        : pod.ready
          ? 0
          : pod.phase === "Failed" || pod.phase === "Succeeded"
            ? 2
            : 1;
    return [...(this.state()?.pods ?? [])].sort((a, b) => priority(a) - priority(b));
  });
  readonly visiblePods = computed(() =>
    this.orderedPods().filter((p) =>
      (p.name + " " + p.containers.map((c) => c.name).join(" "))
        .toLowerCase()
        .includes(this.podSearch().toLowerCase()),
    ),
  );
  readonly unavailable = computed(
    () =>
      this.removed() ||
      (!!this.selectedContainer() && !!this.selectedPod() && !this.selectedContainerState()),
  );
  @Output() viewLogs = new EventEmitter<string>();
  private readonly http = inject(HttpClient);
  private readonly destroyRef = inject(DestroyRef);
  private readonly base = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");
  readonly state = signal<InstancePods | null>(null);
  readonly podGroups = computed(() => {
    const groups = new Map<string, { key: string; reason: string; message: string; pods: Pod[] }>();
    for (const pod of this.state()?.pods ?? []) {
      // Only identical terminal failures are combined; live/starting pods stay individually visible.
      const key =
        pod.phase === "Failed" && !pod.terminating
          ? JSON.stringify(["failure", pod.reason ?? "", pod.message ?? ""])
          : JSON.stringify(["pod", pod.uid]);
      const group = groups.get(key);
      if (group) group.pods.push(pod);
      else
        groups.set(key, {
          key,
          reason: pod.reason || "Pod failed",
          message: pod.message || "",
          pods: [pod],
        });
    }
    return [...groups.values()];
  });
  private isHistoricalPod(pod: Pod): boolean {
    return pod.phase === "Failed" || pod.phase === "Succeeded";
  }
  readonly podSections = computed(() =>
    [
      {
        title: "Current pods",
        history: false,
        groups: this.podGroups().filter((g) => !this.isHistoricalPod(g.pods[0])),
      },
      {
        title: "Previous pods",
        history: true,
        groups: this.podGroups().filter((g) => this.isHistoricalPod(g.pods[0])),
      },
    ].map((section) => ({
      ...section,
      count: section.groups.reduce((total, group) => total + group.pods.length, 0),
    })),
  );
  readonly logPodSections = computed(() => [
    {
      title: "Current pods",
      history: false,
      pods: this.visiblePods().filter((p) => !this.isHistoricalPod(p)),
    },
    {
      title: "Previous pods",
      history: true,
      pods: this.visiblePods().filter((p) => this.isHistoricalPod(p)),
    },
  ]);
  readonly logs = signal<PodLogs | null>(null);
  readonly error = signal("");
  readonly logError = signal("");
  readonly loading = signal(false);
  readonly logsLoading = signal(false);
  readonly selectedUid = signal("");
  readonly selectedContainer = signal("");
  readonly previous = signal(false);
  readonly autoRefresh = signal(false);
  readonly now = signal(Date.now());
  readonly nextRead = signal(0);
  readonly collapsedStreams = new Set<string>();
  rememberStream(event: Event, key: string): void {
    if ((event.target as HTMLDetailsElement).open) this.collapsedStreams.delete(key);
    else this.collapsedStreams.add(key);
  }
  filter = "";
  sinceMinutes = 0;
  wrapLines = false;
  readonly lastLoaded = signal<number | null>(null);
  readonly loadedLineCount = computed(() =>
    (this.logs()?.streams ?? []).reduce(
      (sum, stream) => sum + this.countLines(stream.text || ""),
      0,
    ),
  );

  matchingLineCount(): number {
    return (this.logs()?.streams ?? []).reduce(
      (sum, stream) => sum + this.countLines(this.filtered(stream.text || "")),
      0,
    );
  }

  private countLines(text: string): number {
    return text ? text.replace(/\n$/, "").split("\n").length : 0;
  }
  lines = 200;
  private identity = "";
  private selectionInitialized = false;
  private stateRequest = 0;
  private logRequest = 0;
  readonly removed = computed(
    () =>
      !!this.selectedUid() &&
      !!this.state() &&
      !(this.state()?.pods ?? []).some((p) => p.uid === this.selectedUid()),
  );
  readonly containers = computed(() =>
    [
      ...new Set(
        (this.state()?.pods ?? [])
          .filter((p) => !this.selectedUid() || p.uid === this.selectedUid())
          .flatMap((p) => (p.containers ?? []).map((c) => c.name)),
      ),
    ].sort(),
  );
  readonly canRead = computed(
    () =>
      !this.logsLoading() &&
      !this.unavailable() &&
      !!this.state()?.managed &&
      this.now() >= this.nextRead(),
  );
  readonly output = computed(() =>
    (this.logs()?.streams ?? [])
      .map((s) => {
        const text = s.text ?? "";
        return (
          `[${s.pod_name} | ${s.pod_uid} | ${s.container} | ${s.previous ? "previous" : "current"}]\n` +
          (s.error ? `ERROR: ${s.error}\n` : "") +
          (s.truncated ? "[Truncated]\n" : "") +
          text
        );
      })
      .join("\n\n"),
  );

  constructor() {
    interval(1000)
      .pipe(takeUntilDestroyed())
      .subscribe(() => {
        this.now.set(Date.now());
        if (this.active && this.pendingRead() && this.canRead()) void this.readLogs();
      });
    interval(10000)
      .pipe(takeUntilDestroyed())
      .subscribe(() => {
        if (this.active) void this.refresh(true);
      });
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (this.identity !== this.instanceId) {
      this.identity = this.instanceId;
      this.stateRequest++;
      this.logRequest++;
      this.state.set(null);
      this.logs.set(null);
      this.lastLoaded.set(null);
      this.loading.set(false);
      this.logsLoading.set(false);
      this.selectedUid.set(this.initialPodUid);
      this.selectedContainer.set(this.initialContainer);
      this.previous.set(this.initialPrevious);
      this.pendingRead.set(false);
      this.selectionInitialized = false;
    } else if ((changes["initialPodUid"] || changes["selectionRevision"]) && this.initialPodUid) {
      this.selectContainer(
        this.initialPodUid,
        this.initialContainer ||
          this.preferredContainer(this.state()?.pods.find((p) => p.uid === this.initialPodUid)),
        this.initialPrevious,
      );
    }
    if (this.active && this.instanceId) void this.refresh();
  }

  async refresh(includeLogs = false): Promise<void> {
    if (this.loading()) return;
    const request = ++this.stateRequest;
    this.loading.set(true);
    this.error.set("");
    try {
      const state = await firstValueFrom(
        this.http
          .get<InstancePods>(`${this.base}/api/instances/${this.instanceId}/pods`)
          .pipe(takeUntilDestroyed(this.destroyRef)),
      );
      if (request !== this.stateRequest) return;
      const initialSelection = !this.selectionInitialized;
      this.state.set(state);
      if (!this.selectionInitialized) {
        const pod = this.selectedUid()
          ? state.pods.find((p) => p.uid === this.selectedUid())
          : (this.orderedPods().find((p) => p.ready && !p.terminating) ??
            this.orderedPods().find((p) => p.current_revision && !p.terminating) ??
            this.orderedPods()[0]);
        if (pod) {
          this.selectedUid.set(pod.uid);
          this.selectedContainer.set(this.initialContainer || this.preferredContainer(pod));
        }
        this.selectionInitialized = true;
      }
      if (
        this.view === "logs" &&
        this.active &&
        (initialSelection || (includeLogs && this.autoRefresh()))
      )
        await this.readLogs();
    } catch (error) {
      if (request !== this.stateRequest) return;
      this.state.set(null);
      // A pending response must not restore logs after access or state is lost.
      this.logRequest++;
      this.logsLoading.set(false);
      this.pendingRead.set(false);
      this.logs.set(null);
      this.lastLoaded.set(null);
      this.logError.set("");
      this.error.set(mapApiErrorMessage(error, "Could not read pod state."));
    } finally {
      if (request === this.stateRequest) this.loading.set(false);
    }
  }

  private preferredContainer(pod?: Pod): string {
    return (
      pod?.containers.find((c) => c.name === "triton" && c.kind === "regular")?.name ??
      pod?.containers.find((c) => c.kind === "regular")?.name ??
      pod?.containers[0]?.name ??
      ""
    );
  }

  selectPod(uid: string): void {
    this.selectContainer(
      uid,
      uid ? this.preferredContainer(this.state()?.pods.find((p) => p.uid === uid)) : "",
    );
  }

  selectContainer(uid: string, container: string, previous = false): void {
    this.selectedUid.set(uid);
    this.selectedContainer.set(container);
    this.previous.set(previous);
    this.selectionInitialized = true;
    this.filter = "";
    this.clearLogs();
  }

  setAutoRefresh(enabled: boolean): void {
    this.autoRefresh.set(enabled);
    if (enabled) this.pendingRead.set(true);
    else {
      this.pendingRead.set(false);
      this.logRequest++;
      this.logsLoading.set(false);
    }
  }

  newest(): void {
    const element = this.logViewport?.nativeElement;
    if (element) element.scrollTop = element.scrollHeight;
  }

  async expand(): Promise<void> {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await this.workspace?.nativeElement.requestFullscreen();
    } catch {
      this.logError.set("Your browser could not open fullscreen mode.");
    }
  }

  clearLogs(): void {
    this.logRequest++;
    this.logsLoading.set(false);
    this.logs.set(null);
    this.lastLoaded.set(null);
    this.logError.set("");
    this.pendingRead.set(this.view === "logs" && this.active);
    if (this.pendingRead() && this.canRead()) void this.readLogs();
  }

  async readLogs(): Promise<void> {
    if (!this.canRead()) return;
    this.pendingRead.set(false);
    const request = ++this.logRequest;
    this.nextRead.set(Date.now() + 5000);
    this.logsLoading.set(true);
    this.logError.set("");
    let params = new HttpParams()
      .set("previous", this.previous())
      .set("lines", this.lines)
      .set("byte_limit", 262144);
    if (this.selectedUid()) params = params.set("pod_uid", this.selectedUid());
    if (this.selectedContainer()) params = params.set("container", this.selectedContainer());
    if (this.sinceMinutes)
      params = params.set("since", new Date(Date.now() - this.sinceMinutes * 60000).toISOString());
    try {
      const logs = await firstValueFrom(
        this.http
          .get<PodLogs>(`${this.base}/api/instances/${this.instanceId}/pod-logs`, { params })
          .pipe(takeUntilDestroyed(this.destroyRef)),
      );
      if (request === this.logRequest) {
        const viewport = this.logViewport?.nativeElement;
        const scrollTop = viewport?.scrollTop ?? 0;
        const scrollLeft = viewport?.scrollLeft ?? 0;
        this.logs.set(logs);
        if (viewport)
          requestAnimationFrame(() => {
            if (request === this.logRequest && viewport.isConnected) {
              viewport.scrollTop = scrollTop;
              viewport.scrollLeft = scrollLeft;
            }
          });
        this.lastLoaded.set(Date.now());
      }
    } catch (error) {
      if (request !== this.logRequest) return;
      if (error instanceof HttpErrorResponse && error.status === 429) {
        this.nextRead.set(Date.now() + 5000);
        this.pendingRead.set(true);
        return;
      }
      this.logs.set(null);
      this.logError.set(mapApiErrorMessage(error, "Could not read container logs."));
    } finally {
      if (request === this.logRequest) this.logsLoading.set(false);
    }
  }

  filtered(text: string): string {
    return this.filter
      ? text
          .split("\n")
          .filter((line) => line.toLowerCase().includes(this.filter.toLowerCase()))
          .join("\n")
      : text;
  }

  age(created?: string | null): string {
    if (!created) return "Unknown";
    const seconds = Math.max(0, Math.floor((this.now() - Date.parse(created)) / 1000));
    return seconds < 60
      ? `${seconds}s`
      : seconds < 3600
        ? `${Math.floor(seconds / 60)}m`
        : `${Math.floor(seconds / 3600)}h`;
  }

  download(): void {
    const url = URL.createObjectURL(
      new Blob([this.output()], { type: "text/plain;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `instance-${this.instanceId}-container-logs.txt`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
