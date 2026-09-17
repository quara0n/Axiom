import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const workspace = process.argv[2];
const results = [];

function check(name, fn) {
  try {
    results.push([name, fn() === true, ""]);
  } catch (error) {
    results.push([name, false, String((error && error.message) || error)]);
  }
}

check("index.html is a browser page that loads a module", () => {
  const file = join(workspace, "index.html");
  if (!existsSync(file)) return false;
  const html = readFileSync(file, "utf8");
  return /<script[^>]+type\s*=\s*["']module["']/i.test(html) && /<canvas/i.test(html);
});

let createLevel = null;
try {
  ({ createLevel } = await import(pathToFileURL(join(workspace, "src", "sim.js")).href));
} catch (error) {
  console.log(`could not import src/sim.js: ${error.message}`);
}

const need = (fn) => typeof createLevel === "function" && fn() === true;
const snapshot = (state) => JSON.stringify({
  score: state.score,
  phase: state.phase,
  birds: state.birds.map((bird) => [bird.x, bird.y, bird.vx, bird.vy,
                                     bird.launched === true, bird.spent === true]),
  pigs: state.pigs.map((pig) => [pig.x, pig.y, pig.radius, pig.alive === true]),
});
const draw = (game, index, x, y) => {
  const bird = game.state.birds[index];
  bird.x = x; bird.y = y; bird.vx = 0; bird.vy = 0;
};
const settle = (game, seconds = 4) => {
  for (let step = 0; step < seconds * 120 && game.state.phase === "flying"; step += 1) {
    game.step(1 / 120);
  }
};

check("the level exposes the documented shape", () => need(() => {
  const game = createLevel();
  const state = game && game.state;
  return typeof game.launch === "function" && typeof game.step === "function"
    && typeof game.restart === "function"
    && state && Array.isArray(state.birds) && Array.isArray(state.pigs)
    && typeof state.score === "number" && typeof state.phase === "string"
    && state.birds.length === 3 && state.pigs.length > 0;
}));

check("a new level starts ready to aim", () => need(() => {
  const { birds, pigs, score, phase } = createLevel().state;
  return phase === "aiming" && score === 0
    && birds.every((bird) => bird.launched !== true)
    && pigs.every((pig) => pig.alive !== false);
}));

check("launching a bird starts the flight", () => need(() => {
  const game = createLevel();
  game.launch(0, { angle: Math.PI / 4, power: 1 });
  return game.state.birds[0].launched === true && game.state.phase === "flying";
}));

check("a launched bird follows a projectile arc", () => need(() => {
  const game = createLevel();
  const startX = game.state.birds[0].x;
  game.launch(0, { angle: Math.PI / 4, power: 1 });
  let highest = game.state.birds[0].y;
  let rose = false;
  let fell = false;
  for (let step = 0; step < 240; step += 1) {
    game.step(1 / 120);
    const bird = game.state.birds[0];
    if (bird.y > highest) { highest = bird.y; rose = true; } else if (rose) { fell = true; }
  }
  return rose && fell && game.state.birds[0].x > startX + 5;
}));

check("gravity brings the bird down and the ground stops it", () => need(() => {
  const game = createLevel();
  game.launch(0, { angle: Math.PI / 4, power: 1 });
  let lowest = Infinity;
  for (let step = 0; step < 12 * 120; step += 1) {
    game.step(1 / 120);
    lowest = Math.min(lowest, game.state.birds[0].y);
    if (game.state.phase !== "flying") break;
  }
  return lowest >= -1e-6 && game.state.phase !== "flying";
}));

check("hitting a pig takes it out and scores", () => need(() => {
  const game = createLevel({ pigs: [{ x: 12, y: 6, radius: 1.5 }, { x: 25, y: 3, radius: 1.5 }] });
  game.launch(0, { angle: 0.7, power: 1 });
  draw(game, 0, 12, 6);
  game.step(1 / 120);
  return game.state.pigs[0].alive === false
    && game.state.pigs[1].alive !== false
    && game.state.score > 0;
}));

check("clearing every pig wins the level", () => need(() => {
  const game = createLevel({ pigs: [{ x: 12, y: 6, radius: 1.5 }] });
  game.launch(0, { angle: 0.7, power: 1 });
  draw(game, 0, 12, 6);
  game.step(1 / 120);
  return game.state.pigs[0].alive === false && game.state.phase === "won";
}));

check("using every bird without clearing the pigs loses", () => need(() => {
  const game = createLevel({ pigs: [{ x: 40, y: 3, radius: 1.5 }] });
  for (let index = 0; index < 3; index += 1) {
    game.launch(index, { angle: 0, power: 0.2 });
    settle(game, 3);
  }
  return game.state.phase === "lost";
}));

check("restart returns the opening state exactly", () => need(() => {
  const game = createLevel();
  const start = snapshot(game.state);
  game.launch(0, { angle: 0.9, power: 1 });
  for (let step = 0; step < 200; step += 1) game.step(1 / 120);
  game.restart();
  return snapshot(game.state) === start;
}));

let passed = 0;
for (const [name, ok, detail] of results) {
  console.log(`${ok ? "pass" : "FAIL"}  ${name}${detail ? ` (${detail})` : ""}`);
  if (ok) passed += 1;
}
console.log(`SCORE: ${passed}/${results.length}`);
process.exit(passed === results.length ? 0 : 1);
