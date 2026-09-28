# Assignment: Automated Warehouse Incident Analysis

Analyze synthetic operational data from an automated warehouse. The goal is to reach a clear, data-supported conclusion without knowing how the data generator works.

## Questions

1. Which **machine and day** represent the main acute incident? Support your conclusion with at least three KPIs (for example cycle time, queue time, error rate, and downtime).
2. Which machine shows the **most significant recurring reliability problem during the month**? Distinguish between absolute error count, error rate, and number of days with errors.
3. For both selected machines, inspect `event_log.csv` and formulate a **root-cause hypothesis**. Clearly separate what the data demonstrate from what they only suggest.
4. Explain why a visual peak in Power BI and a ranking by total number of errors do not necessarily identify the same machine.
5. Propose one additional step you would take in a real warehouse before a technical intervention.

## Recommended time limit

3 hours.

Use a coarse-to-fine workflow:

`data quality -> screening -> candidate ranking -> drill-down -> event log -> conclusion`

## Self-check

After completing the analysis, open `expected_solution.json` and compare it with your conclusions.
