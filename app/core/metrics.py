"""Prometheus-compatible metrics, shared between the API process and workers."""

from prometheus_client import Counter, Gauge, Histogram

jobs_created_total = Counter("jobs_created_total", "Total number of jobs created")
jobs_completed_total = Counter("jobs_completed_total", "Total number of jobs completed")
jobs_failed_total = Counter("jobs_failed_total", "Total number of jobs permanently failed")
jobs_retried_total = Counter("jobs_retried_total", "Total number of job retry attempts")
jobs_cancelled_total = Counter("jobs_cancelled_total", "Total number of jobs cancelled")

job_processing_seconds = Histogram(
    "job_processing_seconds", "Time spent executing a job handler", ["job_type"]
)

queue_depth = Gauge("queue_depth", "Number of jobs currently PENDING")
active_workers = Gauge("active_workers", "Number of currently running worker tasks")
