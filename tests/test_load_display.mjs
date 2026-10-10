import assert from 'node:assert/strict';
import { loadDisplay, loadUnitFactors, weightExplanation } from '../frontend/src/result-display.js';

const backpackLoad = 25 * loadUnitFactors.lbf;
assert.equal(loadDisplay(backpackLoad).label, '25 lb');
const kg = loadDisplay(backpackLoad, 'kg');
assert.equal(kg.label, '11.34 kg');
assert.ok(Math.abs(kg.magnitude * loadUnitFactors.kg - backpackLoad) < 1e-10,
  'Changing displayed units must preserve the test load.');
assert.equal(loadDisplay(9.80665, 'kg').label, '1 kg');
assert.equal(loadDisplay(9.80665, 'lbf').label, '2.205 lb');
assert.equal(loadDisplay(9.80665, 'N').label, '9.807 N', 'Explicit engineering units remain available.');
assert.equal(loadDisplay(backpackLoad, 'unknown').label, '25 lb');
assert.equal(weightExplanation(backpackLoad, 'lbf'), 'Like hanging 11.34 kg from the part.');
assert.equal(weightExplanation(9.80665, 'kg'), 'Like hanging 2.205 lb from the part.');
assert.equal(weightExplanation(0, 'kg'), 'Enter a positive load.');
console.log('Load display checks passed: pounds, kilograms, preserved physical load and plain-language equivalents.');
