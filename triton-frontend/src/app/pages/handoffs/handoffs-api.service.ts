import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { BASE_PATH, CodeServerDTO } from "../../api/generated";

export interface SourceInput {
  kind: "s3";
  profile_id: number;
  path: string;
  version_id?: string;
  expected_digest?: string;
}
export interface RepositoryInput {
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
  dataset: SourceInput | null;
  baseline: SourceInput | null;
  expected_behavior: string | null;
  acceptance_criteria: string | null;
}
export interface ResolvedInput {
  version_id: string;
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
  id: number;
  name: string;
  bucket: string;
  prefix: string;
}

@Injectable({ providedIn: "root" })
export class HandoffsApiService {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");
  submit(request: HandoffRequest) {
    return this.http.post<HandoffReceipt>(this.base + "/api/preparation/handoffs", request);
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
  openWorkspace(taskId: string) {
    return this.http.post<{ task_id: string; workspace: CodeServerDTO }>(
      this.base + "/api/preparation/tasks/" + encodeURIComponent(taskId) + "/workspace",
      {},
    );
  }
  profiles() {
    return this.http.get<SourceProfile[]>(this.base + "/api/s3-profiles");
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
