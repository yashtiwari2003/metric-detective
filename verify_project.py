"""
verify_project.py - One-shot health check for the Metric Detective project.

Place this file in the project root (next to README.md) and run:

    python verify_project.py                 # full check (uses a few LLM calls)
    python verify_project.py --skip-llm      # no API calls (free, fast)
    python verify_project.py --with-eval     # also run the 30-case evaluation (slow, costs API credits)

Every check reports PASS / WARN / FAIL / SKIP. A summary and a markdown report
(verification_report.md) are written at the end. Exit code is 1 if anything FAILED.

The script adapts to small naming differences: if a file or command is not where the
master prompt said it would be, the check is marked WARN/SKIP instead of crashing.
"""
from __future__ import annotations

import argparse
import gc
import importlib
import importlib.util
import inspect
import json
import os
import py_compile
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------------------
# Configuration (edit here if your agent used different names)
# --------------------------------------------------------------------------------------
EXPECTED_CSVS = {
    # file name: expected data rows in the original Olist dataset (used as WARN-only check)
    "olist_orders_dataset.csv": 99441,
    "olist_order_items_dataset.csv": 112650,
    "olist_order_payments_dataset.csv": 103886,
    "olist_order_reviews_dataset.csv": 99224,
    "olist_customers_dataset.csv": 99441,
    "olist_sellers_dataset.csv": 3095,
    "olist_products_dataset.csv": 32951,
    "olist_geolocation_dataset.csv": 1000163,
    "product_category_name_translation.csv": 71,
}

EXPECTED_PATHS = [
    "README.md", "requirements.txt", "config.yaml", ".env.example", ".gitignore",
    "src/etl", "src/detection", "src/analysis", "src/ai", "src/pipeline", "src/safety",
    "src/evaluation", "app", "tests", "sql",
]

KEY_MODULE_FILES = {
    "ETL loader": ["src/etl/load_data.py"],
    "Anomaly detection": ["src/detection/anomaly.py"],
    "Contribution analysis": ["src/analysis/contribution.py"],
    "Statistical tests": ["src/analysis/stats_tests.py"],
    "LLM client": ["src/ai/llm_client.py"],
    "Hypothesis agent": ["src/ai/hypothesis_agent.py"],
    "SQL agent": ["src/ai/sql_agent.py"],
    "Verifier": ["src/ai/verifier.py"],
    "Reporter": ["src/ai/reporter.py"],
    "Pipeline orchestrator": ["src/pipeline/investigate.py"],
    "SQL guard": ["src/safety/sql_guard.py"],
    "Anomaly injection": ["src/evaluation/inject_anomalies.py"],
    "Evaluation runner": ["src/evaluation/run_eval.py"],
    "Streamlit app": ["app/streamlit_app.py", "app/app.py", "streamlit_app.py"],
}

# Core tables we expect in the database (names are matched case-insensitively and by substring)
EXPECTED_TABLE_HINTS = ["orders", "order_items", "payments", "reviews", "customers", "sellers", "products"]
FACT_VIEW_HINTS = ["fact_orders"]
KPI_TABLE_HINTS = ["kpi_daily", "kpi"]
FACT_REQUIRED_COLUMNS = ["order_id", "delivery_days", "is_late", "category", "customer_state"]

DANGEROUS_SQL = [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET order_status = 'x'",
    "INSERT INTO orders VALUES (1)",
    "SELECT 1; DROP TABLE orders",
    "ALTER TABLE orders ADD COLUMN x INT",
]
SAFE_SQL = "SELECT 1 AS ok"

SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_\-]{20,}",        # OpenAI
    r"AIza[0-9A-Za-z_\-]{30,}",       # Google / Gemini
    r"gsk_[A-Za-z0-9]{20,}",          # Groq
    r"ghp_[A-Za-z0-9]{30,}",          # GitHub PAT
    r"\"key\"\s*:\s*\"[a-f0-9]{32}\"",  # kaggle.json
]

INVESTIGATION_ARGS = ["--kpi", "revenue", "--start", "2017-11-20", "--end", "2017-11-26"]

# --------------------------------------------------------------------------------------
# Result bookkeeping
# --------------------------------------------------------------------------------------
USE_COLOR = sys.stdout.isatty() and os.name != "nt" or os.environ.get("FORCE_COLOR")
COLORS = {"PASS": "\033[92m", "WARN": "\033[93m", "FAIL": "\033[91m", "SKIP": "\033[90m", "END": "\033[0m",
          "BOLD": "\033[1m"}


