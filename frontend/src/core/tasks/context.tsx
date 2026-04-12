import { createContext, useCallback, useContext, useState } from "react";

import type { Subtask } from "./types";

export type ResumeSubtaskFn = (
  taskId: string,
  threadId: string,
  description: string,
  subagentType: string,
) => void;

export interface SubtaskContextValue {
  tasks: Record<string, Subtask>;
  setTasks: (tasks: Record<string, Subtask>) => void;
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

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({});
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  return (
    <SubtaskContext.Provider
      value={{ tasks, setTasks, selectedTaskId, setSelectedTaskId }}
    >
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
  const { tasks, setTasks } = useSubtaskContext();
  const updateSubtask = useCallback(
    (task: Partial<Subtask> & { id: string }) => {
      const existing = tasks[task.id];
      // Never downgrade a terminal status (completed/failed) back to in_progress
      if (
        existing &&
        (existing.status === "completed" || existing.status === "failed") &&
        task.status === "in_progress"
      ) {
        return;
      }
      tasks[task.id] = { ...existing, ...task } as Subtask;
      setTasks({ ...tasks });
    },
    [tasks, setTasks],
  );
  return updateSubtask;
}
