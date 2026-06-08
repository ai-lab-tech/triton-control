import { Component, computed, DestroyRef, inject, OnInit, signal } from "@angular/core";
import { takeUntilDestroyed, toSignal } from "@angular/core/rxjs-interop";
import { filter, interval } from "rxjs";

import { FormsModule } from "@angular/forms";
import { NavigationEnd, Router, RouterLink } from "@angular/router";
import { MatCardModule } from "@angular/material/card";
import { MatButtonModule } from "@angular/material/button";
import { MatIconModule } from "@angular/material/icon";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";
import { MatSelectModule } from "@angular/material/select";
import { Store } from "@ngrx/store";

import { environment } from "../../../environments/environment";
import { AuthStore } from "../../shared/auth/auth.store";
import {
  deleteInstanceRequested,
  instancesListPageOpened,
  instancesListRefreshRequested,
} from "../../state/instances-list/instances-list.actions";
import {
  selectInstances,
  selectInstancesListLoading,
} from "../../state/instances-list/instances-list.selectors";
import {
  selectActiveRunKey,
  selectProfileRunning,
} from "../../state/instances-profile/instances-profile.selectors";
import { isSelfDeployedStarting } from "../../state/instances.utils";
import { type Instance } from "./instances.data";

@Component({
  selector: "app-instances-page",
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    MatCardModule,
    MatButtonModule,
    MatIconModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  styleUrl: "./instances-page.component.scss",
  templateUrl: "./instances-page.component.html",
})
export class InstancesPageComponent implements OnInit {
  private readonly store = inject(Store);
  private readonly auth = inject(AuthStore);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly pollingIntervalMs = environment.instancePollingIntervalMs ?? 10000;

  private readonly _query = signal("");
  private readonly _status = signal("all");

  get query(): string {
    return this._query();
  }
  set query(v: string) {
    this._query.set(v);
  }

  get status(): string {
    return this._status();
  }
  set status(v: string) {
    this._status.set(v);
  }

  readonly instances = toSignal(this.store.select(selectInstances), { initialValue: [] });
  readonly loading = toSignal(this.store.select(selectInstancesListLoading), {
    initialValue: false,
  });
  readonly perfProfileRunning = toSignal(this.store.select(selectProfileRunning), {
    initialValue: false,
  });
  readonly activeProfileRunKey = toSignal(this.store.select(selectActiveRunKey), {
    initialValue: "",
  });
  readonly canDeleteInstances = this.auth.isAdmin;
  readonly activeProfileRunLabel = computed(() => {
    if (!this.perfProfileRunning() || !this.activeProfileRunKey()) {
      return "";
    }
    const parts = this.activeProfileRunKey().split(":");
    if (parts.length < 3) {
      return "Perf Analyzer run in progress";
    }
    const runVersion = parts[parts.length - 1];
    const runModel = parts.slice(1, -1).join(":");
    return `Perf Analyzer running for ${runModel}:${runVersion}`;
  });

  readonly filteredInstances = computed(() => {
    const q = this._query().trim().toLowerCase();
    const status = this._status();
    return this.instances().filter((instance) => {
      const matchesQuery =
        !q || instance.name.toLowerCase().includes(q) || instance.region.toLowerCase().includes(q);
      const matchesStatus = status === "all" || instance.status === status;
      return matchesQuery && matchesStatus;
    });
  });

  constructor() {}

  ngOnInit(): void {
    this.store.dispatch(instancesListPageOpened());

    interval(this.pollingIntervalMs)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        this.store.dispatch(instancesListRefreshRequested());
      });

    this.router.events
      .pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        if (this.router.url.startsWith("/instances")) {
          this._query.set("");
          this._status.set("all");
          this.store.dispatch(instancesListRefreshRequested());
        }
      });
  }

  refresh(): void {
    this.store.dispatch(instancesListRefreshRequested());
  }

  deploymentStarting(instance: Instance): boolean {
    return isSelfDeployedStarting(instance);
  }

  deleteInstance(instanceId: string, instanceName: string, isSelfDeployed = false): void {
    if (!this.canDeleteInstances()) {
      return;
    }
    const confirmed = window.confirm(
      isSelfDeployed
        ? `Delete self-deployed Triton instance "${instanceName}"? This also runs Kubernetes cleanup.`
        : `Delete Triton instance "${instanceName}"? This removes it from Triton Control.`,
    );
    if (!confirmed) {
      return;
    }
    this.store.dispatch(deleteInstanceRequested({ instanceId, instanceName, isSelfDeployed }));
  }
}
