#!/usr/bin/env python3
"""Run one dsh handoff and bind its evidence to a Git workspace. No dependencies."""
import argparse
from contextlib import contextmanager
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(workspace, *args):
    return subprocess.check_output(["git", "-C", str(workspace), *args])


def workspace_root(value):
    workspace = Path(value).resolve(strict=True)
    root = Path(os.fsdecode(git(workspace, "rev-parse", "--show-toplevel")).strip()).resolve()
    if workspace != root:
        raise ValueError("--workspace must name the Git worktree root")
    return workspace


def snapshot(workspace):
    names = sorted(set(git(workspace, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")) - {b""})
    files = {}
    for name in names:
        path = workspace / os.fsdecode(name)
        try:
            info = path.lstat()
        except FileNotFoundError:
            files[os.fsdecode(name)] = {"kind": "missing"}
            continue
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            item = {"kind": "symlink", "sha256": digest(os.fsencode(os.readlink(path))), "mode": mode}
        elif stat.S_ISREG(info.st_mode):
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            item = {"kind": "file", "sha256": h.hexdigest(), "mode": mode}
        else:
            raise ValueError(f"Unsupported workspace entry (including submodule): {path}")
        files[os.fsdecode(name)] = item
    state = {
        "workspace": str(workspace),
        "head": git(workspace, "rev-parse", "HEAD").decode().strip(),
        "index_sha256": digest(git(workspace, "ls-files", "--stage", "-z")),
        "status_sha256": digest(git(workspace, "status", "--porcelain=v1", "-z", "--untracked-files=all")),
        "files": files,
    }
    state["fingerprint"] = digest(json.dumps(state, sort_keys=True).encode())
    return state


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


# Explicit, reviewed source selections only; never clone all repository data.
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_BYTES = 20 * 1024 * 1024


def capture_selectors(names):
    result = []
    for name in names:
        p = Path(name)
        if p.is_absolute() or not p.parts or ".." in p.parts or ".git" in p.parts:
            raise ValueError("--capture must be a specific repository-relative file/directory, not root or .git")
        result.append(p.as_posix())
    return sorted(set(result))


def selected(name, selectors):
    return any(name == prefix or name.startswith(prefix + "/") for prefix in selectors)


def bounded_read(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Evidence file exceeds {MAX_FILE_BYTES} bytes: {path}")
    return data


def capture_content(workspace, state, selectors, output):
    blobs = output / "blobs"
    blobs.mkdir(mode=0o700, exist_ok=True)
    captured = {}
    total = 0
    for name, item in state["files"].items():
        if not selected(name, selectors) or item["kind"] == "missing":
            continue
        path = workspace / name
        if path.parent.resolve() != path.parent:
            raise ValueError(f"Refusing capture through a symlink parent: {name}")
        data = os.fsencode(os.readlink(path)) if item["kind"] == "symlink" else bounded_read(path)
        if len(data) > MAX_FILE_BYTES or digest(data) != item["sha256"]:
            raise ValueError(f"Capture changed during snapshot or exceeds limit: {name}")
        total += len(data)
        if total > MAX_CAPTURE_BYTES:
            raise ValueError("Capture exceeds 20 MiB; narrow the reviewed --capture scope")
        blob = blobs / item["sha256"]
        if not blob.exists():
            blob.write_bytes(data)
        captured[name] = item["sha256"]
    return captured


def incremental_diff(before, after, captured_before, captured_after, selectors, output):
    changes, lines = [], ["# This attempt only; review diff, not an apply-ready patch.\n"]
    for name in sorted(before["files"].keys() | after["files"].keys()):
        old, new = before["files"].get(name), after["files"].get(name)
        if old == new:
            continue
        covered = selected(name, selectors)
        changes.append({"path": name, "before": old, "after": new, "content_captured": covered})
        lines.append(f"\n## {json.dumps(name, ensure_ascii=False)}\n")
        if not covered:
            lines.append("Content NOT captured; hashes only. Further review required.\n")
            continue
        lines.append(f"metadata: {json.dumps(old, sort_keys=True)} -> {json.dumps(new, sort_keys=True)}\n")
        a = (output / "blobs" / captured_before[name]).read_bytes() if name in captured_before else b""
        b = (output / "blobs" / captured_after[name]).read_bytes() if name in captured_after else b""
        try:
            if b"\0" in a or b"\0" in b:
                raise UnicodeError()
            a_text, b_text = a.decode("utf-8"), b.decode("utf-8")
        except UnicodeError:
            lines.append("Binary content: exact before/after bytes stored in blobs by SHA256.\n")
            continue
        label = json.dumps(name, ensure_ascii=False)
        for line in difflib.unified_diff(a_text.splitlines(True), b_text.splitlines(True),
                                         fromfile="before/" + label, tofile="after/" + label):
            lines.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    write_json(output / "delta.json", changes)
    (output / "changes.patch").write_text("".join(lines))


def freeze_inputs(names, output):
    frozen = []
    directory = output / "inputs"
    directory.mkdir(mode=0o700)
    total = 0
    for index, name in enumerate(names):
        argument = Path(name).absolute()
        if argument.is_symlink() or not argument.is_file():
            raise ValueError(f"--input must be a regular file, not a symlink: {argument}")
        source = argument.resolve(strict=True)
        data = bounded_read(source)
        total += len(data)
        if total > MAX_CAPTURE_BYTES:
            raise ValueError("Frozen inputs exceed 20 MiB")
        relative = f"inputs/{index:03d}-{source.name}"
        (output / relative).write_bytes(data)
        frozen.append({"source": str(source), "source_argument": str(argument), "copy": relative, "sha256": digest(data)})
    return frozen


def check_inputs(inputs, output):
    for item in inputs:
        source = Path(item["source"])
        argument = Path(item.get("source_argument", item["source"]))
        if argument.is_symlink() or argument.resolve() != source or source.is_symlink():
            raise ValueError(f"Acceptance input path changed: {source}")
        for path in (source, output / item["copy"]):
            if digest(bounded_read(path)) != item["sha256"]:
                raise ValueError(f"Acceptance input changed: {path}")


def stop_group(process):
    # The harness can own shell grandchildren; end the owned process group too.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            time.sleep(1)
            # Reap an exited leader before signalling again: macOS returns
            # EPERM for a process group containing only the unreaped zombie.
            process.poll()
    process.wait()


@contextmanager
def worktree_lock(workspace):
    lock_dir = Path(tempfile.gettempdir()) / f"codex-dsh-locks-{os.getuid()}"
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    with (lock_dir / digest(os.fsencode(workspace))).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another dsh runner or verification command is writing this worktree") from None
        yield


def run(args):
    workspace = workspace_root(args.workspace)
    task_path = Path(args.task).resolve(strict=True)
    task = task_path.read_bytes()
    if not task.strip():
        raise ValueError("Task file is empty")
    task.decode("utf-8")
    feedback = Path(args.feedback).resolve(strict=True).read_bytes() if args.feedback else None
    if feedback is not None:
        feedback.decode("utf-8")
    executable = shutil.which("dsh")
    if not executable:
        raise ValueError("dsh is not on PATH; read references/local-runtime.md")
    output = Path(args.output).resolve()
    selectors = capture_selectors(args.capture)
    if output == workspace or workspace in output.parents:
        raise ValueError("Evidence output must be outside the worktree")
    with worktree_lock(workspace):
        before = snapshot(workspace)
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        (output / "task.md").write_bytes(task)
        if feedback is not None:
            (output / "feedback.md").write_bytes(feedback)
        write_json(output / "before.json", before)
        captured_before = capture_content(workspace, before, selectors, output)
        inputs = freeze_inputs(args.input, output)
        result = {
            "status": "RUNNING", "workspace": str(workspace),
            "task_source": str(task_path), "task_sha256": digest(task),
            "feedback_sha256": digest(feedback) if feedback is not None else None,
            "executable": executable, "started_at": time.time(), "timeout_seconds": args.timeout,
            "capture_selectors": selectors, "captured_before": captured_before, "inputs": inputs,
        }
        write_json(output / "result.json", result)
        prompt = """You are the DeepSeek Harness IMPLEMENTER in a Codex -> dsh -> Codex workflow.
The task below is the approved scope. Follow applicable AGENTS.md instructions.
Use the task's verified tool entrypoints; Codex tools and authorizations are not inherited.
For further code investigation, use codegraph first: reuse an existing index, sync when stale,
and try codegraph init if unavailable before falling back with the actual error. Do not rebuild routinely.
Choose focused explore/node/callers/impact queries as needed, not a mandatory sequence.
Bound query output; reuse current source already returned and read only missing files or line ranges.
Use rg for exact strings, configuration, and unindexed content. Use affected only to aid test selection,
never to replace required checks. Use other available tools when relevant, without installing tools
or widening permissions on your own. Follow the task's browser reuse and cleanup constraints if applicable.
Investigate before editing, implement the smallest correct change, and run the specified checks.
Do not weaken acceptance criteria or tests to make them pass. Do not modify the task contract.
Do not commit, push, deploy, alter credentials, modify another workspace, or spawn other agents.
If a necessary action exceeds the authorized scope, report the concrete blocker and evidence.
Do not bypass approval or sandbox settings. Record a denied operation instead of retrying with broader permissions.
Your final response is an execution report, NOT a final acceptance decision. Include changed
files, rationale, exact verification commands and exit outcomes, deviations, and remaining work.
Briefly report key tool queries and findings, plus any failed-tool fallback; do not dump full source/logs.
Never report a check as passed if it did not run. Codex will inspect and rerun checks independently.
Use the supplied baseline facts and precise acceptance cases; do not repeat completed investigation
without contradictory evidence. Run focused tests after edits and one final relevant suite; repeat
only after a relevant code/input/environment change or to investigate a failure.
Acceptance input files and their recorded copies are frozen. Do not edit them to satisfy tests.

--- TASK CONTRACT ---
""" + task.decode("utf-8")
        if feedback is not None:
            prompt += "\n--- CODEX REWORK FINDINGS ---\n" + feedback.decode("utf-8")
        if inputs:
            prompt += "\n--- FROZEN ACCEPTANCE INPUTS (read-only, including source paths) ---\n" + json.dumps(inputs, ensure_ascii=False)
        (output / "prompt.txt").write_text(prompt)
        process = None
        try:
            with (output / "execution.md").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
                process = subprocess.Popen(
                    [executable, "--profile", "headless", prompt], cwd=workspace,
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True,
                )
                try:
                    code = process.wait(timeout=args.timeout)
                    result.update(status="EXECUTED" if code == 0 else "EXECUTION_FAILED", exit_code=code)
                except subprocess.TimeoutExpired:
                    stop_group(process)
                    result.update(status="TIMED_OUT", exit_code=124)
                except KeyboardInterrupt:
                    stop_group(process)
                    result.update(status="INTERRUPTED", exit_code=130)
        except OSError as error:
            result.update(status="LAUNCH_FAILED" if process is None else "EXECUTION_FAILED",
                          exit_code=1, error=str(error))
        finally:
            if process is not None and process.poll() is None:
                stop_group(process)
            result["finished_at"] = time.time()
            try:
                after = snapshot(workspace)
                write_json(output / "after.json", after)
                result["candidate_fingerprint"] = after["fingerprint"]
                captured_after = capture_content(workspace, after, selectors, output)
                result["captured_after"] = captured_after
                incremental_diff(before, after, captured_before, captured_after, selectors, output)
                (output / "git-status.txt").write_bytes(git(workspace, "status", "--short", "--untracked-files=all"))
                check_inputs(inputs, output)
                if digest(task_path.read_bytes()) != result["task_sha256"]:
                    raise ValueError("Source task changed during execution")
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                result.update(status="EVIDENCE_FAILED", exit_code=1, error=str(error))
            write_json(output / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result["exit_code"]


def check(args):
    output = Path(args.output).resolve(strict=True)
    result = json.loads((output / "result.json").read_text())
    task = (output / "task.md").read_bytes()
    if digest(task) != result["task_sha256"]:
        raise ValueError("Recorded task contract has changed")
    if digest(Path(result["task_source"]).read_bytes()) != result["task_sha256"]:
        raise ValueError("Source task contract has changed; review/replan against the new contract")
    if result.get("feedback_sha256") is not None:
        if digest((output / "feedback.md").read_bytes()) != result["feedback_sha256"]:
            raise ValueError("Recorded rework findings have changed")
    check_inputs(result.get("inputs", []), output)
    for mapping in (result.get("captured_before", {}), result.get("captured_after", {})):
        for sha in set(mapping.values()):
            if digest(bounded_read(output / "blobs" / sha)) != sha:
                raise ValueError("Captured content evidence changed")
    current = snapshot(workspace_root(result["workspace"]))
    if current["fingerprint"] != result.get("candidate_fingerprint"):
        raise ValueError("Workspace differs from this candidate; old review evidence is stale")
    print(json.dumps({"status": "CANDIDATE_UNCHANGED", "fingerprint": current["fingerprint"],
                      "execution_status": result["status"], "acceptance": "Codex review still required"}, indent=2))
    return 0


def record(args):
    """Record a real preflight/verification command; never infer business PASS."""
    workspace = workspace_root(args.workspace)
    with worktree_lock(workspace):
        return record_command(args, workspace)


def record_command(args, workspace):
    output = Path(args.output).resolve()
    if output == workspace or workspace in output.parents:
        raise ValueError("Command evidence must be outside the worktree")
    command = args.command_argv
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValueError("record requires a command after --")
    if args.candidate:
        candidate = json.loads((Path(args.candidate) / "result.json").read_text())
        if Path(candidate["workspace"]) != workspace:
            raise ValueError("Candidate belongs to another workspace")
        check(argparse.Namespace(output=args.candidate))
    before = snapshot(workspace)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = {"status": "RUNNING", "argv": command, "workspace": str(workspace),
              "executable": shutil.which(command[0]), "started_at": time.time(),
              "before_fingerprint": before["fingerprint"], "candidate": args.candidate}
    write_json(output / "result.json", result)
    process = None
    try:
        with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                code = process.wait(timeout=args.timeout)
                result.update(status="COMPLETED" if code == 0 else "COMMAND_FAILED", exit_code=code)
            except subprocess.TimeoutExpired:
                stop_group(process)
                result.update(status="TIMED_OUT", exit_code=124)
            except KeyboardInterrupt:
                stop_group(process)
                result.update(status="INTERRUPTED", exit_code=130)
    except OSError as error:
        result.update(status="LAUNCH_FAILED", exit_code=1, error=str(error))
    finally:
        if process is not None and process.poll() is None:
            stop_group(process)
        result["finished_at"] = time.time()
        try:
            result["after_fingerprint"] = snapshot(workspace)["fingerprint"]
            result["workspace_changed"] = result["before_fingerprint"] != result["after_fingerprint"]
            if args.candidate:
                check(argparse.Namespace(output=args.candidate))
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            result.update(status="EVIDENCE_FAILED", command_exit_code=result.get("exit_code"),
                          exit_code=1, error=str(error))
        write_json(output / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result["exit_code"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    execute = sub.add_parser("run", help="Run dsh and record a candidate; never marks PASS")
    execute.add_argument("--workspace", required=True)
    execute.add_argument("--task", required=True)
    execute.add_argument("--feedback")
    execute.add_argument("--output", required=True, help="New directory outside the worktree")
    execute.add_argument("--timeout", type=int, default=900)
    execute.add_argument("--capture", action="append", default=[], help="Reviewed relative file/directory to capture; repeatable, no root")
    execute.add_argument("--input", action="append", default=[], help="Frozen acceptance file; repeat for fixture/overlay dependencies")
    execute.set_defaults(handler=run)
    verify = sub.add_parser("check", help="Reject code or task drift; not a test or acceptance check")
    verify.add_argument("--output", required=True)
    verify.set_defaults(handler=check)
    evidence = sub.add_parser("record", help="Run and record one real preflight/verification command without a shell")
    evidence.add_argument("--workspace", required=True)
    evidence.add_argument("--output", required=True, help="New external evidence directory")
    evidence.add_argument("--candidate", help="Attempt to check before and after this command")
    evidence.add_argument("--timeout", type=int, default=300)
    evidence.add_argument("command_argv", nargs=argparse.REMAINDER)
    evidence.set_defaults(handler=record)
    args = parser.parse_args()
    if getattr(args, "timeout", 1) <= 0:
        parser.error("--timeout must be positive")
    if args.command in ("run", "record"):
        signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        return args.handler(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"dsh-workflow: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
