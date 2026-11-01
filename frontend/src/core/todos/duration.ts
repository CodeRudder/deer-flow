import type { Todo } from "./types";

/**
 * How long a todo took, for the phone's list (prototype ⑨).
 *
 * Nothing upstream carries a clock: LangChain's `Todo` is `{content, status}`
 * and the transcript has no timestamps. The two instants therefore come from
 * the backend, which stamps `started_at` / `completed_at` as ISO 8601 UTC when
 * `write_todos` moves an item into `in_progress` / `completed` (see
 * `deerflow.agents.thread_state.apply_todo_ops`).
 */

const SECOND_MS = 1_000;
const MINUTE_S = 60;
const HOUR_S = 60 * MINUTE_S;

/**
 * A duration the way the prototype writes it: `45s`, `12m`, `1h 5m`.
 *
 * The units are the ASCII `s`/`m`/`h` in both locales — the prototype draws
 * them that way in Chinese too, so there is no locale variant to key on.
 * Rolling over to the next unit only on a whole unit keeps the widening
 * number of digits out of the row: `59m` then `1h`, never `0.98h`.
 */
export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / SECOND_MS));
  if (seconds < MINUTE_S) {
    return `${seconds}s`;
  }

  const minutes = Math.floor(seconds / MINUTE_S);
  if (seconds < HOUR_S) {
    return `${minutes}m`;
  }

  const hours = Math.floor(seconds / HOUR_S);
  const rest = minutes % MINUTE_S;
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`;
}

/** An ISO instant as epoch milliseconds, or `null` if it is not one. */
function parseInstant(value: string | undefined): number | null {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * The elapsed time to show beside a todo, in milliseconds, or `null` when
 * there is nothing honest to show.
 *
 * `now` is the elapsed-time clock `TodoList` ticks; a finished todo does not
 * need it, because both of its ends are fixed.
 *
 * Everything that cannot be answered comes back as `null` rather than a
 * number, because the caller's choice is to draw a duration or to draw
 * nothing — never `NaN` / `Invalid Date` / an empty slot:
 *
 * - a todo that never ran (pending, or any status the backend does not know);
 * - a todo whose marks are missing — the case every thread written before
 *   this feature existed is in;
 * - a todo whose marks do not parse;
 * - a running todo before the first tick has read the clock;
 * - a start the backend never observed (an item completed straight from
 *   `pending`), since there is no duration to report rather than a zero;
 * - an end before its start (a clock that went backwards) — clamped, so the
 *   row reads `0s` instead of a negative.
 */
export function todoDurationMs(todo: Todo, now: number | null): number | null {
  const started = parseInstant(todo.started_at);

  if (todo.status === "completed") {
    const completed = parseInstant(todo.completed_at);
    if (started === null || completed === null) {
      return null;
    }
    return Math.max(0, completed - started);
  }

  if (todo.status === "in_progress") {
    if (started === null || now === null) {
      return null;
    }
    return Math.max(0, now - started);
  }

  return null;
}

/** The whole reading, or `null` when the row should draw no time column. */
export function formatTodoDuration(
  todo: Todo,
  now: number | null,
): string | null {
  const ms = todoDurationMs(todo, now);
  return ms === null ? null : formatDuration(ms);
}
