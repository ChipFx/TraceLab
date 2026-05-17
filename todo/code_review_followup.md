# Code review follow-ups

Format: `path:line(s) — problem. Fix: action.`
Severity tags inline: CRIT bug/leak · SMELL architecture · LINT dead/unused · POLISH style.

## Execution order

1. **§A** critical functional bugs — small, verify each
2. **§B** orphan QTimer / singleShot — same pattern, sweep
3. **§G** lint / dead code — quick wins
4. **§E** pytraceview boundary — define new public APIs first; each new theme key needs an entry in `pytraceview/changelog/theme.md` per submodule CLAUDE.md
5. **§C** i18n / `tr()` coverage — mechanical, batch per file
6. **§D** hardcoded colours — theme JSON + code coordinated per CLAUDE.md
7. **§F** architectural smells in recent maths/filter work — last

---

## A. Critical functional bugs

1. **CRIT** `core/filter_dialog.py:123, 301` — `getattr(t, 'processed_data', None)` references nonexistent attribute; only `_processed_data_cache` exists. `n` is always 0, so Nyquist / min-freq columns and the comb-cost warning are silently disabled. **Fix:** use `t.primary().data` (or `t.n_samples`).

2. **CRIT** `core/main_window.py:_reevaluate_maths_traces (~2572)` — `if not any(v == "filtered" ...)` skips recipes where all sources are in "raw" mode. A chain where M2 reads M1 in raw mode never re-evaluates after M1's segments change. **Fix:** re-evaluate when any direct/transitive source changed, regardless of mode.

3. **CRIT** `core/maths_status_block.py:174-177` — `any_filt` checks `recipe.filter_mode == "filtered"` (the default), so every maths trace shows FILT unconditionally. **Fix:** also require that at least one source trace `has_filter`.

4. **CRIT** `core/main_window.py:~3026` — `open(p).read()` for NOTICE/LICENSE leaks the file handle. **Fix:** `with open(p, encoding="utf-8") as fh:`.

## B. Orphan QTimer / singleShot (CLAUDE.md: no orphan timers)

All four cases: replace `QTimer.singleShot(0, callable)` with the Qt6 context-object overload `QTimer.singleShot(0, self, callable)` so the call is dropped if the parent dies.

5. **CRIT** `core/apply_maths_dialog.py:70` — `_UnitLineEdit.focusInEvent`.
6. **CRIT** `core/import_dialog.py:44` — same pattern.
7. **CRIT** `pytraceview/channel_panel.py:666` — `ChannelPanel.__init__`'s `_update_minimum_width` call.
8. **CRIT** `core/main_window.py:~3445` — `_estimate_period_async` per-trace timeout `QTimer(self)` accumulates as MainWindow children when the function restarts before completion. **Fix:** keep timers in a dict keyed by trace.name, explicitly stop+delete on result/timeout.

## C. i18n / tr() coverage (CLAUDE.md: wrap every user-visible string)

For QObject subclasses use `self.tr(...)`. For module/painter code use `QCoreApplication.translate("Ctx", ...)`.

9. **SMELL** `core/main_window.py` lines ~2476, 2641, 2648, 2673, 2679, 2730, 2752, 2772, 2815, 2896 — `_status_lbl.setText(...)` in maths/filter methods are unwrapped English.
10. **SMELL** `core/main_window.py:_build_menus (~781-1370)` — ~150 menu labels, statustips, tooltips literal English; only ~3 `self.tr()` calls exist. Sweep every `addAction`, `setStatusTip`, `setToolTip`.
11. **SMELL** `core/main_window.py:2063, 2155, 3032, 3203, 3243, 3330, 3606, 4552` — 8 `dlg.setWindowTitle("…")` without `tr()`.
12. **SMELL** `core/main_window.py:1496, 1669, 2493, 2978, 2998, 4605` — `QMessageBox.*` with untranslated literals.
13. **SMELL** `core/fft_dialog.py:129, 846, 865, 877` — `setWindowTitle("FFT Analysis")` + 3 MessageBox titles.
14. **SMELL** `core/theme_editor.py:230, 358, 416, 448` — window title + prompts.
15. **SMELL** `core/import_dialog.py:465, 916, 1055, 1228, 1379` — dialog titles and MessageBox strings.
16. **SMELL** `core/edit_filter_stack_dialog.py:81, 89` — `self.tr(f"…{trace_label}…")` uses f-strings inside `tr()`; pylupdate6 can't extract them. **Fix:** `self.tr("Filter Stack — {}").format(trace_label)` (Python format), not f-string.
17. **SMELL** `core/scope_status_bar.py:153, 164` — painted "TIME BASE" / "TRIGGER" labels literal. Use `QCoreApplication.translate("ScopeStatusBar", "…")`.
18. **SMELL** `core/channel_status_block.py:242, 259` — painted "APERIODIC" / "EXTRAP" badges literal. Use `QCoreApplication.translate("ChannelStatusBlock", "…")`.

