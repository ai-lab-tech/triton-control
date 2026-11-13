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

export interface TeamInput {
  name: string;
  is_active: boolean;
}

export interface TeamUser {
  id: number;
  name: string;
  email: string;
  role: string;
  is_active: boolean;
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

export interface TeamEvent {
  id: number;
  actor_user_id: number;
  subject_user_id: number | null;
  action: string;
  changes: { before?: TeamInput; after?: TeamInput };
  created_at: string;
}

@Injectable({ providedIn: "root" })
export class TeamsApiService {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(BASE_PATH, { optional: true }) ?? ""}`.replace(/\/$/, "");
  private readonly url = this.base + "/api/teams";

  list(): Observable<Team[]> {
    return this.http.get<Team[]>(this.url);
  }

  users(): Observable<TeamUser[]> {
    return this.http.get<TeamUser[]>(this.base + "/api/auth/users");
  }

  create(input: TeamInput): Observable<Team> {
    return this.http.post<Team>(this.url, input);
  }

  update(id: string, input: TeamInput): Observable<Team> {
    return this.http.put<Team>(`${this.url}/${id}`, input);
  }

  members(id: string): Observable<TeamMember[]> {
    return this.http.get<TeamMember[]>(`${this.url}/${id}/members`);
  }

  setMember(id: string, userId: number, active: boolean): Observable<TeamMember[]> {
    const url = `${this.url}/${id}/members/${userId}`;
    return active ? this.http.put<TeamMember[]>(url, {}) : this.http.delete<TeamMember[]>(url);
  }

  history(id: string): Observable<TeamEvent[]> {
    return this.http.get<TeamEvent[]>(`${this.url}/${id}/events`);
  }
}
