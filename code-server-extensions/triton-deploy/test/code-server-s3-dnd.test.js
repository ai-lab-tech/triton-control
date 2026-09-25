const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { s3DragCopy, patchWorkbench } = require("../code-server-s3-dnd");
const item = (scheme, authority = "", extra = {}) => ({ resource: { scheme, authority }, ...extra });
const local = item("vscode-remote", "workspace", { isDirectory: true });
const s3 = item("triton-s3", "profile-1", { isDirectory: true });
const other = item("triton-s3", "profile-2", { isDirectory: true });

test("Windows defaults and modifier keys agree for every S3 direction", () => {
  for (const [source, destination, copy] of [[local, s3, true], [s3, local, true],
    [s3, other, true], [other, s3, true], [s3, s3, false]]) {
    assert.equal(s3DragCopy({}, [source], destination, false), copy);
    assert.equal(s3DragCopy({ ctrlKey: true }, [source], destination, true), true);
    assert.equal(s3DragCopy({ shiftKey: true }, [source], destination, false), false);
    assert.equal(s3DragCopy({ altKey: true }, [source], destination, true), true);
  }
  assert.equal(s3DragCopy({}, [s3, local], s3, false), true);
  assert.equal(s3DragCopy({}, [local], { parent: s3 }, false), true);
});

test("ordinary workspace transfers, root rearrangement, and external drags retain native behavior", () => {
  for (const fallback of [false, true]) {
    for (const [items, target] of [[[local], local], [[{ ...s3, isRoot: true }], other],
      [[], s3], [undefined, s3], [[local], undefined]])
      assert.equal(s3DragCopy({}, items, target, fallback), fallback);
  }
});

// Exercise the generated methods, not just the standalone policy. The anchors
// intentionally pin the supported upstream bundle; a bundle change fails closed.
const source = `class Test {
handleDragOver(o,e,t,i,n){let r=n&&(n.ctrlKey&&!ht||n.altKey&&ht),s=o instanceof cK;return r}
drop(items,e,n){const s=new Map(items.map(x=>[x,true]));let l=i5e([...s.keys()],m=>m.resource),c=n.ctrlKey&&!ht||n.altKey&&ht;return c}
}`;
test("patch updates both drag feedback and drop action, is idempotent, and rejects incompatible bundles", () => {
  const browserSource = source.replaceAll("cK", "uK").replaceAll("i5e", "e5e");
  assert.equal(patchWorkbench(patchWorkbench(browserSource)), patchWorkbench(browserSource));
  const patched = patchWorkbench(source);
  assert.equal(patchWorkbench(patched), patched);
  assert.throws(() => patchWorkbench(source.replace("handleDragOver", "changed")), /Unsupported/);
  assert.throws(() => patchWorkbench(source + source), /Unsupported/);
  assert.throws(() => patchWorkbench(patched.replace("c=this.tritonS3DragCopy", "c=broken")), /Incomplete/);
  const controller = vm.runInNewContext(patched + ";new Test()", {
    ht: false, cK: class {}, y_: { getStatsFromDragAndDropData: x => x.elements }, i5e: x => x,
  });
  for (const [from, to] of [[local, s3], [s3, local], [s3, s3], [s3, other]]) {
    for (const event of [{}, { ctrlKey: true }, { shiftKey: true }]) {
      const expected = s3DragCopy(event, [from], to, !!event.ctrlKey);
      assert.equal(controller.handleDragOver({ elements: [from] }, to, 0, 0, event), expected);
      assert.equal(controller.drop([from], to, event), expected);
    }
  }
});


test("endpoint bucket boundaries default to copy while folders in one bucket default to move", () => {
  const entry = (path) => ({ resource: { scheme: "triton-s3", authority: "endpoint-1", path }, isDirectory: true });
  assert.equal(s3DragCopy({}, [entry("/first-bucket/a")], entry("/second-bucket"), false), true);
  assert.equal(s3DragCopy({}, [entry("/first-bucket/a")], entry("/first-bucket/b"), false), false);
  assert.equal(s3DragCopy({ shiftKey: true }, [entry("/first-bucket/a")], entry("/second-bucket"), false), false);
  const patched = patchWorkbench(source);
  const prior = patched.replace(/  const volume = [\s\S]*?volume\(resource\) !== volume\(destination.resource\)\);/, `  return items.some(({ resource }) => resource.scheme !== destination.resource.scheme ||
    resource.authority !== destination.resource.authority);`);
  assert.notEqual(prior, patched);
  assert.equal(patchWorkbench(prior), patched);
});
