"""
run.py
=======
CLI entry point for the full Phase 5 ML pipeline. Runs each script in
dependency order, in its OWN subprocess — not by importing each module's
main() in-process.

WHY SUBPROCESS, NOT IN-PROCESS IMPORT:
Every script in this pipeline (sentiment.py, segmentation.py, clv_model.py,
churn_model.py, next_purchase.py) defines its own module-level argparse
parser and calls sys.exit() on certain failure paths (e.g. sentiment.py
exits if LeIA isn't installed; next_purchase.py exits if refresh_log is
empty). Importing five such modules into one process means: their
argparse parsers would collide on sys.argv, a sys.exit() in any one of
them would kill the entire orchestrator instead of just that step, and
their independent logging.FileHandler setups would all attach to the
same process's logging tree. Subprocess isolation avoids all of this and
matches how a real production pipeline runner would compose independently
-developed, independently-testable scripts — which is also exactly how
this pipeline was actually built and verified, script by script.

EXECUTION ORDER (matches the Phase 5 plan's dependency chain):
    1. sentiment.py       -> mart.sentiment_scores
    2. segmentation.py    -> mart.rfm_features
    3. clv_model.py       -> mart.clv_features
    4. churn_model.py     -> mart.customer_360 (also backfills avg_sentiment_score from step 1's output)
    5. next_purchase.py   -> mart.customer_360
    6. (action queue rule engine — see ACTION QUEUE note below)

ACTION QUEUE NOTE:
The original Phase 5 plan called for a 6th step writing mart.crm_action_queue
via rules defined in config.py. That rule engine is NOT included in this
file — config.py in this project is the DATABASE CONNECTION file (see
config.py's actual contents: DB_CONFIG + CONNECTION_STRING, nothing else),
not a rules/thresholds file. Bolting CRM business rules onto the DB config
module would conflate two unrelated concerns. The action-queue rule engine
is a distinct, sizeable piece of logic (segment/churn/CLV threshold rules
writing structured trigger_reason text) that deserves its own file
(e.g. action_rules.py) and its own review pass — flagged here as a
deliberate scope boundary for THIS file, not a silent omission. run.py's
--actions flag below is wired and ready; it currently raises
NotImplementedError with a clear message rather than pretending to work.

USAGE:
    python run.py --all
    python run.py --sentiment
    python run.py --segment
    python run.py --clv
    python run.py --churn
    python run.py --next-purchase
    python run.py --actions
    python run.py --all --dry-run
    python run.py --churn --threshold 0.35
    python run.py --all --force
"""

import argparse
import logging
import os
import subprocess
import sys
import time

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/run_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.log"),
    ],
)
logger = logging.getLogger("run")


# =============================================================================
# Step definitions — script, the CLI args it actually supports (confirmed
# against each script's argparse setup, not assumed), and which top-level
# run.py flags map onto which per-script flag.
# =============================================================================

STEPS = [
    {
        "name": "sentiment",
        "script": "sentiment.py",
        "supports": {"dry_run": "--dry-run", "force": "--force", "batch_size": "--batch-size"},
    },
    {
        "name": "segment",
        "script": "segmentation.py",
        "supports": {"dry_run": "--dry-run", "batch_size": "--batch-size"},
        # segmentation.py's --force-k / --k are step-specific, not exposed
        # at the run.py level — pass-through args (below) cover this.
    },
    {
        "name": "clv",
        "script": "clv_model.py",
        "supports": {"dry_run": "--dry-run", "batch_size": "--batch-size"},
        # clv_model.py has no --force flag (it always retrains; there's no
        # "skip if already scored" concept for a regression refit the way
        # sentiment.py has for per-review scores) — --force at the run.py
        # level is silently a no-op for this step, documented below in main().
    },
    {
        "name": "churn",
        "script": "churn_model.py",
        "supports": {"dry_run": "--dry-run", "batch_size": "--batch-size", "threshold": "--threshold"},
    },
    {
        "name": "next_purchase",
        "script": "next_purchase.py",
        "supports": {"dry_run": "--dry-run", "batch_size": "--batch-size"},
    },
]

STEP_ORDER = ["sentiment", "segment", "clv", "churn", "next_purchase"]


def build_step_command(step: dict, args: argparse.Namespace) -> list:
    cmd = [sys.executable, step["script"]]

    if args.dry_run and "dry_run" in step["supports"]:
        cmd.append(step["supports"]["dry_run"])

    if args.force and "force" in step["supports"]:
        cmd.append(step["supports"]["force"])

    if args.batch_size is not None and "batch_size" in step["supports"]:
        cmd += [step["supports"]["batch_size"], str(args.batch_size)]

    if step["name"] == "churn" and args.threshold is not None:
        cmd += [step["supports"]["threshold"], str(args.threshold)]

    return cmd


