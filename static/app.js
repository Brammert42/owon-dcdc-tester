// =====================================================================
// OWON DC/DC Tester v2 — Frontend
// =====================================================================

// SocketIO
const socket = io({ transports: ['websocket', 'polling'] });

// ── State ─────────────────────────────────────────────────────────
const state = {
    manualPoints: [],
    sweepPoints: [],
    sweepSteps: [],
    prodPoints: [],
    status: {},
    config: {},
    sweepRunning: false,
    sweepPaused: false,
    prodRunning: false,
    lastVoltageCurveManual: null,
    lastVoltageCurveSweep: null,
    voltageCurvesManual: {},
    voltageCurvesSweep: {},
};

// ── Init ──────────────────────────────────────────────────────────

async function init() {
    // Fetch config
    try {
        const cfg = await apiGet('/api/config');
        state.config = cfg;
    } catch (e) {
        console.error('Failed to fetch config:', e);
    }

    // Restore any previously-taken measurements (after page refresh / reconnection)
    try {
        const meas = await apiGet('/api/measurements');
        if (meas.manual && meas.manual.length) {
            state.manualPoints = meas.manual;
            for (let i = 0; i < meas.manual.length; i++) {
                addPointToTable('manual-body', meas.manual[i], i + 1);
            }
        }
        if (meas.sweep && meas.sweep.length) {
            state.sweepPoints = meas.sweep;
            for (let i = 0; i < meas.sweep.length; i++) {
                const p = meas.sweep[i];
                const body = document.getElementById('sweep-body');
                body.innerHTML += `<tr>
                    <td>${i + 1}</td>
                    <td>${p.step_name || ''}</td>
                    <td>${p.vin?.toFixed(3) ?? ''}</td>
                    <td>${p.iin?.toFixed(4) ?? ''}</td>
                    <td>${p.pin?.toFixed(3) ?? ''}</td>
                    <td>${p.vout?.toFixed(3) ?? ''}</td>
                    <td>${p.iout?.toFixed(4) ?? ''}</td>
                    <td>${p.pout?.toFixed(3) ?? ''}</td>
                    <td>${p.efficiency_percent?.toFixed(1) ?? ''}</td>
                </tr>`;
                state.sweepSteps.push({ name: p.step_name || '', index: i });
            }
        }
        if (meas.production && meas.production.length) {
            state.prodPoints = meas.production;
        }
    } catch (e) {
        console.error('Failed to restore measurements:', e);
    }

    // If we restored data, render the initial graphs
    if (state.sweepPoints.length) {
        setTimeout(() => updateSweepGraph(), 100);
    }
    if (state.manualPoints.length) {
        setTimeout(() => updateManualGraph(), 100);
    }

    // Show dry-run badge
    if (DRY_RUN) {
        document.getElementById('dry-run-badge').style.display = 'inline';
        document.getElementById('status-badge').textContent = '🧪 DRY-RUN';
        document.getElementById('status-badge').className = 'badge badge-warning';
    }

    // Tab switching — re-render graphs when tab becomes visible (Plotly
    // can't render into a hidden div, so we must re-plot after show)
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            document.getElementById(`tab-${tab}`).classList.add('active');

            // Re-render graphs that may have been hidden during sweep
            if (tab === 'sweep') {
                // Small delay so Plotly can measure the now-visible container
                setTimeout(() => {
                    updateSweepGraph();
                    const graphEl = document.getElementById('graph-sweep');
                    if (graphEl && graphEl.data && graphEl.data.length) {
                        Plotly.Plots.resize(graphEl);
                    }
                }, 50);
            } else if (tab === 'manual') {
                setTimeout(() => {
                    updateManualGraph();
                    const graphEl = document.getElementById('graph-manual');
                    if (graphEl && graphEl.data && graphEl.data.length) {
                        Plotly.Plots.resize(graphEl);
                    }
                }, 50);
            }

            if (tab === 'database') {
                requestDbData();
                // Auto-scan ports if not already populated
                const psuSelect = document.getElementById('psu-port-select');
                if (psuSelect && psuSelect.options.length <= 1) {
                    scanPorts();
                }
            }
        });
    });

    // Production label enable/disable start button
    document.getElementById('prod-label').addEventListener('input', e => {
        document.getElementById('btn-prod-start').disabled = !e.target.value.trim();
    });

    await refreshGraphs();
    const profile = await apiGet('/api/production/profile');
    document.getElementById('prod-profile-name').textContent = profile.profile?.name || 'No profile';
    const thermal = profile.profile?.thermal_check || {};
    document.getElementById('thermal-enabled').checked = thermal.enabled || false;
    document.getElementById('thermal-limit').value = thermal.max_temp_c ?? 80;
    document.getElementById('thermal-warmup').value = thermal.warmup_s ?? 30;
    // Initial poll
    await pollStatus();
    setTimeout(async function pollLoop() { await pollStatus(); setTimeout(pollLoop,2000); },2000);
}

