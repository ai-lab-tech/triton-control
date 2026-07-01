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
const testGlobal = globalThis as typeof globalThis & { monaco?: any };
const monacoEditorStub = {
  dispose: () => undefined,
  focus: () => undefined,
  getModel: () => null,
  getPosition: () => null,
  getValue: () => "",
  layout: () => undefined,
  onDidChangeModelContent: () => ({ dispose: () => undefined }),
  setModel: () => undefined,
  setPosition: () => undefined,
  setValue: () => undefined,
  updateOptions: () => undefined,
};
const baseMonaco = {
  editor: {
    create: () => monacoEditorStub,
    createModel: () => ({ dispose: () => undefined, uri: "file://test" }),
    getModelMarkers: () => [],
    setModelMarkers: () => undefined,
  },
  MarkerSeverity: { Error: 8, Hint: 1, Info: 2, Warning: 4 },
  Uri: {
    file: (path: string) => ({ path, toString: () => `file://${path}` }),
    parse: (value: string) => ({ path: value, toString: () => value }),
  },
};
let monacoMock = baseMonaco;

Object.defineProperty(testGlobal, "monaco", {
  configurable: true,
  get: () => monacoMock,
  set: (value) => {
    monacoMock = {
      ...baseMonaco,
      ...value,
      editor: {
        ...baseMonaco.editor,
        ...value?.editor,
      },
      MarkerSeverity: {
        ...baseMonaco.MarkerSeverity,
        ...value?.MarkerSeverity,
      },
      Uri: {
        ...baseMonaco.Uri,
        ...value?.Uri,
      },
    };
  },
});
testGlobal.monaco = testGlobal.monaco;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const context = (require as any).context("./", true, /\.spec\.ts$/);
context.keys().forEach(context);
