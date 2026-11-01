import { ChevronUpIcon, ListTodoIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { formatTodoDuration, type Todo } from "@/core/todos";
import { cn } from "@/lib/utils";

import {
  QueueItem,
  QueueItemContent,
  QueueItemIndicator,
  QueueList,
} from "../ai-elements/queue";

/**
 * How long one running task's name is held before the track moves on, and how
 * long the slide between two names takes (prototype ⑨: 9s standing still + 1s
 * sliding, so 10s an item and a 20s round for two).
 *
 * `NOW_SLIDE_MS` has to match the rail's `duration-1000` — it is how long the
 * track is given to land on the copy it loops onto before it is wound back to
 * the top.
 */
const NOW_ROTATE_MS = 10_000;
const NOW_SLIDE_MS = 1_000;

/** One rail block: the rail's `h-4.5`, which is also the slide's step. */
const NOW_ITEM_REM = 1.125;

/**
 * How often the running durations are re-read.
 *
 * The prototype's durations are minute-grained, but the first minute of a run
 * reads in seconds (`45s`), so the tick has to be a second. One ticker feeds
 * both the list's rows and the "now" row, which is what keeps the two readings
 * of the same task equal.
 */
const ELAPSED_TICK_MS = 1_000;

/** True while the reader has asked their system for no animation. */
function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

/**
 * The wall clock the running durations are read against, or `null` until the
 * first tick.
 *
 * Only started while something is actually running: a finished list is a fixed
 * reading, so the desktop's call sites acquire no timer from a prop they never
 * pass. The first render reads `null` rather than `Date.now()` because this
 * component also renders on the server, where a clock read would disagree with
 * the client's own first render.
 *
 * Deliberately not gated on `prefers-reduced-motion`: the numbers *are* the
 * information, and a reader who asked for less motion still gets to watch the
 * elapsed time move — there is no animation here to suppress.
 */
function useElapsedClock(enabled: boolean): number | null {
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), ELAPSED_TICK_MS);
    return () => clearInterval(timer);
  }, [enabled]);

  return now;
}

/**
 * The row under the header (prototype ⑨⑩): the task that is running now, and
 * how long it has been running.
 *
 * Only rendered when something is — see `TodoList`'s `showProgress`. A single
 * running task sits still; several take turns, sliding up every 10s.
 *
 * The rail carries a copy of the first item at its end, so the track can slide
 * onto that copy and then be wound back to the top on a frame that is
 * pixel-identical to the one it left: the loop never visibly rewinds through
 * the names. Under `prefers-reduced-motion: reduce` nothing turns over at all
 * and the first item simply stays.
 *
 * The elapsed time is the *shown* item's, never the first running one's: the
 * rail and the number turn over together, which is the only arrangement in
 * which the pair still reads as one task.
 */
function TodoNowRow({
  running,
  now,
  showDurations,
}: {
  running: Todo[];
  now: number | null;
  showDurations: boolean;
}) {
  const reduced = usePrefersReducedMotion();
  const rotating = running.length > 1 && !reduced;
  const rail = rotating ? [...running, ...running.slice(0, 1)] : running;
  const [index, setIndex] = useState(0);
  const [round, setRound] = useState(0);

  // The list can shrink under the rotation (a task completing is exactly the
  // event that changes it), which would leave the window looking past the end
  // of the rail. Clamping keeps the current name on screen until the next
  // tick puts the index back in range.
  const step = Math.min(index, rail.length - 1);

  useEffect(() => {
    if (!rotating) return;
    const timer = setInterval(
      () => setIndex((current) => (current + 1) % rail.length),
      NOW_ROTATE_MS,
    );
    return () => clearInterval(timer);
  }, [rail.length, rotating]);

  useEffect(() => {
    // The name on screen is the copy of the first one: wind the rail back to
    // the top. `key={round}` re-mounts it, so the jump is a first frame rather
    // than a transition, and since both frames hold the same name it is
    // invisible.
    if (!rotating || step !== rail.length - 1) return;
    const timer = setTimeout(() => {
      setIndex(0);
      setRound((current) => current + 1);
    }, NOW_SLIDE_MS);
    return () => clearTimeout(timer);
  }, [rail.length, rotating, step]);

  // The modulo maps the trailing copy back onto the item it copies, so the
  // reading is always the reading of the name in the window.
  const shown = running[step % running.length];
  const duration =
    showDurations && shown ? formatTodoDuration(shown, now) : null;

  return (
    <div
      data-testid="todo-now"
      className="bg-accent text-muted-foreground flex items-center gap-2 px-3.5 pb-2.5 text-xs"
    >
      <span
        aria-hidden="true"
        className="bg-foreground/62 size-1.5 shrink-0 rounded-full"
      />
      {/* An 18px window onto the rail. `h-4.5` / `leading-4.5` and
          `NOW_ITEM_REM` are that same 18px: the slide step has to match the
          block height or the names drift out of the window. */}
      <span className="h-4.5 min-w-0 flex-1 overflow-hidden">
        <span
          key={round}
          data-testid="todo-now-rail"
          // `block` is load-bearing: a `transform` on a non-replaced inline
          // element does nothing at all, silently.
          className="block transition-transform duration-1000 ease-out motion-reduce:transition-none"
          style={{ transform: `translateY(-${step * NOW_ITEM_REM}rem)` }}
        >
          {rail.map((item, i) => (
            <span
              key={i}
              // The trailing copy is the first name over again; the reader has
              // heard it once already.
              aria-hidden={rotating && i === rail.length - 1 ? true : undefined}
              className="block h-4.5 truncate leading-4.5"
            >
              {item.content ?? ""}
            </span>
          ))}
        </span>
      </span>
      {/* `text-xs` (12px) and a brighter tint than the list's own column: of
          everything the folded panel keeps, this is the number that moves. */}
      {duration !== null && (
        <span
          data-testid="todo-now-duration"
          className="text-foreground/62 shrink-0 text-xs tabular-nums"
        >
          {duration}
        </span>
      )}
    </div>
  );
}

