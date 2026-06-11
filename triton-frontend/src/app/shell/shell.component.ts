import { Component, computed, effect, inject, signal } from "@angular/core";

import { Router, RouterLink, RouterLinkActive, RouterOutlet } from "@angular/router";
import { BreakpointObserver, Breakpoints } from "@angular/cdk/layout";
import { takeUntilDestroyed, toSignal } from "@angular/core/rxjs-interop";
import { map } from "rxjs/operators";
import { interval } from "rxjs";

import { MatSidenavModule } from "@angular/material/sidenav";
import { MatToolbarModule } from "@angular/material/toolbar";
import { MatListModule } from "@angular/material/list";
import { MatIconModule } from "@angular/material/icon";
import { MatButtonModule } from "@angular/material/button";
import { MatMenuModule } from "@angular/material/menu";
import { MatDividerModule } from "@angular/material/divider";
import { MatDialog, MatDialogModule } from "@angular/material/dialog";

import { NewInstanceDialogComponent } from "../pages/instances/new-instance-dialog/new-instance-dialog.component";
import { AuthStore } from "../shared/auth/auth.store";
import { AuthService } from "../shared/auth/auth.service";
import { environment } from "../../environments/environment";
import { Store } from "@ngrx/store";
import { selectDashboardFleetHealthPercentage } from "../state/dashboard/dashboard.selectors";
import { dashboardRefreshRequested } from "../state/dashboard/dashboard.actions";

type NavItem = {
  label: string;
  icon: string;
  path: string;
  disabledReason?: string;
};

@Component({
  selector: "app-shell",
  standalone: true,
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    MatSidenavModule,
    MatToolbarModule,
    MatListModule,
    MatIconModule,
    MatButtonModule,
    MatMenuModule,
    MatDividerModule,
    MatDialogModule,
  ],
  styleUrl: "./shell.component.scss",
  templateUrl: "./shell.component.html",
})
export class ShellComponent {
  private readonly auth = inject(AuthStore);
  private readonly oidc = inject(AuthService);
  private readonly store = inject(Store);
  readonly userName = this.auth.userName;
  readonly role = this.auth.role;
  readonly isAdmin = this.auth.isAdmin;
  readonly isLoggedIn = this.auth.isLoggedIn;
  readonly canWriteInstances = this.auth.canWriteInstances;
  readonly kubernetesEnabled = signal(false);
  readonly kubernetesCapabilityLoaded = signal(false);

  readonly showAdminMenu = computed(() => true);

  readonly navItems = computed<NavItem[]>(() => {
    const kubernetesActionDisabledReason = this.kubernetesActionDisabledReason();
    const items: NavItem[] = [
      { label: "Dashboard", icon: "grid_view", path: "/dashboard" },
      { label: "Triton Instances", icon: "dns", path: "/instances" },
      {
        label: "Code Servers",
        icon: "terminal",
        path: "/code-servers",
        disabledReason: kubernetesActionDisabledReason,
      },
      {
        label: "Add Deployment",
        icon: "rocket_launch",
        path: "/deployments/new",
        disabledReason: kubernetesActionDisabledReason,
      },
      {
        label: "Perf Analyzer",
        icon: "speed",
        path: "/perf-analyzers",
        disabledReason: kubernetesActionDisabledReason,
      },
    ];
    return items;
  });

  readonly kubernetesActionDisabledReason = computed(() => {
    if (!this.canWriteInstances()) {
      return "Requires member or admin access.";
    }
    if (!this.kubernetesCapabilityLoaded()) {
      return "Checking Kubernetes capability.";
    }
    if (!this.kubernetesEnabled()) {
      return "Available when Triton Control itself runs in Kubernetes.";
    }
    return undefined;
  });

  private readonly router = inject(Router);
  private readonly breakpointObserver = inject(BreakpointObserver);
  private readonly dialog = inject(MatDialog);
  private readonly fleetHealthPollingIntervalMs = environment.instancePollingIntervalMs ?? 10000;
  readonly appVersion = environment.appVersion;

  readonly isHandset = toSignal(
    this.breakpointObserver.observe(Breakpoints.Handset).pipe(map((state) => state.matches)),
    { initialValue: false },
  );

  readonly navOpen = signal(false);
  readonly navOpened = computed(() => (this.isHandset() ? this.navOpen() : true));
  readonly navCollapsed = signal(false);
  readonly adminMenuOpen = signal(false);
  readonly fleetHealthPercentage = toSignal(
    this.store.select(selectDashboardFleetHealthPercentage),
    { initialValue: null as number | null },
  );
  readonly fleetHealthLabel = computed(() => {
    const percentage = this.fleetHealthPercentage();
    if (!this.isLoggedIn()) {
      return "Fleet health n/a";
    }
    return percentage == null ? "Fleet health loading..." : `Fleet health ${percentage}%`;
  });

  readonly initials = computed(() => {
    const parts = this.userName().trim().split(/\s+/);
    return (parts[0]?.[0] ?? "A") + (parts[1]?.[0] ?? "B");
  });

  constructor() {
    effect(() => {
      if (!this.isLoggedIn()) {
        this.kubernetesEnabled.set(false);
        this.kubernetesCapabilityLoaded.set(false);
        return;
      }
      void this.refreshKubernetesCapability();
    });

    interval(this.fleetHealthPollingIntervalMs)
      .pipe(takeUntilDestroyed())
      .subscribe(() => {
        if (this.isLoggedIn()) {
          this.store.dispatch(dashboardRefreshRequested());
          if (!this.kubernetesCapabilityLoaded()) {
            void this.refreshKubernetesCapability();
          }
        }
      });
  }

  private async refreshKubernetesCapability(): Promise<void> {
    try {
      const settings = await this.oidc.getOidcSettings();
      this.kubernetesEnabled.set(!!settings.kubernetesEnabled);
      this.kubernetesCapabilityLoaded.set(true);
    } catch (error) {
      this.kubernetesEnabled.set(false);
      this.kubernetesCapabilityLoaded.set(false);
      // Keep a lightweight signal in dev tools when capability fetch fails.
      console.warn("Failed to load kubernetes capability for shell", error);
    }
  }

  toggleNav() {
    if (this.isHandset()) {
      this.navOpen.update((open: boolean) => !open);
      return;
    }
    this.navCollapsed.update((collapsed: boolean) => !collapsed);
  }

  closeNavOnMobile() {
    if (this.isHandset()) {
      this.navOpen.set(false);
    }
  }

  go(path: string) {
    this.closeNavOnMobile();
    this.adminMenuOpen.set(false);
    void this.router.navigateByUrl(path);
  }

  toggleAdminMenu() {
    this.adminMenuOpen.update((open: boolean) => !open);
  }

  closeAdminMenu() {
    this.adminMenuOpen.set(false);
  }

  login() {
    this.oidc.login();
    this.adminMenuOpen.set(false);
  }

  logout() {
    this.oidc.logout();
    this.adminMenuOpen.set(false);
    void this.router.navigateByUrl("/signin");
  }

  openNewInstanceDialog() {
    if (!this.canWriteInstances()) {
      return;
    }
    this.dialog.open(NewInstanceDialogComponent, {
      width: "420px",
      panelClass: "custom-dialog",
    });
  }
}
