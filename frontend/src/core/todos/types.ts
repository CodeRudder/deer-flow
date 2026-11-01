export interface Todo {
  content?: string;
  status?: "pending" | "in_progress" | "completed";
  /**
   * ISO 8601 UTC, stamped by the backend when `write_todos` moved this item
   * into `in_progress` (see `apply_todo_ops`). Absent on every todo written
   * before the stamps existed, and on any item that never ran — readers must
   * treat a missing mark as "no duration", never as the epoch.
   */
  started_at?: string;
  /** ISO 8601 UTC, stamped when the item moved into `completed`. */
  completed_at?: string;
}
