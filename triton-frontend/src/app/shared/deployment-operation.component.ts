import { Component, DestroyRef, inject, input, OnInit, signal } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { MatButtonModule } from "@angular/material/button";
import { catchError, EMPTY, exhaustMap, firstValueFrom, timer } from "rxjs";
import { BASE_PATH } from "../api/generated";
import { AuthStore } from "./auth/auth.store";
import { mapApiErrorMessage } from "./api-error-message";

export interface DeploymentOperation {
  id: string;
  state: string;
  cancel_requested: boolean;
  attempts?: number;
  message: string;
}

@Component({
  selector: "app-deployment-operation",
  standalone: true,
  imports: [MatButtonModule],
  template: `
    @if (operation(); as op) {
      <section class="operation" aria-label="Deployment creation" aria-live="polite">
        <strong>Deployment creation: {{ label(op) }}</strong>
        <p>{{ op.message }}</p>
        @if (error() || connectionUnavailable()) {
          <p>Last known state. Waiting for the backend connection to recover.</p>
        }
        @if (auth.canWriteInstances()) {
          @if (op.state === "failed" && !op.cancel_requested) {
            <button
              mat-stroked-button
              [disabled]="busy() || !!error() || connectionUnavailable()"
              (click)="act('retry')"
            >
              Retry creation
            </button>
          }
          @if (op.state !== "succeeded" && op.state !== "cancelled" && !op.cancel_requested) {
            <button
              mat-stroked-button
              [disabled]="busy() || !!error() || connectionUnavailable()"
              (click)="act('cancel')"
            >
              Cancel creation
            </button>
          }
        }
      </section>
    }
    @if (error()) {
      <p role="alert">{{ error() }}</p>
    }
  `,
  styles: [
    `
      .operation {
        padding: 16px;
        margin: 16px 0;
        border: 1px solid #cbd5e1;
        border-radius: 12px;
      }
      button {
        margin-right: 8px;
      }
    `,
  ],
})
export class DeploymentOperationComponent implements OnInit {
  readonly instanceId = input.required<string>();
  readonly connectionUnavailable = input(false);
  readonly operation = signal<DeploymentOperation | null>(null);
  readonly error = signal("");
  readonly busy = signal(false);
  readonly auth = inject(AuthStore);
  private readonly http = inject(HttpClient);
  private readonly destroy = inject(DestroyRef);
  private readonly base = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");

  ngOnInit(): void {
    timer(0, 3000)
      .pipe(
        exhaustMap(() =>
          this.http
            .get<DeploymentOperation | null>(
              `${this.base}/api/deployments/${encodeURIComponent(this.instanceId())}/operation`,
            )
            .pipe(
              catchError((err) => {
                this.error.set(
                  mapApiErrorMessage(err, "Creation status is temporarily unavailable."),
                );
                return EMPTY;
              }),
            ),
        ),
        takeUntilDestroyed(this.destroy),
      )
      .subscribe((op) => {
        this.operation.set(op);
        this.error.set("");
      });
  }

  label(op: DeploymentOperation): string {
    if (op.cancel_requested && op.state !== "cancelled") return "Cancellation requested";
    if (op.state === "pending" && (op.attempts ?? 0) > 0) return "In progress";
    return (
      (
        {
          pending: "Queued",
          running: "In progress",
          succeeded: "Completed",
          failed: "Failed",
          cancelled: "Cancelled",
        } as Record<string, string>
      )[op.state] ?? op.state
    );
  }

  async act(action: "retry" | "cancel"): Promise<void> {
    const op = this.operation();
    if (!op || this.busy() || this.error() || this.connectionUnavailable()) return;
    this.busy.set(true);
    try {
      this.operation.set(
        await firstValueFrom(
          this.http.post<DeploymentOperation>(
            `${this.base}/api/operations/${encodeURIComponent(op.id)}/${action}`,
            {},
          ),
        ),
      );
      this.error.set("");
    } catch (err) {
      this.error.set(mapApiErrorMessage(err, "Could not update deployment creation."));
    } finally {
      this.busy.set(false);
    }
  }
}