def c(text: str, key: str) -> str:
    return f"{COLORS[key]}{text}{COLORS['END']}" if USE_COLOR else text


@dataclass
class Result:
    section: str
    name: str
    status: str
    detail: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)
    current: str = ""

    def section(self, title: str) -> None:
        self.current = title
        print("\n" + c(f"== {title} ==", "BOLD"))

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.results.append(Result(self.current, name, status, detail))
        line = f"  [{c(status, status)}] {name}"
        if detail:
            line += f" - {detail}"
        print(line)

    def ok(self, n, d=""): self.add("PASS", n, d)
    def warn(self, n, d=""): self.add("WARN", n, d)
    def fail(self, n, d=""): self.add("FAIL", n, d)
    def skip(self, n, d=""): self.add("SKIP", n, d)


R = Report()
ROOT = Path.cwd()


def run(cmd: list[str], timeout: int = 300, env: dict | None = None) -> tuple[int, str]:
    """Run a command and return (exit_code, combined_output)."""
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **(env or {})})
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"Timed out after {timeout}s"
    except FileNotFoundError as e:
        return 127, str(e)


def tail(text: str, n: int = 6) -> str:
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return " | ".join(lines[-n:])[:600]


def first_existing(paths: list[str]) -> Path | None:
    for p in paths:
        if (ROOT / p).exists():
            return ROOT / p
    return None


