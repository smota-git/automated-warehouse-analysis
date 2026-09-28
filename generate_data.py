#!/usr/bin/env python3
"""Generate a reproducible synthetic automated-warehouse analytics dataset.

The generator keeps the number of operation rows fixed by default (30,000),
while changing timestamps, materials, locations and normal process variation with
--seed. Topology and incident placement are randomized by default. Use --no-rand-topo and
--no-rand-inc to disable either randomization. Two designed pattern types are
always present by default:

1) one acute incident with a strong simultaneous KPI peak;
2) one recurring reliability issue with failures spread across multiple days.

The script also writes expected_solution.json, so the exercise can be solved
blind and checked afterwards.
"""
from pathlib import Path
import argparse, json, sqlite3, secrets
import numpy as np
import pandas as pd

ERROR_CODES = ["E02", "E07", "E14", "E21"]
OPERATIONS = ["STORE", "RETRIEVE", "TRANSFER"]


def build_topology(rng, randomize=True):
    if randomize:
        counts = {
            "A": int(rng.integers(4, 7)),
            "B": int(rng.integers(7, 10)),  # keep B07 available
            "C": int(rng.integers(4, 7)),
            "D": int(rng.integers(4, 7)),   # keep D02 available
        }
    else:
        counts = {"A": 5, "B": 7, "C": 5, "D": 5}
    machines = [f"{z}{i:02d}" for z,n in counts.items() for i in range(1,n+1)]
    return counts, machines