## D. Hardcoded colours (CLAUDE.md: theme-managed only)

Pattern: pull from `self._pal` (status block / scope bar) or `self._pv` (channel panel) or `self.theme.{pv,sb}(...)`. Each new key must be added to every theme in `themes/` (and the pytraceview submodule changelog for items 24, 25).

19. **SMELL** `core/channel_status_block.py:250, 254, 267, 271` — APERIODIC bg `#cc7700`, EXTRAP bg `#006699`, badge text `#ffffff`. **Fix:** add `badge_aperiod_bg/fg`, `badge_extrap_bg/fg` to statusbar palette in every theme.
20. **SMELL** `core/apply_maths_dialog.py:593-595 (_LockedRow badge)` — hardcodes `#886600` / `#ffffff` instead of using existing `maths_id_badge_bg/fg` theme keys. **Fix:** pass palette via parent, read keys.
21. **SMELL** `core/apply_maths_dialog.py:~304` — unit-hint label `#888`. **Fix:** reuse `info_dim` or add `hint_dim_fg`.
22. **SMELL** `core/filter_dialog.py:~90, 200-202, 228, 243, 247, 250` — active-filter banner, comb-warning, Apply button, validation-feedback styles hardcoded. **Fix:** add `feedback_ok_bg/fg`, `feedback_warn_bg/fg`, `feedback_error_bg/fg` palette keys.
23. **SMELL** `core/edit_filter_stack_dialog.py:46-60` — drag-grip `#666`, delete-button `#884444`/`#ff6666`. **Fix:** thread palette via parent.
24. **SMELL** `pytraceview/channel_panel.py:154, 196-198, 281, 286-289 (ChannelRow grip/delete/badge)` — grip `#555`, delete `#884444`/`#ff6666`, badge hover `#cc4400`, empty-badge `#555`/`#444`. **Fix:** add to plotview palette + entries in `pytraceview/changelog/theme.md`.
25. **SMELL** `core/scope_status_bar.py:31` — `_sep_widget(color="#1e1e38")` default. **Fix:** drop default, force explicit colour.
26. **SMELL** `core/main_window.py:1803` — taskbar icon fill `QColor("#060610")`. **Fix:** `self.theme.sb("logo_bg")`.
27. **SMELL** `core/main_window.py:3366` — OK button styled `background:#2060c0;color:white;`. **Fix:** rely on global stylesheet / theme accent.
28. **SMELL** `core/trigger_panel.py:58-60` — TRIGGER header `#888` before palette applied. **Fix:** defer style to `set_palette`.
29. **SMELL** `core/cursor_panel.py:213, 222, 229, 254` — initial colours hardcoded as constructor defaults. **Fix:** apply palette before first paint.
30. **SMELL** `core/fft_dialog.py:350-428` — whole FFT dialog hardcoded (`#050508`, `#e0e0e0`, `#aaa`, `#333`, `#e05050`, `#ff6666`, …). FFT dialog is theme-blind. **Fix:** pull from active theme palette.
31. **SMELL** `core/import_dialog.py:491-1464` (multiple) — ~15 hardcoded hexes for panel bg, banner, button, hint text. **Fix:** route via `_pv` palette.
32. **POLISH** `themes/dark.json` — missing `interp_sinc_color` / `interp_cub_color` (present in every other theme; fallback covers it). **Fix:** add for parity.

## E. TraceLab → pytraceview boundary

Each item requires adding a public API to pytraceview and an entry in `pytraceview/changelog/theme.md` if it touches the theme schema.

33. **SMELL** `core/main_window.py:2389-2400, 2402-2408 (_zoom_full_safe)` — calls `lane.getPlotItem().setXRange/setYRange/disableAutoRange` directly. **Fix:** add `TraceView.set_lane_range(lane, x0, x1, y0, y1)`.
34. **SMELL** `core/main_window.py:2315, 2339` — reads `getattr(ax, '_last_tick_result', None)` (private cache in `pytraceview/display_items.py`). **Fix:** expose `get_axis_major_tick(plot_item)` on TraceView or EngineeringAxisItem.
35. **SMELL** `core/main_window.py:3434, 3465, 3478 + core/channel_panel.py:51 + core/main_window.py:3169, 3688, 3883, 2298` — TraceLab monkey-patches `trace._period_timed_out` and `trace._interp_mode_override` on pytraceview's TraceModel. **Fix:** keep both in dicts on MainWindow keyed by trace.name; pass via existing public APIs.
36. **SMELL** `core/main_window.py:~2471, 2697, 2722` — same pattern: `trace._filter_stack_summary` monkey-patched from outside. **Fix:** keep in a TraceLab `_filter_stack_summaries` dict and have the status block read it from a parameter, mirroring how `_maths_recipes` works.

## F. Architectural smells in recent maths/filter work