// ── API helpers ──────────────────────────────────────────────────

async function apiGet(url) {
    const r = await fetch(url);
    return r.json();
}

async function apiPost(url, data = {}, method = 'POST') {
    const r = await fetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    return r.json();
}

// ── Status ───────────────────────────────────────────────────────

async function pollStatus() {
    try {
        const s = await apiGet('/api/status');
        state.status = s;
        updateUI(s);
    } catch (e) {
        console.error('Status poll failed:', e);
    }
}

function updateUI(s) {
    // Readings
    document.getElementById('g-vin').textContent = s.psu?.vin?.toFixed(3) ?? '0.000';
    document.getElementById('g-iin').textContent = s.psu?.iin?.toFixed(4) ?? '0.0000';
    document.getElementById('g-pin').textContent = s.psu?.pin?.toFixed(3) ?? '0.000';
    document.getElementById('g-vout').textContent = s.load?.vout?.toFixed(3) ?? '0.000';
    document.getElementById('g-iout').textContent = s.load?.iout?.toFixed(4) ?? '0.0000';
    document.getElementById('g-pout').textContent = s.load?.pout?.toFixed(3) ?? '0.000';
    document.getElementById('g-eff').textContent = (s.efficiency ?? 0).toFixed(1);

    // Status badge
    const badge = document.getElementById('status-badge');
    const statusText = s.status || 'unknown';
    badge.textContent = statusText.toUpperCase().replace('_', ' ');
    badge.className = `badge badge-${statusText}`;

    // PSU/Load state
    const psuEl = document.getElementById('g-psu-state');
    psuEl.textContent = s.psu_output_on == null ? 'UNKNOWN' : s.psu_output_on ? 'ON' : 'OFF';
    psuEl.className = `badge ${s.psu_output_on ? 'badge-success' : 'badge-idle'}`;

    const loadEl = document.getElementById('g-load-state');
    loadEl.textContent = s.load_input_on == null ? 'UNKNOWN' : s.load_input_on ? 'ON' : 'OFF';
    loadEl.className = `badge ${s.load_input_on ? 'badge-success' : 'badge-idle'}`;

    // Connected
    document.getElementById('g-connected').textContent = s.connected ? 'Yes' : 'No';

    // Sweep buttons
    const isSweepRunning = s.sweep_state === 'running';
    const isSweepPaused = s.sweep_state === 'paused';
    state.sweepRunning = isSweepRunning;
    state.sweepPaused = isSweepPaused;
    document.getElementById('btn-sweep-start').disabled = isSweepRunning || isSweepPaused;
    document.getElementById('btn-sweep-pause').disabled = true;
    document.getElementById('btn-sweep-resume').disabled = true;
    document.getElementById('btn-sweep-stop').disabled = !isSweepRunning && !isSweepPaused;

    // Production buttons
    const isProdRunning = s.prod_state === 'running';
    state.prodRunning = isProdRunning;
    document.getElementById('thermal-settings').disabled = isProdRunning || !!s.busy;
    document.getElementById('btn-prod-start').disabled = isProdRunning || !!s.busy || !!s.fault || !s.connected || !document.getElementById('prod-label').value.trim();
    document.getElementById('btn-prod-abort').disabled = !isProdRunning;
}

// =================================================================
// CONNECTION
// =================================================================

async function connectInstruments() {
    const r = await apiPost('/api/connect');
    if (r.success) {
        await pollStatus();
        showToast(r.message, 'success');
    } else {
        showToast(r.message, 'error');
    }
}

async function refreshIDN() {
    await pollStatus();
    showToast('Status refreshed', 'info');
}

// =================================================================
// PSU / LOAD
// =================================================================

async function psuOn() {
    const r = await apiPost('/api/psu/output_on');
    if (r.success) await pollStatus();
}

async function psuOff() {
    const r = await apiPost('/api/psu/output_off');
    if (r.success) await pollStatus();
}

async function manualSetPSU() {
    const voltage = parseFloat(document.getElementById('manual-psu-voltage').value);
    const current = parseFloat(document.getElementById('manual-psu-current').value);
    const r = await apiPost('/api/psu/set', { voltage, current });
    if (r.success) {
        await apiPost('/api/manual/set_psu_voltage', { voltage });
        await pollStatus();
        const res = r.result;
        if (res && res.voltage_readback) {
            const vMatch = Math.abs(res.voltage_readback - voltage) < 1.0;
            showToast(`PSU → ${voltage}V / ${current}A limit ${res.current_sent ? '✅' : '⚠️'}`, vMatch ? 'success' : 'warning');
        } else {
            showToast(`PSU set to ${voltage}V / ${current}A limit ✅`, 'success');
        }
    } else {
        showToast(r.message, 'error');
    }
}

