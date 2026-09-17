/** Slingshot physics and rules.
 *
 *  Everything the game decides lives here, in fixed steps, so the rules can be
 *  checked without a browser. Rendering reads this state and never writes it.
 */

const DEFAULTS = {
  gravity: 9.81,
  launchSpeed: 18,
  groundY: 0,
  birds: 3,
  pigs: [{ x: 14, y: 1.2, radius: 1.2 }, { x: 20, y: 4, radius: 1.2 }],
};

const number = (value, fallback) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

export function createLevel(config = {}) {
  const settings = { ...DEFAULTS, ...config };
  const pigTemplate = (config.pigs || DEFAULTS.pigs).map((pig) => ({
    x: number(pig.x, 0), y: number(pig.y, 0), radius: number(pig.radius, 1),
  }));
  const birdCount = Math.max(0, Math.floor(number(settings.birds, DEFAULTS.birds)));

  const opening = () => ({
    birds: Array.from({ length: birdCount }, () => ({
      x: 0, y: 0, vx: 0, vy: 0, launched: false, spent: false,
    })),
    pigs: pigTemplate.map((pig) => ({ ...pig, alive: true })),
    score: 0,
    phase: "aiming",
  });

  const level = {
    settings,
    state: opening(),
    launch(index, options = {}) {
      if (level.state.phase !== "aiming" && level.state.phase !== "settled") return false;
      const bird = level.state.birds[index];
      if (!bird || bird.launched) return false;
      const angle = number(options.angle, 0);
      const power = Math.min(Math.max(number(options.power, 0), 0), 1);
      const speed = settings.launchSpeed * power;
      bird.x = 0;
      bird.y = 0;
      bird.vx = Math.cos(angle) * speed;
      bird.vy = Math.sin(angle) * speed;
      bird.launched = true;
      bird.spent = false;
      level.state.phase = "flying";
      return true;
    },
    step(dt) {
      if (level.state.phase !== "flying") return;
      const elapsed = Math.max(0, number(dt, 0));
      for (const bird of level.state.birds) {
        if (!bird.launched || bird.spent) continue;
        bird.x += bird.vx * elapsed;
        bird.y += bird.vy * elapsed;
        bird.vy -= settings.gravity * elapsed;
        if (bird.y <= settings.groundY) {
          bird.y = settings.groundY;
          bird.vx = 0;
          bird.vy = 0;
          bird.spent = true;
        }
        for (const pig of level.state.pigs) {
          if (!pig.alive) continue;
          const dx = bird.x - pig.x;
          const dy = bird.y - pig.y;
          if (Math.sqrt(dx * dx + dy * dy) <= pig.radius) {
            pig.alive = false;
            level.state.score += 100;
          }
        }
      }
      if (level.state.pigs.every((pig) => !pig.alive)) {
        level.state.phase = "won";
        return;
      }
      if (level.state.birds.some((bird) => bird.launched && !bird.spent)) return;
      level.state.phase = level.state.birds.every((bird) => bird.spent) ? "lost" : "settled";
    },
    restart() {
      level.state = opening();
    },
  };
  return level;
}
