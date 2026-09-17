import { createLevel } from "./sim.js";

const canvas = document.getElementById("view");
const context = canvas.getContext("2d");
const hud = document.getElementById("hud");
const game = createLevel();

const SCALE = 22;
const ORIGIN_X = 90;
const GROUND_Y = 0;
let aiming = null;

function resize() {
  canvas.width = innerWidth * devicePixelRatio;
  canvas.height = innerHeight * devicePixelRatio;
  context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  game.originY = innerHeight - 110;
}
addEventListener("resize", resize);
resize();

const toScreen = (x, y) => [ORIGIN_X + x * SCALE, game.originY - (y - GROUND_Y) * SCALE];
const toWorld = (sx, sy) => [(sx - ORIGIN_X) / SCALE, GROUND_Y + (game.originY - sy) / SCALE];

function aimFrom(event) {
  if (game.state.phase !== "aiming" && game.state.phase !== "settled") return null;
  const [wx, wy] = toWorld(event.offsetX, event.offsetY);
  const power = Math.min(Math.hypot(wx, -wy) / 12, 1);
  const angle = Math.max(-0.1, Math.atan2(Math.max(wy, 0.1), Math.max(wx, 0.1)));
  return { angle, power };
}

canvas.addEventListener("pointerdown", (event) => { aiming = aimFrom(event) || aiming; });
canvas.addEventListener("pointermove", (event) => { if (aiming) aiming = aimFrom(event) || aiming; });
canvas.addEventListener("pointerup", () => { if (aiming) launch(aiming); aiming = null; });

let keyboard = { angle: Math.PI / 4, power: 0.8 };
addEventListener("keydown", (event) => {
  const key = event.key.toLowerCase();
  if (key === "r") game.restart();
  if (key === "arrowup") keyboard.angle = Math.min(keyboard.angle + 0.06, 1.3);
  if (key === "arrowdown") keyboard.angle = Math.max(keyboard.angle - 0.06, 0.1);
  if (key === " ") { launch(keyboard); event.preventDefault(); }
});

function launch(aim) {
  const next = game.state.birds.findIndex((bird) => !bird.launched);
  if (next >= 0) game.launch(next, aim);
}

function circle(x, y, radius, colour) {
  const [sx, sy] = toScreen(x, y);
  context.beginPath();
  context.arc(sx, sy, radius * SCALE, 0, Math.PI * 2);
  context.fillStyle = colour;
  context.fill();
}

function draw() {
  context.fillStyle = "#101521";
  context.fillRect(0, 0, innerWidth, innerHeight);
  context.fillStyle = "#1d2a1f";
  context.fillRect(0, game.originY, innerWidth, innerHeight - game.originY);
  context.strokeStyle = "#3f6b46";
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(0, game.originY);
  context.lineTo(innerWidth, game.originY);
  context.stroke();

  if (aiming) {
    const [sx, sy] = toScreen(0, 0);
    const length = 60 + aiming.power * 90;
    context.strokeStyle = "#ffd166";
    context.setLineDash([5, 5]);
    context.beginPath();
    context.moveTo(sx, sy);
    context.lineTo(sx + Math.cos(aiming.angle) * length, sy - Math.sin(aiming.angle) * length);
    context.stroke();
    context.setLineDash([]);
  }

  for (const pig of game.state.pigs) {
    if (pig.alive) circle(pig.x, pig.y, pig.radius, "#7ad48f");
    else circle(pig.x, GROUND_Y + 0.2, 0.4, "#4a5a4d");
  }
  for (const bird of game.state.birds) {
    if (bird.launched) circle(bird.x, bird.y, 0.55, bird.spent ? "#8a8f9c" : "#ff6b6b");
  }
  const next = game.state.birds.find((bird) => !bird.launched);
  if (next) circle(0, 0.6, 0.55, "#ff6b6b");

  const left = game.state.birds.filter((bird) => !bird.launched).length;
  hud.innerHTML = `poeng <b>${game.state.score}</b> &middot; fugler igjen <b>${left}</b>`
    + ` &middot; <b>${game.state.phase}</b>`;
}

let previous = performance.now();
function frame(now) {
  const dt = Math.min((now - previous) / 1000, 0.05);
  previous = now;
  for (let step = 0; step < 8; step += 1) game.step(dt / 8);
  draw();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
