import { Component, inject, signal } from "@angular/core";
import { DatePipe } from "@angular/common";
import { RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { firstValueFrom } from "rxjs";
import { PreparationApiService, QueueEntry } from "./preparation-api.service";
import { mapApiErrorMessage } from "../../shared/api-error-message";

@Component({
  selector: "app-engineering-queue",
  standalone: true,
  imports: [DatePipe, RouterLink, MatButtonModule],
  styleUrl: "./preparation.scss",
  template: `
    <h1>Engineering queue</h1>
    <p>Review submitted models, missing inputs and assigned preparation work.</p>
    <a mat-stroked-button routerLink="/preparation/handoffs/new">Submit model</a>
    <button mat-button (click)="load(true)" [disabled]="busy()">Refresh</button>
    @if (error()) {
      <p role="alert">{{ error() }}</p>
    }
    @if (busy()) {
      <p role="status">Loading tasks…</p>
    }
    @for (entry of entries(); track entry.task.id) {
      <article>
        <h2>
          <a [routerLink]="['/preparation/tasks', entry.task.id]">{{ entry.task.title }}</a>
        </h2>
        <p>
          Task status: <strong>{{ entry.task.state }}</strong>
        </p>
        <dl>
          <dt>Models</dt>
          <dd>{{ entry.source.models.join(", ") || entry.task.title }}</dd>
          <dt>Source</dt>
          <dd>{{ entry.source.kind }} · {{ entry.source.revision || "Revision unavailable" }}</dd>
          <dt>Submitted by</dt>
          <dd>{{ entry.submitter_name }}</dd>
          <dt>Team</dt>
          <dd>{{ entry.team_name }}</dd>
          <dt>Assignee</dt>
          <dd>{{ entry.assignee_name || "Unassigned" }}</dd>
          <dt>Submitted</dt>
          <dd>{{ entry.task.created_at | date: "medium" }}</dd>
        </dl>
        @if (entry.task.missing_inputs.length) {
          <p>Missing inputs: {{ entry.task.missing_inputs.join(", ") }}</p>
        }
        @if (entry.actions.claim) {
          <button mat-stroked-button (click)="claim(entry)" [disabled]="busy()">Claim task</button>
        }
      </article>
    } @empty {
      @if (!busy() && !error()) {
        <p>No visible preparation tasks.</p>
      }
    }
    @if (hasMore()) {
      <button mat-button (click)="load()" [disabled]="busy()">Load more tasks</button>
    }
  `,
})
export class EngineeringQueueComponent {
  private readonly api = inject(PreparationApiService);
  readonly entries = signal<QueueEntry[]>([]);
  readonly busy = signal(false);
  readonly error = signal("");
  readonly hasMore = signal(false);
  constructor() {
    void this.load(true);
  }
  async load(reset = false) {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set("");
    try {
      const rows = await firstValueFrom(this.api.queue(reset ? 0 : this.entries().length));
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
    let message = "";
    try {
      await firstValueFrom(this.api.action(entry.task, "claim"));
    } catch (error) {
      message = mapApiErrorMessage(error, "Could not claim task. Reload its current assignment.");
    } finally {
      this.busy.set(false);
    }
    await this.load(true);
    if (message) this.error.set(message);
  }
}