async function manualSetLoad() {
    const mode = document.getElementById('manual-load-mode').value;
    const current = parseFloat(document.getElementById('manual-load-current').value);
    let r1 = await apiPost('/api/load/set_mode', { mode });
    let r2 = await apiPost('/api/load/set_current', { current });
    if (r1.success && r2.success) {
        await pollStatus();
        showToast(`Load set to ${current}A ${mode}`, 'success');
    } else {
        showToast(r2.message || r1.message || 'Error', 'error');
    }
}

async function loadOn() {
    const r = await apiPost('/api/load/input_on');
    if (r.success) await pollStatus();
}

async function loadOff() {
    const r = await apiPost('/api/load/input_off');
    if (r.success) await pollStatus();
}

// =================================================================
// EMERGENCY STOP
// =================================================================

async function emergencyStop() {
    await apiPost('/api/emergency_stop');
    showToast('🚨 Emergency stop activated!', 'error');
    await pollStatus();
}

// =================================================================
// MANUAL TAB
// =================================================================

async function manualRecord() {
    const stepName = document.getElementById('manual-step-name').value || `Point ${state.manualPoints.length + 1}`;
    const psuVoltage = parseFloat(document.getElementById('manual-psu-voltage').value) || 84.0;
    const psuCurrent = parseFloat(document.getElementById('manual-psu-current').value) || 5.0;
    const loadCurrent = Number(document.getElementById('manual-load-current').value);
    const nomVout = state.config?.safety_limits?.nominal_output_voltage || 12;
    const r = await apiPost('/api/manual/record', {
        step_name: stepName,
        step_index: state.manualPoints.length,
        psu_voltage: psuVoltage,
        psu_current_limit: psuCurrent,
        load_current: loadCurrent,
        requested_power_w: loadCurrent * nomVout,
        input_voltage_group: psuVoltage,
    });
    if (r.success && r.point) {
        // The measurement event adds this point exactly once.
    } else {
        showToast(r.message || 'Record failed', 'error');
    }
}

function addManualPoint(p) {
    state.manualPoints.push(p);
    addPointToTable('manual-body', p, state.manualPoints.length);
    updateManualGraph();
}

async function manualClear() {
    state.manualPoints = [];
    state.lastVoltageCurveManual = null;
    state.voltageCurvesManual = {};
    document.getElementById('manual-body').innerHTML = '';
    await refreshGraphs();
    await apiPost('/api/manual/clear_run');
    showToast('Manual run cleared', 'info');
}

async function manualNewRun() {
    state.manualPoints = [];
    state.lastVoltageCurveManual = null;
    state.voltageCurvesManual = {};
    document.getElementById('manual-body').innerHTML = '';
    await refreshGraphs();
    await apiPost('/api/manual/start_new_run');
    showToast('New run started', 'info');
}

// ── Manual Graph ────────────────────────────────────────────────

async function refreshGraphs() {
    const data = await apiGet('/api/graph');
    if (!data.success) return;
    const traces = data.selected.map(run => {
        const pts = [...run.points].sort((a,b) => a.pout-b.pout);
        return {x:pts.map(p=>p.pout), y:pts.map(p=>p.efficiency_percent), mode:'lines+markers',
                name:`#${run.run_id} ${run.run_name}`, type:'scatter'};
    });
    const layout = {title:'Selected completed runs', xaxis:{title:'Output power (W)'},
                    yaxis:{title:'Efficiency (%)'}, paper_bgcolor:'#1a1a2e',plot_bgcolor:'#1a1a2e',font:{color:'#eee'}};
    Plotly.react('graph-manual',traces,layout,{responsive:true});
    Plotly.react('graph-sweep',traces,layout,{responsive:true});
    const panel=document.getElementById('graph-selection');
    panel.replaceChildren();
    for (const run of data.runs) {
        const label=document.createElement('label');
        const check=document.createElement('input'); check.type='checkbox';
        check.checked=data.selected.some(r=>r.run_id===run.run_id);
        check.onchange=async()=>{const r=await apiPost('/api/graph/select',{run_id:run.run_id});
            if(!r.success) showToast(r.message,'error'); await refreshGraphs();};
        label.append(check,document.createTextNode(` #${run.run_id} ${run.run_name} `));panel.append(label);
    }
}
function updateManualGraph() { return refreshGraphs(); }

// =================================================================
// SWEEP TAB
// =================================================================

