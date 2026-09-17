/** Cart pricing. Amounts are dollars; results are rounded to whole cents. */
export function total(items, options = {}) {
  const { discountPercent = 0 } = options;
  const subtotal = items.reduce((sum, item) => sum + item.price * item.quantity, 0);
  const discount = subtotal * (discountPercent / 100);
  // BUG: the result is rounded to dollars and then divided, so every amount is
  // scaled by 1/100. Fix the rounding so cents survive.
  return Math.round(subtotal - discount) / 100;
}
