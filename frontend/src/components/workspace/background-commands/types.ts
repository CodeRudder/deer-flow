export type CommandStatus =
  | "running"
  | "completed"
  | "failed"
  | "killed"
  | "timed_out";

export interface BackgroundCommand {
  command_id: string;
  command: string;
  description: string;
  status: CommandStatus;
  pid: number | null;
  started_at: string;
  return_code: number | null;
}

export interface CommandOutput {
  command_id: string;
  status: string;
  output: string;
  log_file: string | null;
  pagination: {
    total_lines: number;
    start_line: number;
    line_count: number;
    has_more: boolean;
  };
}

export interface KillResult {
  killed: boolean;
  message: string;
  final_output: string | null;
}