async function sweepGenerate() {
    state.sweepSteps = [];
    const data = {
        input_voltage: parseFloat(document.getElementById('sweep-voltage').value),
        input_current_limit: parseFloat(document.getElementById('sweep-current-limit').value),
        start_power: parseFloat(document.getElementById('sweep-start').value),
        end_power: parseFloat(document.getElementById('sweep-end').value),
        num_steps: parseInt(document.getElementById('sweep-steps').value),
        nominal_output_voltage: parseFloat(document.getElementById('sweep-dut-vout').value),
    };

    const r = await apiPost('/api/sweep/generate', data);
    if (!r.success || !r.steps) {
        showToast(r.message || 'Failed to generate steps', 'error');
        return;
    }

    state.sweepSteps = r.steps;
    const body = document.getElementById('sweep-steps-body');
    body.innerHTML = '';
    for (const s of r.steps) {
        body.innerHTML += `<tr><td>${s.index}</td><td>${s.name}</td><td>${(s.load_current_a || s.load_current || 0).toFixed(4)}</td><td>${s.requested_power_w.toFixed(2)}</td></tr>`;
    }
    document.getElementById('sweep-steps-card').style.display = 'block';
    showToast(`Generated ${r.steps.length} steps`, 'info');
}

async function sweepStart() {
    await sweepGenerate();
    if (!state.sweepSteps.length) {
        showToast('Generate steps first!', 'error');
        return;
    }

    const data = {
        input_voltage: parseFloat(document.getElementById('sweep-voltage').value),
        input_current_limit: parseFloat(document.getElementById('sweep-current-limit').value),
        steps: state.sweepSteps,
        wait_time: parseFloat(document.getElementById('sweep-wait').value),
        nominal_output_voltage: parseFloat(document.getElementById('sweep-dut-vout').value),
        run_name: document.getElementById('sweep-run-name').value.trim() || '',
    };

    const r = await apiPost('/api/sweep/start', data);
    if (r.success) {
        document.getElementById('sweep-progress').textContent = '▶ Sweep in progress...';
        showToast('Sweep started', 'success');
    } else {
        showToast(r.message, 'error');
    }
}

async function sweepPause() {
    const r = await apiPost('/api/sweep/pause');
    if (r.success) {
        document.getElementById('sweep-progress').textContent = '⏸ Sweep paused';
        showToast('Sweep paused', 'info');
    }
}

async function sweepResume() {
    const r = await apiPost('/api/sweep/resume');
    if (r.success) {
        document.getElementById('sweep-progress').textContent = '▶ Sweep resumed...';
        showToast('Sweep resumed', 'success');
    }
}

async function sweepStop() {
    const r = await apiPost('/api/sweep/stop');
    if (r.success) {
        document.getElementById('sweep-progress').textContent = '⏹ Sweep stopped';
        showToast('Sweep stopped', 'info');
    }
}

async function sweepClear() {
    state.sweepPoints = [];
    state.lastVoltageCurveSweep = null;
    state.voltageCurvesSweep = {};
    document.getElementById('sweep-body').innerHTML = '';
    document.getElementById('sweep-table-wrap').style.display = 'none';
    document.getElementById('sweep-table-toggle').textContent = '▼ show';
    document.getElementById('sweep-progress').textContent = 'Cleared';
    await refreshGraphs();
    await apiPost('/api/sweep/clear_run');
}

async function sweepNewRun() {
    state.sweepPoints = [];
    state.lastVoltageCurveSweep = null;
    state.voltageCurvesSweep = {};
    document.getElementById('sweep-body').innerHTML = '';
    document.getElementById('sweep-table-wrap').style.display = 'none';
    document.getElementById('sweep-table-toggle').textContent = '▼ show';
    document.getElementById('sweep-progress').textContent = 'Ready';
    await refreshGraphs();
    await apiPost('/api/sweep/start_new_run');
}

function toggleSweepTable() {
    const wrap = document.getElementById('sweep-table-wrap');
    const toggle = document.getElementById('sweep-table-toggle');
    const shown = wrap.style.display !== 'none';
    wrap.style.display = shown ? 'none' : 'block';
    toggle.textContent = shown ? '▶ show' : '▼ hide';
}

function addSweepPoint(p) {
    state.sweepPoints.push(p);
    const i = state.sweepPoints.length;
    const body = document.getElementById('sweep-body');
    body.innerHTML += `<tr>
        <td>${i}</td>
        <td>${p.step_name || ''}</td>
        <td>${p.vin?.toFixed(3) ?? ''}</td>
        <td>${p.iin?.toFixed(4) ?? ''}</td>
        <td>${p.pin?.toFixed(3) ?? ''}</td>
        <td>${p.vout?.toFixed(3) ?? ''}</td>
        <td>${p.iout?.toFixed(4) ?? ''}</td>
        <td>${p.pout?.toFixed(3) ?? ''}</td>
        <td>${p.efficiency_percent?.toFixed(1) ?? ''}</td>
    </tr>`;

    // Auto-show table on first point
    if (i === 1) {
        document.getElementById('sweep-table-wrap').style.display = 'block';
        document.getElementById('sweep-table-toggle').textContent = '▼ hide';
    }

    // Scroll to bottom
    const wrapper = body.closest('.table-wrapper');
    if (wrapper) wrapper.scrollTop = wrapper.scrollHeight;

    // Active points update the table only.
}

function updateSweepGraph() { return refreshGraphs(); }

// =================================================================
// PRODUCTION TAB
// =================================================================

