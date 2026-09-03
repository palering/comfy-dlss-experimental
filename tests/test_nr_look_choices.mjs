import test from "node:test";
import assert from "node:assert/strict";
import { migrateLookChoices } from "../web/nr_look_choices.js";

const definitions = { nr_style: ["COMBO", {
  options: ["Default", "Natural", "Cinematic"],
  dlss_legacy_labels: { "0": "Default", "1": "Natural", "2": "Cinematic" },
}] };

test("legacy labels migrate without changing other widget values", () => {
  for (const [value, expected] of [[0, "Default"], [1, "Natural"], [2, "Cinematic"]]) {
    const widgets = [{ name: "nr_style", value }, { name: "intensity", value: 1 }];
    migrateLookChoices(widgets, definitions);
    assert.equal(widgets[0].value, expected);
    assert.equal(widgets[1].value, 1);
    migrateLookChoices(widgets, definitions);
    assert.equal(widgets[0].value, expected);
  }
});

test("unknown values and connected input metadata are not guessed", () => {
  for (const value of ["Cinematic", "1", 3, true, null, 1.5]) {
    const widgets = [{ name: "nr_style", value }];
    migrateLookChoices(widgets, definitions);
    assert.equal(widgets[0].value, value);
  }
  migrateLookChoices(undefined, definitions);
  const widgets = [{ name: "nr_style", value: 1 }];
  migrateLookChoices(widgets, {});
  assert.equal(widgets[0].value, 1);
});
