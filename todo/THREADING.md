# TraceLab — Non-blocking UI threading plan

Goal: any operation that can take >~16ms must not freeze the Qt event loop.
The user must always be able to pan, zoom, and interact while heavy work runs.
Every step below is independently testable and mergeable.

---

## Step 1 — Thread-pool interpolation in set_interp_mode / set_interp_mode_for_trace  [HIGHEST PRIORITY]

**Problem**: set_interp_mode() loops over all lanes calling refresh_curve()
synchronously. Each call may run sinc FFT or scipy CubicSpline. With many
channels this takes 2-3 s, freezing the UI completely.

**Approach**:
- Add an InterpolationWorker(QRunnable) that computes (t, y) for one lane and
  emits a result signal with (lane_id, t, y).
- In set_interp_mode(), instead of calling lane.refresh_curve() directly:
  1. Set self._interp_mode immediately (so UI state is consistent).
  2. Post a "pending interp" flag per lane.
  3. Submit one QRunnable per lane to QThreadPool.globalInstance().
  4. Start a 300 ms QTimer. If it fires, post a notice to NoticeBarWidget:
     "Applying interpolation…" (key: "interp_processing").
  5. Each QRunnable emits result on the main thread via a queued signal.
     The slot calls lane._curve.setData(t, y) + _apply_resolved_style().
  6. When all lanes have reported back (track count), clear the notice.

**Notes**:
- scipy / numpy release the GIL, so all lanes can run truly in parallel.
- lane._curve.setData() must only be called from the main thread — emit a
  signal from the worker, connect with Qt.ConnectionType.QueuedConnection.
- Use a generation counter or cancellation flag per lane so stale results from
  a superseded mode change are silently dropped.
- The same pattern applies to set_interp_mode_for_trace (single lane, simpler).

**Acceptance criteria**:
- Clicking CUB or SINC returns control to the UI immediately.
- Notice appears after 300 ms if rendering is still in progress.
- Notice disappears when the last lane finishes.
- Panning and zooming while the pool runs produces correct final output
  (stale in-flight results are discarded if the view range changed).

---

## Step 2 — Debounce + background interp on pan/zoom

**Problem**: every sigRangeChanged fires _add_trace_curve() on all lanes
synchronously. At sinc/cubic mode with many channels this causes jank on drag.

**Approach**:
- _range_timer already debounces at 100 ms — this is the right hook point.
- When _range_timer fires and interp mode is sinc/cubic: instead of calling
  refresh_curve() directly, submit workers as in Step 1.
- While workers are in flight, keep showing the current (slightly stale) curve.
  Only swap data when the new result arrives.
- No notice needed for pan/zoom (too frequent); just non-blocking redraw.

**Notes**:
- Use the same generation counter as Step 1 — if a new pan happens before the
  previous batch completes, cancel/discard the old batch.
- Linear mode stays synchronous (it's O(n) slicing + downsample, always fast).

---

## Step 3 — Thread-safe data loading

**Problem**: data_loader.py parses CSV/binary files on the main thread.
Large files (100 MB+) can block the UI for several seconds.

**Approach**:
- Wrap the parse call in a QThread (same pattern as _PeriodEstimateWorker).
- Show a progress notice immediately on load start.
- Emit a signal with the loaded TraceModel list when done.
- On signal: call batch_add_traces() and clear the notice.
- The import dialog stays open and responsive during load.
- On error: emit an error signal, show an error notice.

**Notes**:
- The parser must not touch any Qt widgets (it shouldn't — check first).
- segment detection (csv_detector) can run on the same thread as parsing.

---

## Step 4 — Background period estimation (already threaded — audit)

Period estimation already uses _PeriodEstimateWorker (QThread). This step is
an audit pass:
- Verify the notice bar is wired up for period estimation (if not, add it).
- Verify the 300 ms deferred-notice pattern is consistent with what Step 1
  introduces, and consolidate if there is duplicated timer logic.

---

## Step 5 — Background retrigger / averaging computation

**Problem**: _run_retrigger_analysis() (or equivalent) runs synchronously when
waveform averaging / retrigger mode is active and new data arrives.

**Approach**:
- Identify all entry points that trigger re-analysis.
- Move the heavy maths (averaging, correlation, interpolation of result) to a
  QRunnable / QThread.
- Keep per-trace result-display update on the main thread via queued signal.
- Show/clear a notice for "Averaging…" / "Retrigger processing…".

---

## General patterns to use throughout

1. **Worker**: QRunnable subclass, emits result via a bridge QObject signal
   (QRunnable itself cannot emit signals — use a helper object).
2. **Main-thread update**: always via Qt.ConnectionType.QueuedConnection so
   the slot runs in the event loop, never from a worker thread directly.
3. **Cancellation**: a generation counter (int, incremented each time the user
   changes mode/range) lets workers self-discard stale results without locking.
4. **Notice pattern**: start QTimer(300 ms) at work submission; show notice on
   timeout; clear notice when last worker signals done. Reuse the same notice
   key ("interp_processing", "loading", etc.) so re-submissions auto-replace.
5. **Never QThreadPool for Qt-widget work**: all widget mutations (setData,
   setPen, addItem, removeItem) happen on the main thread only.
