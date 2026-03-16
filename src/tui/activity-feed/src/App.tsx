import { useEffect, useRef, useState } from "react";
import { useKeyboard, useRenderer } from "@opentui/react";
import type { ActivitySession, ActivitySnapshot } from "./types";
import { loadActivitySnapshot } from "./api";
import { jumpSelection, moveSelection, toggleExpandedSession } from "./state";

type ActivityFeedAppProps = {
  rootPath: string;
  packageRootPath?: string;
  pythonPath: string;
  runId?: string;
  initialSnapshot?: ActivitySnapshot | null;
  initialError?: string | null;
  loader?: (pythonPath: string, rootPath: string, packageRootPath: string, runId?: string) => ActivitySnapshot;
  pollIntervalMs?: number;
  poll?: boolean;
  initialSelectedIndex?: number;
};

type ActivityScreenProps = {
  snapshot: ActivitySnapshot | null;
  error: string | null;
  selectedIndex: number;
  expandedIds: ReadonlySet<string>;
};

const ROLE_WIDTH = 10;
const ROUND_WIDTH = 7;
const TMUX_WIDTH = 20;
const TASK_WIDTH = 34;
const STATUS_WIDTH = 12;

export function ActivityFeedApp({
  rootPath,
  packageRootPath = rootPath,
  pythonPath,
  runId,
  initialSnapshot = null,
  initialError = null,
  loader = loadActivitySnapshot,
  pollIntervalMs = 500,
  poll = true,
  initialSelectedIndex = 0,
}: ActivityFeedAppProps) {
  const renderer = useRenderer();
  const [snapshot, setSnapshot] = useState<ActivitySnapshot | null>(initialSnapshot);
  const [error, setError] = useState<string | null>(initialError);
  const [selectedIndex, setSelectedIndex] = useState(initialSelectedIndex);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());

  const sessions = snapshot?.sessions ?? [];
  const sessionsRef = useRef<ActivitySession[]>(sessions);
  const selectedIndexRef = useRef(selectedIndex);

  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  useEffect(() => {
    selectedIndexRef.current = selectedIndex;
  }, [selectedIndex]);

  useEffect(() => {
    if (initialSnapshot !== null || initialError !== null) {
      return;
    }
    try {
      setSnapshot(loader(pythonPath, rootPath, packageRootPath, runId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [initialError, initialSnapshot, loader, packageRootPath, pythonPath, rootPath, runId]);

  useEffect(() => {
    if (!poll) {
      return;
    }
    const timer = setInterval(() => {
      try {
        const next = loader(pythonPath, rootPath, packageRootPath, runId);
        setSnapshot((current) => {
          const nextSerialized = JSON.stringify(next);
          const currentSerialized = current ? JSON.stringify(current) : null;
          return currentSerialized === nextSerialized ? current : next;
        });
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }, pollIntervalMs);
    return () => clearInterval(timer);
  }, [loader, packageRootPath, poll, pollIntervalMs, pythonPath, rootPath, runId]);

  useEffect(() => {
    setSelectedIndex((current) => {
      if (sessions.length === 0) {
        return 0;
      }
      return Math.min(current, sessions.length - 1);
    });
  }, [sessions.length]);

  useKeyboard((key) => {
    const currentSessions = sessionsRef.current;
    if (key.name === "escape" || key.name === "q") {
      renderer.destroy();
      return;
    }
    if (currentSessions.length === 0) {
      return;
    }
    if (key.name === "down" || key.name === "j") {
      setSelectedIndex((current) => moveSelection(current, 1, currentSessions.length));
      return;
    }
    if (key.name === "up" || key.name === "k") {
      setSelectedIndex((current) => moveSelection(current, -1, currentSessions.length));
      return;
    }
    if (key.name === "g" && !key.shift) {
      setSelectedIndex(jumpSelection(currentSessions.length, "start"));
      return;
    }
    if ((key.name === "g" && key.shift) || key.name === "end") {
      setSelectedIndex(jumpSelection(currentSessions.length, "end"));
      return;
    }
    if (key.name === "enter" || key.name === "space") {
      const selected = currentSessions[selectedIndexRef.current];
      setExpandedIds((current) => toggleExpandedSession(current, selected));
    }
  });

  return (
    <ActivityFeedScreen
      snapshot={snapshot}
      error={error}
      selectedIndex={selectedIndex}
      expandedIds={expandedIds}
    />
  );
}

export function ActivityFeedScreen({
  snapshot,
  error,
  selectedIndex,
  expandedIds,
}: ActivityScreenProps) {
  const sessions = snapshot?.sessions ?? [];
  const selectedId = sessions[selectedIndex]?.id ?? null;

  return (
    <box flexDirection="column" width="100%" height="100%" padding={1} backgroundColor="#11161d">
      <Header snapshot={snapshot} error={error} />
      <box marginTop={1}>
        <text fg="#8ea0b3">{formatColumns("role", "round", "tmux session", "task", "status")}</text>
      </box>
      <box marginBottom={1}>
        <text fg="#425365">{horizontalRule()}</text>
      </box>
      <scrollbox flexGrow={1}>
        <box flexDirection="column" paddingRight={1}>
          {sessions.length === 0 ? (
            <text fg="#7f8a99">No agent sessions yet.</text>
          ) : (
            sessions.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                selected={selectedId === session.id}
                expanded={expandedIds.has(session.id)}
              />
            ))
          )}
        </box>
      </scrollbox>
    </box>
  );
}

function Header({
  snapshot,
  error,
}: {
  snapshot: ActivitySnapshot | null;
  error: string | null;
}) {
  const runLabel = snapshot
    ? `run ${snapshot.run.id}  ${snapshot.run.status}  phase=${snapshot.run.current_phase}  rounds=${snapshot.run.rounds_completed}`
    : "Loading activity feed...";

  return (
    <box flexDirection="column">
      <text>
        <strong>bab activity</strong>
        <span fg="#8ea0b3">  {runLabel}</span>
      </text>
      <box flexDirection="row" gap={2}>
        <text fg="#7f8a99">move: j/k or arrows</text>
        <text fg="#7f8a99">expand: enter/space</text>
        <text fg="#7f8a99">jump: g/G</text>
        <text fg="#7f8a99">quit: q</text>
      </box>
      {error ? <text fg="#ff8c82">Snapshot error: {error}</text> : null}
    </box>
  );
}

function SessionRow({
  session,
  selected,
  expanded,
}: {
  session: ActivitySession;
  selected: boolean;
  expanded: boolean;
}) {
  const roleLabel = fitValue(session.role, ROLE_WIDTH);
  const roundLabel = fitValue(session.round_index === null ? "R?" : `R${session.round_index}`, ROUND_WIDTH);
  const tmuxLabel = fitValue(session.tmux_session, TMUX_WIDTH);
  const taskLabel = fitValue(taskValue(session), TASK_WIDTH);
  const statusValue = finalStatus(session);
  const statusLabel = fitValue(statusValue, STATUS_WIDTH);
  const rowColor = selected ? "#11161d" : "#d7e2ef";
  const rowBackground = selected ? "#f0c674" : undefined;

  return (
    <box flexDirection="column">
      <text fg={rowColor} bg={rowBackground}>
        {selected ? "> " : "  "}
        {formatColumns(roleLabel, roundLabel, tmuxLabel, taskLabel, statusLabel)}
      </text>
      {expanded && session.task_description ? (
        <text fg={selected ? "#f0c674" : "#9fb1c3"}>
          {selected ? "  " : "    "}
          {session.task_description}
        </text>
      ) : null}
    </box>
  );
}

function formatColumns(role: string, round: string, tmux: string, task: string, status: string) {
  return [
    padCell(role, ROLE_WIDTH),
    padCell(round, ROUND_WIDTH),
    padCell(tmux, TMUX_WIDTH),
    padCell(task, TASK_WIDTH),
    padCell(status, STATUS_WIDTH),
  ].join("  ");
}

function padCell(value: string, width: number) {
  const trimmed = fitValue(value, width);
  return trimmed.padEnd(width, " ");
}

function fitValue(value: string, width: number) {
  if (value.length <= width) {
    return value;
  }
  if (width <= 1) {
    return value.slice(0, width);
  }
  return `${value.slice(0, width - 1)}…`;
}

function taskValue(session: ActivitySession) {
  if (session.role !== "worker") {
    return "-";
  }
  return session.task_title || session.task_id || "Unlabeled task";
}

function finalStatus(session: ActivitySession) {
  return session.terminal_outcome || session.status;
}

function horizontalRule() {
  return "-".repeat(ROLE_WIDTH + ROUND_WIDTH + TMUX_WIDTH + TASK_WIDTH + STATUS_WIDTH + 8);
}
