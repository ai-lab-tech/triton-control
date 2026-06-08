import { Injectable, inject } from "@angular/core";
import { isPlatformBrowser } from "@angular/common";
import { PLATFORM_ID } from "@angular/core";
import { HttpClient, HttpErrorResponse } from "@angular/common/http";
import { Router } from "@angular/router";
import { firstValueFrom } from "rxjs";

import { AuthStore } from "./auth.store";
import {
  AuthService as GeneratedAuthService,
  DefaultService,
  LoginRequest,
  LoginResponse,
  OidcSettingsDTO,
  UpdateOidcSettingsRequest,
  UsersService,
  BASE_PATH,
  SelfRegisterRequest,
  BootstrapRegisterRequest,
} from "../../api/generated/index";

export interface OidcSettings {
  oidcEnabled: boolean;
  issuer: string;
  clientId: string;
  clientSecret: string;
  clientSecretConfigured: boolean;
  redirectUri: string;
  scopes: string;
  strictDiscoveryDocumentValidation: boolean;
  caCertificate: string;
  apiBaseUrl: string;
  configSource: "db" | "env";
  kubernetesEnabled: boolean;
}

@Injectable({ providedIn: "root" })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly authApi = inject(GeneratedAuthService);
  private readonly usersApi = inject(UsersService);
  private readonly defaultApi = inject(DefaultService);
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));
  private readonly router = inject(Router);
  private readonly basePath = inject(BASE_PATH, { optional: true }) as string | null;
  private readonly tokenStorageKey = "triton_access_token";
  private readonly authState = inject(AuthStore);
  private logoutTimerId: number | null = null;

  private get apiBaseUrl(): string {
    return `${this.basePath ?? ""}`.trim().replace(/\/$/, "");
  }

  async init(): Promise<void> {
    // SSR-safe: do nothing server-side
    if (!this.isBrowser) return;

    const stored = localStorage.getItem(this.tokenStorageKey);
    if (stored) {
      if (this.isTokenExpired(stored)) {
        localStorage.removeItem(this.tokenStorageKey);
      } else {
        this.authState.setAccessToken(stored);
        this.scheduleAutoLogout(stored);
      }
    }

    // Token first, then optional OIDC session fallback.
    await this.refreshSession().catch(() => {
      // unauthenticated is fine; UI shows login button
    });
  }

  loginWithOidc(): void {
    if (!this.isBrowser) return;
    void this.startOidcLogin();
  }

  login(): void {
    if (!this.isBrowser) return;
    void this.getAuthOptions()
      .then(({ oidcEnabled }) => {
        if (oidcEnabled) {
          this.loginWithOidc();
          return;
        }
        void this.router.navigateByUrl("/signin");
      })
      .catch(() => {
        void this.router.navigateByUrl("/signin");
      });
  }

  private async startOidcLogin(): Promise<void> {
    const { oidcEnabled } = await this.getAuthOptions().catch(() => ({ oidcEnabled: false }));
    if (!oidcEnabled) {
      await this.router.navigateByUrl("/signin");
      return;
    }
    // Optional BFF/OIDC flow starts on backend.
    window.location.href = `${this.apiBaseUrl}/login`;
  }

  async loginWithPassword(email: string, password: string): Promise<void> {
    try {
      const res = (await firstValueFrom(
        this.usersApi.loginEndpointApiAuthLoginPost({ email, password } as LoginRequest),
      )) as LoginResponse;

      const token = res.access_token ?? "";
      if (!token) {
        throw new Error("Missing token in login response");
      }

      this.authState.setAuthenticatedUser({
        name: res.user?.name ?? email,
        email: res.user?.email ?? email,
        role: res.user?.role ?? "User",
        authProvider: (res.user?.auth_provider ?? "local") as "local" | "oidc",
        accessToken: token,
        accessAllowed: res.user?.is_active !== false,
      });
      localStorage.setItem(this.tokenStorageKey, token);
      this.scheduleAutoLogout(token);
    } catch (error: unknown) {
      if (error instanceof HttpErrorResponse) {
        const detail = (error.error?.detail as string | undefined) ?? "";
        if (detail) {
          throw new Error(detail);
        }
      }
      throw error;
    }
  }

  async registerWithPassword(email: string, password: string, name?: string): Promise<void> {
    try {
      const selfRegister: SelfRegisterRequest = {
        email: email,
        password: password,
        name: name,
      };
      await firstValueFrom(this.usersApi.selfRegisterEndpointApiAuthSelfRegisterPost(selfRegister));
    } catch (error: unknown) {
      if (error instanceof HttpErrorResponse) {
        const detail = (error.error?.detail as string | undefined) ?? "";
        if (detail) {
          throw new Error(detail);
        }
      }
      throw error;
    }
  }

  logout(): void {
    if (!this.isBrowser) return;

    this.clearLocalSession();

    // Also clear backend session cookie (if OIDC session was used).
    void this.authApi.logoutLogoutPost().subscribe({
      next: () => void 0,
      error: () => void 0,
    });
  }

  clearLocalSession(): void {
    if (!this.isBrowser) return;
    this.clearLogoutTimer();
    this.authState.logout();
    localStorage.removeItem(this.tokenStorageKey);
  }

  getAccessToken(): string {
    const token = this.authState.accessToken();
    if (!token) return "";
    if (this.isTokenExpired(token)) {
      this.clearLocalSession();
      return "";
    }
    return token;
  }

  async getAuthOptions(): Promise<{ oidcEnabled: boolean }> {
    const res = (await firstValueFrom(this.usersApi.authOptionsEndpointApiAuthOptionsGet())) as {
      oidc_enabled?: boolean;
    };
    return { oidcEnabled: !!res?.oidc_enabled };
  }

  async getBootstrapStatus(): Promise<{ oidcEnabled: boolean; needsSetup: boolean }> {
    const res = (await firstValueFrom(
      this.usersApi.bootstrapStatusEndpointApiAuthBootstrapStatusGet(),
    )) as {
      oidc_enabled?: boolean;
      needs_setup?: boolean;
    };
    return {
      oidcEnabled: !!res?.oidc_enabled,
      needsSetup: !!res?.needs_setup,
    };
  }

  async registerFirstUser(email: string, password: string): Promise<void> {
    const bootstrapRegister: BootstrapRegisterRequest = {
      email: email,
      password: password,
    };
    await firstValueFrom(
      this.usersApi.bootstrapRegisterEndpointApiAuthBootstrapRegisterPost(bootstrapRegister),
    );
  }

  async getOidcSettings(): Promise<OidcSettings> {
    const res = (await firstValueFrom(
      this.usersApi.getOidcSettingsEndpointApiAuthSettingsGet(),
    )) as OidcSettingsDTO & {
      client_secret?: string;
      client_secret_configured?: boolean;
      ca_certificate?: string;
      config_source?: "db" | "env";
      kubernetes_enabled?: boolean;
    };

    return {
      oidcEnabled: !!res?.oidc_enabled,
      issuer: res?.issuer ?? "",
      clientId: res?.client_id ?? "",
      clientSecret: res?.client_secret ?? "",
      clientSecretConfigured: !!res?.client_secret_configured,
      redirectUri: res?.redirect_uri ?? "",
      scopes: res?.scopes ?? "",
      strictDiscoveryDocumentValidation: !!res?.strict_discovery_document_validation,
      caCertificate: res?.ca_certificate ?? "",
      apiBaseUrl: res?.api_base_url ?? "",
      configSource: res?.config_source === "env" ? "env" : "db",
      kubernetesEnabled: !!res?.kubernetes_enabled,
    };
  }

  async saveOidcSettings(settings: OidcSettings): Promise<OidcSettings | null> {
    const payload: UpdateOidcSettingsRequest = {
      oidc_enabled: settings.oidcEnabled,
      issuer: settings.issuer,
      client_id: settings.clientId,
      client_secret: settings.clientSecret,
      redirect_uri: settings.redirectUri,
      scopes: settings.scopes,
      strict_discovery_document_validation: settings.strictDiscoveryDocumentValidation,
      ca_certificate: settings.caCertificate,
      api_base_url: settings.apiBaseUrl,
    };

    try {
      const res = (await firstValueFrom(
        this.usersApi.putOidcSettingsEndpointApiAuthSettingsPut(payload),
      )) as (OidcSettingsDTO & {
        client_secret?: string;
        client_secret_configured?: boolean;
        ca_certificate?: string;
        config_source?: "db" | "env";
        kubernetes_enabled?: boolean;
      }) & {
        preflight_required?: boolean;
        authorize_url?: string;
      };

      if (res?.preflight_required && res?.authorize_url) {
        if (this.isBrowser) {
          window.location.href = res.authorize_url;
        }
        return null;
      }

      return {
        oidcEnabled: !!res?.oidc_enabled,
        issuer: res?.issuer ?? "",
        clientId: res?.client_id ?? "",
        clientSecret: res?.client_secret ?? "",
        clientSecretConfigured: !!res?.client_secret_configured,
        redirectUri: res?.redirect_uri ?? "",
        scopes: res?.scopes ?? "",
        strictDiscoveryDocumentValidation: !!res?.strict_discovery_document_validation,
        caCertificate: res?.ca_certificate ?? "",
        apiBaseUrl: res?.api_base_url ?? "",
        configSource: res?.config_source === "env" ? "env" : "db",
        kubernetesEnabled: !!res?.kubernetes_enabled,
      };
    } catch (error: unknown) {
      if (error instanceof HttpErrorResponse) {
        const detail = (error.error?.detail as string | undefined) ?? "";
        if (detail) {
          throw new Error(detail);
        }
      }
      throw error;
    }
  }

  async refreshSession(): Promise<void> {
    if (!this.isBrowser) return;

    const token = this.authState.accessToken();
    try {
      const me = (await firstValueFrom(this.defaultApi.authMeApiAuthMeGet())) as {
        authenticated?: boolean;
        access_allowed?: boolean;
        user?: { name?: string; email?: string; role?: string; auth_provider?: "local" | "oidc" };
      };
      if (!me?.authenticated) {
        this.authState.logout();
        localStorage.removeItem(this.tokenStorageKey);
        return;
      }

      const user = me.user ?? {};
      const name = user.name || user.email || "User";
      const accessAllowed = me.access_allowed !== false;
      const inferredProvider = user.auth_provider
        ? (user.auth_provider as "local" | "oidc")
        : token
          ? "local"
          : "oidc";
      const sessionLooksOidc = inferredProvider === "oidc";
      if (sessionLooksOidc && token) {
        localStorage.removeItem(this.tokenStorageKey);
        this.authState.setAccessToken("");
      }
      this.authState.setAuthenticatedUser({
        name,
        email: user.email,
        role: user.role ?? "User",
        authProvider: inferredProvider,
        accessToken: sessionLooksOidc ? "" : token,
        accessAllowed,
      });
      if (token && !sessionLooksOidc) {
        this.scheduleAutoLogout(token);
      }
      return;
    } catch {
      const { oidcEnabled } = await this.getAuthOptions().catch(() => ({ oidcEnabled: false }));
      if (!oidcEnabled) {
        this.authState.logout();
        localStorage.removeItem(this.tokenStorageKey);
        throw new Error("Not authenticated");
      }
      try {
        const oidc = await this.getOidcSession();
        if (!oidc.authenticated) {
          throw new Error("No OIDC session");
        }
        const user = oidc.user ?? {};
        const name = user.name || user.preferred_username || user.email || "User";
        const role = user.role ?? "viewer";
        const accessAllowed = user.access_allowed !== false;
        this.authState.setAuthenticatedUser({
          name,
          email: user.email,
          role,
          authProvider: "oidc",
          accessAllowed,
        });
        return;
      } catch {
        this.authState.logout();
        localStorage.removeItem(this.tokenStorageKey);
        throw new Error("Not authenticated");
      }
    }
  }

  async getOidcSession(): Promise<{
    authenticated: boolean;
    user?: {
      name?: string;
      preferred_username?: string;
      email?: string;
      role?: string;
      access_allowed?: boolean;
      auth_provider?: string;
    };
  }> {
    const res = (await firstValueFrom(this.authApi.whoamiApiWhoamiGet())) as {
      authenticated?: boolean;
      user?: {
        name?: string;
        preferred_username?: string;
        email?: string;
        role?: string;
        access_allowed?: boolean;
        auth_provider?: string;
      };
    };
    return { authenticated: !!res?.authenticated, user: res?.user };
  }

  private parseJwtExp(token: string): number | null {
    try {
      const payload = JSON.parse(atob(token.split(".")[1] ?? ""));
      const exp = Number(payload?.exp);
      return Number.isFinite(exp) ? exp : null;
    } catch {
      return null;
    }
  }

  private isTokenExpired(token: string): boolean {
    const exp = this.parseJwtExp(token);
    if (!exp) return true;
    const nowSec = Math.floor(Date.now() / 1000);
    return exp <= nowSec;
  }

  private clearLogoutTimer(): void {
    if (this.logoutTimerId !== null && this.isBrowser) {
      window.clearTimeout(this.logoutTimerId);
    }
    this.logoutTimerId = null;
  }

  private scheduleAutoLogout(token: string): void {
    if (!this.isBrowser) return;
    this.clearLogoutTimer();
    const exp = this.parseJwtExp(token);
    if (!exp) return;
    const delayMs = exp * 1000 - Date.now();
    if (delayMs <= 0) {
      this.clearLocalSession();
      if (this.router.url !== "/signin") {
        void this.router.navigate(["/signin"], {
          queryParams:
            this.router.url && this.router.url !== "/" ? { returnUrl: this.router.url } : undefined,
        });
      }
      return;
    }
    this.logoutTimerId = window.setTimeout(() => {
      this.clearLocalSession();
      if (this.router.url !== "/signin") {
        void this.router.navigate(["/signin"], {
          queryParams:
            this.router.url && this.router.url !== "/" ? { returnUrl: this.router.url } : undefined,
        });
      }
    }, delayMs);
  }
}