export function TodoList({
  className,
  todos,
  collapsed: controlledCollapsed,
  hidden = false,
  onToggle,
  showProgress = false,
  showDurations = false,
}: {
  className?: string;
  todos: Todo[];
  collapsed?: boolean;
  hidden?: boolean;
  onToggle?: () => void;
  /** Show the header's progress counts and the running-task row (⑨⑩). */
  showProgress?: boolean;
  /** Show how long each todo took, beside its name (⑨). */
  showDurations?: boolean;
}) {
  const [internalCollapsed, setInternalCollapsed] = useState(true);
  const isControlled = controlledCollapsed !== undefined;
  const collapsed = isControlled ? controlledCollapsed : internalCollapsed;

  const handleToggle = () => {
    if (isControlled) {
      onToggle?.();
    } else {
      setInternalCollapsed((prev) => !prev);
    }
  };

  const completed = todos.filter((todo) => todo.status === "completed").length;
  const running = todos.filter((todo) => todo.status === "in_progress");
  // Everything that is neither done nor running is still waiting — a todo
  // that has no status at all included, so the three counts always add up to
  // the list.
  const waiting = todos.length - completed - running.length;

  // A finished todo needs no clock (both of its ends are fixed), so nothing
  // here ticks — and the desktop, which passes neither prop, never reaches it.
  const now = useElapsedClock(showDurations && running.length > 0);

  return (
    <div
      className={cn(
        "flex h-fit w-full origin-bottom translate-y-4 flex-col overflow-hidden rounded-t-xl border border-b-0 bg-white backdrop-blur-sm transition-all duration-200 ease-out",
        hidden ? "pointer-events-none translate-y-8 opacity-0" : "",
        className,
      )}
    >
      <header
        className={cn(
          "bg-accent flex min-h-8 shrink-0 cursor-pointer items-center justify-between px-4 text-sm transition-all duration-300 ease-out",
        )}
        onClick={handleToggle}
      >
        <div className="text-muted-foreground">
          <div className="flex items-center justify-center gap-2">
            <ListTodoIcon className="size-4" />
            <div>To-dos</div>
          </div>
        </div>
        {/* `className` is left undefined rather than made conditional through
            `cn()` when the reading is off: the desktop's three call sites
            must keep byte-for-byte the header they have today, and `cn()`
            would emit an empty `class=""`. The wrapper only becomes a flex
            row for the two right-hand items once there is something to put
            beside the chevron. */}
        <div className={showProgress ? "flex items-center gap-2.5" : undefined}>
          {/* Prototype ⑨⑩: done, running, waiting. The tints are the
              prototype's own — the running count is the darkest, because it
              is the one that means "now". The counts live in the header and
              not in the list below it, so folding the panel away leaves them
              on screen. An empty list draws none at all: `0 0 0` is noise,
              and the phone page never mounts the panel without todos. */}
          {showProgress && todos.length > 0 && (
            <span
              data-testid="todo-counts"
              className="flex items-center gap-2.5 text-xs tabular-nums"
            >
              <span className="text-muted-foreground">{`✓ ${completed}`}</span>
              <span className="text-foreground/85">{`◐ ${running.length}`}</span>
              <span className="text-muted-foreground/65">{`○ ${waiting}`}</span>
            </span>
          )}
          <ChevronUpIcon
            className={cn(
              "text-muted-foreground size-4 transition-transform duration-300 ease-out",
              collapsed ? "" : "rotate-180",
            )}
          />
        </div>
      </header>
      {/* The running-task row is a sibling of the header, never a child of the
          collapsible body: prototype ⑩ keeps it on screen while the panel is
          folded, which is the whole point of the row. */}
      {showProgress && running.length > 0 && (
        <TodoNowRow running={running} now={now} showDurations={showDurations} />
      )}
      <main
        className={cn(
          "bg-accent flex grow px-2 transition-all duration-300 ease-out",
          collapsed ? "h-0 pb-3" : "h-28 pb-4",
        )}
      >
        <QueueList className="bg-background mt-0 w-full rounded-t-xl">
          {todos.map((todo, i) => {
            // `null` draws no node at all rather than an empty span: a pending
            // todo, or one written before the stamps existed, must leave no
            // gap and no stray element where the column would be. `min-w-0`
            // is what lets the name truncate so the column can have its width
            // — added only alongside the column, so the desktop's rows are
            // untouched.
            const duration = showDurations
              ? formatTodoDuration(todo, now)
              : null;

            return (
              <QueueItem key={i + (todo.content ?? "")}>
                <div className="flex items-center gap-2">
                  <QueueItemIndicator
                    className={
                      todo.status === "in_progress" ? "bg-primary/70" : ""
                    }
                    completed={todo.status === "completed"}
                  />
                  <QueueItemContent
                    className={cn(
                      todo.status === "in_progress" ? "text-primary/70" : "",
                      showDurations && "min-w-0",
                    )}
                    completed={todo.status === "completed"}
                  >
                    {todo.content}
                  </QueueItemContent>
                  {duration !== null && (
                    <span
                      data-testid="todo-duration"
                      className={cn(
                        "shrink-0 text-[11.5px] tabular-nums",
                        todo.status === "in_progress"
                          ? "text-foreground/62"
                          : "text-muted-foreground",
                      )}
                    >
                      {duration}
                    </span>
                  )}
                </div>
              </QueueItem>
            );
          })}
        </QueueList>
      </main>
    </div>
  );
}
