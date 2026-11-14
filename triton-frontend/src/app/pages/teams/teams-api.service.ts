import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";
import { BASE_PATH } from "../../api/generated";

export interface Team {
  id: string;
  name: string;
  is_active: boolean;
  created_at: string;
}

export interface TeamMember {
  user_id: number;
  name: string;
  email: string;
  role: string;
  is_active: boolean;
  user_is_active: boolean;
  eligible: boolean;
  updated_at: string;
}

@Injectable({ providedIn: "root" })
export class TeamsApiService {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");
  private readonly url = this.base + "/api/teams";

  engineeringGroup(): Observable<Team> {
    return this.http.put<Team>(`${this.url}/engineering`, {});
  }

  members(id: string): Observable<TeamMember[]> {
    return this.http.get<TeamMember[]>(`${this.url}/${id}/members`);
  }

  setMember(id: string, userId: number, active: boolean): Observable<TeamMember[]> {
    const url = `${this.url}/${id}/members/${userId}`;
    return active ? this.http.put<TeamMember[]>(url, {}) : this.http.delete<TeamMember[]>(url);
  }
}
