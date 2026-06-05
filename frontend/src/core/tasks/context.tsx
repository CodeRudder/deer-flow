import {
  createContext,
  useCallback,
  useContext,
  useMemo,
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
    <SubtaskContext.Provider value={value}>
      {children}
    </SubtaskContext.Provider>
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
  const { setTasks } = useSubtaskContext();
  const updateSubtask = useCallback(
    (task: Partial<Subtask> & { id: string }) => {
      setTasks((currentTasks) => {
        const existing = currentTasks[task.id];
        // Never downgrade a terminal status back to in_progress.
        if (
          existing &&
          (existing.status === "completed" ||
            existing.status === "failed" ||
            existing.status === "interrupted") &&
          task.status === "in_progress"
        ) {
          return currentTasks;
        }
        if (!hasSubtaskChanges(existing, task)) {
          return currentTasks;
        }
        return {
          ...currentTasks,
          [task.id]: { ...existing, ...task } as Subtask,
        };
      });
    },
    [setTasks],
  );
  return updateSubtask;
}
