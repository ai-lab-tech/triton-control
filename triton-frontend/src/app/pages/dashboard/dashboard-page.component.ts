import { Component, effect, inject } from "@angular/core";

import { MatCardModule } from "@angular/material/card";
import { MatButtonModule } from "@angular/material/button";
import { MatIconModule } from "@angular/material/icon";
import { MatTableModule } from "@angular/material/table";
import { MatChipsModule } from "@angular/material/chips";
import { RouterLink } from "@angular/router";

import { Store } from "@ngrx/store";
import { toSignal } from "@angular/core/rxjs-interop";

import { StatCardComponent } from "../../shared/stat-card/stat-card.component";
import { AuthStore } from "../../shared/auth/auth.store";
import { AuthService } from "../../shared/auth/auth.service";
import { dashboardPageOpened, dashboardReset } from "../../state/dashboard/dashboard.actions";
import {
  selectDashboardAlerts,
  selectDashboardInstances,
  selectDashboardStats,
} from "../../state/dashboard/dashboard.selectors";
import {
  type InstanceRow,
  type AlertItem,
  type StatCard,
} from "../../state/dashboard/dashboard.reducer";

@Component({
  selector: "app-dashboard-page",
  standalone: true,
  imports: [
    MatCardModule,
    MatButtonModule,
    MatIconModule,
    MatTableModule,
    MatChipsModule,
    RouterLink,
    StatCardComponent,
  ],
  styleUrl: "./dashboard-page.component.scss",
  templateUrl: "./dashboard-page.component.html",
})
export class DashboardPageComponent {
  readonly auth = inject(AuthStore);
  private readonly store = inject(Store);
  private readonly oidc = inject(AuthService);

  readonly stats = toSignal(this.store.select(selectDashboardStats), {
    initialValue: [] as StatCard[],
  });
  readonly instances = toSignal(this.store.select(selectDashboardInstances), {
    initialValue: [] as InstanceRow[],
  });
  readonly alerts = toSignal(this.store.select(selectDashboardAlerts), {
    initialValue: [] as AlertItem[],
  });

  readonly displayedColumns = ["name", "url", "models", "createdAt"];

  constructor() {
    // Reset + reload whenever login state changes
    effect(() => {
      if (!this.auth.isLoggedIn()) {
        this.store.dispatch(dashboardReset());
        return;
      }
      this.store.dispatch(dashboardPageOpened());
    });
  }

  login(): void {
    this.oidc.login();
  }
}
