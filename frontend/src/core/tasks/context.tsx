import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

import type { Subtask } from "./types";

export type ResumeSubtaskFn = (
  taskId: string,
  threadId: string,
  description: string,
  subagentType: string,
) => void;

function isTerminalSubtaskStatus(status: Subtask["status"] | undefined) {
  return (
    status === "completed" || status === "failed" || status === "interrupted"
  );
}

export interface SubtaskContextValue {
  tasks: Record<string, Subtask>;
  setTasks: Dispatch<SetStateAction<Record<string, Subtask>>>;
  selectedTaskId: string | null;
  setSelectedTaskId: (id: string | null) => void;
  resumeSubtask?: ResumeSubtaskFn;
}

export const SubtaskContext = createContext<SubtaskContextValue>({
  tasks: {},
  setTasks: () => {
    /* noop */
  },
  selectedTaskId: null,
  setSelectedTaskId: () => {
    /* noop */
  },
});

function hasSubtaskChanges(
  existing: Subtask | undefined,
  next: Partial<Subtask> & { id: string },
) {
  if (!existing) {
    return true;
  }
  return Object.entries(next).some(([key, value]) => {
    return existing[key as keyof Subtask] !== value;
  });
}

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({});
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const value = useMemo(
    () => ({ tasks, setTasks, selectedTaskId, setSelectedTaskId }),
    [tasks, selectedTaskId],
  );

  return (
    <SubtaskContext.Provider value={value}>{children}</SubtaskContext.Provider>
  );
}

export function useSubtaskContext() {
  const context = useContext(SubtaskContext);
  if (context === undefined) {
    throw new Error(
      "useSubtaskContext must be used within a SubtaskContext.Provider",
    );
  }
  return context;
}

export function useSubtask(id: string) {
  const { tasks } = useSubtaskContext();
  return tasks[id];
}

export function useSubtasks() {
  const { tasks } = useSubtaskContext();
  return Object.values(tasks);
}

export function useUpdateSubtask() {
  const { tasks, setTasks } = useSubtaskContext();
  const shouldNotifyAfterRenderRef = useRef(false);
  // No deps: must run after every render to check the ref set during render.
  useEffect(() => {
    if (!shouldNotifyAfterRenderRef.current) {
      return;
    }
    shouldNotifyAfterRenderRef.current = false;
    setTasks({ ...tasks });
  });

  const updateSubtask = useCallback(
    (task: Partial<Subtask> & { id: string }) => {
      const previous = tasks[task.id];
      const previousStatus = previous?.status;
      // MessageList writes the pending task tool-call state before parsing the
      // matching ToolMessage in the same render. Keep terminal results stable
      // across the next render so the refresh notification does not loop.
      const next = {
        ...previous,
        ...task,
        ...(task.status === "in_progress" &&
        isTerminalSubtaskStatus(previousStatus)
          ? { status: previousStatus }
          : {}),
      } as Subtask;
      if (!hasSubtaskChanges(previous, next)) {
        return;
      }

      const becameTerminal =
        isTerminalSubtaskStatus(next.status) && previousStatus !== next.status;

      tasks[task.id] = next;

      if (task.latestMessage) {
        setTasks({ ...tasks });
      } else if (becameTerminal) {
        shouldNotifyAfterRenderRef.current = true;
      }
    },
    [setTasks, tasks],
  );

  return updateSubtask;
}
