import { Component, computed, inject, signal } from "@angular/core";
import { DatePipe } from "@angular/common";
import { finalize } from "rxjs";

import { MatButtonModule } from "@angular/material/button";
import { MatCardModule } from "@angular/material/card";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatIconModule } from "@angular/material/icon";
import { MatSelectModule } from "@angular/material/select";
import { MatTableModule } from "@angular/material/table";

import { ErrorLogEvent, ErrorLogReporterService } from "../../shared/error-log-reporter.service";

@Component({
  selector: "app-error-logs-page",
  standalone: true,
  imports: [
    DatePipe,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatIconModule,
    MatSelectModule,
    MatTableModule,
  ],
  styleUrl: "./error-logs-page.component.scss",
  templateUrl: "./error-logs-page.component.html",
})
export class ErrorLogsPageComponent {
  private readonly logs = inject(ErrorLogReporterService);

  readonly source = signal("");
  readonly events = signal<ErrorLogEvent[]>([]);
  readonly loading = signal(false);
  readonly error = signal("");
  readonly selected = signal<ErrorLogEvent | null>(null);
  readonly selectedDetail = computed(() => this.selected()?.detail || "No detail captured.");

  readonly displayedColumns = ["created_at", "source", "level", "status_code", "path", "message"];

  constructor() {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.error.set("");
    this.logs
      .list(this.source(), 200)
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({
        next: (events) => {
          this.events.set(events);
          if (this.selected() && !events.some((event) => event.id === this.selected()?.id)) {
            this.selected.set(null);
          }
        },
        error: () => this.error.set("Failed to load error logs."),
      });
  }

  selectSource(source: string): void {
    this.source.set(source);
    this.refresh();
  }

  selectEvent(event: ErrorLogEvent): void {
    this.selected.set(event);
  }
}