37. **SMELL** `core/main_window.py:2542 (_on_maths_applied)` — `result_trace.set_user_color(self.theme.maths_color(maths_count))` sets `use_theme_color=False`; theme switches won't recolour maths traces. **Fix:** add `TraceModel.reset_color_to_maths_theme(index)` mirroring `reset_color_to_theme`; teach `sync_theme_color` to route to `theme.maths_color(idx)` when set.
38. **SMELL** `pytraceview/channel_panel.py:670-680` + `core/apply_maths_dialog.py:73-83` — `next_available_id` duplicated. **Fix:** dialog calls panel's `next_available_id()` via parent, or extract to a shared util in `pytraceview/maths_engine.py`.
39. **SMELL** `core/main_window.py:_apply_maths_id_remap (~2598-2618)` — mutates `trace.maths_id` in a single pass; safe today only because IDs are unique. **Fix:** snapshot old IDs first, write after.
40. **SMELL** `pytraceview/maths_engine.py:113 (infer_unit)` — outer `try/except Exception` swallows everything. **Fix:** narrow to `(SyntaxError, ValueError, TypeError, KeyError)`.
41. **SMELL** `core/main_window.py:_apply_filter_stack (~2714)` — bare `except Exception:` around `sosfiltfilt` silently drops non-`FilterEngineError` crashes. **Fix:** surface via `_status_lbl` or notice bar.
42. **SMELL** `core/main_window.py:_recalc_filters_for_trace (~2790-2810)` — unconditionally calls `_reevaluate_maths_traces()`. **Fix:** only re-evaluate the dependent subtree of `trace_name` (use topo order to find descendants).
43. **SMELL** `core/main_window.py:_cleanup_maths_traces (~2877-2897)` — always calls `_renumber_maths_ids` even when no orphans removed. **Fix:** skip when `not orphans`.
44. **SMELL** `core/filter_dialog.py:64-81` — docstring says trace-selection list is hidden in single-trace mode but the table is still built. **Fix:** skip `grp_trace` groupbox when `_single_trace` is set.
45. **SMELL** `core/main_window.py:_reevaluate_maths_traces (~2585)` — relies on `_plot.add_trace(existing)` to refresh; never calls `_channel_panel.refresh_all()`. Inconsistent with `_on_maths_applied`. **Fix:** one explicit refresh at end of loop.

## G. Lint / dead code

46. **LINT** `core/apply_maths_dialog.py:32, 102` — `QObject` imported but unused; `self._existing_recipes` stored but never read. **Fix:** drop both.
47. **LINT** `core/filter_dialog.py:25` — `describe_recipe` imported but unused. **Fix:** drop.
48. **LINT** `pytraceview/maths_engine.py:29-30` — `field` and `Optional` imported but unused. **Fix:** trim.
49. **LINT** `core/maths_status_block.py:20, 98` — `QFontMetrics` imported at top then re-imported locally inside `_truncate`. **Fix:** drop local re-import.
50. **LINT** `core/main_window.py:2536, 2548 (_on_maths_applied)` — `self._channel_panel.refresh_all()` called twice. **Fix:** keep only the trailing call.
51. **LINT** `core/maths_status_block.py` module docstring (~14) — claims "All colours come from the theme palette — no hardcoded hex values" but file uses `_pal.get(..., "#hex")` fallbacks throughout. **Fix:** remove the claim, or drop the fallbacks (latter ties to §D24).
52. **LINT** `core/filter_dialog.py:30-31` — `_parse_si_freq = parse_si_freq` / `_format_si_freq = format_si_freq` aliases are refactor leftovers. **Fix:** drop, call public names directly.
53. **LINT** `core/edit_filter_stack_dialog.py:83` — `self._trace_label` stored but never read after `__init__`. **Fix:** drop.
54. **LINT** `pytraceview/channel_panel.py:569, 695` — `maths_id_changed` signal still defined and emitted but no `.connect()` exists in this project (we removed the auto-eval connection). Keep for external pytraceview hosts or remove. **Fix:** if keeping, document in `pytraceview/CLAUDE.md` as a host-extension hook; otherwise remove the signal + emits.
55. **LINT** `core/main_window.py:594-595` — `notice_cycle_interval_s` read from settings but never written in `_save_settings`. **Fix:** add to save dict.
56. **LINT** `core/main_window.py:590` — `"language"` setting key read at startup; never written; no UI to change. **Fix:** add language switcher persistence, or document as manual-edit key.
57. **LINT** `core/main_window.py:2455` — `self._settings["fft_max_freq"] = dlg.max_freq_hz` written every dialog close, never read at startup. **Fix:** read in `_build_ui` to restore last FFT range.
58. **LINT** `core/main_window.py:4709-4712` — comment claims "Parentless timers (_status_bar_refresh_timer, …) are not stopped by Qt's parent-child teardown", but those timers ARE parented to `self`. **Fix:** delete or correct the misleading comment.
