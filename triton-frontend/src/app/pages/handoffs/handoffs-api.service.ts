import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { BASE_PATH, CodeServerDTO, ControlLineage } from "../../api/generated";

export interface WorkspaceSizing {
  input_bytes: number;
  recommended_gib: number;
  maximum_gib: number;
  existing_storage_size: string | null;
  default_image: string;
  existing_image?: string | null;
  existing_gpu_count?: number | null;
  existing_cpu?: string | null;
  existing_memory?: string | null;
}

export interface TaskWorkspaceOptions {
  storage_size: string;
  image: string;
  gpu_count: number;
  cpu?: string;
  memory?: string;
}

export interface CaptureOperation {
  id: string;
  state: string;
  message: string;
  result: { handoff_id?: string; bytes_verified?: number };
}
export interface SourceInput {
  kind: "s3";
  profile_id: number;
  path: string;
  version_id?: string;
  expected_digest?: string;
}
export interface RepositoryInput {
  model?: { name: string; versions: string[] };
  kind: "s3_repository";
  profile_id: number;
  path: string;
}
export interface HandoffRequest {
  schema_version: 1;
  origin: { installation_id: string; record_id: string };
  previous_handoff_id?: string | null;
  revision: string;
  title: string;
  source: SourceInput | RepositoryInput;
  lineage?: ControlLineage | null;
  dataset: SourceInput | null;
  baseline: SourceInput | null;
  expected_behavior: string | null;
  acceptance_criteria: string | null;
}
export interface ResolvedInput {
  version_id: string | null;
  key: string;
  file: { path?: string; digest: string; size: number };
}
export interface ResolvedRepository {
  key: string;
  files: ResolvedInput[];
}
export interface HandoffReceipt {
  id: string;
  request: HandoffRequest;
  source: ResolvedInput | ResolvedRepository;
  source_snapshot?: { inventory: string };
  dataset: ResolvedInput | null;
  baseline: ResolvedInput | null;
  task: {
    id: string;
    revision?: number;
    assignee_user_id?: number | null;
    state: string;
    missing_inputs: string[];
    blocked_reasons: string[];
  };
  created_at: string;
}
export interface HandoffNotification {
  id: string;
  handoff_id: string;
  message: string;
  read_at: string | null;
  created_at: string;
}
export interface SourceProfile {
  endpoint: string;
  id: number;
  name: string;
  bucket: string;
  prefix: string;
}

@Injectable({ providedIn: "root" })
export class HandoffsApiService {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");
  submit(request: HandoffRequest, submissionId: string) {
    return this.http.post<CaptureOperation>(
      this.base + "/api/preparation/handoff-captures",
      request,
      {
        headers: {
          "Idempotency-Key": bytesToHex(
            sha256(new TextEncoder().encode(`${submissionId}:${JSON.stringify(request)}`)),
          ),
        },
      },
    );
  }
  capture(id: string) {
    return this.http.get<CaptureOperation>(this.base + "/api/operations/" + encodeURIComponent(id));
  }
  captureAction(id: string, action: "cancel" | "retry") {
    return this.http.post<CaptureOperation>(
      this.base + "/api/operations/" + encodeURIComponent(id) + "/" + action,
      {},
    );
  }
  get(id: string) {
    return this.http.get<HandoffReceipt>(
      this.base + "/api/preparation/handoffs/" + encodeURIComponent(id),
    );
  }
  claim(taskId: string, revision: number) {
    return this.http.post(
      this.base + "/api/preparation-tasks/" + encodeURIComponent(taskId) + "/claim",
      {
        expected_revision: revision,
      },
    );
  }
  workspaceSizing(taskId: string) {
    return this.http.get<WorkspaceSizing>(
      this.base + "/api/preparation/tasks/" + encodeURIComponent(taskId) + "/workspace-sizing",
    );
  }
  openWorkspace(taskId: string, workspace?: TaskWorkspaceOptions) {
    return this.http.post<{ task_id: string; workspace: CodeServerDTO }>(
      this.base + "/api/preparation/tasks/" + encodeURIComponent(taskId) + "/workspace",
      workspace ? { workspace } : {},
    );
  }
  profiles() {
    return this.http.get<SourceProfile[]>(this.base + "/api/s3-profiles");
  }
  submissionPolicy() {
    return this.http.get<{ require_s3_versioning: boolean }>(
      this.base + "/api/preparation/handoff-submission-policy",
    );
  }
  notifications() {
    return this.http.get<HandoffNotification[]>(
      this.base + "/api/preparation/notifications?limit=100",
    );
  }
  markRead(id: string) {
    return this.http.put<HandoffNotification>(
      this.base + "/api/preparation/notifications/" + encodeURIComponent(id) + "/read",
      {},
    );
  }
}
