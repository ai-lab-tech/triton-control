import { Component, inject, signal } from "@angular/core";
import { DatePipe } from "@angular/common";
import { RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { firstValueFrom } from "rxjs";
import { PreparationApiService, QueueEntry } from "./preparation-api.service";
import { AuthStore } from "../../shared/auth/auth.store";
import { mapApiErrorMessage } from "../../shared/api-error-message";

@Component({
  selector: "app-engineering-queue",
  standalone: true,
  imports: [DatePipe, RouterLink, MatButtonModule],
  styleUrl: "./engineering-queue.component.scss",
  template: `
    <header>
      <h1 class="page-title">Engineering queue</h1>
      <div class="controls">
        <button mat-button (click)="load(true)" [disabled]="busy()">Refresh</button>
        <a mat-stroked-button routerLink="/preparation/handoffs/new">Submit model</a>
      </div>
    </header>
    <div class="queue-surface card">
      <div class="toolbar">
        <div class="controls" role="group" aria-label="Queue view">
          @for (option of views; track option.value) {
            <button
              mat-button
              [attr.aria-pressed]="view() === option.value"
              [disabled]="busy()"
              (click)="selectView(option.value)"
            >
              {{ option.label }}
            </button>
          }
        </div>
        <form (submit)="applySearch(searchInput.value, $event)">
          <input
            #searchInput
            type="search"
            maxlength="200"
            aria-label="Search task titles"
            placeholder="Search task titles"
          />
          <button mat-button [disabled]="busy()">Search</button>
        </form>
      </div>
      <div role="status">
        @if (claimed(); as entry) {
          <p>
            Assigned to you ({{ assignee(entry) }}).
            <a [routerLink]="['/preparation/tasks', entry.task.id]">Open task</a>
          </p>
        }
        @if (busy()) {
          <p>Loading tasks…</p>
        }
      </div>
      @if (error()) {
        <p role="alert">{{ error() }}</p>
      }
      @if (entries().length) {
        <div class="table-scroll" role="region" aria-label="Preparation tasks" tabindex="0">
          <table>
            <thead>
              <tr>
                <th scope="col">Task</th>
                <th scope="col">Status</th>
                <th scope="col">Assigned to</th>

                <th scope="col"><span class="visually-hidden">Action</span></th>
              </tr>
            </thead>
            <tbody>
              @for (entry of entries(); track entry.task.id) {
                <tr>
                  <th scope="row">
                    <a [routerLink]="['/preparation/tasks', entry.task.id]">{{
                      entry.task.title
                    }}</a>
                    <small
                      >{{ entry.submitter_name }} ·
                      {{ entry.task.created_at | date: "mediumDate" }}</small
                    >
                    @if (entry.source.models.length) {
                      <small>{{ entry.source.models.join(", ") }}</small>
                    }
                  </th>
                  <td>
                    <span class="status" [attr.data-state]="entry.task.state">{{
                      status(entry)
                    }}</span>
                    @if (entry.task.missing_inputs.length) {
                      <small class="missing"
                        >Missing: {{ entry.task.missing_inputs.map(inputLabel).join(", ") }}</small
                      >
                    }
                  </td>
                  <td class="owner">
                    <span class="mobile-label">Assigned to </span>{{ assignee(entry) }}
                  </td>
                  <td class="row-action">
                    @if (entry.actions.claim) {
                      <button
                        mat-stroked-button
                        (click)="claim(entry)"
                        [disabled]="busy()"
                        [attr.aria-label]="'Claim task: ' + entry.task.title"
                      >
                        Claim task
                      </button>
                    } @else {
                      <a mat-button [routerLink]="['/preparation/tasks', entry.task.id]">Open</a>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      } @else if (!busy() && !error()) {
        <p class="empty">{{ search ? "No tasks match your search." : emptyMessages[view()] }}</p>
      }
      @if (hasMore()) {
        <button mat-button (click)="load()" [disabled]="busy()">Load more tasks</button>
      }
    </div>
  `,
})
export class EngineeringQueueComponent {
  private readonly api = inject(PreparationApiService);
  private readonly auth = inject(AuthStore);
  readonly views = [
    { value: "all", label: "All active" },
    { value: "unassigned", label: "Unassigned" },
    { value: "mine", label: "My tasks" },
  ] as const;
  readonly view = signal<(typeof this.views)[number]["value"]>("all");
  readonly emptyMessages = {
    all: "No active preparation tasks.",
    mine: "No tasks are assigned to you.",
    unassigned: "No unassigned preparation tasks.",
  };
  readonly claimed = signal<QueueEntry | null>(null);
  search = "";
  readonly entries = signal<QueueEntry[]>([]);
  readonly busy = signal(false);
  readonly error = signal("");
  readonly hasMore = signal(false);
  constructor() {
    void this.load(true);
  }
  selectView(view: (typeof this.views)[number]["value"]) {
    if (this.busy() || view === this.view()) return;
    this.view.set(view);
    this.entries.set([]);
    this.hasMore.set(false);
    void this.load(true);
  }
  applySearch(value: string, event: Event) {
    event.preventDefault();
    if (this.busy()) return;
    this.search = value.trim();
    this.entries.set([]);
    this.hasMore.set(false);
    void this.load(true);
  }
  assignee(entry: QueueEntry) {
    return (
      entry.assignee_name ||
      (entry.task.assignee_user_id === null ? "Unassigned" : "User " + entry.task.assignee_user_id)
    );
  }
  status(entry: QueueEntry) {
    return (
      (
        {
          queued: "Queued",
          claimed: "Assigned",
          awaiting_information: "Awaiting information",
        } as Record<string, string>
      )[entry.task.state] ?? this.inputLabel(entry.task.state)
    );
  }
  inputLabel(value: string) {
    const label = value.replaceAll("_", " ");
    return label.charAt(0).toUpperCase() + label.slice(1);
  }
  async load(reset = false) {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set("");
    try {
      const rows = await firstValueFrom(
        this.api.queue(reset ? 0 : this.entries().length, this.view(), this.search),
      );
      this.entries.set(reset ? rows : [...this.entries(), ...rows]);
      this.hasMore.set(rows.length === 50);
    } catch (error) {
      this.error.set(mapApiErrorMessage(error, "Could not load engineering tasks."));
    } finally {
      this.busy.set(false);
    }
  }
  async claim(entry: QueueEntry) {
    if (this.busy() || !entry.actions.claim) return;
    this.busy.set(true);
    this.error.set("");
    this.claimed.set(null);
    let message = "";
    try {
      const task = await firstValueFrom(this.api.action(entry.task, "claim"));
      const updated = {
        ...entry,
        task,
        assignee_name: this.auth.userName(),
        actions: { ...entry.actions, claim: false },
      };
      this.claimed.set(updated);
      this.entries.update((rows) =>
        this.view() === "unassigned"
          ? rows.filter((row) => row.task.id !== task.id)
          : rows.map((row) => (row.task.id === task.id ? updated : row)),
      );
    } catch (error) {
      message = mapApiErrorMessage(error, "Could not claim task. Reload its current assignment.");
    } finally {
      this.busy.set(false);
    }
    await this.load(true);
    if (message) this.error.set(message);
  }
}
