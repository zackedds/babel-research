export type DisplayStatus = "running" | "finished" | "failed";

export type ActivitySession = {
  id: string;
  tmux_session: string;
  role: string;
  status: string;
  display_status: DisplayStatus;
  terminal_outcome: string | null;
  round_index: number | null;
  task_id: string | null;
  task_title: string | null;
  task_description: string | null;
  started_at: string;
  finished_at: string | null;
};

export type ActivitySnapshot = {
  run: {
    id: string;
    status: string;
    current_phase: string;
    rounds_completed: number;
    created_at: string;
    updated_at: string;
  };
  running: ActivitySession[];
  history: ActivitySession[];
  sessions: ActivitySession[];
};
