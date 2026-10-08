export function formatQuantity(value) {
  if(value!==0&&Math.abs(value)<.001)return value.toExponential(2);
  return value.toLocaleString('en-US',{maximumSignificantDigits:4});
}

export function movementLabel(scale) {
  if(scale===1)return 'Movement at actual scale · 1×';
  return `Movement ${scale<1?'reduced':'exaggerated'} ${formatQuantity(scale)}× · visual only`;
}
