import { pathToFileURL } from "node:url";
import { join } from "node:path";

const workspace = process.argv[2];
const results = [];

function check(name, fn) {
  try {
    results.push([name, fn() === true]);
  } catch (error) {
    results.push([name, false, error && error.message]);
  }
}

let total = null;
try {
  ({ total } = await import(pathToFileURL(join(workspace, "src", "cart.js")).href));
} catch (error) {
  console.log(`could not import src/cart.js: ${error.message}`);
}

if (typeof total !== "function") {
  console.log("SCORE: 0/8");
  process.exit(1);
}

check("an empty cart costs nothing", () => total([]) === 0);
check("whole-dollar amounts are not scaled", () => total([{ price: 4, quantity: 2 }]) === 8);
check("cents survive the discount rounding",
  () => total([{ price: 2.5, quantity: 3 }], { discountPercent: 10 }) === 6.75);
check("a repeating discount still lands on whole cents",
  () => total([{ price: 9.99, quantity: 3 }], { discountPercent: 15 }) === 25.47);
check("a 100 percent discount is zero",
  () => total([{ price: 5, quantity: 1 }], { discountPercent: 100 }) === 0);
check("a zero quantity contributes nothing",
  () => total([{ price: 7, quantity: 0 }, { price: 1, quantity: 2 }]) === 2);
check("a sub-cent price still rounds to cents",
  () => total([{ price: 0.1, quantity: 3 }]) === 0.3);
check("the caller's items are not mutated", () => {
  const items = [{ price: 3, quantity: 2 }];
  const before = JSON.stringify(items);
  total(items, { discountPercent: 25 });
  return JSON.stringify(items) === before;
});

let passed = 0;
for (const [name, ok, detail] of results) {
  console.log(`${ok ? "pass" : "FAIL"}  ${name}${detail ? ` (${detail})` : ""}`);
  if (ok) passed += 1;
}
console.log(`SCORE: ${passed}/${results.length}`);
process.exit(passed === results.length ? 0 : 1);
