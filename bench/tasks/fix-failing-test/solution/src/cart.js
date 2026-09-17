/** Cart pricing. Amounts are dollars; results are rounded to whole cents. */
export function total(items, options = {}) {
  const { discountPercent = 0 } = options;
  const subtotal = items.reduce((sum, item) => sum + item.price * item.quantity, 0);
  const discount = subtotal * (discountPercent / 100);
  return Math.round((subtotal - discount) * 100) / 100;
}
