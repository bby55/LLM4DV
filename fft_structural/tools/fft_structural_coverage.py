"""Run reproducible GHDL structural coverage for the fpga-fft VHDL RTL.

The runner deliberately reports only metrics backed by GHDL coverage data.
GHDL provides line and branch coverage; expression, toggle and FSM metrics are
reported as unavailable unless a future simulator adapter supplies them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "upstream"
DEFAULT_OUTPUT_ROOT = Path(os.environ.get("LLM4DV_OUTPUT_ROOT", str(ROOT.parent.parent / "outputs")))

# The default campaign targets the upstream 1024-point wide generated core.
# Smaller butterfly tests remain available through repeated --testbench flags.
DEFAULT_TESTBENCHES = ("test_fft1024",)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: Sequence[str], cwd: Path, commands: List[dict]) -> subprocess.CompletedProcess[str]:
    started = datetime.now().isoformat(timespec="seconds")
    process = subprocess.run(
        [str(item) for item in command],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    commands.append(
        {
            "started": started,
            "cwd": str(cwd),
            "command": [str(item) for item in command],
            "returncode": process.returncode,
            "output": process.stdout,
        }
    )
    return process


def discover_rtl(include_generated: bool, include_axi: bool) -> List[Path]:
    # Keep the reusable root RTL, excluding optional 1M/large generator units
    # whose generated dependencies are outside the 1024-point profile.
    excluded_root = {"twiddle_generator_1m.vhd", "twiddle_generator_large.vhd"}
    files = sorted(path for path in UPSTREAM.glob("*.vhd") if path.name not in excluded_root)
    if include_generated:
        generated = UPSTREAM / "generated" / "fft1024_wide"
        # The repository contains several alternative generated wrappers which
        # redeclare entities.  Compile the core profile used by test_fft1024.
        files.extend(
            generated / name
            for name in (
                "fft1024_wide.vhd",
                "fft1024_wide_sub16.vhd",
                "fft1024_wide_sub16_2.vhd",
                "fft1024_wide_sub64.vhd",
            )
        )
        files.extend(sorted((UPSTREAM / "generated" / "twiddle").glob("*.vhd")))
        # The generated wide core instantiates the upstream behavioral DSP
        # model.  It is synthesizable-independent RTL and part of this DUT
        # profile, so include it in the manifest and coverage denominator.
        files.append(UPSTREAM / "xilinx" / "dsp48e1_multadd.vhd")
    if include_axi:
        # Keep axi-util's reusable RTL only; its nested tests/synthtest units
        # are testbenches and must not enter the DUT coverage denominator.
        files.extend(sorted((UPSTREAM / "axi-util").glob("*.vhd")))
    return files


def source_manifest(files: Iterable[Path]) -> dict:
    entries = []
    physical_lines = 0
    for path in files:
        lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        physical_lines += lines
        try:
            display_path = path.relative_to(ROOT).as_posix()
        except ValueError:
            display_path = path.as_posix()
        entries.append(
            {
                "path": display_path,
                "physical_lines": lines,
                "sha256": sha256(path),
            }
        )
    return {"files": len(entries), "physical_lines": physical_lines, "entries": entries}


def parse_lcov(text: str, source_root: Path) -> Dict[str, Dict[Tuple, int]]:
    """Parse the line/branch subset of LCOV emitted by ``ghdl coverage``."""

    lines: Dict[Tuple, int] = {}
    branches: Dict[Tuple, int] = {}
    current: Optional[Path] = None
    for raw in text.splitlines():
        if raw.startswith("SF:"):
            candidate = Path(raw[3:]).resolve()
            try:
                candidate.relative_to(source_root.resolve())
            except ValueError:
                current = None
            else:
                current = candidate
        elif current is not None and raw.startswith("DA:"):
            line_number, count = raw[3:].split(",", 1)
            lines[(str(current), int(line_number))] = int(count)
        elif current is not None and raw.startswith("BRDA:"):
            line_number, block, branch, count = raw[5:].split(",", 3)
            branches[(str(current), int(line_number), block, branch)] = 0 if count == "-" else int(count)
    return {"line": lines, "branch": branches}


def merge_points(point_sets: Iterable[Dict[str, Dict[Tuple, int]]]) -> Dict[str, Dict[Tuple, int]]:
    merged: Dict[str, Dict[Tuple, int]] = {"line": {}, "branch": {}}
    for points in point_sets:
        for kind in merged:
            for key, count in points[kind].items():
                merged[kind][key] = max(merged[kind].get(key, 0), count)
    return merged


def metrics(points: Dict[str, Dict[Tuple, int]]) -> dict:
    result = {}
    for kind in ("line", "branch"):
        total = len(points[kind])
        covered = sum(1 for count in points[kind].values() if count > 0)
        result[kind] = {
            "covered": covered,
            "total": total,
            "pct": round(covered * 100.0 / total, 2) if total else None,
        }
        if not total:
            result[kind]["status"] = "unavailable"
    for kind in ("expression", "toggle", "fsm_state", "fsm_transition"):
        result[kind] = {"covered": None, "total": None, "pct": None, "status": "unavailable"}
    return result


def write_lcov(path: Path, points: Dict[str, Dict[Tuple, int]]) -> None:
    by_file: Dict[str, List[Tuple[int, int]]] = {}
    for (filename, line_number), count in points["line"].items():
        by_file.setdefault(filename, []).append((line_number, count))
    branch_by_file: Dict[str, List[Tuple[int, str, str, int]]] = {}
    for (filename, line_number, block, branch), count in points["branch"].items():
        branch_by_file.setdefault(filename, []).append((line_number, block, branch, count))
    output = []
    for filename in sorted(set(by_file) | set(branch_by_file)):
        output.append("SF:" + filename)
        output.extend("DA:%d,%d" % item for item in sorted(by_file.get(filename, [])))
        output.extend("BRDA:%d,%s,%s,%d" % item for item in sorted(branch_by_file.get(filename, [])))
        output.append("end_of_record")
    path.write_text("\n".join(output) + ("\n" if output else ""), encoding="utf-8")


def find_ghdl(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit if Path(explicit).exists() else None
    return shutil.which(os.environ.get("GHDL", "ghdl"))


def analyze_all(ghdl: str, workdir: Path, sources: List[Path], cwd: Path, commands: List[dict]) -> None:
    pending = list(sources)
    failures: Dict[Path, str] = {}
    # VHDL units have dependencies; retry failed units after each successful pass.
    while pending:
        progress = False
        next_pending = []
        for source in pending:
            # Coverage instrumentation is an analysis-time option for the
            # packaged GHDL mcode backend; the generated executable then runs
            # with ordinary simulation options.
            process = run([ghdl, "-a", "--std=08", "--coverage", "--workdir=" + str(workdir), str(source)], cwd, commands)
            if process.returncode == 0:
                progress = True
                failures.pop(source, None)
            else:
                failures[source] = process.stdout
                next_pending.append(source)
        if not progress:
            details = "\n".join("%s\n%s" % (path, failures[path]) for path in next_pending)
            raise RuntimeError("GHDL analysis made no progress:\n" + details)
        pending = next_pending


def run_testbench(
    ghdl: str,
    testbench: str,
    test_file: Path,
    sources: List[Path],
    run_dir: Path,
    stop_time: str,
    commands: List[dict],
) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    workdir = run_dir / "work"
    workdir.mkdir()
    analyze_all(ghdl, workdir, sources + [test_file], run_dir, commands)
    elaborate = run([ghdl, "-e", "--std=08", "--workdir=" + str(workdir), testbench], run_dir, commands)
    if elaborate.returncode:
        raise RuntimeError("GHDL elaboration failed for %s" % testbench)
    simulate = run(
        [ghdl, "-r", "--std=08", "--workdir=" + str(workdir), "--coverage", testbench, "--stop-time=" + stop_time],
        run_dir,
        commands,
    )
    (run_dir / "simulation.log").write_text(simulate.stdout, encoding="utf-8")
    if simulate.returncode:
        raise RuntimeError("GHDL simulation failed for %s" % testbench)
    coverage_files = sorted(run_dir.glob("coverage-*.json"))
    if not coverage_files:
        raise RuntimeError("GHDL simulation produced no coverage-*.json file for %s" % testbench)
    coverage = run([ghdl, "coverage", "--format=lcov", *coverage_files], run_dir, commands)
    (run_dir / "coverage.info").write_text(coverage.stdout, encoding="utf-8")
    if coverage.returncode:
        raise RuntimeError("GHDL coverage extraction failed for %s" % testbench)
    points = parse_lcov(coverage.stdout, UPSTREAM)
    if not points["line"] and not points["branch"]:
        raise RuntimeError("GHDL produced no RTL line/branch coverage for %s" % testbench)
    return {
        "testbench": testbench,
        "simulation_output_sha256": hashlib.sha256(simulate.stdout.encode()).hexdigest(),
        "coverage_sha256": hashlib.sha256(coverage.stdout.encode()).hexdigest(),
        "metrics": metrics(points),
        "points": points,
    }


def write_report(path: Path, summary: dict) -> None:
    final = summary["final_metrics"]
    lines = [
        "# FFT 结构覆盖率验证报告",
        "",
        "本报告只统计 GHDL 实际产生的 LCOV 数据。没有数据的覆盖类型明确标记为 unavailable，不用程序数量或静态语法推算。",
        "",
        "## 运行信息",
        "",
        "- 上游快照：`%s`" % summary["upstream_commit"],
        "- RTL 文件：%d 个，物理行：%d" % (summary["source_manifest"]["files"], summary["source_manifest"]["physical_lines"]),
        "- 迭代：%d；测试平台：%s" % (summary["iterations"], ", ".join(summary["testbenches"])),
        "- 覆盖率有效：`%s`" % summary["coverage_valid"],
        "",
        "## 指标",
        "",
        "| 指标 | 已覆盖 | 总数 | 百分比 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for kind in ("line", "branch", "expression", "toggle", "fsm_state", "fsm_transition"):
        item = final[kind]
        if item.get("status") == "unavailable":
            lines.append("| %s | unavailable | unavailable | unavailable |" % kind)
        else:
            lines.append("| %s | %s | %s | %s%% |" % (kind, item["covered"], item["total"], item["pct"]))
    lines += [
        "",
        "## 真实性门禁",
        "",
        "- 编译与仿真成功：`%s`" % summary["validity_gates"]["simulations_passed"],
        "- LCOV 含 RTL 数据：`%s`" % summary["validity_gates"]["native_coverage_nonempty"],
        "- 确定性重放：`%s`" % summary["validity_gates"]["deterministic_replay"],
        "- 源码清单已保存：`%s`" % summary["validity_gates"]["source_manifest_saved"],
        "",
        "FSM、表达式和翻转覆盖率没有被本运行器伪造；需要额外的 VHDL 覆盖率适配器时再启用。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True, help="外部输出目录")
    parser.add_argument("--iterations", type=int, default=1, help="重复运行测试平台的次数")
    parser.add_argument("--cycles", type=int, default=2000, help="每个测试平台的仿真时间（约按 ns 使用）")
    parser.add_argument("--attempts", type=int, default=1, help="保留 CLI 兼容性；确定性 VHDL 流不调用模型")
    parser.add_argument("--ghdl", help="GHDL 可执行文件路径；默认读取 GHDL 环境变量或 PATH")
    parser.add_argument("--testbench", action="append", dest="testbenches", help="测试平台实体名，可重复指定")
    parser.add_argument("--include-generated", action="store_true", default=True, help="把 1024 点 generated core 纳入 RTL 清单（默认开启）")
    parser.add_argument("--no-generated", dest="include_generated", action="store_false", help="不纳入 generated core")
    parser.add_argument("--include-axi", action="store_true", help="把 axi-util/ 下的 VHDL 纳入 RTL 清单")
    parser.add_argument("--stop-time", default=None, help="GHDL stop-time，默认使用 cycles ns")
    parser.add_argument("--manifest-only", action="store_true", help="只生成源码清单，不需要 GHDL")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.iterations < 1 or args.cycles < 1 or args.attempts < 1:
        raise SystemExit("iterations, cycles and attempts must be positive")
    testbenches = tuple(args.testbenches or DEFAULT_TESTBENCHES)
    source_files = discover_rtl(args.include_generated, args.include_axi)
    missing = [name for name in testbenches if not (UPSTREAM / "tests" / (name + ".vhd")).exists()]
    if missing:
        raise SystemExit("unknown testbench source: " + ", ".join(missing))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = source_manifest(source_files)
    (args.out_dir / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if args.manifest_only:
        print(json.dumps(manifest, indent=2))
        return 0
    ghdl = find_ghdl(args.ghdl)
    if not ghdl:
        raise SystemExit("GHDL not found; install GHDL or pass --ghdl PATH. No coverage is fabricated.")
    commands: List[dict] = []
    point_sets = []
    run_records = []
    replay_hashes = []
    stop_time = args.stop_time or (str(args.cycles) + "ns")
    for iteration in range(1, args.iterations + 1):
        for testbench in testbenches:
            test_file = UPSTREAM / "tests" / (testbench + ".vhd")
            run_dir = args.out_dir / ("iteration_%02d_%s" % (iteration, testbench))
            record = run_testbench(ghdl, testbench, test_file, source_files, run_dir, stop_time, commands)
            point_sets.append(record.pop("points"))
            run_records.append(record)
            if iteration == 1:
                replay_dir = args.out_dir / ("replay_%s" % testbench)
                replay = run_testbench(ghdl, testbench, test_file, source_files, replay_dir, stop_time, commands)
                replay_hashes.append(record["coverage_sha256"] == replay["coverage_sha256"])
    merged = merge_points(point_sets)
    write_lcov(args.out_dir / "coverage.info", merged)
    final_metrics = metrics(merged)
    validity = {
        "simulations_passed": bool(run_records),
        "native_coverage_nonempty": bool(merged["line"] or merged["branch"]),
        "deterministic_replay": all(replay_hashes) if replay_hashes else False,
        "source_manifest_saved": True,
    }
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "upstream_commit": "c64c89e",
        "ghdl": ghdl,
        "iterations": args.iterations,
        "cycles": args.cycles,
        "attempts": args.attempts,
        "testbenches": list(testbenches),
        "source_manifest": manifest,
        "runs": run_records,
        "final_metrics": final_metrics,
        "validity_gates": validity,
        "coverage_valid": all(validity.values()),
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (args.out_dir / "commands.json").write_text(json.dumps(commands, indent=2), encoding="utf-8")
    write_report(args.out_dir / "report_zh.md", summary)
    print(json.dumps({"coverage_valid": summary["coverage_valid"], "final_metrics": final_metrics}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as error:
        print("ERROR:", error, file=sys.stderr)
        raise SystemExit(2)
