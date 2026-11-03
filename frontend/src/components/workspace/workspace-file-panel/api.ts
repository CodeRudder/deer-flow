import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

/**
 * Contract of the gateway's thread-workspace file browser API.
 *
 * GET ``/api/threads/{threadId}/workspace/files?path=<virtual dir>&search=<kw>``
 * returns the (directories-first sorted) listing of one virtual directory:
 *
 * - ``path``          — the virtual directory that was listed
 * - ``entries``       — ``{name, path, type: "file" | "directory",
 *                       size (null for directories), modified_at}``
 * - ``truncated``     — true when the listing was capped by the backend
 *
 * A 404 means the directory does not exist; any other non-2xx is a generic
 * failure (retryable).
 */
export type WorkspaceEntryType = "file" | "directory";

export interface WorkspaceEntry {
  name: string;
  path: string;
  type: WorkspaceEntryType;
  size: number | null;
  modified_at: string | null;
}

export interface WorkspaceListing {
  path: string;
  entries: WorkspaceEntry[];
  truncated: boolean;
}

export const WORKSPACE_ROOT_PATH = "/mnt/user-data";

/** Error kind used by the panel to pick an empty-state vs retry UI. */
export class WorkspaceDirectoryNotFoundError extends Error {
  constructor(path: string) {
    super(`Workspace directory not found: ${path}`);
    this.name = "WorkspaceDirectoryNotFoundError";
  }
}

function workspaceFilesURL(
  threadId: string,
  path: string,
  search?: string,
): string {
  const params = new URLSearchParams({ path });
  if (search) params.set("search", search);
  return `${getBackendBaseURL()}/api/threads/${threadId}/workspace/files?${params.toString()}`;
}

export async function fetchWorkspaceListing(
  threadId: string,
  path: string = WORKSPACE_ROOT_PATH,
  search?: string,
): Promise<WorkspaceListing> {
  const response = await fetchWithAuth(
    workspaceFilesURL(threadId, path, search),
  );
  if (response.status === 404) {
    throw new WorkspaceDirectoryNotFoundError(path);
  }
  if (!response.ok) {
    throw new Error(
      `Failed to list workspace files: ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as WorkspaceListing;
}
