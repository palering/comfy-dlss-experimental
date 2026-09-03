import test from "node:test";
import assert from "node:assert/strict";
import { visibleRuntimeFields, setRuntimeWidgetVisible } from "../web/runtime_platform_state.js";

test("backend host, not browser host, controls auto visibility", () => {
  assert.deepEqual(visibleRuntimeFields({system_platform: "auto"}, "windows"),
    {linux_execution: false, proton: false, proton_path: false, linux_display_backend: false});
  assert.equal(visibleRuntimeFields({system_platform: "auto"}, "linux").proton_path, true);
});
test("explicit selection allows editing a portable workflow; native reserve hides Proton", () => {
  assert.equal(visibleRuntimeFields({system_platform: "linux"}, "windows").proton, true);
  assert.deepEqual(visibleRuntimeFields({system_platform: "linux", linux_execution: "native_reserved"}, "linux"),
    {linux_execution: true, proton: false, proton_path: false, linux_display_backend: false});
});
test("hide/show retains values, serialization and callbacks", () => {
  const compute = () => [200, 24], serialize = () => "GE";
  const widget = {type: "combo", value: "GE", options: {}, computeSize: compute, serializeValue: serialize};
  setRuntimeWidgetVisible(widget, false);
  assert.deepEqual(widget.computeSize(), [0, -4]);
  assert.equal(widget.value, "GE");
  assert.equal(widget.serializeValue(), "GE");
  setRuntimeWidgetVisible(widget, false);
  setRuntimeWidgetVisible(widget, true);
  assert.equal(widget.type, "combo");
  assert.equal(widget.computeSize, compute);
  assert.equal(widget.hidden, false);
});
