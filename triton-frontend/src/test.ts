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
const normalizeMonaco = (value: typeof testGlobal.monaco) => {
  const normalized = {
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

  if (typeof normalized.editor.create !== "function") {
    normalized.editor.create = baseMonaco.editor.create;
  }
  if (typeof normalized.editor.createModel !== "function") {
    normalized.editor.createModel = baseMonaco.editor.createModel;
  }
  if (typeof normalized.editor.getModelMarkers !== "function") {
    normalized.editor.getModelMarkers = baseMonaco.editor.getModelMarkers;
  }
  if (typeof normalized.editor.setModelMarkers !== "function") {
    normalized.editor.setModelMarkers = baseMonaco.editor.setModelMarkers;
  }

  return normalized;
};
const withBaseMonaco = normalizeMonaco;
let monacoMock = withBaseMonaco(testGlobal.monaco);

const syncMonacoGlobals = (value?: unknown) => {
  monacoMock = withBaseMonaco(value as typeof testGlobal.monaco);
  const nextMonaco = withBaseMonaco(monacoMock);

  Object.defineProperty(testGlobal, "monaco", {
    configurable: true,
    enumerable: true,
    get: () => nextMonaco,
    set: (newValue) => {
      monacoMock = withBaseMonaco(newValue as typeof testGlobal.monaco);
    },
  });

  for (const target of [globalThis, window, self] as Array<typeof globalThis>) {
    if (target && target !== testGlobal) {
      Object.defineProperty(target, "monaco", {
        configurable: true,
        enumerable: true,
        get: () => nextMonaco,
        set: (newValue) => {
          monacoMock = withBaseMonaco(newValue as typeof testGlobal.monaco);
        },
      });
    }
  }

  return nextMonaco;
};

syncMonacoGlobals(testGlobal.monaco);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const context = (require as any).context("./", true, /\.spec\.ts$/);
context.keys().forEach(context);
