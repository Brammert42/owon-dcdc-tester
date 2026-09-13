# OWON DC/DC tester — desktop application

Python 3.11+ on Linux. Controls an OWON SPE15054 PSU and OEL1520 electronic load.
The desktop is the primary application; the optional localhost web interface uses
the same engine, drivers, validation and storage.

## Start

From this directory, using the installed environment:

```bash
.venv/bin/python run_desktop.py
```

The current config selects **real hardware**, but startup does not connect or
enable outputs. Click Connect to identify both instruments and verify outputs OFF.
If configured aliases no longer exist, discovery considers stable USB paths and
raw ttyUSB/ttyACM devices. Ambiguous or incorrect identities are rejected.
Use Identify ports while disconnected to choose devices explicitly.

Every desktop launch starts in **Operator mode**, with only Production visible.
Operators can connect/disconnect using saved ports, scan a PCB, run/abort tests,
enter a requested thermal reading, and use Emergency Stop. The displayed recipe
is read-only. Manual, Sweep, Graph, History, port selection and Test settings are
shown only in **Manager mode**. Use the header mode button to switch; no PIN is
required. Mode switches are disabled during tests. Returning to Operator mode
shuts down and disconnects connected instruments before changing the view.

In Manager mode, open **Test settings** to edit the profile name, input voltage
and current limit, nominal output voltage, electrical steps, thermal enable/limit/
warm-up, and completion sounds. Disconnect instruments and click **Save production
settings**. Validation and atomic saving to config.json happen before the editor
reports success. Saved settings apply immediately to subsequent tests and survive
restarts. Unsaved edits never affect production runs; reopening Manager mode reloads
the saved recipe. Settings cannot be saved while instruments are connected.

Completion sounds are enabled by default: an ascending three-note cue for PASS,
and repeated low tones for FAIL or an incomplete/error result. Sound plays after
the engine publishes the final result. Manager settings include sound preview
buttons and a saved enable/disable checkbox; audio uses the system output/volume.
The visual result remains authoritative if no audio device is available.

For simulation, with data kept under `data/simulation/`:

```bash
.venv/bin/python run_desktop.py --dry-run
```

For a disposable session use `--dry-run --data-dir /tmp/my-dcdc-session`.
Dependencies are in `requirements.txt`; `requirements-tested.txt` records the direct
versions used for regression testing. Neither launcher installs packages.

## Linux development and Windows delivery

On Linux, create or activate `.venv`, install `requirements.txt`, and run
`.venv/bin/python run_desktop.py`. Make a change, run the validation command below,
then commit and push it to the repository's `main` branch. GitHub Actions builds the
Windows package automatically on every push to `main`; it can also be started from
the repository's **Actions** tab with **Build Windows application** → **Run workflow**.

