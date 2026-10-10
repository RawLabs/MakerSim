export function formatQuantity(value) {
  if(value!==0&&Math.abs(value)<.001)return value.toExponential(2);
  return value.toLocaleString('en-US',{maximumSignificantDigits:4});
}

export const loadUnitFactors = {lbf: 4.4482216152605, kg: 9.80665, stone: 14 * 4.4482216152605, N: 1};
const loadUnitLabels = {lbf: 'lb', kg: 'kg', stone: 'stone', N: 'N'};

export function loadDisplay(forceNewtons, unit = 'lbf') {
  if (!Object.hasOwn(loadUnitFactors, unit)) unit = 'lbf';
  const magnitude = forceNewtons / loadUnitFactors[unit];
  return {unit, magnitude, label: `${formatQuantity(magnitude)} ${loadUnitLabels[unit]}`};
}

export function weightExplanation(forceNewtons, unit) {
  if (!(forceNewtons > 0) || !Number.isFinite(forceNewtons)) return 'Enter a positive load.';
  const equivalent = loadDisplay(forceNewtons, unit === 'kg' ? 'lbf' : 'kg');
  return `Like hanging ${equivalent.label} from the part.`;
}

export function movementLabel(scale) {
  if(scale===1)return 'Movement at actual scale · 1×';
  return `Movement ${scale<1?'reduced':'exaggerated'} ${formatQuantity(scale)}× · visual only`;
}
