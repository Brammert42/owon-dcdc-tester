# Desktop robustness implementation

## Scope and baseline

The pre-change repository had separate manual/production and sweep drivers, an
independently launchable monolith, worker-thread Qt updates and nonterminal sweep
abort handling. The baseline was inspected without instrument I/O. Implementation
was developed in a separate staging tree. The installed `.venv` is Python 3.11.15;
tested direct dependency versions are recorded in `requirements-tested.txt`.

## Requested improvement mapping

1. **Baseline/regressions:** isolated unittest suite covering sequence, transport,
   storage, Qt responsiveness/thread delivery and Flask integration. No test connects
   physical instruments or uses production data.
2. **Terminal fault/abort:** a synchronous sequence executor raises out of the loop;
   an owning worker performs both shutdown attempts and immutable finalization.
   Emergency cancellation is set before waiting for I/O. Shutdown errors are retained.
3. **Hardware ownership:** one shared pair, a whole-operation lease, RLock-protected
   I/O, OS serial exclusivity and a process hardware lease. Overlapping tests/manual
   changes, reconnects and scans are rejected.
4. **Validation:** finite nonnegative numbers, all planned steps validated before ON,
   complete valid measurement samples, output collapse/over-limit checks, explicit
   fault acknowledgement. No partial-sample zero substitution.
5. **Production traceability:** strict labels, indexed steps, profile/config/identity
   snapshots, separate retest IDs, PASS/FAIL/ERROR/ABORTED outcomes, both DB and CSV.
   SQLite results are authoritative; export failures are separately tracked/retried.
6. **Serial consolidation:** one transport/driver API; exact model classification,
   no same-device roles, optional exact ID binding, unique discovery candidates,
   bounded transport I/O and no stale automatic command replay.
7. **Desktop threading:** serialized background commands, separate emergency worker,
   queued Qt signal delivery, cached status and asynchronous close. Timer tests
   establish that settling does not block the GUI.
8. **Graphs:** shared completed-run selection by ID, <=10, watts on X, empty initial
   selection, restored history, updated desktop checkbox lists, no partial traces.
9. **Configuration/storage/simulation:** validated atomic config snapshots, a single
   profile source and simulator, instance-scoped Store, additive database migration
   with backup, unique CSV/log paths, timezone-aware timestamps and tested versions.
10. **Retirement:** app.py forwards to run_web.py. Root helpers and old driver names
    are compatibility imports; they no longer contain independent hardware logic.
    The old SessionModel implementation is retired. Existing screenshots/historical
    planning documents are retained as historical material, not specifications.

## Architecture

```text
Qt widgets -> Controller worker pool -> TestEngine operation lease
                                           |
Web routes -------------------------------+
                                           |
                 SweepRunner (manual/sweep/production step executor)
                                           |
                       OwonPSU + OwonLoad / SimPSU + SimLoad
                                           |
                             exclusive SerialResource handles

Measurements -> validity/safety -> SQLite Store -> atomic CSV export
                                   |
                            completed RunDataManager
                                   |
                       queued Qt / SocketIO event delivery
                                   |
                         Matplotlib / Plotly selected runs
```

The main change references are TestEngine._claim/_action/_run/_finish/_shutdown,
SweepRunner.prepare/measure, measurement.take_measurement, Store.update_run_result,
Controller.submit/_deliver, and RunDataManager selection methods.

## Regression coverage

Tests exercise successful runs, failure on the first step without later activation,
load and PSU faults, invalid/partial measurements, immediate settling cancellation,
emergency latching, workflow exclusion, production failures and indexed CSV data,
duplicate-label retests, immutable terminal results, storage/export failures,
completed-only graphs and restoration, persistent diagnostic logging, exclusive
serial parameters, wrong/swapped identification, truncated responses, legacy-schema
backup/preservation, new-session crash recovery, Qt signal thread affinity and
window close during production.

## Intentional behavior changes

- Desktop is primary; startup never auto-connects hardware.
- Apply PSU/load keeps outputs off. Record explicitly applies settings and energizes.
- Manual/sweep/production now use the same sample-count setting and strict validation.
- Load and PSU are off before changing voltage; both are off at run completion.
  Legacy keep-output-on sweep flags are no longer supported.
- Pause/Resume has been removed from desktop and disabled/rejected in web.
- Repeated labels are separate retests; graph identity is run ID, not label/name.
- Production profiles live in config.json. The inspected data/profiles directory
  was empty; named fallback profiles are no longer silently substituted.
- Legacy function-style database calls are retired; consumers use engine.store.
- Final CSV export failure preserves the saved SQLite result and latches a separate
  export fault. Startup retries pending exports. Historical NULL results are not
  invented or repaired.

## Remaining bench validation

No physical ON/OFF or identification commands were sent during implementation.
The sandbox could not see the host's USB serial devices. Verify actual firmware
readbacks for OUTPut?, FUNCtion?, MODE? and CURRent? before a real DUT acceptance run.
The existing set-voltage/current writes are preserved because earlier code reports
that those registers cannot reliably be read back while the SPE is off.

This is software hardening, not certification of physical shutdown. USB loss or a
crashed host can leave previously energized hardware on; use independent hardware
protection. Stop is cooperative between bounded serial commands. Monitoring occurs
at samples and idle polling, not as a real-time hardware protection loop.

## Installation and rollback

The installer checks source hashes against the inspected baseline, refuses to
replace files while a known tester launcher is running, backs up changed sources,
and atomically replaces each file. It does not start the application or modify
results. The first normal application startup performs the backed-up DB migration.

For rollback, stop the application and restore the source backup. Preserve a copy
of the current database before considering any database rollback: restoring the
pre-migration database would discard newer test results. The migration only adds
columns, so historical data is retained.
