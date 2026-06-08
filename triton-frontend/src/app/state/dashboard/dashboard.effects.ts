import { Injectable, inject } from "@angular/core";
import { forkJoin, of } from "rxjs";
import { catchError, map, switchMap } from "rxjs/operators";
import { Actions, createEffect, ofType } from "@ngrx/effects";

import { DashboardService, InstancesService, TritonInstanceDTO } from "../../api/generated/index";
import { type AlertItem, type InstanceRow, type StatCard } from "./dashboard.reducer";
import {
  dashboardDataLoadFailed,
  dashboardDataLoaded,
  dashboardPageOpened,
  dashboardRefreshRequested,
} from "./dashboard.actions";

@Injectable()
export class DashboardEffects {
  private readonly actions$ = inject(Actions);
  private readonly instancesApi = inject(InstancesService);
  private readonly dashboardApi = inject(DashboardService);

  readonly loadData$ = createEffect(() =>
    this.actions$.pipe(
      ofType(dashboardPageOpened, dashboardRefreshRequested),
      switchMap(() =>
        forkJoin([
          this.instancesApi.listInstancesApiInstancesGet(),
          this.dashboardApi.listDashboardAlertsApiDashboardAlertsGet(),
        ]).pipe(
          map(([rows, alertsRaw]) => {
            const safeRows = (rows as TritonInstanceDTO[]) ?? [];
            const instances: InstanceRow[] = safeRows.map((row) => ({
              id: String(row.id ?? ""),
              name: row.name ?? "",
              url: row.url ?? "",
              models: (row.model_names ?? []).length,
              createdAt: row.created_at ? new Date(row.created_at).toLocaleString() : "",
            }));

            const configuredCount = instances.length;
            const healthyCount = safeRows.filter((r) => !!r.health_live && !!r.health_ready).length;
            const downCount = safeRows.filter((r) => !r.health_live).length;

            const stats: StatCard[] = [
              {
                icon: "dns",
                label: "Configured Instances",
                value: configuredCount,
                trend: "",
                tone: "teal",
              },
              {
                icon: "verified",
                label: "Healthy Instances",
                value: healthyCount,
                trend: "live and ready Tritons",
                tone: "sky",
              },
              {
                icon: "shield",
                label: "Alerts",
                value: downCount,
                trend: "down Triton instances",
                tone: "rose",
              },
            ];

            const alerts: AlertItem[] = Array.isArray(alertsRaw) ? alertsRaw : [];

            return dashboardDataLoaded({ instances, alerts, stats });
          }),
          catchError(() => of(dashboardDataLoadFailed())),
        ),
      ),
    ),
  );
}
