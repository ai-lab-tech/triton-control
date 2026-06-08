import { provideZoneChangeDetection } from "@angular/core";
import { provideServerRendering } from "@angular/platform-server";
import { bootstrapApplication } from "@angular/platform-browser";
import { provideAnimationsAsync } from "@angular/platform-browser/animations/async";
import { provideRouter, withInMemoryScrolling } from "@angular/router";

import { AppComponent } from "./app/app.component";
import { APP_ROUTES } from "./app/app.routes";

const bootstrap = () =>
  bootstrapApplication(AppComponent, {
    providers: [
      provideZoneChangeDetection(),
      provideServerRendering(),
      provideAnimationsAsync(),
      provideRouter(APP_ROUTES, withInMemoryScrolling({ scrollPositionRestoration: "top" })),
    ],
  });

export default bootstrap;
