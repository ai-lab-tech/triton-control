import "zone.js/testing";
import { getTestBed } from "@angular/core/testing";
import {
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting,
} from "@angular/platform-browser-dynamic/testing";

getTestBed().initTestEnvironment(BrowserDynamicTestingModule, platformBrowserDynamicTesting(), {
  teardown: { destroyAfterEach: true },
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const context = (require as any).context("./", true, /\.spec\.ts$/);
context.keys().forEach(context);
