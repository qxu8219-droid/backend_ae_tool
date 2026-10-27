"""Pure explicit assumptions; no robot I/O, implicit clipping or gap reanchoring."""
import math


def gripper(value, config):
    if value is None: return (None, None)
    fraction = (value - config.native_closed) / (config.native_open - config.native_closed)
    return (config.zero1_m + fraction * config.travel1_m, config.zero2_m + fraction * config.travel2_m)


def integrate_pose(times, velocities, input_valid, config):
    x, y, yaw = config.initial_pose
    positions, qualities = [], []
    valid = bool(input_valid and input_valid[0])
    for index, time in enumerate(times):
        valid = valid and input_valid[index]
        if index:
            dt = time - times[index - 1]
            valid = valid and 0 < dt <= config.max_gap_s + 1e-12
            if valid:
                vx, vy, wz = [value * scale + bias for value, scale, bias in zip(velocities[index - 1], config.velocity_scale, config.velocity_bias)]
                if config.velocity_frame == 'world':
                    dx, dy = vx * dt, vy * dt
                else:
                    turn = wz * dt
                    if abs(turn) < 1e-8:
                        sinc = dt * (1 - turn * turn / 6)
                        cosc = dt * (turn / 2 - turn ** 3 / 24)
                    else:
                        sinc, cosc = math.sin(turn) / wz, (1 - math.cos(turn)) / wz
                    body_x, body_y = sinc * vx - cosc * vy, cosc * vx + sinc * vy
                    dx = math.cos(yaw) * body_x - math.sin(yaw) * body_y
                    dy = math.sin(yaw) * body_x + math.cos(yaw) * body_y
                candidate = x + dx, y + dy, yaw + wz * dt
                valid = all(math.isfinite(value) for value in candidate)
                if valid: x, y, yaw = candidate
        positions.append((x, y, yaw)); qualities.append(bool(valid))
    return positions, qualities
