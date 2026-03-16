import { afterEach, expect, test } from "bun:test";
import { testRender } from "@opentui/react/test-utils";
import { act } from "react";
import { ActivityFeedScreen } from "./App";
import { jumpSelection, moveSelection, toggleExpandedSession } from "./state";
import type { ActivitySnapshot } from "./types";

let testSetup: Awaited<ReturnType<typeof testRender>> | undefined;

afterEach(async () => {
  if (testSetup) {
    await act(async () => {
      testSetup.renderer.destroy();
    });
    testSetup = undefined;
  }
});

const snapshot: ActivitySnapshot = {
  run: {
    id: "deadbeef",
    status: "running",
    current_phase: "worker",
    rounds_completed: 2,
    created_at: "2026-03-16T10:00:00+00:00",
    updated_at: "2026-03-16T10:05:00+00:00",
  },
  running: [
    {
      id: "worker-2",
      tmux_session: "orch-worker-2",
      role: "worker",
      status: "running",
      display_status: "running",
      terminal_outcome: null,
      round_index: 3,
      task_id: "task-2",
      task_title: "Add activity feed",
      task_description: "Build the interactive activity feed UI.",
      started_at: "2026-03-16T10:04:00+00:00",
      finished_at: null,
    },
  ],
  history: [
    {
      id: "planner-1",
      tmux_session: "orch-planner-1",
      role: "planner",
      status: "stopped",
      display_status: "finished",
      terminal_outcome: "completed",
      round_index: 2,
      task_id: null,
      task_title: null,
      task_description: null,
      started_at: "2026-03-16T10:01:00+00:00",
      finished_at: "2026-03-16T10:03:00+00:00",
    },
    {
      id: "worker-1",
      tmux_session: "orch-worker-1",
      role: "worker",
      status: "stopped",
      display_status: "failed",
      terminal_outcome: "failed",
      round_index: 2,
      task_id: "task-1",
      task_title: "Stabilize driver",
      task_description: "Lock duplicate scheduling.",
      started_at: "2026-03-16T09:58:00+00:00",
      finished_at: "2026-03-16T10:02:00+00:00",
    },
  ],
  sessions: [],
};

snapshot.sessions = [...snapshot.running, ...snapshot.history];

test("screen renders a single session table", async () => {
  testSetup = await testRender(
    <ActivityFeedScreen
      snapshot={snapshot}
      error={null}
      selectedIndex={0}
      expandedIds={new Set()}
    />,
    { width: 120, height: 40 },
  );

  await act(async () => {
    await testSetup.renderOnce();
  });
  expect(testSetup.captureCharFrame()).toMatchSnapshot();
});

test("screen renders expanded worker details", async () => {
  testSetup = await testRender(
    <ActivityFeedScreen
      snapshot={snapshot}
      error={null}
      selectedIndex={2}
      expandedIds={new Set(["worker-1"])}
    />,
    { width: 120, height: 44 },
  );

  await act(async () => {
    await testSetup.renderOnce();
  });
  const frame = testSetup.captureCharFrame();
  expect(frame).toContain("Lock duplicate scheduling.");
  expect(frame).toContain("failed");
});

test("selection helpers clamp movement and jumps", () => {
  expect(moveSelection(0, -1, snapshot.sessions.length)).toBe(0);
  expect(moveSelection(0, 1, snapshot.sessions.length)).toBe(1);
  expect(moveSelection(2, 1, snapshot.sessions.length)).toBe(2);
  expect(jumpSelection(snapshot.sessions.length, "start")).toBe(0);
  expect(jumpSelection(snapshot.sessions.length, "end")).toBe(2);
});

test("toggle helper expands only worker sessions with descriptions", () => {
  const expanded = toggleExpandedSession(new Set<string>(), snapshot.history[1]);
  expect(expanded.has("worker-1")).toBe(true);
  const collapsed = toggleExpandedSession(expanded, snapshot.history[1]);
  expect(collapsed.has("worker-1")).toBe(false);
  const unchanged = toggleExpandedSession(new Set<string>(), snapshot.history[0]);
  expect(unchanged.size).toBe(0);
});

test("screen prefers terminal outcome in the status column", async () => {
  testSetup = await testRender(
    <ActivityFeedScreen
      snapshot={snapshot}
      error={null}
      selectedIndex={1}
      expandedIds={new Set()}
    />,
    { width: 120, height: 20 },
  );

  await act(async () => {
    await testSetup.renderOnce();
  });
  const frame = testSetup.captureCharFrame();
  expect(frame).toContain("completed");
  expect(frame).not.toContain("finished");
});
