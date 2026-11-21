import { InstallationService } from "../shared/installation/installation.service";
import { HandoffNotificationsComponent } from "../pages/handoffs/handoff-notifications.component";
import { Component, computed, effect, inject, signal } from "@angular/core";

import { Router, RouterLink, RouterLinkActive, RouterOutlet } from "@angular/router";
import { BreakpointObserver, Breakpoints } from "@angular/cdk/layout";
import { takeUntilDestroyed, toSignal } from "@angular/core/rxjs-interop";
import { map } from "rxjs/operators";
import { interval } from "rxjs";

import { MatSidenavModule } from "@angular/material/sidenav";
import { MatListModule } from "@angular/material/list";
import { MatIconModule } from "@angular/material/icon";
import { MatButtonModule } from "@angular/material/button";
import { MatToolbarModule } from "@angular/material/toolbar";
import { MatMenuModule } from "@angular/material/menu";
import { MatDividerModule } from "@angular/material/divider";

import { AuthStore } from "../shared/auth/auth.store";
import { AuthService } from "../shared/auth/auth.service";
import { ChromeService } from "../shared/chrome.service";
import { WorkspaceDirectory } from "../shared/workspace-directory.service";
import { environment } from "../../environments/environment";
import { Store } from "@ngrx/store";
import { selectDashboardFleetHealthPercentage } from "../state/dashboard/dashboard.selectors";
import { dashboardRefreshRequested } from "../state/dashboard/dashboard.actions";

type NavItem = {
  label: string;
  icon: string;
  path: string;
  disabledReason?: string;
  section?: "Serving" | "Development";
};

@Component({
  selector: "app-shell",
  standalone: true,
  imports: [
    HandoffNotificationsComponent,
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    MatSidenavModule,
    MatListModule,
    MatIconModule,
    MatButtonModule,
    MatToolbarModule,
    MatMenuModule,
    MatDividerModule,
  ],
  styleUrl: "./shell.component.scss",
  templateUrl: "./shell.component.html",
})
export class ShellComponent {
  readonly installation = inject(InstallationService);
  private readonly auth = inject(AuthStore);
  private readonly oidc = inject(AuthService);
  private readonly store = inject(Store);
  private readonly chrome = inject(ChromeService);
  readonly workspaceDirectory = inject(WorkspaceDirectory);
  readonly workspaces = this.workspaceDirectory.workspaces;
  readonly workspaceLinkMatch = {
    paths: "exact",
    queryParams: "exact",
    fragment: "ignored",
    matrixParams: "ignored",
  } as const;
  readonly topbarHidden = this.chrome.topbarHidden;
  readonly userName = this.auth.userName;
  readonly role = this.auth.role;
  readonly isAdmin = this.auth.isAdmin;
  readonly isLoggedIn = this.auth.isLoggedIn;
  readonly canWriteInstances = computed(
    () => this.auth.canWriteInstances() && this.installation.allows("direct_deployment"),
  );
  readonly kubernetesEnabled = signal(false);
  readonly kubernetesCapabilityLoaded = signal(false);

  readonly navItems = computed<NavItem[]>(() => {
    const kubernetesActionDisabledReason = this.kubernetesActionDisabledReason();
    const items: NavItem[] = [
      { label: "Dashboard", icon: "grid_view", path: "/dashboard" },
      { label: "Instances", icon: "dns", path: "/instances", section: "Serving" },
      {
        label: "Add deployment",
        icon: "add",
        path: "/deployments/new",
        section: "Serving",
        disabledReason: kubernetesActionDisabledReason,
      },
      ...(this.installation.allows("development_jobs")
        ? [
            {
              label: "Engineering queue",
              icon: "assignment",
              path: "/preparation",
              section: "Development" as const,
            },
          ]
        : []),
      {
        label: "Workspaces",
        section: "Development",
        icon: "terminal",
        path: "/workspaces",
        disabledReason: kubernetesActionDisabledReason,
      },
      {
        label: "Argo Workflows",
        section: "Development",
        icon: "account_tree",
        path: "/workflows",
        disabledReason: kubernetesActionDisabledReason,
      },
      {
        label: "MLflow",
        section: "Development",
        icon: "insights",
        path: "/mlflow",
        disabledReason: kubernetesActionDisabledReason,
      },
    ];
    return items.filter((item) => {
      if (["/workspaces", "/workflows", "/mlflow"].includes(item.path))
        return this.installation.allows("development_tools");
      if (item.path === "/deployments/new") return this.installation.allows("direct_deployment");
      return true;
    });
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
      if (
        !this.isLoggedIn() ||
        !this.canWriteInstances() ||
        !this.installation.allows("development_tools")
      ) {
        this.workspaceDirectory.clear();
        return;
      }
      if (this.kubernetesEnabled()) void this.workspaceDirectory.refresh().catch(() => undefined);
    });
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
      const options = await this.oidc.getAuthOptions();
      this.kubernetesEnabled.set(!!options.kubernetesEnabled);
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
}
