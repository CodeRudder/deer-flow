import { Loader2Icon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";

import { fetchOutput } from "./api";
import type { CommandOutput } from "./types";

export function CommandOutputView({
  threadId,
  commandId,
  status,
}: {
  threadId: string;
  commandId: string;
  status: string;
}) {
  const [output, setOutput] = useState<CommandOutput | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState<number>(0); // 0 = tail mode

  const lineCount = 30;

  const loadOutput = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchOutput(
        threadId,
        commandId,
        page === 0 ? undefined : (page - 1) * lineCount,
        lineCount,
      );
      setOutput(result);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [threadId, commandId, page]);

  useEffect(() => {
    void loadOutput();
  }, [loadOutput]);

  // Auto-refresh for running commands
  useEffect(() => {
    if (status !== "running") return;
    const id = setInterval(() => void loadOutput(), 3000);
    return () => clearInterval(id);
  }, [status, loadOutput]);

  if (loading && !output) {
    return (
      <div className="flex items-center justify-center py-8 text-muted-foreground">
        <Loader2Icon className="mr-2 size-4 animate-spin" />
        Loading output...
      </div>
    );
  }

  if (!output) {
    return (
      <div className="py-4 text-center text-sm text-muted-foreground">
        No output available
      </div>
    );
  }

  // Split metadata header from actual output
  const parts = output.output.split("\n\n");
  const header = parts[0];
  const content = parts.slice(1).join("\n\n");

  return (
    <div className="flex flex-col gap-2">
      {header && (
        <div className="text-xs text-muted-foreground">{header}</div>
      )}
      <pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 text-xs">
        {content || "(no output)"}
      </pre>
      {output.pagination && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            Lines {output.pagination.start_line + 1}-
            {output.pagination.start_line + output.pagination.line_count} of{" "}
            {output.pagination.total_lines}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={output.pagination.start_line === 0}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!output.pagination.has_more}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
