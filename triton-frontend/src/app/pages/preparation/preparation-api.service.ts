import { inject, Injectable } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { BASE_PATH } from "../../api/generated";

export interface Task {
  id: string;
  title: string;
  state: string;
  revision: number;
  assigned_team_id: string;
  submitter_user_id: number;
  assignee_user_id: number | null;
  source_snapshot_id: string | null;
  missing_inputs: string[];
  blocked_reasons: string[];
  created_at: string;
  updated_at: string;
}
export interface QueueEntry {
  task: Task;
  source: {
    kind: string;
    revision: string | null;
    models: string[];
    snapshot_id: string | null;
    handoff_id: string | null;
    instance_id: number | null;
  };
  team_name: string;
  submitter_name: string;
  assignee_name: string | null;
  actions: {
    claim: boolean;
    release: boolean;
    request_information: boolean;
    open_workspace: boolean;
  };
}
export interface Overview extends QueueEntry {
  conversions?: {
    export_id: string;
    directory: string;
    status: string;
    bucket: string | null;
    prefix: string | null;
  }[];
  candidates: {
    installation_id: string;
    record_id: string;
    candidate_digest: string;
    created_at: string;
  }[];
}
export interface Attempt {
  id: string;
  created_at: string;
  attempt: {
    operation: string;
    tool: string | null;
    input_inventory: string;
    output_inventory: string;
    origin: { installation_id: string; record_id: string };
  };
}
export interface TaskEvent {
  id: number;
  action: string;
  revision: number;
  created_at: string;
  changes: Record<string, unknown>;
}
export interface SnapshotReceipt {
  id: string;
  manifest_digest: string;
  task: Task;
  manifest: {
    instance_id: number;
    instance_name: string;
    captured_at: string;
    previous_snapshot_id: string | null;
    gaps: string[];
    inventory: { files: { path: string; size: number; digest: string }[] };
    workspace_inventory: { files: { path: string; size: number; digest: string }[] };
    request: { expected_behavior: string | null; acceptance_criteria: string | null };
  };
}
export interface CaptureOperation {
  id: string;
  kind: string;
  target: string;
  state: string;
  message: string;
  cancel_requested: boolean;
  result: Record<string, unknown>;
}
export interface SnapshotSource {
  kind: "hf" | "example" | "manual";
  repository?: string;
  revision?: string;
  resolved_commit?: string;
  serving_manifest?: string;
  acquisition_record?: string;
  checkpoint_files?: string[];
  commit?: string;
  project_path?: string;
  author?: string;
  ai_assisted?: boolean;
  workspace_model_root?: string;
  model_card?: string;
  license_file?: string;
  source_documents?: string[];
}
export interface SnapshotRequest {
  source?: SnapshotSource;
  workspace?: { workspace_id: number; files: string[] };
  title: string;
  models: { name: string; versions: string[] }[];
  deployment_uid?: string;
  expected_generation?: number;
  repository?: { profile_id: number; prefix: string };
  expected_behavior: string | null;
  acceptance_criteria: string | null;
}
@Injectable({ providedIn: "root" })
export class PreparationApiService {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");
  queue(offset = 0, view = "all", search = "") {
    return this.http.get<QueueEntry[]>(`${this.base}/api/preparation-tasks/queue`, {
      params: { limit: 50, offset, view, ...(search ? { search } : {}) },
    });
  }
  overview(id: string) {
    return this.http.get<Overview>(
      `${this.base}/api/preparation-tasks/${encodeURIComponent(id)}/overview`,
    );
  }
  action(
    task: Task,
    action: "claim" | "release-claim" | "request-information",
    extra: Record<string, unknown> = {},
  ) {
    return this.http.post<Task>(
      `${this.base}/api/preparation-tasks/${encodeURIComponent(task.id)}/${action}`,
      { ...extra, expected_revision: task.revision },
    );
  }
  attempts(id: string, offset = 0) {
    return this.http.get<Attempt[]>(
      `${this.base}/api/preparation-tasks/${encodeURIComponent(id)}/attempts`,
      { params: { limit: 50, offset } },
    );
  }
  events(id: string, offset = 0) {
    return this.http.get<TaskEvent[]>(
      `${this.base}/api/preparation-tasks/${encodeURIComponent(id)}/events`,
      { params: { limit: 50, offset } },
    );
  }
  snapshot(id: string) {
    return this.http.get<SnapshotReceipt>(
      `${this.base}/api/preparation/snapshots/${encodeURIComponent(id)}`,
    );
  }
  capture(instanceId: string, request: SnapshotRequest, key: string) {
    return this.http.post<{ operation: CaptureOperation; snapshot_url: string }>(
      `${this.base}/api/instances/${encodeURIComponent(instanceId)}/preparation-requests`,
      request,
      { headers: { "Idempotency-Key": key } },
    );
  }
  operation(id: string) {
    return this.http.get<CaptureOperation>(`${this.base}/api/operations/${encodeURIComponent(id)}`);
  }
  operationAction(id: string, action: "cancel" | "retry") {
    return this.http.post<CaptureOperation>(
      `${this.base}/api/operations/${encodeURIComponent(id)}/${action}`,
      {},
    );
  }
}
