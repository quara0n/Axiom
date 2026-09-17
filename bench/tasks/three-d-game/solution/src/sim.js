/** Deterministic track-racing simulation.
 *
 * Everything the game does happens here, in fixed steps, so the driving model can
 * be verified without a browser. Rendering reads this state and never writes it.
 */

const DEFAULTS = {
  width: 20,
  length: 40,
  maxSpeed: 20,
  accel: 12,
  brakeForce: 18,
  friction: 2,
  turnRate: 2,
  checkpoints: [
    { x: 0, z: 8, radius: 2 },
    { x: 8, z: 16, radius: 2 },
    { x: -8, z: 24, radius: 2 },
    { x: 0, z: 34, radius: 2 },
  ],
};

const number = (value, fallback) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

export function createGame(config = {}) {
  const settings = { ...DEFAULTS, ...config };
  const checkpoints = (config.checkpoints || DEFAULTS.checkpoints)
    .map((point) => ({ x: number(point.x, 0), z: number(point.z, 0), radius: number(point.radius, 1) }));
  const halfWidth = number(settings.width, DEFAULTS.width) / 2;
  const halfLength = number(settings.length, DEFAULTS.length) / 2;

  const opening = () => ({
    player: { x: 0, z: 0, heading: 0, speed: 0 },
    lap: 0,
    checkpoint: 0,
    elapsed: 0,
  });

  const game = {
    settings,
    checkpoints,
    state: opening(),
    step(input = {}, dt = 0) {
      const elapsed = number(dt, 0);
      const throttle = number(input.throttle, 0);
      const brake = number(input.brake, 0);
      const steer = number(input.steer, 0);
      const player = game.state.player;

      let speed = player.speed + throttle * settings.accel * elapsed;
      speed -= brake * settings.brakeForce * elapsed;
      speed -= settings.friction * elapsed;
      speed = Math.min(Math.max(speed, 0), settings.maxSpeed);

      const grip = Math.min(1, speed / 5);
      player.heading += steer * settings.turnRate * elapsed * grip;
      player.speed = speed;
      player.x += Math.sin(player.heading) * speed * elapsed;
      player.z += Math.cos(player.heading) * speed * elapsed;

      if (player.x > halfWidth || player.x < -halfWidth) {
        player.x = Math.min(Math.max(player.x, -halfWidth), halfWidth);
        player.speed = 0;
      }
      if (player.z > halfLength || player.z < -halfLength) {
        player.z = Math.min(Math.max(player.z, -halfLength), halfLength);
        player.speed = 0;
      }

      const target = checkpoints[game.state.checkpoint];
      if (target) {
        const dx = player.x - target.x;
        const dz = player.z - target.z;
        if (Math.sqrt(dx * dx + dz * dz) <= target.radius) {
          game.state.checkpoint += 1;
          if (game.state.checkpoint >= checkpoints.length) {
            game.state.lap += 1;
            game.state.checkpoint = 0;
          }
        }
      }
      game.state.elapsed += elapsed;
    },
    restart() {
      game.state = opening();
    },
  };
  return game;
}
