"""The ``sovereign-agents`` command: keys, runs, the owner's yes or no, receipts, and a demo."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from claude_agent_sdk import ClaudeSDKError

from sovereign_agents.kit import (
    PRIVATE_KEY_FILE,
    TERMINAL,
    ChangeExpired,
    Clock,
    Engine,
    KeyFileError,
    Ledger,
    LedgerError,
    Runner,
    SystemClock,
    apply_approved,
    check_slug,
    decide,
    format_micros,
    iso,
    load_public_pem,
    load_signer,
    new_keys,
    pending,
    verify_all,
)

from .config import KitConfig
from .connectors.business import Business, load_sample_business
from .engines.agent_sdk import AgentSdkEngine
from .engines.fake_model import FakeModelEngine, load_fake_calls
from .jobs import JOBS

AGENT_SDK = "agent-sdk"
FAKE_MODEL = "fake-model"


def sdk_engine(config: KitConfig) -> Engine:
    return AgentSdkEngine(
        config_root=config.home.sdk_config_root,
        api_key_set=config.api_key_set,
        model=config.model,
    )


@dataclass(frozen=True)
class Context:
    config: KitConfig
    out: TextIO
    ask: Callable[[str], str]
    clock: Clock
    engine: Callable[[KitConfig], Engine]


class CliError(Exception):
    """A problem the person running the command can fix."""


def _business(args: argparse.Namespace, ctx: Context) -> Business:
    slug = check_slug(args.business)
    return load_sample_business(Path(args.samples), slug, ctx.config.home.outbox_dir(slug))


def _ledger(ctx: Context) -> Ledger:
    ctx.config.home.root.mkdir(parents=True, exist_ok=True)
    return Ledger(ctx.config.home.ledger_path)


def _keys_new(args: argparse.Namespace, ctx: Context) -> int:
    business = check_slug(args.business)
    keys_dir = args.dir if args.dir is not None else ctx.config.home.keys_dir(business)
    public_path = new_keys(keys_dir)
    private_path = public_path.parent / PRIVATE_KEY_FILE
    print(f"Made a new Ed25519 key pair for {business}.", file=ctx.out)
    print(f"Private key: {private_path} (mode 600, stays on this host)", file=ctx.out)
    print(f"Public key:  {public_path}", file=ctx.out)
    print("Publish the public key at /.well-known/sovereign-receipts/ed25519.pem:", file=ctx.out)
    print(public_path.read_text("ascii"), end="", file=ctx.out)
    return 0


def _run(args: argparse.Namespace, ctx: Context) -> int:
    spec = JOBS.get(args.job)
    if spec is None:
        raise CliError(f"no job {args.job!r}; the jobs are: {', '.join(sorted(JOBS))}")
    business = _business(args, ctx)
    signer = load_signer(business.slug, ctx.config.home.keys_dir(business.slug))
    if args.engine == FAKE_MODEL:
        # Every business v1 loads is a sample (load_sample_business refuses any other), and
        # the fake model's calls live in the sample's own folder.
        engine: Engine = FakeModelEngine(
            config_root=ctx.config.home.sdk_config_root,
            calls=load_fake_calls(Path(args.samples), business.slug, spec.name),
        )
    else:
        engine = ctx.engine(ctx.config)
    ledger = _ledger(ctx)
    try:
        runner = Runner(
            ledger=ledger,
            signer=signer,
            clock=ctx.clock,
            budget=ctx.config.budget,
            engine=engine,
        )
        run_ctx = runner.begin(spec.name, sample=business.sample)
        result = asyncio.run(runner.run(run_ctx, spec.build(run_ctx, business)))
    finally:
        ledger.close()
    label = " (sample)" if business.sample else ""
    lines = [
        f"Run {result.run_id} for {business.slug}{label}, {spec.name}: {result.outcome}",
        f"Cost: {format_micros(result.cost_micros)} in {result.duration_ms} ms",
        f"Staged: {', '.join(result.staged) or 'nothing'}",
        f"Flagged for the owner: {', '.join(result.flags) or 'nothing'}",
        f"Refused calls: {len(result.refusals)}, listed in the run receipt",
        f"Receipt: {result.receipt['id']}",
        f"Next: sovereign-agents approve --business {business.slug}",
    ]
    print("\n".join(lines), file=ctx.out)
    return 0


def _approve(args: argparse.Namespace, ctx: Context) -> int:
    business = _business(args, ctx)
    signer = load_signer(business.slug, ctx.config.home.keys_dir(business.slug))
    say = ctx.out.write
    ledger = _ledger(ctx)
    try:
        flags = ledger.open_flags(business.slug)
        if flags:
            say("Reported by the agent; nothing was done about these:\n")
            for flag in flags:
                say(f"  {flag.source_id}: {flag.note}\n")
            ledger.mark_flags_seen([flag.flag_id for flag in flags], ctx.clock.now())
        changes = pending(ledger, business.slug, ctx.clock)
        if not changes:
            say("Nothing waits for your yes.\n")
        for change in changes:
            label = " (sample)" if business.sample else ""
            say(f"\n{change.change_id}{label}, until {iso(change.expires_at)}\n")
            say(f"{change.summary}\n")
            answer = ctx.ask("Yes, do this? [y/N] ").strip().lower()
            try:
                decide(
                    ledger,
                    change.change_id,
                    yes=answer in ("y", "yes"),
                    decided_by=args.owner,
                    channel=TERMINAL,
                    clock=ctx.clock,
                )
            except ChangeExpired:
                say("It expired before your answer; nothing will be done.\n")
                continue
            say("Yes.\n" if answer in ("y", "yes") else "No; nothing will be done.\n")
        report = apply_approved(
            ledger=ledger,
            signer=signer,
            clock=ctx.clock,
            appliers={name: spec.applier(business) for name, spec in JOBS.items()},
            sample=business.sample,
        )
    finally:
        ledger.close()
    for change_id, receipt_id in report.applied:
        say(f"Applied {change_id}; receipt {receipt_id}\n")
    for change_id, refusal in report.refused:
        say(f"Not applied {change_id}: {refusal}\n")
    for change_id in report.unfinished:
        say(
            f"Not finished {change_id}: an apply pass claimed it and did not mark it applied;"
            " the kit does not apply it again on its own.\n"
        )
    say(f"Apply pass {report.run_id}; receipt {report.receipt['id']}\n")
    return 0


def _receipts_verify(args: argparse.Namespace, ctx: Context) -> int:
    business = check_slug(args.business)
    public = load_public_pem(ctx.config.home.keys_dir(business))
    ledger = _ledger(ctx)
    try:
        results = verify_all(ledger, business, public)
    finally:
        ledger.close()
    failed = 0
    for receipt_id, result in results:
        status = "ok" if result.ok else f"FAILED {result.reason}"
        print(f"{receipt_id}: {status}", file=ctx.out)
        failed += 0 if result.ok else 1
    print(f"{len(results)} receipt(s), {failed} failed.", file=ctx.out)
    return 1 if failed else 0


def _demo(args: argparse.Namespace, ctx: Context) -> int:
    """The whole loop on a sample business with the fake model: keys (made once), a run, the
    owner's yes or no in this terminal, the apply pass, and a check of every receipt."""
    business = check_slug(args.business)
    say = ctx.out.write
    if not (ctx.config.home.keys_dir(business) / PRIVATE_KEY_FILE).exists():
        say("1. Keys\n")
        _keys_new(argparse.Namespace(business=business, dir=None), ctx)
    else:
        say(f"1. Keys: {business} already has a key pair; using it.\n")
    say("\n2. A run with the fake model (no model is asked, nothing is spent)\n")
    _run(
        argparse.Namespace(
            business=business, job=args.job, samples=args.samples, engine=FAKE_MODEL
        ),
        ctx,
    )
    say("\n3. Your yes or no\n")
    _approve(argparse.Namespace(business=business, samples=args.samples, owner=args.owner), ctx)
    say("\n4. Every receipt, checked with the verifiable-receipts verifier\n")
    return _receipts_verify(argparse.Namespace(business=business), ctx)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sovereign-agents",
        description="One agent, one job: it proposes, the owner says yes, the kit acts and signs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    keys = commands.add_parser("keys", help="the business's signing keys")
    keys_commands = keys.add_subparsers(dest="keys_command", required=True)
    keys_new = keys_commands.add_parser("new", help="make a new Ed25519 key pair for a business")
    keys_new.add_argument("--business", required=True, help="the business slug")
    keys_new.add_argument(
        "--dir", type=Path, default=None, help="where to write the keys (default: under the home)"
    )
    keys_new.set_defaults(handler=_keys_new)

    run = commands.add_parser("run", help="run one job once for a business")
    run.add_argument("--business", required=True, help="the business slug")
    run.add_argument("--job", required=True, help="the job's name")
    run.add_argument("--samples", default="samples", help="the folder of sample businesses")
    run.add_argument(
        "--engine",
        choices=(AGENT_SDK, FAKE_MODEL),
        default=AGENT_SDK,
        help="agent-sdk runs Claude on ANTHROPIC_API_KEY; fake-model plays a sample's fixed calls",
    )
    run.set_defaults(handler=_run)

    approve = commands.add_parser("approve", help="say yes or no to staged changes, then apply")
    approve.add_argument("--business", required=True, help="the business slug")
    approve.add_argument("--samples", default="samples", help="the folder of sample businesses")
    approve.add_argument("--owner", default="owner", help="who is answering, for the record")
    approve.set_defaults(handler=_approve)

    demo = commands.add_parser(
        "demo", help="the whole loop on a sample business with the fake model"
    )
    demo.add_argument("--business", default="sample-bakery", help="the sample business slug")
    demo.add_argument("--job", default="supplier_reorders", help="the job's name")
    demo.add_argument("--samples", default="samples", help="the folder of sample businesses")
    demo.add_argument("--owner", default="owner", help="who is answering, for the record")
    demo.set_defaults(handler=_demo)

    receipts = commands.add_parser("receipts", help="the signed receipts in the ledger")
    receipts_commands = receipts.add_subparsers(dest="receipts_command", required=True)
    receipts_verify = receipts_commands.add_parser("verify", help="check every receipt")
    receipts_verify.add_argument("--business", required=True, help="the business slug")
    receipts_verify.set_defaults(handler=_receipts_verify)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    ask: Callable[[str], str] = input,
    clock: Clock | None = None,
    engine: Callable[[KitConfig], Engine] = sdk_engine,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = Context(
            config=KitConfig.from_env(os.environ if env is None else env),
            out=out or sys.stdout,
            ask=ask,
            clock=clock or SystemClock(),
            engine=engine,
        )
        status: int = args.handler(args, ctx)
    except (CliError, ClaudeSDKError, KeyFileError, LedgerError, ValueError) as exc:
        print(f"error: {exc}", file=err or sys.stderr)
        return 2
    return status