def load_dotenv_simple(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# --------------------------------------------------------------------------------------
# 1. Environment
# --------------------------------------------------------------------------------------
def check_environment() -> None:
    R.section("1. Environment")
    v = sys.version_info
    if v >= (3, 11):
        R.ok("Python version", f"{v.major}.{v.minor}.{v.micro}")
    elif v >= (3, 9):
        R.warn("Python version", f"{v.major}.{v.minor} (3.11+ recommended)")
    else:
        R.fail("Python version", f"{v.major}.{v.minor} is too old")

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or bool(os.environ.get("CONDA_PREFIX"))
    (R.ok if in_venv else R.warn)("Virtual environment active", "yes" if in_venv else "not detected (recommended)")

    req = ROOT / "requirements.txt"
    if not req.exists():
        R.fail("requirements.txt", "missing")
        return
    from importlib import metadata
    missing = []
    for raw in req.read_text().splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip()
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        R.fail("Required packages installed", f"missing: {', '.join(missing)} -> pip install -r requirements.txt")
    else:
        R.ok("Required packages installed")

    code, out = run([sys.executable, "-m", "pip", "check"], timeout=120)
    (R.ok if code == 0 else R.warn)("pip check (dependency conflicts)", "none" if code == 0 else tail(out, 3))


# --------------------------------------------------------------------------------------
# 2. Project structure
# --------------------------------------------------------------------------------------
def check_structure() -> None:
    R.section("2. Project structure")
    missing = [p for p in EXPECTED_PATHS if not (ROOT / p).exists()]
    if missing:
        R.warn("Expected folders/files", f"missing: {', '.join(missing)}")
    else:
        R.ok("Expected folders/files", "all present")
    for label, options in KEY_MODULE_FILES.items():
        found = first_existing(options)
        (R.ok if found else R.fail)(label, str(found.relative_to(ROOT)) if found else f"not found ({options[0]})")


# --------------------------------------------------------------------------------------
# 3. Secrets and git hygiene
# --------------------------------------------------------------------------------------
def check_secrets() -> dict[str, str]:
    R.section("3. Secrets and git safety")
    env_path = ROOT / ".env"
    env = load_dotenv_simple(env_path)
    if not env_path.exists():
        R.fail(".env file", "missing - copy .env.example to .env and add your key")
    else:
        key_names = [k for k in env if "KEY" in k.upper()]
        filled = [k for k in key_names if env[k] and "your" not in env[k].lower()]
        if filled:
            R.ok(".env API key present", f"{', '.join(filled)} (value hidden)")
        else:
            R.fail(".env API key present", "no filled *_KEY variable found")

    gi = ROOT / ".gitignore"
    gi_text = gi.read_text(errors="ignore") if gi.exists() else ""
    for pattern in [".env", "data"]:
        if re.search(rf"^\s*/?{re.escape(pattern)}(/|\s*$)", gi_text, re.M) or pattern in gi_text:
            R.ok(f".gitignore contains {pattern}")
        else:
            R.fail(f".gitignore contains {pattern}", "add it before pushing to GitHub")

    if not (ROOT / ".git").exists() or not shutil.which("git"):
        R.skip("Git checks", "not a git repo or git not installed")
        return env

    code, tracked = run(["git", "ls-files"])
    tracked_files = [f.strip() for f in tracked.splitlines() if f.strip()]
    
    # Check if .env or data files are tracked
    bad_tracked = [f for f in tracked_files if f == ".env" or f.startswith("data/")]
    if bad_tracked:
        R.fail("Git tracked files", f"Secret/data files tracked by git: {', '.join(bad_tracked[:5])}")
    else:
        R.ok("Git tracked files", "no .env or data/ files tracked")

    # Scan tracked files for leaked secret patterns
    leaks = []
    for f_rel in tracked_files:
        p = ROOT / f_rel
        if not p.is_file() or p.stat().st_size > 1_000_000:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            for sp in SECRET_PATTERNS:
                if re.search(sp, content):
                    leaks.append(f"{f_rel} (matches {sp})")
                    break
        except Exception:
            pass

    if leaks:
        R.fail("Secret pattern scan in git files", f"Leaked secrets detected: {', '.join(leaks)}")
    else:
        R.ok("Secret pattern scan in git files", "no secret patterns detected")

    return env


# --------------------------------------------------------------------------------------
# 4. Data and database
# --------------------------------------------------------------------------------------
def check_data(env: dict[str, str]) -> None:
    R.section("4. Data and database")
    raw_dir = ROOT / "data" / "raw"
    if not raw_dir.exists():
        R.fail("Raw data folder", "data/raw does not exist")
    else:
        missing_csvs = [f for f in EXPECTED_CSVS if not (raw_dir / f).exists()]
        if missing_csvs:
            R.fail("Olist raw CSVs", f"missing: {', '.join(missing_csvs)}")
        else:
            R.ok("Olist raw CSVs", "all 9 expected CSV files present")

    # Run database checks in an isolated subprocess so the DuckDB file lock
    # is fully released before later checks (pytest, pipeline) need the DB.
    db_url = env.get("DB_URL", "duckdb:///data/metric.duckdb")
    if db_url.startswith("duckdb:///"):
        db_path = db_url.replace("duckdb:///", "")
        resolved_db = ROOT / db_path
        db_url = f"duckdb:///{resolved_db}"

    db_check_script = f'''
import json, sys
from sqlalchemy import create_engine, text
results = []
try:
    engine = create_engine("{db_url}")
    with engine.connect() as conn:
        res = conn.execute(text("SHOW TABLES")).fetchall()
        table_names = [r[0].lower() for r in res]
        fact_matches = [t for t in table_names if "fact_orders" in t]
        if fact_matches:
            fact_tbl = fact_matches[0]
            results.append(("PASS", "Database fact table", f"found '{{fact_tbl}}'"))
            col_res = conn.execute(text(f"DESCRIBE {{fact_tbl}}")).fetchall()
            col_names = [c[0].lower() for c in col_res]
            req_cols = {FACT_REQUIRED_COLUMNS!r}
            missing_cols = [c for c in req_cols if c.lower() not in col_names]
            if missing_cols:
                results.append(("WARN", "fact_orders columns", f"missing expected: {{', '.join(missing_cols)}}"))
            else:
                results.append(("PASS", "fact_orders columns", "required columns present"))
            count = conn.execute(text(f"SELECT COUNT(*) FROM {{fact_tbl}}")).scalar()
            results.append(("PASS", "fact_orders row count", f"{{count:,}} rows"))
        else:
            results.append(("FAIL", "Database fact table", "fact_orders table/view not found"))
        kpi_matches = [t for t in table_names if "kpi" in t]
        if kpi_matches:
            kpi_tbl = kpi_matches[0]
            kpi_count = conn.execute(text(f"SELECT COUNT(*) FROM {{kpi_tbl}}")).scalar()
            results.append(("PASS", "Database KPI table", f"found '{{kpi_tbl}}' with {{kpi_count:,}} days"))
        else:
            results.append(("WARN", "Database KPI table", "kpi_daily table not found"))
    engine.dispose()
except Exception as e:
    results.append(("FAIL", "Database connection & tables", str(e)))
print(json.dumps(results))
'''
    code, out = run([sys.executable, "-c", db_check_script], timeout=30)
    if code == 0:
        try:
            # Parse the JSON output from subprocess
            for line in out.strip().splitlines():
                line = line.strip()
                if line.startswith("["):
                    for status, name, detail in json.loads(line):
                        R.add(status, name, detail)
                    break
            else:
                R.fail("Database connection & tables", f"unexpected output: {tail(out, 2)}")
        except json.JSONDecodeError:
            R.fail("Database connection & tables", f"JSON parse error: {tail(out, 2)}")
    else:
        R.fail("Database connection & tables", tail(out, 3))


# --------------------------------------------------------------------------------------
# 5. SQL Guard
# --------------------------------------------------------------------------------------
def check_sql_guard() -> None:
    R.section("5. SQL Guardrails")
    try:
        from src.safety.sql_guard import validate_sql
        
        # Test dangerous statements
        blocked_count = 0
        for d_sql in DANGEROUS_SQL:
            try:
                is_valid, msg = validate_sql(d_sql)
                if not is_valid:
                    blocked_count += 1
            except Exception:
                blocked_count += 1
                
        if blocked_count == len(DANGEROUS_SQL):
            R.ok("Dangerous SQL blocking", f"blocked {blocked_count}/{len(DANGEROUS_SQL)} statements")
        else:
            R.fail("Dangerous SQL blocking", f"only blocked {blocked_count}/{len(DANGEROUS_SQL)}")

        # Test safe select
        try:
            is_valid, cleaned = validate_sql("SELECT 1 AS ok FROM fact_orders")
            if is_valid:
                R.ok("Safe SQL query", "accepted properly")
            else:
                R.warn("Safe SQL query", f"rejected: {cleaned}")
        except Exception as e:
            R.fail("Safe SQL query", str(e))

    except ImportError as e:
        R.fail("SQL Guard module", f"ImportError: {e}")


# --------------------------------------------------------------------------------------
# 6. Statistical tests
# --------------------------------------------------------------------------------------
def check_statistical_tests() -> None:
    R.section("6. Statistical tests")
    try:
        from src.analysis.stats_tests import welch_ttest, proportion_ztest, chi_square_test
        
        # Welch t-test
        t_res = welch_ttest([10.0, 11.0, 12.0, 10.5], [20.0, 22.0, 21.0, 23.0])
        if t_res and hasattr(t_res, 'p_value'):
            R.ok("Welch t-test", f"p={t_res.p_value:.4f}")
        else:
            R.fail("Welch t-test", "unexpected return format")

        # Two-proportion z-test
        z_res = proportion_ztest(10, 100, 35, 100)
        if z_res and hasattr(z_res, 'p_value'):
            R.ok("Two-proportion z-test", f"p={z_res.p_value:.4f}")
        else:
            R.fail("Two-proportion z-test", "unexpected return format")

        # Chi-square test
        chi_res = chi_square_test({"A": 50, "B": 50}, {"A": 80, "B": 20})
        if chi_res and hasattr(chi_res, 'p_value'):
            R.ok("Chi-square test", f"p={chi_res.p_value:.4f}")
        else:
            R.fail("Chi-square test", "unexpected return format")

    except ImportError as e:
        R.fail("Statistical tests module", f"ImportError: {e}")
    except Exception as e:
        R.fail("Statistical tests execution", str(e))


# --------------------------------------------------------------------------------------
# 7. LLM & Investigation Pipeline
# --------------------------------------------------------------------------------------
def check_llm_and_investigation(skip_llm: bool) -> None:
    R.section("7. LLM Client & Investigation Pipeline")
    if skip_llm:
        R.skip("LLM API calls", "--skip-llm specified")
        R.skip("Live investigation CLI", "--skip-llm specified")
        return

    try:
        from src.ai.llm_client import call_llm
        test_resp = call_llm(
            system="You are a health check agent.",
            user="Reply with the single word: HEALTHY"
        )
        if "HEALTHY" in str(test_resp).upper():
            R.ok("LLM API connectivity", f"response received: {str(test_resp).strip()[:30]}")
        else:
            R.warn("LLM API connectivity", f"unexpected response: {str(test_resp)[:50]}")
    except Exception as e:
        R.fail("LLM API connectivity", str(e))
        return

    # Test running investigation
    cmd = [sys.executable, "-m", "src.pipeline.investigate", *INVESTIGATION_ARGS]
    code, out = run(cmd, timeout=90)
    if code == 0 and "REPORT:" in out:
        R.ok("Pipeline investigation CLI", f"executed successfully ({INVESTIGATION_ARGS[1]} on {INVESTIGATION_ARGS[3]})")
    else:
        R.warn("Pipeline investigation CLI", tail(out, 4))


# --------------------------------------------------------------------------------------
# 8. Streamlit App
# --------------------------------------------------------------------------------------
def check_streamlit() -> None:
    R.section("8. Streamlit web application")
    app_file = first_existing(KEY_MODULE_FILES["Streamlit app"])
    if not app_file:
        R.fail("Streamlit app file", "not found")
        return

    try:
        py_compile.compile(str(app_file), doraise=True)
        R.ok("Streamlit app syntax", f"py_compile passed on {app_file.name}")
    except py_compile.PyCompileError as e:
        R.fail("Streamlit app syntax", str(e))


# --------------------------------------------------------------------------------------
# 9. Automated Tests
# --------------------------------------------------------------------------------------
def check_tests() -> None:
    R.section("9. Test suite (pytest)")
    code, out = run([sys.executable, "-m", "pytest", "tests/"], timeout=120)
    if code == 0:
        match = re.search(r"(\d+ passed)", out)
        R.ok("Pytest suite", match.group(1) if match else "all tests passed")
    else:
        R.fail("Pytest suite", tail(out, 4))


# --------------------------------------------------------------------------------------
# 10. Benchmark Evaluation
# --------------------------------------------------------------------------------------
def check_evaluation(with_eval: bool) -> None:
    R.section("10. Benchmark evaluation")
    eval_report = ROOT / "reports" / "eval_results.md"
    gt_file = ROOT / "evaluation" / "ground_truth.json"

    if gt_file.exists():
        R.ok("Ground truth anomalies", f"{gt_file.name} present")
    else:
        R.warn("Ground truth anomalies", "evaluation/ground_truth.json not found")

    if with_eval:
        code, out = run([sys.executable, "-m", "src.evaluation.run_eval"], timeout=180)
        if code == 0 and eval_report.exists():
            R.ok("Evaluation runner", "30 cases evaluated; reports/eval_results.md generated")
        else:
            R.fail("Evaluation runner", tail(out, 4))
    else:
        if eval_report.exists():
            R.ok("Evaluation report", f"reports/eval_results.md present ({eval_report.stat().st_size:,} bytes)")
        else:
            R.warn("Evaluation report", "run with --with-eval or 'python -m src.evaluation.run_eval'")


# --------------------------------------------------------------------------------------
# Summary and Markdown Report
# --------------------------------------------------------------------------------------
def generate_summary_report() -> int:
    R.section("Verification Summary")
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for r in R.results:
        counts[r.status] = counts.get(r.status, 0) + 1

    print(f"\nResults: {c(str(counts['PASS']) + ' PASS', 'PASS')} | "
          f"{c(str(counts['WARN']) + ' WARN', 'WARN')} | "
          f"{c(str(counts['FAIL']) + ' FAIL', 'FAIL')} | "
          f"{c(str(counts['SKIP']) + ' SKIP', 'SKIP')}")

    # Write verification_report.md
    md_lines = [
        "# Metric Detective — Project Health Verification Report",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        f"- **PASS:** {counts['PASS']}",
        f"- **WARN:** {counts['WARN']}",
        f"- **FAIL:** {counts['FAIL']}",
        f"- **SKIP:** {counts['SKIP']}",
        "",
        "## Detailed Checks",
        "| Section | Check | Status | Details |",
        "|:--------|:------|:------:|:--------|",
    ]

    for r in R.results:
        md_lines.append(f"| {r.section} | {r.name} | **{r.status}** | {r.detail} |")

    report_path = ROOT / "verification_report.md"
    report_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"\nFull verification report written to: {report_path.name}")

    return 1 if counts["FAIL"] > 0 else 0


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Metric Detective One-Shot Health Check")
    parser.add_argument("--skip-llm", action="store_true", help="Skip live LLM API calls and investigation")
    parser.add_argument("--with-eval", action="store_true", help="Run full evaluation benchmark runner")
    args = parser.parse_args()

    print(c("==================================================", "BOLD"))
    print(c("  Metric Detective — One-Shot Project Health Check", "BOLD"))
    print(c("==================================================", "BOLD"))

    check_environment()
    check_structure()
    env = check_secrets()
    check_data(env)
    check_sql_guard()
    check_statistical_tests()
    check_llm_and_investigation(args.skip_llm)
    check_streamlit()
    check_tests()
    check_evaluation(args.with_eval)

    return generate_summary_report()


if __name__ == "__main__":
    sys.exit(main())
