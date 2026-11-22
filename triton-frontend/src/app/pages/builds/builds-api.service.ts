import { Injectable, inject } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { BASE_PATH } from "../../api/generated";

export interface BuildAttempt {
  number: number;
  state: string;
  destination: string;
  final_digest: string | null;
  context_id: string;
  cleaned: boolean;
  created_at: string;
  finished_at: string | null;
}
export interface TaskBuild {
  id: string;
  task_id: string;
  attempts: BuildAttempt[];
  operation: {
    state: string;
    cancel_requested: boolean;
    message: string;
    result: Record<string, unknown> | null;
  };
}
export interface BuildOptions {
  task_id: string;
  repositories: string[];
  local_archive?: boolean;
  max_context_bytes: number;
}
export interface BuildLogs {
  attempt: number;
  output: string;
  expired: boolean;
  truncated: boolean;
  retained: boolean;
}
export function imageReference(attempt: BuildAttempt): string | null {
  return attempt.state === "succeeded" && /^sha256:[a-f0-9]{64}$/.test(attempt.final_digest ?? "")
    ? attempt.destination.replace(/:[^/:]+$/, "") + "@" + attempt.final_digest
    : null;
}
export function buildLabel(build: TaskBuild): string {
  if (["succeeded", "failed", "cancelled"].includes(build.operation.state))
    return build.operation.state;
  if (build.operation.cancel_requested) return "Cancelling";
  if (build.attempts.at(-1)?.finished_at && build.operation.state !== "pending")
    return "Retaining result / cleanup";
  const phase = build.operation.result?.["phase"];
  return phase === "pushing" ? "Pushing" : phase === "building" ? "Building" : "Queued";
}
@Injectable({ providedIn: "root" })
export class BuildsApiService {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");
  options(taskId: string) {
    return this.http.get<BuildOptions>(`${this.base}/api/builds/options`, {
      params: { task_id: taskId },
    });
  }
  list(taskId: string, offset = 0) {
    return this.http.get<TaskBuild[]>(`${this.base}/api/builds`, {
      params: { task_id: taskId, limit: 50, offset },
    });
  }
  action(id: string, action: "cancel" | "retry") {
    return this.http.post<TaskBuild>(
      `${this.base}/api/builds/${encodeURIComponent(id)}/${action}`,
      {},
    );
  }
  logs(id: string, attempt: number) {
    return this.http.get<BuildLogs>(`${this.base}/api/builds/${encodeURIComponent(id)}/logs`, {
      params: { attempt },
    });
  }
}
