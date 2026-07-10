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
  onDidBlurEditorWidget: () => ({ dispose: () => undefined }),
  onDidChangeModelContent: () => ({ dispose: () => undefined }),
  setModel: () => undefined,
  setPosition: () => undefined,
  setTheme: () => undefined,
  setValue: () => undefined,
  updateOptions: () => undefined,
};
const baseMonaco = {
  editor: {
    create: () => monacoEditorStub,
    createDiffEditor: () => monacoEditorStub,
    createModel: () => ({ dispose: () => undefined, uri: "file://test" }),
    getModel: () => null,
    getModelMarkers: () => [],
    setModelMarkers: () => undefined,
    setTheme: () => undefined,
  },
  MarkerSeverity: { Error: 8, Hint: 1, Info: 2, Warning: 4 },
  Uri: {
    file: (path: string) => ({ path, toString: () => `file://${path}` }),
    parse: (value: string) => ({ path: value, toString: () => value }),
  },
};

const normalizeMonacoEditor = (value: typeof testGlobal.monaco) =>
  new Proxy(
    {
      ...baseMonaco.editor,
      ...value,
    },
    {
      get(target, prop: keyof typeof baseMonaco.editor) {
        const current = target[prop];
        const fallback = baseMonaco.editor[prop];
        if (typeof fallback === "function" && typeof current !== "function") {
          return fallback;
        }
        return current;
      },
    },
  );

const normalizeMonaco = (value: typeof testGlobal.monaco) => {
  const normalized = {
    ...baseMonaco,
    ...value,
    MarkerSeverity: {
      ...baseMonaco.MarkerSeverity,
      ...value?.MarkerSeverity,
    },
    Uri: {
      ...baseMonaco.Uri,
      ...value?.Uri,
    },
  };

  let editor = normalizeMonacoEditor(value?.editor);
  Object.defineProperty(normalized, "editor", {
    configurable: true,
    enumerable: true,
    get: () => editor,
    set: (newValue) => {
      editor = normalizeMonacoEditor(newValue);
    },
  });

  return normalized;
};
const withBaseMonaco = normalizeMonaco;
let monacoMock = withBaseMonaco(testGlobal.monaco);

const currentMonaco = () => {
  monacoMock = withBaseMonaco(monacoMock);
  return monacoMock;
};

const syncMonacoGlobals = (value?: unknown) => {
  monacoMock = withBaseMonaco(value as typeof testGlobal.monaco);

  Object.defineProperty(testGlobal, "monaco", {
    configurable: true,
    enumerable: true,
    get: currentMonaco,
    set: (newValue) => {
      monacoMock = withBaseMonaco(newValue as typeof testGlobal.monaco);
    },
  });

  for (const target of [globalThis, window, self] as Array<typeof globalThis>) {
    if (target && target !== testGlobal) {
      Object.defineProperty(target, "monaco", {
        configurable: true,
        enumerable: true,
        get: currentMonaco,
        set: (newValue) => {
          monacoMock = withBaseMonaco(newValue as typeof testGlobal.monaco);
        },
      });
    }
  }

  return monacoMock;
};

syncMonacoGlobals(testGlobal.monaco);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const context = (require as any).context("./", true, /\.spec\.ts$/);
context.keys().forEach(context);
