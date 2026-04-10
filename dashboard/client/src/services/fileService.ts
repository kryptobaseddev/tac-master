/**
 * Stub fileService for tac-master.
 *
 * The orchestrator_3_stream version of this service opens files in the
 * developer's IDE via a backend endpoint. tac-master doesn't support
 * that (runs happen in isolated worktrees on a remote LXC), so this
 * stub returns a no-op result. The UI elements that call this remain
 * functional but the "open in IDE" buttons simply won't jump.
 */

export interface OpenFileResult {
  success: boolean;
  /** 'success' | 'error' — for compatibility with callers checking result.status */
  status?: 'success' | 'error';
  message?: string;
}

export async function openFileInIDE(_absolutePath: string): Promise<OpenFileResult> {
  return {
    success: false,
    message: "Opening files in IDE is not supported in tac-master. The file lives on the remote LXC inside an isolated worktree.",
  };
}
