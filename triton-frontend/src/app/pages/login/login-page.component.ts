import { Component, computed, effect, inject } from "@angular/core";
import { ActivatedRoute } from "@angular/router";
import { Router } from "@angular/router";
import { FormsModule } from "@angular/forms";
import { Store } from "@ngrx/store";
import { toSignal } from "@angular/core/rxjs-interop";

import { MatCardModule } from "@angular/material/card";
import { MatButtonModule } from "@angular/material/button";
import { MatIconModule } from "@angular/material/icon";
import { MatFormFieldModule } from "@angular/material/form-field";
import { MatInputModule } from "@angular/material/input";

import { AuthStore } from "../../shared/auth/auth.store";
import {
  continueRequested,
  loginPageOpened,
  loginWithOidcRequested,
  loginWithPasswordFailed,
  loginWithPasswordRequested,
  registerModeEnabled,
  registerWithPasswordFailed,
  registerWithPasswordRequested,
} from "../../state/login/login.actions";
import {
  EMAIL_POLICY_MESSAGE,
  isValidEmail,
  isValidPassword,
  PASSWORD_POLICY_MESSAGE,
} from "../../shared/password-policy";
import {
  selectLoginError,
  selectLoginLoading,
  selectLoginNeedsBootstrap,
  selectLoginNotice,
  selectLoginOidcEnabled,
  selectLoginRegisterMode,
} from "../../state/login/login.selectors";

@Component({
  selector: "app-login-page",
  standalone: true,
  imports: [
    FormsModule,
    MatCardModule,
    MatButtonModule,
    MatIconModule,
    MatFormFieldModule,
    MatInputModule,
  ],
  templateUrl: "./login-page.component.html",
  styleUrl: "./login-page.component.scss",
})
export class LoginPageComponent {
  private readonly store = inject(Store);
  private readonly authState = inject(AuthStore);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private autoRedirectIssued = false;

  // Auth session state (lives in AuthStateService, used by guards/interceptors too)
  readonly userName = this.authState.userName;
  readonly isLoggedIn = this.authState.isLoggedIn;
  readonly accessAllowed = this.authState.accessAllowed;

  // Store-backed login page state
  readonly oidcEnabled = toSignal(this.store.select(selectLoginOidcEnabled), {
    initialValue: false,
  });
  readonly needsBootstrap = toSignal(this.store.select(selectLoginNeedsBootstrap), {
    initialValue: false,
  });
  readonly loading = toSignal(this.store.select(selectLoginLoading), { initialValue: false });
  readonly error = toSignal(this.store.select(selectLoginError), { initialValue: "" });
  readonly notice = toSignal(this.store.select(selectLoginNotice), { initialValue: "" });
  readonly registerMode = toSignal(this.store.select(selectLoginRegisterMode), {
    initialValue: false,
  });

  // Computed from store signals + session signals
  readonly isPendingApproval = computed(() => this.isLoggedIn() && !this.accessAllowed());
  readonly isPendingApprovalError = computed(() =>
    this.error().toLowerCase().includes("account pending admin approval"),
  );
  readonly returnUrl = computed(() =>
    this.normalizeReturnUrl(this.route.snapshot.queryParamMap.get("returnUrl")),
  );

  // Local form-only state
  email = "";
  password = "";
  confirmPassword = "";

  constructor() {
    this.store.dispatch(loginPageOpened());
    effect(() => {
      if (this.autoRedirectIssued) return;
      if (!this.isLoggedIn() || !this.accessAllowed()) return;
      this.autoRedirectIssued = true;
      const target = this.returnUrl() || "/dashboard";
      queueMicrotask(() => {
        void this.navigateToTarget(target);
      });
    });
  }

  loginWithPassword(): void {
    if (this.oidcEnabled()) {
      this.store.dispatch(
        loginWithPasswordFailed({ message: "Password login is disabled when OIDC is enabled." }),
      );
      return;
    }
    if (!this.email.trim() || !this.password) {
      this.store.dispatch(loginWithPasswordFailed({ message: "Email and password are required." }));
      return;
    }
    if (!isValidEmail(this.email)) {
      this.store.dispatch(loginWithPasswordFailed({ message: EMAIL_POLICY_MESSAGE }));
      return;
    }
    if (this.needsBootstrap() && !this.confirmPassword) {
      this.store.dispatch(loginWithPasswordFailed({ message: "Please confirm your password." }));
      return;
    }
    if (this.needsBootstrap() && this.password !== this.confirmPassword) {
      this.store.dispatch(loginWithPasswordFailed({ message: "Passwords do not match." }));
      return;
    }
    if (this.needsBootstrap() && !isValidPassword(this.password)) {
      this.store.dispatch(loginWithPasswordFailed({ message: PASSWORD_POLICY_MESSAGE }));
      return;
    }
    this.store.dispatch(
      loginWithPasswordRequested({
        email: this.email.trim(),
        password: this.password,
        needsBootstrap: this.needsBootstrap(),
        returnUrl: this.returnUrl(),
      }),
    );
  }

  registerWithPassword(): void {
    if (this.oidcEnabled()) {
      this.store.dispatch(
        registerWithPasswordFailed({ message: "Registration is disabled when OIDC is enabled." }),
      );
      return;
    }
    if (!this.email.trim() || !this.password || !this.confirmPassword) {
      this.store.dispatch(
        registerWithPasswordFailed({ message: "Email and password are required." }),
      );
      return;
    }
    if (!isValidEmail(this.email)) {
      this.store.dispatch(registerWithPasswordFailed({ message: EMAIL_POLICY_MESSAGE }));
      return;
    }
    if (this.password !== this.confirmPassword) {
      this.store.dispatch(registerWithPasswordFailed({ message: "Passwords do not match." }));
      return;
    }
    if (!isValidPassword(this.password)) {
      this.store.dispatch(registerWithPasswordFailed({ message: PASSWORD_POLICY_MESSAGE }));
      return;
    }
    this.store.dispatch(
      registerWithPasswordRequested({ email: this.email.trim(), password: this.password }),
    );
    this.password = "";
    this.confirmPassword = "";
  }

  enableRegisterMode(): void {
    this.store.dispatch(registerModeEnabled());
  }

  loginWithOidc(): void {
    this.store.dispatch(loginWithOidcRequested());
  }

  continue(): void {
    if (this.isLoggedIn() && !this.accessAllowed()) {
      this.store.dispatch(
        loginWithPasswordFailed({ message: "Your account is pending admin approval." }),
      );
      return;
    }
    const target = this.returnUrl() || "/dashboard";
    void this.navigateToTarget(target);
    this.store.dispatch(continueRequested({ returnUrl: target }));
  }

  private normalizeReturnUrl(raw: string | null): string {
    const fallback = "/dashboard";
    if (!raw) return fallback;
    let value = raw.trim();
    try {
      value = decodeURIComponent(value);
    } catch {
      // keep original when decoding fails
    }
    if (!value.startsWith("/")) {
      value = `/${value}`;
    }
    return value || fallback;
  }

  private async navigateToTarget(target: string): Promise<void> {
    try {
      const ok = await this.router.navigateByUrl(target, { replaceUrl: true });
      if (!ok) {
        window.location.assign(target);
      }
    } catch {
      window.location.assign(target);
    }
  }
}
