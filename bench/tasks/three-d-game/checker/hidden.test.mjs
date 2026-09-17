import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const workspace = process.argv[2];
const results = [];
const close = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;

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

let createGame = null;
try {
  ({ createGame } = await import(pathToFileURL(join(workspace, "src", "sim.js")).href));
} catch (error) {
  console.log(`could not import src/sim.js: ${error.message}`);
}

const need = (fn) => typeof createGame === "function" && fn() === true;

check("the simulation exposes the documented shape", () => need(() => {
  const game = createGame();
  const player = game && game.state && game.state.player;
  return typeof game.step === "function"
    && typeof game.restart === "function"
    && player && ["x", "z", "heading", "speed"].every((key) => typeof player[key] === "number")
    && Array.isArray(game.checkpoints) && game.checkpoints.length >= 3;
}));

check("a new game starts at rest on the line", () => need(() => {
  const { player, lap, checkpoint, elapsed } = createGame().state;
  return lap === 0 && checkpoint === 0 && close(player.x, 0) && close(player.z, 0)
    && close(player.heading, 0) && close(player.speed, 0) && close(elapsed, 0);
}));

check("throttle accelerates forward along +z and respects the speed ceiling", () => need(() => {
  const game = createGame();
  for (let index = 0; index < 20; index += 1) game.step({ throttle: 1 }, 0.05);
  const { player } = game.state;
  return player.speed > 1 && player.speed <= 20 && player.z > 0.5 && close(player.x, 0);
}));

check("braking stops the car and never reverses it", () => need(() => {
  const game = createGame();
  for (let index = 0; index < 20; index += 1) game.step({ throttle: 1 }, 0.05);
  const peak = game.state.player.speed;
  let lowest = Infinity;
  for (let index = 0; index < 40; index += 1) {
    game.step({ brake: 1 }, 0.05);
    lowest = Math.min(lowest, game.state.player.speed);
  }
  return peak > 1 && game.state.player.speed === 0 && lowest >= 0;
}));

check("steering turns a moving car and leaves a stationary one alone", () => need(() => {
  const moving = createGame();
  for (let index = 0; index < 20; index += 1) moving.step({ throttle: 1 }, 0.05);
  const before = moving.state.player.heading;
  for (let index = 0; index < 10; index += 1) moving.step({ throttle: 0.5, steer: 1 }, 0.05);
  const turned = Math.abs(moving.state.player.heading - before) > 0.05;
  const parked = createGame();
  parked.step({ steer: 1 }, 0.05);
  return turned && close(parked.state.player.heading, 0, 1e-9);
}));

check("walls contain the car and do not let speed build against them", () => need(() => {
  const game = createGame({ width: 20, length: 40 });
  for (let index = 0; index < 200; index += 1) game.step({ throttle: 1 }, 0.05);
  const { player } = game.state;
  return Math.abs(player.z) <= 20 + 1e-6 && Math.abs(player.x) <= 10 + 1e-6 && player.speed < 1;
}));

const TRACK = [
  { x: 0, z: 5, radius: 1 },
  { x: 6, z: 10, radius: 1 },
  { x: -6, z: 15, radius: 1 },
];

const place = (game, point) => {
  game.state.player.x = point.x;
  game.state.player.z = point.z;
  game.step({}, 0.016);
};

check("checkpoints count only in order", () => need(() => {
  const game = createGame({ checkpoints: TRACK });
  place(game, TRACK[1]);
  const outOfOrder = game.state.checkpoint;
  place(game, TRACK[0]);
  const first = game.state.checkpoint;
  place(game, TRACK[1]);
  const second = game.state.checkpoint;
  return outOfOrder === 0 && first === 1 && second === 2;
}));

check("clearing the last checkpoint completes a lap and re-arms the track", () => need(() => {
  const game = createGame({ checkpoints: TRACK });
  for (const point of TRACK) place(game, point);
  return game.state.lap === 1 && game.state.checkpoint === 0;
}));

check("restart restores the opening state exactly", () => need(() => {
  const game = createGame();
  const clone = (state) => ({ ...state.player, lap: state.lap, checkpoint: state.checkpoint, elapsed: state.elapsed });
  const start = clone(game.state);
  for (let index = 0; index < 30; index += 1) game.step({ throttle: 1, steer: 0.5 }, 0.05);
  game.restart();
  const now = clone(game.state);
  return Object.keys(start).every((key) => close(now[key], start[key], 1e-9));
}));

let passed = 0;
for (const [name, ok, detail] of results) {
  console.log(`${ok ? "pass" : "FAIL"}  ${name}${detail ? ` (${detail})` : ""}`);
  if (ok) passed += 1;
}
console.log(`SCORE: ${passed}/${results.length}`);
process.exit(passed === results.length ? 0 : 1);
