import { Component, computed, effect, inject, signal } from "@angular/core";
import { DatePipe } from "@angular/common";
import { RouterLink } from "@angular/router";
import { MatButtonModule } from "@angular/material/button";
import { MatMenuModule } from "@angular/material/menu";
import { MatIconModule } from "@angular/material/icon";
import { MatBadgeModule } from "@angular/material/badge";
import { firstValueFrom, timer } from "rxjs";
import { AuthStore } from "../../shared/auth/auth.store";
import { HandoffsApiService, HandoffNotification } from "./handoffs-api.service";

@Component({
  selector: "app-handoff-notifications",
  standalone: true,
  imports: [DatePipe, RouterLink, MatButtonModule, MatMenuModule, MatIconModule, MatBadgeModule],
  template: `@if (allowed()) {
    <button
      mat-icon-button
      [matMenuTriggerFor]="menu"
      (menuOpened)="refresh()"
      aria-label="Engineering notifications"
      [matBadge]="unread() >= 100 ? '99+' : unread()"
      [matBadgeHidden]="!unread()"
      matBadgeSize="small"
    >
      <mat-icon>notifications_none</mat-icon>
    </button>
    <mat-menu #menu="matMenu"
      ><div class="heading">Engineering notifications</div>
      @if (failed()) {
        <button mat-menu-item (click)="refresh()">Could not load notifications. Retry</button>
      } @else if (!items().length) {
        <div class="empty">No handoff notifications.</div>
      }
      @for (item of items(); track item.id) {
        <a
          mat-menu-item
          [routerLink]="['/preparation/handoffs', item.handoff_id]"
          (click)="markRead(item)"
          [class.unread]="!item.read_at"
          ><mat-icon>{{ item.read_at ? "drafts" : "mark_email_unread" }}</mat-icon
          ><span>Model handoff · {{ item.created_at | date: "short" }}</span></a
        >
      }
    </mat-menu>
  }`,
  styles: [
    `
      .heading,
      .empty {
        padding: 12px 16px;
        color: #475569;
      }
      .heading {
        font-weight: 650;
      }
      .unread {
        font-weight: 650;
      }
    `,
  ],
})
export class HandoffNotificationsComponent {
  private readonly api = inject(HandoffsApiService);
  private readonly auth = inject(AuthStore);
  readonly allowed = this.auth.canWriteInstances;
  readonly items = signal<HandoffNotification[]>([]);
  readonly failed = signal(false);
  readonly unread = computed(() => this.items().filter((item) => !item.read_at).length);
  private loading = false;
  constructor() {
    effect((cleanup) => {
      const email = this.auth.userEmail();
      if (!this.allowed()) {
        this.items.set([]);
        return;
      }
      const subscription = timer(0, 30000).subscribe(() => {
        if (email === this.auth.userEmail()) void this.refresh();
      });
      cleanup(() => {
        subscription.unsubscribe();
        this.items.set([]);
      });
    });
  }
  async refresh() {
    if (this.loading || !this.allowed()) return;
    const email = this.auth.userEmail();
    this.loading = true;
    try {
      const items = await firstValueFrom(this.api.notifications());
      if (email === this.auth.userEmail() && this.allowed()) {
        this.items.set(items);
        this.failed.set(false);
      }
    } catch {
      if (email === this.auth.userEmail()) this.failed.set(true);
    } finally {
      this.loading = false;
    }
  }
  async markRead(item: HandoffNotification) {
    try {
      const updated = await firstValueFrom(this.api.markRead(item.id));
      this.items.update((items) =>
        items.map((value) => (value.id === updated.id ? updated : value)),
      );
    } catch {
      this.failed.set(true);
    }
  }
}