def run_step(step: dict, args: argparse.Namespace) -> bool:
    """
    Runs one step as a subprocess, streaming its output live rather than
    capturing and replaying it — this matters for the longer-running
    steps (clv_model.py with --tune, segmentation.py's silhouette search)
    where seeing progress in real time is more useful than a wall of
    text after the fact.
    Returns True on success (exit code 0), False otherwise. Does NOT
    raise — the caller (main()) decides whether one step's failure
    should stop the whole --all run.
    """
    cmd = build_step_command(step, args)
    logger.info("=" * 70)
    logger.info("STEP: %s  ->  %s", step["name"], " ".join(cmd))
    logger.info("=" * 70)

    start = time.time()
    result = subprocess.run(cmd)
    elapsed = time.time() - start

    if result.returncode == 0:
        logger.info("Step '%s' completed successfully in %.1fs.", step["name"], elapsed)
        return True
    else:
        logger.error("Step '%s' FAILED (exit code %d) after %.1fs.", step["name"], result.returncode, elapsed)
        return False


def run_actions_step(args: argparse.Namespace):
    raise NotImplementedError(
        "The action-queue rule engine (mart.crm_action_queue) is not implemented in run.py. "
        "Per the Phase 5 plan, this needs its own rules module (e.g. action_rules.py) reading "
        "churn_probability / clv_predicted_6m / rfm_segment / is_churned thresholds and writing "
        "structured trigger_reason text — that's a distinct piece of business logic deserving "
        "its own file and review, not a few lines bolted onto this orchestrator. "
        "See the ACTION QUEUE NOTE in this file's module docstring."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Phase 5 ML pipeline orchestrator. Runs each step as an isolated subprocess.",
        epilog="Steps run in dependency order when --all is given: "
               "sentiment -> segment -> clv -> churn -> next_purchase.",
    )

    step_group = parser.add_mutually_exclusive_group(required=True)
    step_group.add_argument("--all", action="store_true", help="Run all steps in dependency order.")
    step_group.add_argument("--sentiment", action="store_true", help="Run sentiment.py only.")
    step_group.add_argument("--segment", action="store_true", help="Run segmentation.py only.")
    step_group.add_argument("--clv", action="store_true", help="Run clv_model.py only.")
    step_group.add_argument("--churn", action="store_true", help="Run churn_model.py only.")
    step_group.add_argument("--next-purchase", dest="next_purchase", action="store_true", help="Run next_purchase.py only.")
    step_group.add_argument("--actions", action="store_true", help="Run the action-queue rule engine (NOT YET IMPLEMENTED — see module docstring).")

    parser.add_argument("--dry-run", action="store_true",
                         help="Pass --dry-run through to every step that supports it (no DB writes anywhere).")
    parser.add_argument("--force", action="store_true",
                         help="Pass --force through to steps that support it (currently: sentiment.py only — see STEPS).")
    parser.add_argument("--batch-size", type=int, default=None,
                         help="Pass --batch-size through to every step that supports it.")
    parser.add_argument("--threshold", type=float, default=None,
                         help="Pass --threshold through to churn_model.py only (ignored by other steps).")
    parser.add_argument("--stop-on-failure", action="store_true", default=True,
                         help="(default: on) Stop the --all run if any step fails. Use --no-stop-on-failure to continue regardless.")
    parser.add_argument("--no-stop-on-failure", dest="stop_on_failure", action="store_false")

    args = parser.parse_args()

    if args.actions:
        run_actions_step(args)
        return

    if args.force:
        non_force_steps = [s["name"] for s in STEPS if "force" not in s["supports"]]
        logger.info(
            "--force given. Note: only sentiment.py currently supports a --force flag "
            "(re-score already-scored reviews). It will be silently ignored for: %s.",
            ", ".join(non_force_steps),
        )

    if args.all:
        selected_steps = [s for s in STEPS if s["name"] in STEP_ORDER]
        logger.info("Running ALL steps in dependency order: %s", " -> ".join(s["name"] for s in selected_steps))
    else:
        flag_to_step = {
            "sentiment": "sentiment", "segment": "segment", "clv": "clv",
            "churn": "churn", "next_purchase": "next_purchase",
        }
        selected_name = next(name for name, step_name in flag_to_step.items() if getattr(args, name))
        selected_steps = [s for s in STEPS if s["name"] == selected_name]

    results = {}
    pipeline_start = time.time()

    for step in selected_steps:
        success = run_step(step, args)
        results[step["name"]] = success
        if not success and args.stop_on_failure and len(selected_steps) > 1:
            logger.error(
                "Stopping pipeline after '%s' failed (use --no-stop-on-failure to continue anyway, "
                "though downstream steps may fail too if they depend on this step's output).",
                step["name"],
            )
            break

    total_elapsed = time.time() - pipeline_start
    logger.info("=" * 70)
    logger.info("PIPELINE SUMMARY (%.1fs total):", total_elapsed)
    for name, success in results.items():
        logger.info("  %-15s %s", name, "OK" if success else "FAILED")
    logger.info("=" * 70)

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()