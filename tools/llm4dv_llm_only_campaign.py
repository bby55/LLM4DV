"""Run independent LLM-only structural coverage campaigns for LLM4DV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import llm4dv_structural_coverage as core


Stimulus = Tuple[int, int]


def zero_counts(points: Dict[str, dict]) -> Dict[str, dict]:
    return {raw: {"attrs": dict(point["attrs"]), "count": 0} for raw, point in points.items()}


def is_closed(metric_set: Dict[str, dict]) -> bool:
    return all(metric_set[name]["pct"] == 100.0 for name in ("line", "branch", "expr", "toggle"))


def stimulus_hash(stimuli: Sequence[Stimulus]) -> str:
    payload = "".join("%d %d\n" % item for item in stimuli).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def read_stimulus(path: Path) -> List[Stimulus]:
    return [tuple(map(int, line.split())) for line in path.read_text(encoding="ascii").splitlines() if line.strip()]


def metric_text(metric: dict) -> str:
    return "N/A" if metric["pct"] is None else "%d/%d (%.2f%%)" % (
        metric["covered"], metric["total"], metric["pct"]
    )


def mutate_result(result_path: Path, output_path: Path, stimuli: Sequence[Stimulus]) -> bool:
    rows = list(csv.DictReader(result_path.open(encoding="utf-8")))
    if not rows:
        return False
    rows[0]["stride_1_valid"] = str(1 - int(rows[0]["stride_1_valid"]))
    with output_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return not core.check_results(stimuli, output_path)["pass"]


def run_cross_simulator_replays(out_dir: Path, campaigns: Sequence[dict], env: Dict[str, str], commands: List[dict]) -> dict:
    iverilog = shutil.which("iverilog")
    vvp = shutil.which("vvp")
    testbench = core.ROOT / "tools" / "stride_detector_iverilog_tb.sv"
    if not iverilog or not vvp:
        return {"pass": False, "reason": "iverilog or vvp not found", "cases": []}
    replay_dir = out_dir / "cross_simulator_replay"
    replay_dir.mkdir(exist_ok=True)
    executable = replay_dir / "stride_detector_iverilog.vvp"
    core.run([
        iverilog, "-g2012", "-s", "stride_detector_iverilog_tb", "-o", str(executable),
        str(core.DUT), str(testbench),
    ], core.ROOT, env, commands)
    cases = []
    for campaign in campaigns:
        row = campaign["iterations"][0]
        stimulus_path = Path(row["artifacts"]["stimulus"])
        verilator_result = Path(row["artifacts"]["result"])
        iverilog_result = replay_dir / ("campaign_%02d_result.csv" % campaign["campaign"])
        core.run([
            vvp, str(executable), "+STIMULUS=" + str(stimulus_path), "+RESULT=" + str(iverilog_result)
        ], core.ROOT, env, commands)
        stimuli = read_stimulus(stimulus_path)
        scoreboard = core.check_results(stimuli, iverilog_result)
        with verilator_result.open(encoding="utf-8") as stream:
            verilator_rows = list(csv.DictReader(stream))
        with iverilog_result.open(encoding="utf-8") as stream:
            iverilog_rows = list(csv.DictReader(stream))
        exact_match = verilator_rows == iverilog_rows
        cases.append({
            "campaign": campaign["campaign"], "cycles": len(stimuli),
            "scoreboard": scoreboard, "verilator_iverilog_exact_match": exact_match,
            "stimulus": str(stimulus_path), "verilator_result": str(verilator_result),
            "iverilog_result": str(iverilog_result),
        })
    result = {"pass": all(case["scoreboard"]["pass"] and case["verilator_iverilog_exact_match"] for case in cases),
              "cases": cases}
    (replay_dir / "cross_replay.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def write_report(path: Path, summary: dict) -> None:
    lines = [
        "# LLM4DV 全 LLM 结构覆盖独立重复实验", "",
        "- 模型：`%s`" % summary["model"],
        "- 实验：%d 个独立 campaign，每组 %d 个有效 LLM 生成轮次。" % (
            summary["campaign_count"], summary["iterations_per_campaign"]),
        "- 复位由 testbench 固定执行；复位后的全部 `valid/value` 测试刺激均来自模型。",
        "- 总体真实性判定：**%s**。" % ("通过" if summary["coverage_valid"] else "未通过"), "",
        "## 独立实验结果", "",
        "| Campaign | 首次全覆盖轮次 | Line | Branch | Expr | Toggle | 状态 | 转移 | 判分 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for campaign in summary["campaigns"]:
        metrics = campaign["final_metrics"]
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            campaign["campaign"], campaign["first_full_coverage_iteration"] or "未达到",
            metric_text(metrics["line"]), metric_text(metrics["branch"]),
            metric_text(metrics["expr"]), metric_text(metrics["toggle"]),
            "%d/2" % len(campaign["observed_states"]),
            "%d/2" % len(campaign["observed_transitions"]),
            "PASS" if campaign["all_scoreboards_pass"] else "FAIL",
        ))
    lines.extend([
        "", "## 逐轮累计曲线", "",
        "| Campaign | 轮次 | 周期 | 新覆盖点 | Line | Branch | Expr | Toggle | 生成尝试 | 判分 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for campaign in summary["campaigns"]:
        for row in campaign["iterations"]:
            metrics = row["cumulative_metrics"]
            lines.append("| %d | %d | %d | %d | %.2f%% | %.2f%% | %.2f%% | %.2f%% | %d | %s |" % (
                campaign["campaign"], row["iteration"], row["cycles"], row["new_covered_points"],
                metrics["line"]["pct"], metrics["branch"]["pct"], metrics["expr"]["pct"],
                metrics["toggle"]["pct"], row["generation_attempts"],
                "PASS" if row["scoreboard"]["pass"] else "FAIL",
            ))
    lines.extend([
        "", "## 可靠性门禁", "",
        "- 接受的真实 LLM 轮次：%d/%d；底层模型调用次数：%d（包括格式失败后的重试）。" % (
            summary["accepted_llm_generations"], summary["required_llm_generations"], summary["model_call_count"]),
        "- 唯一刺激文件：%d/%d；不会把同一个模型响应复制成多轮。" % (
            summary["unique_stimulus_count"], summary["accepted_llm_generations"]),
        "- RTL scoreboard：%s；篡改输出负向自检：%s。" % (
            "全部通过" if summary["validity_gates"]["all_rtl_scoreboards_pass"] else "存在失败",
            "通过" if summary["validity_gates"]["scoreboard_mutation_detected"] else "失败"),
        "- Verilator/Icarus 跨模拟器逐周期复放：%s（每个 campaign 1 例）。" % (
            "通过" if summary["validity_gates"]["cross_simulator_replay_pass"] else "失败"),
        "- 每个 campaign 从零覆盖数据库独立累计；没有沿用其他 campaign 的覆盖命中。",
        "- 每轮单独保存模型请求、原始响应、过滤后的刺激、RTL 输出和原生 coverage.dat。",
        "- 没有 coverage exclusion；失败轮次不会合入累计数据库。",
        "- 状态覆盖来自逐周期读取真实 RTL `stride_2_state_q`；Verilator 原生 FSM 自动提取仍为 N/A。", "",
        "## 结论", "",
        summary["conclusion"],
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--model", default="glm-5.2")
    parser.add_argument("--campaigns", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--max-generation-attempts", type=int, default=4)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    if args.campaigns < 1 or args.iterations < 1:
        parser.error("campaigns and iterations must be positive")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    commands: List[dict] = []
    env = core.build_simulator(commands, args.rebuild)
    version = core.run([str(core.VERILATOR), "--version"], core.ROOT, env, commands).strip()

    probe_dir = out_dir / "instrumentation_probe_not_counted"
    probe_dir.mkdir(exist_ok=True)
    probe_stimulus = probe_dir / "stimulus.txt"
    probe_result = probe_dir / "result.csv"
    probe_coverage = probe_dir / "coverage.dat"
    core.write_stimulus(probe_stimulus, [])
    core.run([str(core.SIM), str(probe_stimulus), str(probe_result), str(probe_coverage)], core.ROOT, env, commands)
    universe = zero_counts(core.parse_coverage(probe_coverage))
    denominator = core.metrics(universe)

    campaign_results = []
    all_hashes = []
    first_result_path = None
    first_stimuli: List[Stimulus] = []
    for campaign_index in range(1, args.campaigns + 1):
        campaign_dir = out_dir / ("campaign_%02d" % campaign_index)
        campaign_dir.mkdir(exist_ok=True)
        accepted_coverages: List[Dict[str, dict]] = []
        iterations = []
        covered_before = 0
        campaign_states = set()
        campaign_transitions = set()
        first_full = None
        for iteration_index in range(1, args.iterations + 1):
            case_dir = campaign_dir / ("iteration_%02d" % iteration_index)
            case_dir.mkdir(exist_ok=True)
            iteration_path = case_dir / "iteration.json"
            if iteration_path.exists():
                row = json.loads(iteration_path.read_text(encoding="utf-8"))
                case_coverage = core.parse_coverage(Path(row["artifacts"]["coverage"]))
                if row["coverage_accepted"]:
                    accepted_coverages.append(case_coverage)
                    campaign_states.update(row["scoreboard"].get("observed_states", []))
                    campaign_transitions.update(row["scoreboard"].get("observed_transitions", []))
                iterations.append(row)
                all_hashes.append(row["stimulus_sha256"])
                cumulative = core.merge_coverage(accepted_coverages) if accepted_coverages else universe
                covered_before = sum(point["count"] > 0 for point in cumulative.values())
                if first_full is None and is_closed(row["cumulative_metrics"]):
                    first_full = iteration_index
                if first_result_path is None:
                    first_result_path = Path(row["artifacts"]["result"])
                    first_stimuli = read_stimulus(Path(row["artifacts"]["stimulus"]))
                print("RESUME CAMPAIGN %d ITER %02d scoreboard=%s" % (
                    campaign_index, iteration_index, "PASS" if row["scoreboard"]["pass"] else "FAIL"
                ), flush=True)
                continue
            feedback = core.merge_coverage(accepted_coverages) if accepted_coverages else universe
            stimuli: List[Stimulus] = []
            llm_info = {}
            attempts_path = case_dir / "generation_attempts.json"
            attempt_records = json.loads(attempts_path.read_text(encoding="utf-8")) if attempts_path.exists() else []
            for attempt in range(len(attempt_records) + 1, args.max_generation_attempts + 1):
                stimuli, llm_info = core.llm_round(feedback, args.model, case_dir, attempt)
                attempt_records.append(llm_info)
                if stimuli:
                    break
            attempts_path.write_text(
                json.dumps(attempt_records, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            if not stimuli:
                raise RuntimeError("campaign %d iteration %d has no accepted LLM generation after %d attempts" % (
                    campaign_index, iteration_index, args.max_generation_attempts
                ))

            stimulus_path = case_dir / "stimulus.txt"
            result_path = case_dir / "result.csv"
            coverage_path = case_dir / "coverage.dat"
            core.write_stimulus(stimulus_path, stimuli)
            core.run([str(core.SIM), str(stimulus_path), str(result_path), str(coverage_path)], core.ROOT, env, commands)
            scoreboard = core.check_results(stimuli, result_path)
            case_coverage = core.parse_coverage(coverage_path)
            if scoreboard["pass"]:
                accepted_coverages.append(case_coverage)
                campaign_states.update(scoreboard["observed_states"])
                campaign_transitions.update(scoreboard["observed_transitions"])
            cumulative = core.merge_coverage(accepted_coverages) if accepted_coverages else universe
            standalone_metrics = core.metrics(case_coverage)
            cumulative_metrics = core.metrics(cumulative)
            covered_now = sum(point["count"] > 0 for point in cumulative.values())
            row = {
                "campaign": campaign_index,
                "iteration": iteration_index,
                "model": args.model,
                "generation_status": llm_info.get("status"),
                "generation_attempts": len(attempt_records),
                "cycles": len(stimuli),
                "stimulus_sha256": stimulus_hash(stimuli),
                "scoreboard": scoreboard,
                "coverage_accepted": scoreboard["pass"],
                "new_covered_points": covered_now - covered_before,
                "standalone_metrics": standalone_metrics,
                "cumulative_metrics": cumulative_metrics,
                "artifacts": {
                    "request": str(case_dir / ("llm_request_%02d.json" % len(attempt_records))),
                    "response": str(case_dir / ("llm_response_%02d.json" % len(attempt_records))),
                    "stimulus": str(stimulus_path),
                    "result": str(result_path),
                    "coverage": str(coverage_path),
                },
            }
            iterations.append(row)
            all_hashes.append(row["stimulus_sha256"])
            iteration_path.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
            covered_before = covered_now
            if first_full is None and is_closed(cumulative_metrics):
                first_full = iteration_index
            if first_result_path is None:
                first_result_path, first_stimuli = result_path, stimuli
            print(
                "CAMPAIGN %d ITER %02d cycles=%3d new=%3d line=%6.2f branch=%6.2f expr=%6.2f toggle=%6.2f attempts=%d scoreboard=%s" % (
                    campaign_index, iteration_index, len(stimuli), row["new_covered_points"],
                    cumulative_metrics["line"]["pct"], cumulative_metrics["branch"]["pct"],
                    cumulative_metrics["expr"]["pct"], cumulative_metrics["toggle"]["pct"],
                    len(attempt_records), "PASS" if scoreboard["pass"] else "FAIL",
                ), flush=True,
            )

        final_coverage = core.merge_coverage(accepted_coverages) if accepted_coverages else universe
        core.write_coverage(campaign_dir / "merged_coverage.dat", final_coverage)
        core.annotate_source(campaign_dir / "annotated_stride_detector.sv.txt", final_coverage)
        campaign_summary = {
            "campaign": campaign_index,
            "iterations": iterations,
            "first_full_coverage_iteration": first_full,
            "final_metrics": core.metrics(final_coverage),
            "all_scoreboards_pass": all(row["scoreboard"]["pass"] for row in iterations),
            "observed_states": sorted(campaign_states),
            "observed_transitions": sorted(campaign_transitions),
            "missed_points": core.missed_points(final_coverage, limit=100),
        }
        (campaign_dir / "campaign_summary.json").write_text(
            json.dumps(campaign_summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        campaign_results.append(campaign_summary)

    mutation_detected = bool(first_result_path) and mutate_result(
        first_result_path, out_dir / "scoreboard_mutation_result.csv", first_stimuli
    )
    cross_replay = run_cross_simulator_replays(out_dir, campaign_results, env, commands)
    required_generations = args.campaigns * args.iterations
    model_call_count = sum(
        len(json.loads(path.read_text(encoding="utf-8")))
        for path in out_dir.glob("campaign_*/iteration_*/generation_attempts.json")
    )
    accepted_generations = sum(
        row["generation_status"] == "accepted" for campaign in campaign_results for row in campaign["iterations"]
    )
    all_scoreboards = all(campaign["all_scoreboards_pass"] for campaign in campaign_results)
    all_campaigns_closed = all(is_closed(campaign["final_metrics"]) for campaign in campaign_results)
    all_state_closed = all(
        len(campaign["observed_states"]) == 2 and len(campaign["observed_transitions"]) == 2
        for campaign in campaign_results
    )
    unique_stimuli = len(set(all_hashes))
    coverage_valid = (
        accepted_generations == required_generations and all_scoreboards and mutation_detected
        and all_state_closed and len(universe) > 0 and cross_replay["pass"]
    )
    first_full_values = [campaign["first_full_coverage_iteration"] for campaign in campaign_results]
    conclusion = (
        "三组独立实验均达到 line/branch/expr/toggle 100%%；首次达到全覆盖的轮次分别为 %s。"
        "因此 100%% 不是把混合测试累计到第十轮后挑出的偶然结果，而是在各自清零的覆盖数据库上独立复现。"
        % first_full_values
        if all_campaigns_closed else
        "并非所有独立实验都达到 100%；结果按实际覆盖率保留，不能宣称稳定闭合。"
    )
    summary = {
        "schema": "llm4dv-all-llm-independent-campaigns-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": args.model,
        "campaign_count": args.campaigns,
        "iterations_per_campaign": args.iterations,
        "required_llm_generations": required_generations,
        "accepted_llm_generations": accepted_generations,
        "model_call_count": model_call_count,
        "unique_stimulus_count": unique_stimuli,
        "coverage_valid": coverage_valid,
        "all_campaigns_structurally_closed": all_campaigns_closed,
        "coverage_denominator": denominator,
        "dut": str(core.DUT),
        "dut_sha256": core.sha256(core.DUT),
        "toolchain": {"verilator": version, "gxx": str(core.GXX)},
        "semantics": {
            "post_reset_stimuli": "all accepted iterations are generated by the configured LLM",
            "testbench_reset": "fixed harness setup; not counted as an LLM generation iteration",
            "coverage_exclusions_applied": False,
            "cross_campaign_coverage_reuse": False,
            "failed_iterations_merged": False,
            "native_verilator_fsm_extraction": "N/A; state evidence is sampled from RTL register",
        },
        "validity_gates": {
            "all_required_llm_generations_accepted": accepted_generations == required_generations,
            "all_rtl_scoreboards_pass": all_scoreboards,
            "scoreboard_mutation_detected": mutation_detected,
            "all_campaigns_state_and_transitions_complete": all_state_closed,
            "native_coverage_universe_nonempty": len(universe) > 0,
            "cross_simulator_replay_pass": cross_replay["pass"],
        },
        "cross_simulator_replay": cross_replay,
        "campaigns": campaign_results,
        "conclusion": conclusion,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "commands.json").write_text(json.dumps(commands, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(out_dir / "report_zh.md", summary)

    evidence_files = [path for path in out_dir.rglob("*") if path.is_file() and path.name != "evidence_manifest.json"]
    manifest = {
        "root": str(out_dir),
        "files": [{"path": str(path.relative_to(out_dir)), "size": path.stat().st_size, "sha256": core.sha256(path)}
                  for path in sorted(evidence_files)],
    }
    (out_dir / "evidence_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("FINAL valid=%s all_campaigns_closed=%s report=%s" % (
        coverage_valid, all_campaigns_closed, out_dir / "report_zh.md"
    ), flush=True)
    return 0 if coverage_valid else 1


if __name__ == "__main__":
    sys.exit(main())