def generate(out_dir: Path, seed=None, n_records=30000, rand_topo=True, rand_inc=True):
    # If no seed is supplied, create a fresh one. The chosen seed is stored in
    # expected_solution.json so the same dataset can be reproduced later.
    if seed is None:
        seed = secrets.randbits(64)
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    topology, machines = build_topology(rng, rand_topo)

    if rand_inc:
        primary_machine = str(rng.choice(machines))
        chronic_machine = str(rng.choice([m for m in machines if m != primary_machine]))
        primary_day = int(rng.integers(8, 25))
        candidates = [d for d in range(2, 31) if d != primary_day]
        chronic_days = set(map(int, rng.choice(candidates, size=8, replace=False)))
    else:
        primary_machine = "B07"
        chronic_machine = "D02"
        primary_day = 18
        chronic_days = {6, 7, 11, 13, 17, 20, 29, 30}
    primary_start = pd.Timestamp(f"2026-08-{primary_day:02d} 13:30:00")
    primary_end   = pd.Timestamp(f"2026-08-{primary_day:02d} 20:00:00")

    # Generate timestamps uniformly over August, with a mild daytime traffic peak.
    start = pd.Timestamp("2026-08-01 00:00:00")
    end = pd.Timestamp("2026-09-01 00:00:00")
    sec = int((end-start).total_seconds())
    ts = start + pd.to_timedelta(rng.integers(0, sec, size=n_records), unit="s")
    ts = pd.Series(ts).sort_values(ignore_index=True)

    # Traffic weights: the two incident machines are somewhat busier, but not absurdly so.
    weights = np.ones(len(machines), dtype=float)
    for m, mult in [(primary_machine, 2.2), (chronic_machine, 1.7)]:
        if m in machines: weights[machines.index(m)] *= mult
    weights /= weights.sum()
    machine_id = rng.choice(machines, size=n_records, p=weights)
    zone = np.array([m[0] for m in machine_id])

    operation_type = rng.choice(OPERATIONS, size=n_records, p=[0.38,0.42,0.20])
    material_id = [f"MAT_{i:03d}" for i in rng.integers(1, 81, size=n_records)]

    src=[]; dst=[]
    for z in zone:
        a=int(rng.integers(1,41)); b=int(rng.integers(1,41))
        if b==a: b = 1 + (b % 40)
        src.append(f"{z}-{a:02d}"); dst.append(f"{z}-{b:02d}")

    # Machine-specific baselines and operation-type effects.
    machine_base = {m: float(rng.normal(92, 8)) for m in machines}
    op_effect = {"STORE": 0.0, "RETRIEVE": 8.0, "TRANSFER": -10.0}
    hours = ts.dt.hour.to_numpy()
    busy = ((hours >= 7) & (hours <= 19)).astype(float)
    cycle = np.array([machine_base[m] for m in machine_id]) + np.array([op_effect[o] for o in operation_type])
    cycle += 4.0*busy + rng.normal(0, 8, size=n_records)
    cycle = np.clip(cycle, 35, None)
    queue = rng.gamma(shape=1.8, scale=3.3, size=n_records) + 5.5*busy

    # Baseline errors.
    p_error = np.full(n_records, 0.0035)
    # Recurring issue: persistent moderate reliability problem over several days.
    days = ts.dt.day.to_numpy()
    chronic = (machine_id == chronic_machine)
    p_error[chronic] = 0.014
    chronic_cluster = chronic & np.isin(days, list(chronic_days))
    p_error[chronic_cluster] = 0.055
    cycle[chronic_cluster] += rng.normal(10, 3, chronic_cluster.sum())
    queue[chronic_cluster] += rng.normal(5, 2, chronic_cluster.sum())

    # Acute incident: strong KPI peak that is visible in hourly data.
    acute = (machine_id == primary_machine) & (ts.to_numpy() >= primary_start.to_datetime64()) & (ts.to_numpy() < primary_end.to_datetime64())
    cycle[acute] += rng.normal(105, 12, acute.sum())
    queue[acute] += rng.normal(34, 8, acute.sum())
    p_error[acute] = 0.48

    is_error = rng.random(n_records) < p_error
    status = np.where(is_error, "ERROR", "OK")
    error_code = np.full(n_records, None, dtype=object)
    # Normal baseline/chronic codes.
    baseline_choices = np.array(["E02","E07","E21"])
    idx = np.where(is_error & ~acute)[0]
    error_code[idx] = rng.choice(baseline_choices, size=len(idx), p=[0.34,0.36,0.30])
    error_code[acute & is_error] = "E14"

    downtime = np.zeros(n_records)
    err_idx = np.where(is_error)[0]
    downtime[err_idx] = np.round(rng.uniform(0.8, 5.5, len(err_idx)), 2)
    # Acute errors have more costly downtime.
    aerr = np.where(acute & is_error)[0]
    downtime[aerr] = np.round(rng.uniform(3.0, 9.0, len(aerr)), 2)

    ops = pd.DataFrame({
        "timestamp": ts.dt.strftime("%Y-%m-%d %H:%M:%S"),
        "zone": zone,
        "machine_id": machine_id,
        "operation_type": operation_type,
        "material_id": material_id,
        "source_location": src,
        "destination_location": dst,
        "cycle_time_sec": np.round(cycle,1),
        "queue_time_sec": np.round(queue,1),
        "status": status,
        "error_code": error_code,
        "downtime_minutes": np.round(downtime,2),
    })
    ops["date"] = ops["timestamp"].str[:10]

    # Event log: every machine has routine records; incident machines also have causal detail.
    events=[]
    for m in machines:
        events.append(("2026-08-01 06:00:00", m, "NORMAL", "N00", "Routine health check: normal operation"))
        events.append(("2026-08-15 06:00:00", m, "INFO", "I00", "Routine diagnostic snapshot"))
    # Acute incident sequence.
    events += [
        (f"2026-08-{primary_day:02d} 13:52:00", primary_machine, "WARNING", "W09", "Rising transfer cycle time"),
        (f"2026-08-{primary_day:02d} 14:03:00", primary_machine, "ALARM", "E14", "Positioning sensor inconsistency"),
        (f"2026-08-{primary_day:02d} 14:17:00", primary_machine, "ALARM", "E14", "Repeated positioning retries"),
        (f"2026-08-{primary_day:02d} 16:10:00", primary_machine, "MAINTENANCE", "M01", "Inspection and sensor alignment"),
        (f"2026-08-{primary_day:02d} 19:42:00", primary_machine, "RECOVERY", "R01", "Performance recovering after intervention"),
        (f"2026-08-{primary_day:02d} 20:05:00", primary_machine, "NORMAL", "N00", "Machine back to normal operation"),
    ]
    # Chronic incident sequence spread over multiple days.
    for d in sorted(chronic_days):
        events.append((f"2026-08-{d:02d} 09:15:00", chronic_machine, "WARNING", "W21", "Intermittent transport confirmation delay"))
    events += [
        (f"2026-08-{sorted(chronic_days)[2]:02d} 11:40:00", chronic_machine, "MAINTENANCE", "M02", "Connector inspection; no permanent fault found"),
        (f"2026-08-{max(chronic_days):02d} 18:20:00", chronic_machine, "MAINTENANCE", "M03", "Communication module connector reseated"),
        (f"2026-08-{max(chronic_days):02d} 19:00:00", chronic_machine, "RECOVERY", "R02", "Error frequency returned to baseline"),
    ]
    event_log = pd.DataFrame(events, columns=["event_time","machine_id","event_type","event_code","message"]).sort_values(["event_time","machine_id"])

    # Hourly aggregation for Power BI. Keep raw error count so a weighted overall rate is possible.
    temp=ops.copy()
    temp["hour"] = pd.to_datetime(temp["timestamp"]).dt.floor("h")
    temp["is_error"] = (temp["status"]=="ERROR").astype(int)
    hourly=(temp.groupby(["hour","zone","machine_id"], as_index=False)
            .agg(operations=("status","size"),
                 errors=("is_error","sum"),
                 avg_cycle_time_sec=("cycle_time_sec","mean"),
                 avg_queue_time_sec=("queue_time_sec","mean"),
                 downtime_minutes=("downtime_minutes","sum")))
    hourly["error_rate"] = hourly["errors"] / hourly["operations"]
    hourly["avg_cycle_time_sec"] = hourly["avg_cycle_time_sec"].round(1)
    hourly["avg_queue_time_sec"] = hourly["avg_queue_time_sec"].round(1)
    hourly["downtime_minutes"] = hourly["downtime_minutes"].round(2)
    hourly["error_rate"] = hourly["error_rate"].round(4)
    hourly["hour"] = hourly["hour"].dt.strftime("%Y-%m-%d %H:%M:%S")

    ops.to_csv(out_dir/"warehouse_operations.csv", index=False)
    hourly.to_csv(out_dir/"hourly_kpis.csv", index=False)
    event_log.to_csv(out_dir/"event_log.csv", index=False)

    db=sqlite3.connect(out_dir/"warehouse.db")
    ops.to_sql("warehouse_operations", db, if_exists="replace", index=False)
    hourly.to_sql("hourly_kpis", db, if_exists="replace", index=False)
    event_log.to_sql("event_log", db, if_exists="replace", index=False)
    db.close()

    # Truth computed from generated data, not hard-coded claims.
    by_machine=(ops.assign(is_error=(ops.status=="ERROR").astype(int))
                  .groupby("machine_id")
                  .agg(operations=("status","size"), errors=("is_error","sum"), error_days=("date", lambda s: s[ops.loc[s.index,"status"].eq("ERROR")].nunique()))
                  .reset_index())
    by_machine["error_rate_pct"] = 100*by_machine.errors/by_machine.operations
    top_abs = by_machine.sort_values(["errors","error_days"], ascending=False).iloc[0]

    daily=(ops.assign(is_error=(ops.status=="ERROR").astype(int))
             .groupby(["date","machine_id"], as_index=False)
             .agg(operations=("status","size"), errors=("is_error","sum"), avg_cycle_time_sec=("cycle_time_sec","mean"), avg_queue_time_sec=("queue_time_sec","mean"), downtime_minutes=("downtime_minutes","sum")))
    daily["error_rate"] = daily.errors/daily.operations
    # Acute candidate score based on simultaneous relative peak of cycle, queue, error rate, downtime.
    for col in ["avg_cycle_time_sec","avg_queue_time_sec","error_rate","downtime_minutes"]:
        med=daily.groupby("machine_id")[col].transform("median")
        mad=(daily[col]-med).abs().groupby(daily.machine_id).transform("median").replace(0,1e-6)
        daily[col+"_score"]=(daily[col]-med)/(mad+1e-6)
    daily["acute_score"] = daily[[c+"_score" for c in ["avg_cycle_time_sec","avg_queue_time_sec","error_rate","downtime_minutes"]]].sum(axis=1)
    top_acute = daily.sort_values("acute_score", ascending=False).iloc[0]

    solution = {
        "seed": seed,
        "records": int(len(ops)),
        "topology": topology,
        "expected_primary_acute_incident": {
            "machine_id": primary_machine,
            "zone": primary_machine[0],
            "date": f"2026-08-{primary_day:02d}",
            "window": [str(primary_start), str(primary_end)],
            "root_cause_hypothesis": "Positioning sensor inconsistency caused repeated retries, higher cycle/queue time, E14 errors and downtime; performance recovered after sensor alignment.",
            "verification_from_generated_data": {"top_machine_day_by_multikpi_score": f"{top_acute.machine_id} / {top_acute.date}"}
        },
        "expected_recurring_reliability_issue": {
            "machine_id": chronic_machine,
            "zone": chronic_machine[0],
            "pattern": "Errors recur across multiple days without a single dominant performance peak.",
            "root_cause_hypothesis": "Intermittent transport-confirmation/communication issue; event log records recurring W21 warnings and later connector intervention.",
            "verification_from_generated_data": {"top_machine_by_absolute_errors": str(top_abs.machine_id), "errors": int(top_abs.errors), "error_days": int(top_abs.error_days)}
        }
    }
    (out_dir/"expected_solution.json").write_text(json.dumps(solution, indent=2), encoding="utf-8")
    return solution

if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--out", default=".")
    ap.add_argument("--seed", type=int, default=None, help="reproducible seed; if omitted, a fresh random seed is generated")
    ap.add_argument("--records", type=int, default=30000)
    ap.add_argument(
        "--rand-topo", "--randomize-topology",
        dest="rand_topo",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="randomize machine counts by zone (default: enabled)"
    )
    ap.add_argument(
        "--rand-inc", "--randomize-incidents",
        dest="rand_inc",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="randomize acute/chronic machines and incident days (default: enabled); expected_solution.json adapts"
    )
    ap.add_argument(
        "--show-solution",
        action="store_true",
        help="print the generated answer key to the console (default: hidden)"
    )
    args=ap.parse_args()
    solution = generate(Path(args.out), args.seed, args.records, args.rand_topo, args.rand_inc)
    if args.show_solution:
        print(json.dumps(solution, indent=2))
    else:
        print(f"Generated dataset with seed: {solution['seed']}")
        print(f"Answer key written to: {Path(args.out) / 'expected_solution.json'}")
        print("Do not open the answer key until you finish the exercise.")