async function prodStart() {
    const label = document.getElementById('prod-label').value.trim();
    if (!label) {
        showToast('Enter a PCB label first', 'error');
        return;
    }

    state.prodPoints = [];
    document.getElementById('prod-body').innerHTML = '';
    document.getElementById('prod-result-card').style.display = 'none';
    document.getElementById('btn-prod-start').disabled = true;
    document.getElementById('btn-prod-abort').disabled = false;

    document.getElementById('thermal-measured').value = '';
    document.getElementById('thermal-check-status').textContent = 'Thermal check pending if enabled.';
    const r = await apiPost('/api/production/start', { label, thermal_check: {
        enabled: document.getElementById('thermal-enabled').checked,
        max_temp_c: document.getElementById('thermal-limit').value,
        warmup_s: document.getElementById('thermal-warmup').value,
    }});
    if (r.success) {
        showToast(`Production test started for label: ${label}`, 'success');
    } else {
        showToast(r.message || 'Failed to start production test', 'error');
        document.getElementById('btn-prod-start').disabled = false;
        document.getElementById('btn-prod-abort').disabled = true;
    }
}

async function prodAbort() {
    const r = await apiPost('/api/production/abort');
    if (r.success) {
        showToast('Production test aborted', 'info');
    }
}

let thermalRunId = null;
let thermalSubmitted = false;
function showThermalCheck(pending) {
    const entry = document.getElementById('thermal-entry');
    if (!pending) { entry.disabled = true; thermalRunId = null; return; }
    if (thermalRunId !== pending.run_id) {
        thermalRunId = pending.run_id;
        thermalSubmitted = false;
        document.getElementById('thermal-measured').value = '';
    }
    entry.disabled = pending.phase !== 'awaiting_reading' || thermalSubmitted;
    const action = pending.phase === 'warming' ? 'Warming up' : 'Enter the camera maximum now';
    document.getElementById('thermal-check-status').textContent =
        `${pending.label}: ${action}. Outputs ON at ${pending.power_w} W requested. ` +
        `${Math.ceil(pending.remaining_s)} s remaining; limit ${pending.max_temp_c}°C.`;
}

async function thermalSubmit() {
    const entry = document.getElementById('thermal-entry');
    if (entry.disabled || thermalRunId === null) return;
    const r = await apiPost('/api/production/thermal', {
        run_id: thermalRunId, max_temp_c: document.getElementById('thermal-measured').value,
    });
    if (r.success) { thermalSubmitted = true; entry.disabled = true; }
    else showToast(r.message || 'Temperature was not accepted', 'error');
}

socket.on('thermal_check', data => showThermalCheck(data.pending));
socket.on('prod_step', data => {
    if (data.step_name === 'Thermal check') {
        document.getElementById('thermal-check-status').textContent = `Thermal ${data.status}: ${data.failure_reason}`;
    }
});
socket.on('prod_result', data => {
    showThermalCheck(null);
    if (data.result !== 'PASS') {
        document.getElementById('thermal-check-status').textContent = `${data.result}: ${data.fail_reason || ''}`;
    }
});

function prodClear() {
    state.prodPoints = [];
    document.getElementById('prod-body').innerHTML = '';
    document.getElementById('prod-result-card').style.display = 'none';
    document.getElementById('prod-result-big').textContent = '—';
    document.getElementById('prod-result-big').className = 'prod-result-pass';
    document.getElementById('prod-fail-reason').textContent = '';
}

function addProdPoint(p) {
    state.prodPoints.push(p);
    const i = state.prodPoints.length;
    const body = document.getElementById('prod-body');
    const passFail = p.pass_fail_status || '';
    const cls = passFail === 'PASS' ? 'badge-success' :
               passFail === 'FAIL' || passFail === 'FAULT' ? 'badge-error' : 'badge-warning';
    body.innerHTML += `<tr>
        <td>${i}</td>
        <td>${p.step_name || ''}</td>
        <td>${p.vin?.toFixed(3) ?? ''}</td>
        <td>${p.iin?.toFixed(4) ?? ''}</td>
        <td>${p.pin?.toFixed(3) ?? ''}</td>
        <td>${p.vout?.toFixed(3) ?? ''}</td>
        <td>${p.iout?.toFixed(4) ?? ''}</td>
        <td>${p.pout?.toFixed(3) ?? ''}</td>
        <td>${p.efficiency_percent?.toFixed(1) ?? ''}</td>
        <td><span class="badge ${cls}">${passFail}</span></td>
    </tr>`;

    const wrapper = body.closest('.table-wrapper');
    if (wrapper) wrapper.scrollTop = wrapper.scrollHeight;
}

