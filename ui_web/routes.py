"""HTTP adapters for the same guarded engine methods used by the desktop."""

import copy
from pathlib import Path
from flask import jsonify, render_template, request, send_file
from test_engine.engine import BusyError
from test_engine.test_profiles import generate_sweep_steps


def register_routes(app, engine):
    def data():
        return request.get_json(silent=True) or {}

    def ok(**kwargs):
        return jsonify(success=True, **kwargs)

    def post(path, fn):
        app.add_url_rule(path, path, lambda: fn(), methods=["POST"])

    @app.errorhandler(BusyError)
    def busy(exc):
        return jsonify(success=False, message=str(exc)), 409

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(success=False, message=str(exc)), 400

    @app.errorhandler(Exception)
    def error(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return jsonify(success=False, message=str(exc)), exc.code
        app.logger.exception("Request failed")
        return jsonify(success=False, message=str(exc)), 500

    @app.get("/")
    def index():
        return render_template(
            "index.html", dry_run=engine.dry_run, config=engine._config
        )

    @app.get("/api/status")
    def status():
        return jsonify(engine.poll_status())

    @app.get("/api/config")
    def config():
        return jsonify(dict(engine._config, dry_run=engine.dry_run))

    def connect():
        engine.connect()
        return ok()

    post("/api/connect", connect)

    def disconnect():
        engine.disconnect()
        return ok()

    post("/api/disconnect", disconnect)

    def emergency():
        engine.emergency_stop()
        return ok(message="OFF commands verified; fault remains latched")

    post("/api/emergency_stop", emergency)

    def acknowledge():
        engine.acknowledge_fault()
        return ok()

    post("/api/acknowledge_fault", acknowledge)

    def set_psu():
        d = data()
        engine.set_psu(d.get("voltage"), d.get("current", d.get("current_limit")))
        return ok()

    for path in ["/api/psu/set", "/api/manual/set_psu"]:
        post(path, set_psu)
    for paths, fn in [
        (["/api/psu/output_on", "/api/manual/psu_on"], engine.psu_on),
        (["/api/psu/output_off", "/api/manual/psu_off"], engine.psu_off),
        (["/api/load/input_on", "/api/manual/load_on"], engine.load_on),
        (["/api/load/input_off", "/api/manual/load_off"], engine.load_off),
    ]:
        for path in paths:
            post(path, lambda f=fn: (f(), ok())[1])

    def set_load():
        engine.set_load(data().get("current"))
        return ok()

    for path in ["/api/load/set_current", "/api/manual/set_load"]:
        post(path, set_load)

    def load_mode():
        if data().get("mode", "CC") != "CC":
            raise ValueError("Only CC is supported")
        return ok(message="CC/NORM is established when applying current")

    post("/api/load/set_mode", load_mode)
    # Compatibility endpoint: actual programming is performed by set_psu.
    post("/api/manual/set_psu_voltage", lambda: ok())
    post("/api/manual/record", lambda: ok(point=engine.manual_record(**data())))
    for path in ["/api/manual/clear", "/api/manual/clear_run"]:
        post(path, lambda: (engine.manual_clear(), ok())[1])
    post("/api/manual/start_new_run", lambda: (engine.manual_start_new_run(), ok())[1])
    post(
        "/api/sweep/generate",
        lambda: ok(
            steps=generate_sweep_steps(
                **{
                    k: v
                    for k, v in data().items()
                    if k
                    in (
                        "input_voltage",
                        "input_current_limit",
                        "start_power",
                        "end_power",
                        "num_steps",
                        "nominal_output_voltage",
                    )
                },
                config=engine._config,
            )
        ),
    )
    post("/api/sweep/start", lambda: ok(started=engine.start_sweep(data())))
    post("/api/sweep/stop", lambda: (engine.stop_sweep(), ok())[1])
    post("/api/sweep/pause", lambda: engine.pause_sweep())
    post("/api/sweep/resume", lambda: engine.resume_sweep())
    post("/api/sweep/clear_run", lambda: (engine.clear_sweep(), ok())[1])
    post("/api/sweep/start_new_run", lambda: (engine.start_new_sweep_run(), ok())[1])

    def production():
        d = data()
        success, message, rid = engine.start_production(
            d.get("label", ""), operator=d.get("operator", ""), thermal_check=d.get("thermal_check")
        )
        return jsonify(success=success, message=message, run_id=rid)

    post("/api/production/start", production)
    post("/api/production/abort", lambda: (engine.abort_production(), ok())[1])

    @app.get("/api/production/thermal")
    def thermal_pending():
        return ok(pending=engine.get_thermal_check())

    def thermal_reading():
        d = data()
        engine.submit_thermal_reading(d.get("run_id"), d.get("max_temp_c"))
        return ok()

    post("/api/production/thermal", thermal_reading)

    @app.route("/api/production/profile", methods=["GET", "POST"])
    def profile():
        if request.method == "POST":
            engine.save_production_profile(data()["profile"])
        return ok(profile=engine.get_production_profile())

    @app.get("/api/measurements")
    def measurements():
        return jsonify(engine.get_all_measurements())

    @app.get("/api/graph")
    def graph():
        return ok(
            runs=engine.get_completed_runs(),
            selected=engine.get_selected_runs(),
            max_visible=engine.run_data.max_visible,
        )

    def selection():
        d = data()
        if d.get("action") == "all":
            engine.select_all_runs()
        elif d.get("action") == "none":
            engine.deselect_all_runs()
        else:
            engine.toggle_run_selection(int(d["run_id"]))
        return ok()

    post("/api/graph/select", selection)

    @app.get("/api/runs")
    def runs():
        return ok(runs=engine.get_runs())

    @app.get("/api/db/data")
    def db_data():
        rows = engine.get_runs()
        for row in rows:
            row["points"] = engine.store.get_run_points(row["id"])
        return ok(runs=rows)

    @app.delete("/api/runs/<int:rid>")
    def delete(rid):
        engine.delete_run(rid)
        return ok()

    @app.patch("/api/runs/<int:rid>/name")
    def rename(rid):
        engine.store.rename_run(rid, str(data().get("name", "")))
        engine._restore_graph()
        return ok()

    post("/api/csv/save", lambda: ok(path=engine.manager.csv_path))

    @app.get("/api/csv/download")
    def download():
        path = engine.manager.csv_path
        if not path or not Path(path).is_file():
            raise ValueError("No CSV available")
        return send_file(path, as_attachment=True)

    @app.get("/api/instruments/scan")
    def scan():
        return ok(results=engine.scan_ports())

    def ports_connect():
        d = data()
        engine.connect(d.get("psu_port") or None, d.get("load_port") or None)
        return ok(status=engine.manager.get_status_dict())

    post("/api/instruments/connect", ports_connect)

    def save_ports():
        c = copy.deepcopy(engine._config)
        d = data()
        c["serial_ports"] = dict(psu=d["psu_port"], load=d["load_port"])
        engine.save_configuration(c)
        return ok()

    post("/api/instruments/save_ports", save_ports)

    def save_configuration():
        c = copy.deepcopy(engine._config)
        d = data()
        for k in ("steps", "safety_limits"):
            if k in d:
                c[k] = d[k]
        engine.save_configuration(c)
        return ok()

    post("/api/config/save", save_configuration)
