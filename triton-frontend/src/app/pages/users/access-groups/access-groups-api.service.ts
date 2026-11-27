import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { BASE_PATH } from "../../../api/generated";

export interface GroupAssignment {
  name: string;
  user_ids: number[];
  instance_ids: number[];
}

export interface AccessGroup extends GroupAssignment {
  id: string;
  revision: number;
}

export interface InstanceOption {
  id: number;
  name: string;
}

@Injectable({ providedIn: "root" })
export class AccessGroupsApiService {
  private readonly http = inject(HttpClient);
  private readonly base = String(inject(BASE_PATH, { optional: true }) ?? "").replace(/\/$/, "");
  private readonly url = this.base + "/api/access-groups";

  list() {
    return this.http.get<AccessGroup[]>(this.url);
  }

  instances() {
    return this.http.get<InstanceOption[]>(this.url + "/instance-options");
  }

  save(assignment: GroupAssignment, existing?: AccessGroup) {
    return existing
      ? this.http.put<AccessGroup>(this.url + "/" + existing.id, {
          ...assignment,
          revision: existing.revision,
        })
      : this.http.post<AccessGroup>(this.url, assignment);
  }

  delete(group: AccessGroup) {
    return this.http.delete<void>(this.url + "/" + group.id, {
      params: { revision: group.revision },
    });
  }
}