function showProdResult(result, failReason) {
    const el = document.getElementById('prod-result-big');
    el.textContent = result;
    el.className = result === 'PASS' ? 'prod-result-pass' : 'prod-result-fail';
    document.getElementById('prod-result-card').style.display = 'block';
    if (failReason) {
        document.getElementById('prod-fail-reason').textContent = failReason;
    } else {
        document.getElementById('prod-fail-reason').textContent = '';
    }
    showToast(`Production test result: ${result}`, result === 'PASS' ? 'success' : 'error');
}

// =================================================================
// CSV
// =================================================================

async function saveCSV() {
    const r = await apiPost('/api/csv/save');
    showToast(r.success ? `CSV saved to ${r.path}` : 'CSV save failed', r.success ? 'success' : 'error');
}

async function downloadCSV() {
    window.location.href = '/api/csv/download';
}

// =================================================================
// TOAST
// =================================================================

function showToast(message, type = 'info') {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateX(-50%) translateY(0)';
    });

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(-50%) translateY(-20px)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ── Table helpers ────────────────────────────────────────────────

function addPointToTable(tbodyId, p, index) {
    const body = document.getElementById(tbodyId);
    const passFail = p.pass_fail_status || '';
    const cls = passFail === 'PASS' ? 'badge-success' :
               passFail === 'FAIL' || passFail === 'FAULT' ? 'badge-error' : 'badge-warning';
    const statusCell = passFail ? `<span class="badge ${cls}">${passFail}</span>` : '';
    body.innerHTML += `<tr>
        <td>${index}</td>
        <td>${p.step_name || ''}</td>
        <td>${p.vin?.toFixed(3) ?? ''}</td>
        <td>${p.iin?.toFixed(4) ?? ''}</td>
        <td>${p.pin?.toFixed(3) ?? ''}</td>
        <td>${p.vout?.toFixed(3) ?? ''}</td>
        <td>${p.iout?.toFixed(4) ?? ''}</td>
        <td>${p.pout?.toFixed(3) ?? ''}</td>
        <td>${p.efficiency_percent?.toFixed(1) ?? ''}</td>
        <td>${statusCell}</td>
    </tr>`;
}

// =================================================================
// SOCKET.IO EVENTS
// =================================================================

socket.on('connect', () => {
    document.getElementById('conn-status').textContent = '✅ Connected';
    document.getElementById('conn-status').style.color = '#51cf66';
    apiGet('/api/production/thermal').then(r => showThermalCheck(r.pending));
});

socket.on('disconnect', () => {
    document.getElementById('conn-status').textContent = '❌ Disconnected';
    document.getElementById('conn-status').style.color = '#ff6b6b';
});

socket.on('connect_error', () => {
    document.getElementById('conn-status').textContent = '⚠️ Reconnecting...';
    document.getElementById('conn-status').style.color = '#ffd43b';
});

socket.on('measurement', (data) => {
    if (data.tab === 'sweep') {
        addSweepPoint(data.point);
        const i = state.sweepPoints.length;
        document.getElementById('sweep-progress').textContent =
            `▶ Step ${i}/${state.sweepSteps.length || '?'}: ${data.point.pout?.toFixed(1) || '?'}W @ η=${data.point.efficiency_percent?.toFixed(1) || '?'}%`;
    } else if (data.tab === 'manual') {
        addManualPoint(data.point);
    }
});

socket.on('sweep_error', (data) => {
    showToast(`Sweep error: ${data.message}`, 'error');
    document.getElementById('sweep-progress').textContent = `❌ ${data.message}`;
});

socket.on('sweep_done', () => {
    document.getElementById('sweep-progress').textContent = '✅ Sweep complete';
    const n = state.sweepPoints.length;
    const avgEff = n > 0 ? (state.sweepPoints.reduce((s, p) => s + (p.efficiency_percent || 0), 0) / n) : 0;
    showToast(`✅ Sweep done — ${n} points, avg η=${avgEff.toFixed(1)}%`, 'success');
    updateSweepGraph();
});

socket.on('clear_run', (data) => {
    if (data.tab === 'manual') {state.manualPoints=[];document.getElementById('manual-body').replaceChildren();}
    if (data.tab === 'sweep') {state.sweepPoints=[];document.getElementById('sweep-body').replaceChildren();}
    if (data.tab === 'production') {state.prodPoints=[];document.getElementById('prod-body').replaceChildren();}
});
socket.on('graph_data_changed', refreshGraphs);

socket.on('status_update', (data) => {
    if (data.status) {
        updateUI(data);
    }
});

socket.on('prod_step', (data) => {
    if (data.point) {
        addProdPoint(data.point);
    }
    if (data.status === 'error') {
        showToast(`Step "${data.step_name}" error: ${data.message}`, 'error');
    }
});

socket.on('prod_result', (data) => {
    showProdResult(data.result, data.fail_reason);
    document.getElementById('btn-prod-start').disabled = false;
    document.getElementById('btn-prod-abort').disabled = true;
});

// =================================================================
// TAB 4: DATABASE VIEWER
// =================================================================