The workflow installs Python, the application dependencies, and PyInstaller on a
Windows runner. It creates a folder-based bundle, runs a simulated startup and
production smoke test, and uploads `OWON-Tester-Windows.zip` as the
`OWON-Tester-Windows` artifact. Download it from the completed workflow run under
**Artifacts**, unzip the complete folder, and launch `OWON_Tester.exe`. Keep the
executable beside its `_internal` folder and the included `README.txt`; Python is
not required on the customer's computer. Windows runtime settings and results are
stored per user under `%LOCALAPPDATA%\OWON-Tester\`.

## Operator workflows

- **Manual:** Apply PSU settings leaves both outputs off. Apply load establishes
  CC/NORM with input off. ON buttons explicitly enable each instrument. Record
  applies all displayed settings, energizes the DUT and records one averaged point.
  Finish run turns both outputs off, closes the run and makes it graphable.
- **Sweep:** Set input voltage/current, output-power range, nominal output voltage,
  point count and settle time. Start regenerates and validates all steps. Watt
  requests are converted to CC current using nominal output voltage; this is not
  closed-loop constant power. Every step begins with load/PSU off, sets up hardware,
  takes measurements, and disables load. Completion disables both outputs.
- **Production:** Scan/type a nonempty PCB identifier and click Start or press Enter.
  The displayed profile comes from config.json. Each retest gets a new run ID even
  when the label is repeated. Step and overall results are recorded; label text is
  selected for the next unit when the test ends. Operator name is optional.
- **Optional production thermal check:** A manager enables Require thermal check
  and saves the recipe before starting a unit. Maximum temperature defaults to **80°C**.
  After all electrical steps pass, the tester holds the final load for an extra
  warm-up period (default **30 seconds**, configurable from 0–300 seconds). Aim the
  THOR002 at the PCB; when prompted, enter the camera's current maximum in Celsius
  and click Record temperature and finish test. Outputs remain ON during warm-up
  and entry, with electrical measurement/limit checks continuing. Entry is allowed
  for at most **60 seconds** after warm-up; timeout shuts down and fails the run.
  Abort/Emergency Stop and application close also cancel this stage.
  A value **at or below** the limit passes the thermal check; a higher value fails
  the whole run. Electrical failures skip thermal checking and remain failures.
  The input starts blank for every run. The reading, threshold, warm-up duration,
  timestamp and `operator_entered` source are stored in SQLite and the run CSV;
  the desktop History tab shows the thermal result and maximum/limit.
  This version uses operator entry, not automatic temperature extraction from USB
  video. The snapshot button remains a separate visual inspection tool. Saved settings
  apply to every subsequent run; defaults are under
  `production_test_profile.thermal_check` in config.json. The check is disabled by
  default. Establish warm-up time, camera placement and threshold using known-good
  and missing-pad boards before using it to detect an absent thermal pad.
- **Graph:** Only completed runs are selectable. Default selection is empty. Up to
  ten traces, keyed by run ID, plot efficiency against measured output power. The
  Matplotlib toolbar saves images. Hide all only changes selection.
- **Thermal snapshot (Production):** Connect one THOR002 over USB and click
  Capture thermal image. Requires `ffmpeg` and permission to access the camera's
  `/dev/video*` device. A 640×480 rendered thermal image is previewed and saved
  under the database directory's `thermal/` folder, with a JSON sidecar containing
  the current PCB identifier, operator and UTC timestamp. Capture runs separately
  from instrument commands and times out after 15 seconds. These are manual visual
  inspection images, without numeric temperature data or automatic pass/fail;
  they are not attached to a database run. An empty identifier saves as unlabelled.
- **History:** Shows prior outcomes, identifiers, shutdown verification and CSV paths.

During a run other hardware workflows are unavailable. Stop/Abort signals
cancellation immediately; the worker performs shutdown and finalizes the result.
There is no misleading Pause/Resume control. Emergency Stop latches a fault and
attempts OFF on both instruments. Acknowledge verifies OFF again before allowing
new ON commands. Closing the desktop requests abort and waits asynchronously for
cleanup; unknown shutdown is reported rather than described as safe.

## Safety behavior and physical limits

- One driver pair and exclusive serial handles per engine; a process lease prevents
  another tester process from claiming hardware. There is no auto-replay after USB
  failure. Reconnect is explicit and revalidates identity.
- Complete step lists are validated before the first ON command. Nonfinite,
  negative, over-limit and missing values are rejected. Every measurement sample
  must be valid; failures are not converted to zeros. Input/output power is V × I,
  efficiency is 100 × Pout / Pin. Acceptance uses unrounded measurements.
- Software checks operate at measurement points and desktop idle polls, not at a
  guaranteed continuous protection rate. Serial reads/writes have finite timeouts;
  a stop cannot preempt a serial command already in progress. Settings/readback
  firmware differences are treated as errors.
- Software cannot guarantee output removal after power loss, SIGKILL, a stuck OS
  driver, USB removal or a failed OFF command. A physical emergency disconnect and
  correctly configured instrument protections remain necessary.
- `psu_output_state_format` preserves the previous voltage-style OUTPut? parsing
  (`auto`/`voltage`: >0.5 means ON); `boolean` requires 0/1 or ON/OFF. Confirm this
  against the unit's firmware before a real DUT run. CC and NORM readbacks are now
  required; unsupported replies fail closed.

## Configuration and results

`config.json` is the single profile/settings source. Configure limits, serial paths,
optional exact `psu_idn`/`load_idn` strings under `instruments`, settling and samples.
A saved configuration is validated and atomically replaced; instruments must be
disconnected. Restart to change simulation mode or storage directories. The old
16-step `steps` array remains historical configuration; generated sweeps use GUI
parameters and production uses `production_test_profile`.

SQLite `data/test_results.sqlite` is authoritative. New runs include a configuration
and profile snapshot, instrument IDs/ports, operator, simulation flag and software
version. Terminal outcomes cannot be relabeled. Historical records are preserved;
on first migration an SQLite backup is created alongside the database. New sessions
left unfinished by a crash become INTERRUPTED next startup; pre-migration unknown
results remain unknown. Timestamps include timezone offsets for new records.

Each run exports `data/test_logs/dcdc_<run_id>_<session>.csv`. CSV includes production
labels, final outcomes and shutdown verification. CSV is generated from committed
SQLite records with atomic file replacement. A failed final export latches a fault,
records an export error, and is retried next startup; it does not erase or relabel
the saved test result. Logs use unique per-session filenames and remain open across
multiple runs. Simulation defaults to a separate database/log directory.

## Validation

Tests use simulated/fake hardware, temporary directories and an offscreen Qt GUI:

```bash
QT_QPA_PLATFORM=offscreen MPLCONFIGDIR=/tmp/owon-test-matplotlib \
  .venv/bin/python -B -m unittest discover -s tests -v
```

`test_sweep_dry_run.py` runs that same isolated suite; it no longer writes to the
production database. See `docs/ROBUSTNESS_CHANGES.md` for architecture and coverage.

## Optional web compatibility

```bash
.venv/bin/python run_web.py --dry-run
```

Defaults to `127.0.0.1:5000`. `app.py` forwards to this launcher; the older monolith
is retired. Do not expose the unauthenticated hardware API to an untrusted network.
Graph selection and completed-run rules match the desktop. Both frontends cannot
simultaneously own the same results database/hardware.
