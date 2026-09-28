-- Automated Warehouse Analytics
-- SQL dialect: SQLite
--
-- Suggested workflow:
--   1) Screen all machines using KPIs.
--   2) Look for machine/day concentrations of errors and degraded performance.
--   3) Select one candidate machine and inspect its error codes.
--   4) Inspect the detailed timeline for the selected machine/day.
--   5) Compare the operational data with the technical event log.
--
-- Replace 'SELECTED_MACHINE' and 'YYYY-MM-DD' in sections 3-5
-- with the machine/date identified during screening.


-- ============================================================
-- 1. KPI SCREENING BY MACHINE
-- ============================================================
-- Broad comparison of workload, speed, queueing, error rate and downtime.

SELECT
    zone,
    machine_id,
    COUNT(*) AS operations,
    ROUND(AVG(cycle_time_sec), 1) AS avg_cycle_time_sec,
    ROUND(AVG(queue_time_sec), 1) AS avg_queue_time_sec,
    SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) AS errors,
    ROUND(
        100.0 * SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) / COUNT(*),
        2
    ) AS error_rate_pct,
    ROUND(SUM(downtime_minutes), 1) AS downtime_minutes
FROM warehouse_operations
GROUP BY zone, machine_id
ORDER BY error_rate_pct DESC, errors DESC, downtime_minutes DESC;


-- ============================================================
-- 2. MACHINE x DAY INCIDENT SCREENING
-- ============================================================
-- Finds days where a machine has a concentrated cluster of errors.
-- The extra KPI columns help distinguish an isolated error cluster
-- from a broader performance degradation.

SELECT
    date,
    zone,
    machine_id,
    COUNT(*) AS operations,
    SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) AS errors,
    ROUND(
        100.0 * SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) / COUNT(*),
        2
    ) AS error_rate_pct,
    ROUND(AVG(cycle_time_sec), 1) AS avg_cycle_time_sec,
    ROUND(AVG(queue_time_sec), 1) AS avg_queue_time_sec,
    ROUND(SUM(downtime_minutes), 1) AS downtime_minutes
FROM warehouse_operations
GROUP BY date, zone, machine_id
HAVING SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) > 0
ORDER BY errors DESC, error_rate_pct DESC, downtime_minutes DESC;


-- ============================================================
-- 3. ERROR-CODE BREAKDOWN FOR A SELECTED MACHINE
-- ============================================================
-- Replace SELECTED_MACHINE with the machine chosen in step 1/2.

SELECT
    machine_id,
    error_code,
    COUNT(*) AS error_count,
    ROUND(SUM(downtime_minutes), 1) AS downtime_minutes
FROM warehouse_operations
WHERE status = 'ERROR'
  AND machine_id = 'SELECTED_MACHINE'
GROUP BY machine_id, error_code
ORDER BY error_count DESC, downtime_minutes DESC;


-- ============================================================
-- 4. DETAILED TIMELINE FOR A SELECTED MACHINE
-- ============================================================
-- Rows are grouped by day according to the number of errors on that day,
-- with the most error-heavy days shown first. Events within each day remain
-- chronological.
--
-- Replace SELECTED_MACHINE before running.

SELECT *
FROM warehouse_operations
WHERE status = 'ERROR'
  AND machine_id = 'SELECTED_MACHINE'
ORDER BY
    COUNT(*) OVER (
        PARTITION BY substr(timestamp, 1, 10)
    ) DESC,
    timestamp;


-- ============================================================
-- 5. SELECTED MACHINE + SELECTED DAY
-- ============================================================
-- Use this after identifying a specific incident date.
-- Replace SELECTED_MACHINE and YYYY-MM-DD.

SELECT *
FROM warehouse_operations
WHERE machine_id = 'SELECTED_MACHINE'
  AND substr(timestamp, 1, 10) = 'YYYY-MM-DD'
ORDER BY timestamp;


-- ============================================================
-- 6. TECHNICAL EVENT LOG FOR THE SELECTED MACHINE
-- ============================================================
-- Compare warnings, alarms, maintenance and recovery events with the
-- operational KPI/error timeline.

SELECT
    event_time,
    machine_id,
    event_type,
    event_code,
    message
FROM event_log
WHERE machine_id = 'SELECTED_MACHINE'
ORDER BY event_time;


-- ============================================================
-- 7. OPTIONAL: HOW MANY ERROR DAYS DOES EACH MACHINE HAVE?
-- ============================================================
-- Useful for distinguishing a one-off acute incident from a recurring
-- reliability problem spread over many days.

SELECT
    machine_id,
    COUNT(DISTINCT date) AS error_days,
    COUNT(*) AS total_errors
FROM warehouse_operations
WHERE status = 'ERROR'
GROUP BY machine_id
ORDER BY error_days DESC, total_errors DESC;
