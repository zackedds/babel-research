import type { ActivitySession } from "./types";

export function moveSelection(current: number, delta: number, length: number) {
  if (length <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(current + delta, length - 1));
}

export function jumpSelection(length: number, target: "start" | "end") {
  if (length <= 0) {
    return 0;
  }
  return target === "start" ? 0 : length - 1;
}

export function toggleExpandedSession(
  expandedIds: ReadonlySet<string>,
  session: ActivitySession | undefined,
) {
  if (!session?.task_description) {
    return new Set(expandedIds);
  }
  const next = new Set(expandedIds);
  if (next.has(session.id)) {
    next.delete(session.id);
  } else {
    next.add(session.id);
  }
  return next;
}
