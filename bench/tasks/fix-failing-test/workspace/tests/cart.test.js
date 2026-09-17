import assert from "node:assert/strict";
import { total } from "../src/cart.js";

assert.equal(total([]), 0);
assert.equal(total([{ price: 4, quantity: 2 }]), 8);
assert.equal(total([{ price: 2.5, quantity: 3 }]), 7.5);
assert.equal(total([{ price: 2.5, quantity: 3 }], { discountPercent: 10 }), 6.75);

console.log("cart tests pass");