async function requestDbData() {
    try {
        const [d, status] = await Promise.all([
            apiGet('/api/db/data'),
            apiGet('/api/status'),
        ]);
        if (d.success && d.runs) {
            renderRunsTable(d.runs);
            document.getElementById('db-count').textContent =
                `${d.runs.length} run(s), ${d.runs.reduce((s, ru) => s + (ru.points || []).length, 0)} points total`;
        }
        updateInstrumentStatus(status);
    } catch (e) {
        console.error('DB data fetch failed:', e);
    }
}

async function scanPorts() {
    const r = await apiGet('/api/instruments/scan');
    const resultsDiv = document.getElementById('scan-results');
    const psuSelect = document.getElementById('psu-port-select');
    const loadSelect = document.getElementById('load-port-select');

    if (!r.success) { resultsDiv.textContent = 'Scan failed'; return; }

    // Build list of unique ports (stable paths first)
    const seen = new Set();
    const ports = [];
    for (const res of r.results) {
        const p = res.port;
        if (!seen.has(p)) { seen.add(p); ports.push(res); }
    }

    // Sort: symlinks and by-id/by-path first, raw ttyUSB last
    const score = (p) => {
        if (p === '/dev/owon_psu' || p === '/dev/owon_load') return 0;
        if (p.startsWith('/dev/serial/by-id/')) return 1;
        if (p.startsWith('/dev/serial/by-path/')) return 2;
        return 9;
    };
    ports.sort((a, b) => score(a.port) - score(b.port));

    // Populate dropdowns — option text is just the port path (clean)
    psuSelect.innerHTML = '<option value="">— select PSU port —</option>';
    loadSelect.innerHTML = '<option value="">— select Load port —</option>';

    let autoPsuPort = null;
    let autoLoadPort = null;

    for (const res of ports) {
        const opt = document.createElement('option');
        opt.value = res.port;
        opt.textContent = res.port;
        psuSelect.appendChild(opt.cloneNode(true));
        loadSelect.appendChild(opt.cloneNode(true));

        // Auto-detect: remember first PSU and Load
        if (res.device_type === 'psu' && !autoPsuPort) autoPsuPort = res.port;
        if (res.device_type === 'load' && !autoLoadPort) autoLoadPort = res.port;
    }

    // Auto-select detected ports
    if (autoPsuPort) psuSelect.value = autoPsuPort;
    if (autoLoadPort) loadSelect.value = autoLoadPort;

    // Scan results table — shows full info
    let html = '<table class="scan-table"><tr><th>Type</th><th>Port</th><th>IDN</th></tr>';
    for (const res of ports) {
        const icon = res.device_type === 'psu' ? '🔌' : res.device_type === 'load' ? '⚡' : '❓';
        const typeLabel = res.device_type === 'psu' ? 'PSU' : res.device_type === 'load' ? 'Load' : res.device_type;
        const portClass = res.port.startsWith('/dev/owon') ? 'port-stable' : '';
        html += `<tr><td><span class="badge badge-${res.device_type === 'psu' ? 'primary' : 'success'}">${icon} ${typeLabel}</span></td>`
              + `<td class="${portClass}">${res.port}</td>`
              + `<td class="cell-idn">${res.idn || res.error || '—'}</td></tr>`;
    }
    html += '</table>';
    resultsDiv.innerHTML = html;

    // Show toast with detected instruments
    if (autoPsuPort && autoLoadPort) {
        showToast(`✅ Auto-detected PSU on ${autoPsuPort}, Load on ${autoLoadPort}`, 'success');
    } else if (!autoPsuPort && !autoLoadPort) {
        showToast('⚠️ No OWON instruments found', 'warning');
    } else {
        const missing = [];
        if (!autoPsuPort) missing.push('PSU');
        if (!autoLoadPort) missing.push('Load');
        showToast(`⚠️ ${missing.join(', ')} not detected`, 'warning');
    }
}

async function connectPorts() {
    const psuPort = document.getElementById('psu-port-select').value;
    const loadPort = document.getElementById('load-port-select').value;
    if (!psuPort || !loadPort) {
        showToast('Select both PSU and Load ports', 'error');
        return;
    }
    const r = await apiPost('/api/instruments/connect', { psu_port: psuPort, load_port: loadPort });
    if (r.success) {
        updateInstrumentStatus(r.status);
        showToast(`Connected PSU=${psuPort}, Load=${loadPort}`, 'success');
    }
}

async function savePorts() {
    const psuPort = document.getElementById('psu-port-select').value;
    const loadPort = document.getElementById('load-port-select').value;
    if (!psuPort || !loadPort) {
        showToast('Select both PSU and Load ports first', 'error');
        return;
    }
    const r = await apiPost('/api/instruments/save_ports', { psu_port: psuPort, load_port: loadPort });
    if (r.success) {
        showToast(r.message, 'success');
    }
}

