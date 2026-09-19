// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Cold-start gaia:// links must wait for the backend: a link that arrives
 * before it is ready is dispatched exactly once afterwards — not early, not lost.
 */

const {
  createStartupDeepLinkQueue,
} = require("../../src/gaia/apps/webui/services/deep-link-queue.cjs");

const LINK = "gaia://hub/install/email";
const OTHER = "gaia://hub/install/gaia";

function makeQueue() {
  const dispatch = jest.fn();
  const reportNotReady = jest.fn();
  const queue = createStartupDeepLinkQueue({
    dispatch,
    reportNotReady,
    logger: { log: jest.fn() },
  });
  return { queue, dispatch, reportNotReady };
}

describe("startup deep-link queue", () => {
  test("a link that arrives before the backend is ready is not dispatched early", () => {
    const { queue, dispatch } = makeQueue();
    expect(queue.accept(LINK)).toBe("queued");
    expect(dispatch).not.toHaveBeenCalled();
    expect(queue.pendingCount).toBe(1);
  });

  test("...and is dispatched exactly once once the backend is ready", () => {
    const { queue, dispatch } = makeQueue();
    queue.accept(LINK);
    expect(queue.drain({ backendReady: true })).toEqual([LINK]);
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenCalledWith(LINK);
  });

  test("the same link from a second launch and from argv is still dispatched once", () => {
    const { queue, dispatch } = makeQueue();
    queue.accept(LINK);
    expect(queue.accept(LINK)).toBe("duplicate");
    queue.drain({ backendReady: true, argvUrl: LINK });
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  test("a cold-start argv link with nothing queued is dispatched once", () => {
    const { queue, dispatch } = makeQueue();
    queue.drain({ backendReady: true, argvUrl: LINK });
    expect(dispatch.mock.calls).toEqual([[LINK]]);
  });

  test("distinct links are all kept, in arrival order", () => {
    const { queue, dispatch } = makeQueue();
    queue.accept(LINK);
    queue.accept(OTHER);
    queue.drain({ backendReady: true });
    expect(dispatch.mock.calls).toEqual([[LINK], [OTHER]]);
  });

  test("draining again never re-dispatches", () => {
    const { queue, dispatch } = makeQueue();
    queue.accept(LINK);
    queue.drain({ backendReady: true });
    expect(queue.drain({ backendReady: true, argvUrl: LINK })).toEqual([]);
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  test("after the drain, new links dispatch immediately, once each", () => {
    const { queue, dispatch } = makeQueue();
    queue.drain({ backendReady: true });
    expect(queue.accept(OTHER)).toBe("dispatched");
    expect(dispatch.mock.calls).toEqual([[OTHER]]);
  });

  test("if the backend never comes up, the user is told — nothing is dispatched blind", () => {
    const { queue, dispatch, reportNotReady } = makeQueue();
    queue.accept(LINK);
    queue.drain({ backendReady: false, argvUrl: OTHER });
    expect(dispatch).not.toHaveBeenCalled();
    expect(reportNotReady).toHaveBeenCalledWith([LINK, OTHER]);
  });

  test("with nothing queued, a failed startup reports nothing", () => {
    const { queue, reportNotReady } = makeQueue();
    queue.drain({ backendReady: false });
    expect(reportNotReady).not.toHaveBeenCalled();
  });

  test("refuses to build without its collaborators", () => {
    expect(() => createStartupDeepLinkQueue({ dispatch: jest.fn() })).toThrow(
      /dispatch and reportNotReady/
    );
  });
});