function updateInstrumentStatus(status) {
    const psuEl = document.getElementById('db-psu-status');
    const loadEl = document.getElementById('db-load-status');
    if (!psuEl || !loadEl) return;

    if (status.psu_connected) {
        const label = status.dry_run ? 'SPE15054 (sim)' : 'PSU';
        psuEl.innerHTML = `🔌 ${label} <span class="badge badge-success">connected</span>`;
    } else {
        psuEl.innerHTML = `❌ PSU <span class="badge badge-idle">disconnected</span>`;
    }

    if (status.load_connected) {
        const label = status.dry_run ? 'OEL (sim)' : 'DC Load';
        loadEl.innerHTML = `🔌 ${label} <span class="badge badge-success">connected</span>`;
    } else {
        loadEl.innerHTML = `❌ DC Load <span class="badge badge-idle">disconnected</span>`;
    }
}

function renderRunsTable(runs) {
    const tbody = document.getElementById('db-runs-body');
    tbody.innerHTML = '';
    for (const run of runs) {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.dataset.runId = run.id;
        tr.innerHTML = `
            <td>${run.id}</td>
            <td><span class="badge badge-${run.test_mode === 'production' ? 'success' : run.test_mode === 'automated_sweep' ? 'primary' : 'info'}">${run.test_mode === 'automated_sweep' ? 'sweep' : run.test_mode}</span></td>
            <td class="cell-ts">${fmtTs(run.timestamp_start)}</td>
            <td class="cell-ts">${fmtTs(run.timestamp_end)}</td>
            <td>${esc(run.unit_serial_or_label || '')}</td>
            <td>${esc(run.profile_name || '')}</td>
            <td>${run.final_result ? `<span class="badge badge-${run.final_result === 'COMPLETE' ? 'success' : run.final_result === 'STOPPED' ? 'warning' : 'error'}">${run.final_result}</span>` : '<span class="badge badge-idle">\u2014</span>'}</td>
            <td>${(run.points || []).length}</td>
            <td style="white-space:nowrap;">
                <button class="btn btn-sm btn-secondary" onclick="event.stopPropagation();showRunDetail(${run.id})">\u25b6</button>
                <button class="btn btn-sm btn-danger" onclick="event.stopPropagation();deleteRun(${run.id})" title="Delete run">\ud83d\uddd1</button>
            </td>
        `;
        tr.addEventListener('click', () => showRunDetail(run.id));
        tbody.appendChild(tr);
    }
}

async function deleteRun(runId) {
    if (!confirm(`Delete run #${runId} and all its points?`)) return;
    const r = await apiPost(`/api/runs/${runId}`, {}, 'DELETE');
    if (r.success) {
        showToast(`Deleted run #${runId}`, 'info');
        requestDbData();
    }
}

function showRunDetail(runId) {
    const card = document.getElementById('db-detail-card');
    const title = document.getElementById('db-detail-title');
    const body = document.getElementById('db-detail-body');

    // Find run data from already-loaded runs
    const rows = document.getElementById('db-runs-body').children;
    let runData = null;
    for (const tr of rows) {
        if (tr.dataset.runId == runId) {
            // Re-fetch to get fresh points
            break;
        }
    }

    // Fetch fresh data for detail
    apiGet('/api/db/data').then(r => {
        if (!r.success) return;
        const run = r.runs.find(x => x.id === runId);
        if (!run) return;
        const pts = run.points || [];
        title.textContent = `Run #${run.id} — ${run.test_mode} — ${run.unit_serial_or_label || 'no label'} — ${pts.length} point(s)`;
        body.innerHTML = '';
        for (const p of pts) {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${p.id}</td>
                <td>${esc(p.step_name || '')}</td>
                <td>${num(p.psu_set_voltage)}</td>
                <td>${num(p.vin)}</td>
                <td>${num(p.iin)}</td>
                <td>${num(p.pin)}</td>
                <td>${num(p.load_set_current)}</td>
                <td>${num(p.vout)}</td>
                <td>${num(p.iout)}</td>
                <td>${num(p.pout)}</td>
                <td>${num(p.efficiency_percent)}%</td>
                <td>${p.step_result ? `<span class="badge badge-${p.step_result === 'PASS' ? 'success' : p.step_result.startsWith('FAIL') ? 'error' : 'info'}">${esc(p.step_result)}</span>` : '<span class="badge badge-idle">—</span>'}</td>
            `;
            body.appendChild(tr);
        }
        card.style.display = 'block';
    });
}

function fmtTs(iso) {
    if (!iso) return '—';
    // ISO: 2026-06-28T18:53:17.123456 -> short
    return iso.substring(0, 19).replace('T', ' ');
}

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function num(v) {
    if (v === null || v === undefined) return '—';
    return Number(v).toFixed(v < 10 && String(v).includes('.') && v < 1 ? 4 : 3);
}

// =================================================================
// START
// =================================================================
document.addEventListener('DOMContentLoaded', init);
