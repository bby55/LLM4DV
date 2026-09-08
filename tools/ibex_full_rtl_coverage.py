"""Iterate real LLM-generated programs against the full elaborated Ibex RTL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

try:
    import llm4dv_structural_coverage as coverage_core
except ModuleNotFoundError:
    from tools import llm4dv_structural_coverage as coverage_core


ROOT = Path(__file__).resolve().parents[1]
IBEX = ROOT / "ibex_cpu"
SIM = Path(os.environ.get(
    "LLM4DV_IBEX_SIM", r"D:\tmp-llm4dv\obj_ibex_cov\Vibex_coverage_top.exe"
))
BASELINE = IBEX / "tools" / "stimuli" / "baseline.hex"
DEFAULT_ENV = Path(os.environ.get("LLM4DV_ENV_FILE", str(ROOT / ".env")))

FSM_STATES = {
    "controller": {
        0: "RESET", 1: "BOOT_SET", 2: "WAIT_SLEEP", 3: "SLEEP", 4: "FIRST_FETCH",
        5: "DECODE", 6: "FLUSH", 7: "IRQ_TAKEN", 8: "DBG_TAKEN_IF", 9: "DBG_TAKEN_ID",
    },
    "id_ex": {0: "FIRST_CYCLE", 1: "MULTI_CYCLE"},
    "load_store": {
        0: "IDLE", 1: "WAIT_GNT_MIS", 2: "WAIT_RVALID_MIS", 3: "WAIT_GNT",
        4: "WAIT_RVALID_MIS_GNTS_DONE",
    },
    "multdiv": {
        0: "MD_IDLE", 1: "MD_ABS_A", 2: "MD_ABS_B", 3: "MD_COMP", 4: "MD_LAST",
        5: "MD_CHANGE_SIGN", 6: "MD_FINISH",
    },
    "multiplier": {0: "ALBL", 1: "ALBH", 2: "AHBL", 3: "AHBH"},
}

FSM_TRANSITIONS = {
    "controller": {
        (0, 1), (1, 4), (2, 3), (3, 4), (4, 5), (4, 7), (4, 8),
        (5, 6), (5, 7), (5, 8), (6, 5), (6, 2), (6, 8), (6, 9),
        (7, 5), (8, 5), (9, 5),
    },
    "id_ex": {(0, 1), (1, 0)},
    "load_store": {
        (0, 1), (0, 2), (0, 3), (1, 2), (2, 0), (2, 3), (2, 4), (3, 0), (4, 0),
    },
    "multdiv": {(0, 1), (0, 6), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 0)},
    "multiplier": {(0, 1), (1, 2), (2, 0), (2, 3), (3, 0)},
}

SCENARIOS = ("normal", "data_stall", "irq", "debug", "mixed")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def merge(all_points: Iterable[Dict[str, dict]]) -> Dict[str, dict]:
    merged: Dict[str, dict] = {}
    for points in all_points:
        for raw, point in points.items():
            if raw not in merged:
                merged[raw] = {"attrs": point["attrs"], "count": 0}
            merged[raw]["count"] += point["count"]
    return merged


def dut_points(points: Dict[str, dict]) -> Dict[str, dict]:
    """Exclude verification-only instrumentation from the DUT denominator."""
    return {
        raw: point for raw, point in points.items()
        if Path(point["attrs"].get("f", "")).name != "ibex_coverage_top.sv"
    }


def point_covered(point: dict) -> bool:
    return point["count"] >= int(point["attrs"].get("s", "1"))


def empty_fsm_observations() -> dict:
    return {"states": {name: set() for name in FSM_STATES},
            "transitions": {name: set() for name in FSM_STATES}}


def merge_fsm(observations: Iterable[dict]) -> dict:
    merged = empty_fsm_observations()
    for observation in observations:
        for machine in FSM_STATES:
            merged["states"][machine].update(observation["states"][machine])
            merged["transitions"][machine].update(observation["transitions"][machine])
    return merged


def fsm_metrics(observation: dict) -> dict:
    machines = {}
    total_states = covered_states = total_transitions = covered_transitions = 0
    for machine, names in FSM_STATES.items():
        state_hits = observation["states"][machine]
        transition_hits = observation["transitions"][machine]
        state_total = len(names)
        transition_total = len(FSM_TRANSITIONS[machine])
        machines[machine] = {
            "states": {
                "covered": len(state_hits), "total": state_total,
                "pct": round(100.0 * len(state_hits) / state_total, 2),
                "hit": [names[value] for value in sorted(state_hits)],
                "missed": [name for value, name in names.items() if value not in state_hits],
            },
            "transitions": {
                "covered": len(transition_hits), "total": transition_total,
                "pct": round(100.0 * len(transition_hits) / transition_total, 2),
                "hit": ["%s->%s" % (names[a], names[b]) for a, b in sorted(transition_hits)],
                "missed": ["%s->%s" % (names[a], names[b])
                           for a, b in sorted(FSM_TRANSITIONS[machine] - transition_hits)],
            },
        }
        total_states += state_total
        covered_states += len(state_hits)
        total_transitions += transition_total
        covered_transitions += len(transition_hits)
    return {
        "states": {"covered": covered_states, "total": total_states,
                   "pct": round(100.0 * covered_states / total_states, 2)},
        "transitions": {"covered": covered_transitions, "total": total_transitions,
                        "pct": round(100.0 * covered_transitions / total_transitions, 2)},
        "machines": machines,
    }


def choose_scenario(iteration: int, current_fsm: dict) -> str:
    """Use a bounded targeted probe, then return to the normal scenario rotation.

    A targeted environment stimulus must not replace the main structural-coverage
    campaign indefinitely: doing so can improve one FSM arc while suppressing
    ordinary line/branch/toggle activity. The first two probe slots are reserved
    for the two known controller holes; all later slots use the regular rotation.
    """
    missed = set(current_fsm["machines"]["controller"]["transitions"]["missed"])
    if iteration == 1 and "FIRST_FETCH->IRQ_TAKEN" in missed:
        return "irq_first_fetch"
    if iteration in (2, 3) and "FLUSH->DBG_TAKEN_IF" in missed:
        return "debug_flush"
    return SCENARIOS[(iteration - 1) % len(SCENARIOS)]


def feedback(points: Dict[str, dict], limit: int = 30) -> List[dict]:
    missed = []
    source_cache: Dict[str, List[str]] = {}
    for point in points.values():
        if point_covered(point):
            continue
        attrs = point["attrs"]
        filename = attrs.get("f", "")
        line = int(attrs.get("l", "0") or 0)
        source = ""
        if filename and line:
            mapped = Path(filename.replace("I:\\", str(IBEX) + os.sep))
            try:
                key = str(mapped)
                if key not in source_cache:
                    source_cache[key] = mapped.read_text(encoding="utf-8", errors="replace").splitlines()
                rows = source_cache[key]
                if line <= len(rows):
                    source = rows[line - 1].strip()
            except OSError:
                pass
        missed.append({
            "type": attrs.get("t", "unknown"),
            "file": Path(filename).name,
            "line": line,
            "condition": attrs.get("o", ""),
            "source": source,
        })
    priority = {"line": 0, "branch": 1, "expr": 2, "toggle": 3}
    missed.sort(key=lambda row: (priority.get(row["type"], 9), row["file"], row["line"]))
    return missed[:limit]


def feedback_summary(points: Dict[str, dict]) -> dict:
    by_file = defaultdict(Counter)
    for point in points.values():
        if not point_covered(point):
            attrs = point["attrs"]
            by_file[Path(attrs.get("f", "unknown")).name][attrs.get("t", "unknown")] += 1
    rows = []
    for filename, counts in by_file.items():
        rows.append({"file": filename, **dict(counts), "total": sum(counts.values())})
    rows.sort(key=lambda row: row["total"], reverse=True)
    return {"highest_uncovered_files": rows[:10], "sample_points": feedback(points)}


def api_config() -> Dict[str, str]:
    config = coverage_core.load_env(DEFAULT_ENV)
    # Never mix an environment key with the repository's private proxy URL.
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_BASE_URL"):
        config["LOCAL_API_KEY"] = os.environ["OPENAI_API_KEY"]
        config["LOCAL_API_BASE"] = os.environ["OPENAI_BASE_URL"]
    return config


def request_program(points: Dict[str, dict], current_fsm: dict, model: str, case_dir: Path,
                    iteration: int, attempt: int) -> Tuple[List[int], dict]:
    config = api_config()
    api_key = config.get("LOCAL_API_KEY")
    base_url = config.get("LOCAL_API_BASE")
    if not api_key or not base_url:
        return [], {"status": "failed", "reason": "API configuration is missing"}

    focus = [
        "integer ALU comparisons, shifts, signed and unsigned corner values",
        "byte/halfword/word loads and stores with legal aligned addresses near 0x80000000",
        "RV32M multiply, high multiply, divide, remainder and divide-by-zero cases",
        "taken/not-taken branches, forward/backward jumps, JAL and JALR dependencies",
        "CSR reads/writes, fences, ECALL/EBREAK and exception-control paths",
        "mixed dependency hazards, extreme operands, loops and memory traffic",
    ][(iteration - 1) % 6]
    missed_controller = current_fsm["machines"]["controller"]["states"]["missed"]
    critical = []
    if "WAIT_SLEEP" in missed_controller or "SLEEP" in missed_controller:
        critical.append(
            "Put a reachable WFI (0x10500073) within the first 8 straight-line instructions, "
            "before every branch or jump, so an IRQ scenario can wake it.")
    if "DBG_TAKEN_ID" in missed_controller:
        critical.append(
            "Put a reachable EBREAK (0x00100073) within the first 12 straight-line instructions; "
            "the debug scenario maps this program at the debug entry address.")
    missed_controller_arcs = current_fsm["machines"]["controller"]["transitions"]["missed"]
    if "FLUSH->DBG_TAKEN_IF" in missed_controller_arcs:
        critical.append(
            "Include an early legal exception producer such as ECALL (0x00000073), "
            "EBREAK (0x00100073), or a deliberately illegal 32-bit word so DECODE can "
            "enter FLUSH; the debug_flush environment scenario will request debug during DECODE.")
    prompt = """You generate a complete RV32IM machine-code program for structural coverage
of the lowRISC Ibex CPU. This is a real RTL run, not a request to estimate coverage.
The program starts at PC 0x00100080. Generate 24 to 64 little-endian 32-bit instruction
words. All instructions must be 32-bit encodings (bits[1:0] == 3). Use only RV32IM plus
standard machine-mode CSR/fence/system instructions. Registers start at zero. Data memory
is readable/writable; use 0x80000000 as a safe data base. Keep control flow within the
    program. Include misaligned memory operations, RV32M operations, exceptions, or WFI when
    they help the requested target. End with 0x0000006f (jal x0,0). This round should emphasize: %s.
Critical reachability requirements: %s

Return exactly one JSON object and no markdown:
{"words":["0x12345678",...],"rationale":"brief intent for instruction groups"}
Never report or claim a coverage percentage. The following feedback was computed from the
    native Verilator database and direct RTL FSM sampling after earlier accepted runs:
    structural=%s
    fsm=%s""" % (
        focus, " ".join(critical) or "none",
        json.dumps(feedback_summary(points), ensure_ascii=False),
        json.dumps({name: {
            "missed_states": current_fsm["machines"][name]["states"]["missed"],
            "missed_transitions": current_fsm["machines"][name]["transitions"]["missed"],
        } for name in current_fsm["machines"]}, ensure_ascii=False))
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.35,
        "max_tokens": 12000,
    }
    request_path = case_dir / ("llm_request_attempt_%02d.json" % attempt)
    response_path = case_dir / ("llm_response_attempt_%02d.json" % attempt)
    request_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    content = ""
    try:
        if config.get("API_TRANSPORT", "").lower() == "schannel":
            curl_config = [
                'noproxy = "*"', "ssl-no-revoke",
                'header = "Authorization: Bearer %s"' % api_key,
                'header = "Content-Type: application/json"', 'request = "POST"',
                "silent", "show-error",
            ]
            cert = config.get("SSL_CERT_PATH", "")
            if cert:
                curl_config.append('cacert = "%s"' % cert)
            proc = subprocess.run(
                ["curl.exe", "--config", "-", "--connect-timeout", "15", "--max-time", "300",
                 "--data-binary", "@" + str(request_path), base_url.rstrip("/") + "/chat/completions"],
                input="\n".join(curl_config), text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=310,
            )
            if proc.returncode:
                raise RuntimeError(proc.stderr[:500])
            raw_api = json.loads(proc.stdout)
            if "choices" not in raw_api:
                raise RuntimeError("API response has no choices: %s" %
                                   json.dumps(raw_api, ensure_ascii=True)[:1200])
            choice = raw_api["choices"][0]
            message = choice["message"]
            content = message.get("content") or ""
            if not content:
                raise RuntimeError("empty model content: finish_reason=%r reasoning_chars=%d" % (
                    choice.get("finish_reason"), len(message.get("reasoning_content") or "")))
        else:
            from openai import OpenAI
            response = OpenAI(api_key=api_key, base_url=base_url).chat.completions.create(**payload)
            content = response.choices[0].message.content or ""

        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise ValueError("response has no JSON object")
        parsed = json.loads(match.group(0))
        words = []
        for raw in parsed.get("words", []):
            word = int(str(raw), 0) & 0xFFFFFFFF
            if word & 3 != 3:
                raise ValueError("compressed/non-32-bit word 0x%08x" % word)
            words.append(word)
        if not 24 <= len(words) <= 64:
            raise ValueError("expected 24..64 words, got %d" % len(words))
        if words[-1] != 0x0000006F:
            raise ValueError("last instruction is not jal x0,0")
        evidence = {
            "status": "accepted", "model": model, "attempt": attempt,
            "word_count": len(words), "rationale": parsed.get("rationale", ""),
            "raw_response": content,
        }
        response_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        return words, evidence
    except Exception as exc:
        evidence = {"status": "failed", "model": model, "attempt": attempt,
                    "reason": repr(exc), "raw_response": content}
        response_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        return [], evidence


def write_program(path: Path, words: Sequence[int]) -> None:
    path.write_text("\n".join("0x%08x" % word for word in words) + "\n", encoding="ascii")


def run_sim(program: Path, case_dir: Path, cycles: int, scenario: str = "normal",
            simulator: Path = SIM) -> Tuple[dict, Path, Path, Path]:
    trace = case_dir / "rvfi_trace.jsonl"
    fsm_trace = case_dir / "fsm_trace.jsonl"
    coverage = case_dir / "coverage.dat"
    env = os.environ.copy()
    env["PATH"] = str(Path(r"C:\msys64\ucrt64\bin")) + os.pathsep + env["PATH"]
    proc = subprocess.run(
        [str(simulator), str(program), str(trace), str(coverage), str(cycles),
         str(fsm_trace), scenario],
        cwd=str(case_dir), env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    summary = json.loads(lines[-1]) if lines else {"healthy": False, "failure": "no summary"}
    summary["returncode"] = proc.returncode
    summary["stdout"] = proc.stdout
    summary["scenario"] = scenario
    return summary, trace, fsm_trace, coverage


def validate_trace(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    errors = []
    for before, after in zip(rows, rows[1:]):
        if after["order"] != before["order"] + 1:
            errors.append("non-consecutive RVFI order")
            break
    if not rows:
        errors.append("empty trace")
    return {"pass": not errors, "rows": len(rows), "errors": errors}


def validate_fsm_trace(path: Path) -> Tuple[dict, dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    observation = empty_fsm_observations()
    errors = []
    previous = None
    for row_index, row in enumerate(rows):
        for machine, names in FSM_STATES.items():
            value = int(row[machine])
            if value not in names:
                errors.append("row %d %s has invalid state %d" % (row_index, machine, value))
                continue
            observation["states"][machine].add(value)
            if previous is not None:
                old = int(previous[machine])
                if old != value:
                    transition = (old, value)
                    if transition not in FSM_TRANSITIONS[machine]:
                        errors.append("row %d %s has illegal transition %d->%d" %
                                      (row_index, machine, old, value))
                    else:
                        observation["transitions"][machine].add(transition)
        previous = row
    if not rows:
        errors.append("empty FSM trace")
    return {"pass": not errors, "rows": len(rows), "errors": errors[:20]}, observation


def mutation_gate(trace: Path, output: Path) -> bool:
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < 2:
        return False
    rows[1]["order"] += 7
    output.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return not validate_trace(output)["pass"]


def fsm_mutation_gate(trace: Path, output: Path) -> bool:
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return False
    rows[0]["controller"] = 15
    output.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result, _ = validate_fsm_trace(output)
    return not result["pass"]


def source_manifest() -> dict:
    rtl = [p for p in IBEX.rglob("*") if p.suffix.lower() in {".sv", ".svh"}
           and "obj_" not in str(p) and "tools" not in p.parts]
    physical_lines = sum(data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
                         for data in (path.read_bytes() for path in rtl))
    entries = [{"path": str(path.relative_to(IBEX)), "sha256": sha256(path)} for path in sorted(rtl)]
    return {"files": len(rtl), "physical_lines": physical_lines, "entries": entries}


def metric_text(metric: dict) -> str:
    if metric["pct"] is None:
        return "N/A"
    return "%d/%d (%.2f%%)" % (metric["covered"], metric["total"], metric["pct"])


def write_report(path: Path, summary: dict) -> None:
    lines = [
        "# LLM4DV / Ibex 完整 RTL 结构覆盖周报", "",
        "- 原始 RTL 目录规模：%d 个 `.sv/.svh` 文件，%d 物理行。" %
        (summary["source_manifest"]["files"], summary["source_manifest"]["physical_lines"]),
        "- 本配置实际展开：%d 个 Verilator 模块；原生结构覆盖分母 %d 点。" %
        (summary["elaborated_modules"], summary["coverage_points"]),
        "- 模型：`%s`；接受的真实 LLM 生成：%d/%d；底层调用：%d。" %
        (summary["model"], summary["accepted_llm_generations"], summary["requested_iterations"],
         summary["model_calls"]),
        "- 复放的既有真实 LLM 程序：%d；本报告累计 LLM 程序：%d。" %
        (summary["seed_replays"], summary["seed_replays"] + summary["accepted_llm_generations"]),
        "- 总体真实性门禁：**%s**。" % ("PASS" if summary["coverage_valid"] else "FAIL"), "",
        "## 结构覆盖结果", "",
        "| 轮次 | 来源/场景 | 退休指令 | 新点 | Line | Branch | Expr | Toggle | FSM状态 | FSM转移 | 门禁 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["iterations"]:
        if "cumulative_metrics" not in row:
            lines.append("| %d | %s | 0 | 0 | N/A | N/A | N/A | N/A | N/A | N/A | FAIL (%s) |" % (
                row["iteration"], row.get("source", "LLM"), row.get("reason", "generation failed")))
            continue
        metrics = row["cumulative_metrics"]
        fm = row["cumulative_fsm_metrics"]
        lines.append("| %d | %s/%s | %d | %d | %s | %s | %s | %s | %s | %s | %s |" % (
            row["iteration"], row["source"], row["simulation"]["scenario"],
            row["simulation"]["retired"], row["new_covered_points"],
            metric_text(metrics["line"]), metric_text(metrics["branch"]),
            metric_text(metrics["expr"]), metric_text(metrics["toggle"]),
            metric_text(fm["states"]), metric_text(fm["transitions"]),
            "PASS" if row["accepted"] else "FAIL",
        ))
    lines.extend([
        "", "## 测试方法与正确性判定", "",
        "- 语句/基本块覆盖：Verilator `line` 插桩点实际执行次数达到阈值即命中。",
        "- 分支覆盖：Verilator `branch` 点分别记录 `if/case/条件运算` 的实际控制流方向。",
        "- 表达式覆盖：`expr` 点记录布尔子条件组合；它与分支覆盖分开统计。",
        "- 翻转覆盖：每个已展开信号位的 `0->1` 和 `1->0` 是独立覆盖点。",
        "- 状态覆盖：逐周期读取当前配置实际实例化的 controller、ID/EX、LSU、mult/div、multiplier 共 28 个 RTL 状态。",
        "- 状态转移：从 RTL next-state 逻辑枚举 41 条合法非自环边；只有真实相邻周期状态变化才命中。",
        "- 每轮程序必须在 Ibex RTL 上产生真实 RVFI 退休事件；`order` 必须逐条连续，x0 写回必须为 0，",
        "  且普通顺序、JAL/JALR、条件分支的下一 PC 由独立 C++ 规则检查。失败轮次不合入累计覆盖。",
        "- 覆盖数字从 `coverage.dat` 解析；LLM 只生成刺激，不提供也不能修改覆盖数字。",
        "- RVFI trace 篡改：%s；FSM 非法状态篡改：%s；同一基线确定性复放：%s。" % (
            "PASS" if summary["validity_gates"]["trace_mutation_detected"] else "FAIL",
            "PASS" if summary["validity_gates"]["fsm_mutation_detected"] else "FAIL",
            "PASS" if summary["validity_gates"]["deterministic_replay"] else "FAIL"),
        "", "## FSM 明细", "",
        "| FSM | 状态覆盖 | 转移覆盖 | 未覆盖状态 |",
        "|---|---:|---:|---|",
    ])
    for name, machine in summary["final_fsm_metrics"]["machines"].items():
        lines.append("| %s | %s | %s | %s |" % (
            name, metric_text(machine["states"]), metric_text(machine["transitions"]),
            ", ".join(machine["states"]["missed"]) or "无"))
    lines.extend([
        "", "## 真实性边界", "",
        "- 这些结果证明的是当前 Ibex 参数配置下、实际展开结构的动态覆盖，不是整个 RISC-V ISA 的形式化正确性证明。",
        "- 未实例化的 ICache/PMP/SecureIbex/RV32B 等参数分支不会出现在本配置分母中；完整配置空间需要单独构建配置矩阵。",
        "- 未达到 100% 时按原数报告，未使用 coverage exclusion，也没有把失败或格式错误的模型响应计入轮次。",
        "- 本次平台期的未覆盖热点集中在 `ibex_core.sv`、CSR、取指、decoder/controller；大量剩余 toggle",
        "  属于未激活的中断、调试、性能计数器及配置相关信号，后续应使用配置矩阵和外部事件刺激，而非伪造程序覆盖。",
        "- 本机 Verilator 5.050 的 `verilator_coverage` wrapper 缺少随包分析二进制；本报告直接逐条解析",
        "  同一 runtime 写出的 `coverage.dat`，并通过固定分母、确定性复放和负向篡改门禁校验。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--model", default="glm-5.2")
    parser.add_argument("--iterations", type=int, default=6)
    parser.add_argument("--cycles", type=int, default=800)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--simulator", type=Path, default=SIM,
                        help="Path to Vibex_coverage_top executable")
    parser.add_argument("--seed-run", type=Path, action="append",
                        help="Replay accepted program.hex files from an earlier run before new iterations")
    args = parser.parse_args()
    simulator = args.simulator.resolve()
    if not simulator.exists():
        raise FileNotFoundError("build the full Ibex simulator first: %s" % simulator)

    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    accepted_coverages = []
    accepted_fsm = []
    iterations = []
    model_calls = 0

    baseline_dir = out / "iteration_00_baseline"
    baseline_dir.mkdir(exist_ok=True)
    baseline_summary, baseline_trace, baseline_fsm_trace, baseline_cov_path = run_sim(
        BASELINE, baseline_dir, args.cycles, "normal", simulator)
    baseline_trace_check = validate_trace(baseline_trace)
    baseline_fsm_check, baseline_fsm = validate_fsm_trace(baseline_fsm_trace)
    baseline_points = dut_points(coverage_core.parse_coverage(baseline_cov_path))
    baseline_accepted = (baseline_summary.get("returncode") == 0 and baseline_summary.get("healthy")
                         and baseline_trace_check["pass"] and baseline_fsm_check["pass"])
    if not baseline_accepted:
        raise RuntimeError("baseline failed truth gates: sim=%s fsm=%s" %
                           (baseline_summary, baseline_fsm_check))
    accepted_coverages.append(baseline_points)
    accepted_fsm.append(baseline_fsm)
    cumulative = merge(accepted_coverages)
    cumulative_fsm = merge_fsm(accepted_fsm)
    iterations.append({
        "iteration": 0, "source": "repository baseline", "accepted": True,
        "simulation": baseline_summary, "trace_check": baseline_trace_check,
        "fsm_trace_check": baseline_fsm_check,
        "new_covered_points": sum(point_covered(p) for p in cumulative.values()),
        "cumulative_metrics": coverage_core.metrics(cumulative),
        "cumulative_fsm_metrics": fsm_metrics(cumulative_fsm),
        "artifacts": {"program": str(BASELINE), "trace": str(baseline_trace),
                      "fsm_trace": str(baseline_fsm_trace), "coverage": str(baseline_cov_path)},
    })

    seed_programs = []
    if args.seed_run:
        seen_program_hashes = set()
        for seed_root in args.seed_run:
            for program in sorted(seed_root.resolve().glob("iteration_*/program.hex")):
                digest = sha256(program)
                if digest not in seen_program_hashes:
                    seed_programs.append(program)
                    seen_program_hashes.add(digest)
    for seed_index, program in enumerate(seed_programs, 1):
        absolute_index = len(iterations)
        case_dir = out / "seed_replays" / ("seed_%02d" % seed_index)
        case_dir.mkdir(parents=True, exist_ok=True)
        scenario = choose_scenario(seed_index, fsm_metrics(cumulative_fsm))
        sim_summary, trace, state_trace, cov_path = run_sim(
            program, case_dir, args.cycles, scenario, simulator)
        trace_check = validate_trace(trace)
        state_check, observation = validate_fsm_trace(state_trace)
        points = dut_points(coverage_core.parse_coverage(cov_path))
        accepted = (sim_summary.get("returncode") == 0 and sim_summary.get("healthy")
                    and trace_check["pass"] and state_check["pass"])
        before = sum(point_covered(p) for p in cumulative.values())
        if accepted:
            accepted_coverages.append(points)
            accepted_fsm.append(observation)
            cumulative = merge(accepted_coverages)
            cumulative_fsm = merge_fsm(accepted_fsm)
        after = sum(point_covered(p) for p in cumulative.values())
        row = {
            "iteration": absolute_index, "source": "prior LLM replay", "accepted": accepted,
            "simulation": sim_summary, "trace_check": trace_check, "fsm_trace_check": state_check,
            "new_covered_points": after - before,
            "standalone_metrics": coverage_core.metrics(points),
            "cumulative_metrics": coverage_core.metrics(cumulative),
            "cumulative_fsm_metrics": fsm_metrics(cumulative_fsm),
            "program_sha256": sha256(program),
            "artifacts": {"program": str(program), "trace": str(trace),
                          "fsm_trace": str(state_trace), "coverage": str(cov_path)},
        }
        (case_dir / "iteration.json").write_text(
            json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
        iterations.append(row)
        print("SEED %d accepted=%s scenario=%s retired=%s state=%s transition=%s" % (
            seed_index, accepted, scenario, sim_summary.get("retired"),
            fsm_metrics(cumulative_fsm)["states"]["pct"],
            fsm_metrics(cumulative_fsm)["transitions"]["pct"]), flush=True)

    for generated_index in range(1, args.iterations + 1):
        absolute_index = len(iterations)
        case_dir = out / ("iteration_%02d_llm" % absolute_index)
        case_dir.mkdir(exist_ok=True)
        words: List[int] = []
        attempts = []
        for attempt in range(1, args.attempts + 1):
            model_calls += 1
            words, evidence = request_program(
                cumulative, fsm_metrics(cumulative_fsm), args.model, case_dir,
                absolute_index, attempt)
            attempts.append(evidence)
            if words:
                break
        (case_dir / "generation_attempts.json").write_text(
            json.dumps(attempts, indent=2, ensure_ascii=False), encoding="utf-8")
        if not words:
            iterations.append({"iteration": absolute_index, "source": "LLM new", "accepted": False,
                               "reason": "no valid generated program"})
            continue
        program = case_dir / "program.hex"
        write_program(program, words)
        scenario = choose_scenario(absolute_index, fsm_metrics(cumulative_fsm))
        sim_summary, trace, state_trace, cov_path = run_sim(
            program, case_dir, args.cycles, scenario, simulator)
        trace_check = validate_trace(trace)
        state_check, observation = validate_fsm_trace(state_trace)
        points = dut_points(coverage_core.parse_coverage(cov_path))
        accepted = (sim_summary.get("returncode") == 0 and sim_summary.get("healthy")
                    and trace_check["pass"] and state_check["pass"])
        before = sum(point_covered(p) for p in cumulative.values())
        if accepted:
            accepted_coverages.append(points)
            accepted_fsm.append(observation)
            cumulative = merge(accepted_coverages)
            cumulative_fsm = merge_fsm(accepted_fsm)
        after = sum(point_covered(p) for p in cumulative.values())
        row = {
            "iteration": absolute_index, "source": "LLM new", "accepted": accepted,
            "simulation": sim_summary, "trace_check": trace_check, "fsm_trace_check": state_check,
            "new_covered_points": after - before,
            "standalone_metrics": coverage_core.metrics(points),
            "cumulative_metrics": coverage_core.metrics(cumulative),
            "cumulative_fsm_metrics": fsm_metrics(cumulative_fsm),
            "program_sha256": sha256(program), "word_count": len(words),
            "generation": attempts[-1],
            "artifacts": {"program": str(program), "trace": str(trace),
                          "fsm_trace": str(state_trace), "coverage": str(cov_path)},
        }
        (case_dir / "iteration.json").write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
        iterations.append(row)
        print("ITER %d accepted=%s scenario=%s retired=%s new=%d line=%s branch=%s expr=%s "
              "toggle=%s state=%s transition=%s" % (
            absolute_index, accepted, scenario, sim_summary.get("retired"), after - before,
            cumulative and coverage_core.metrics(cumulative)["line"]["pct"],
            coverage_core.metrics(cumulative)["branch"]["pct"],
            coverage_core.metrics(cumulative)["expr"]["pct"],
            coverage_core.metrics(cumulative)["toggle"]["pct"],
            fsm_metrics(cumulative_fsm)["states"]["pct"],
            fsm_metrics(cumulative_fsm)["transitions"]["pct"]), flush=True)

    coverage_core.write_coverage(out / "cumulative_coverage.dat", cumulative)
    replay_dir = out / "deterministic_replay"
    replay_dir.mkdir(exist_ok=True)
    replay_summary, replay_trace, replay_fsm_trace, replay_cov = run_sim(
        BASELINE, replay_dir, args.cycles, "normal", simulator)
    deterministic = (replay_summary.get("returncode") == 0 and
                     replay_trace.read_bytes() == baseline_trace.read_bytes() and
                     replay_fsm_trace.read_bytes() == baseline_fsm_trace.read_bytes() and
                     coverage_core.metrics(dut_points(coverage_core.parse_coverage(replay_cov))) ==
                     coverage_core.metrics(baseline_points))
    mutation = mutation_gate(baseline_trace, out / "mutated_trace_must_fail.jsonl")
    state_mutation = fsm_mutation_gate(
        baseline_fsm_trace, out / "mutated_fsm_trace_must_fail.jsonl")
    manifest = source_manifest()
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": args.model, "requested_iterations": args.iterations,
        "accepted_llm_generations": sum(
            row.get("accepted", False) for row in iterations if row.get("source") == "LLM new"),
        "seed_replays": sum(
            row.get("accepted", False) for row in iterations if row.get("source") == "prior LLM replay"),
        "model_calls": model_calls, "coverage_points": len(cumulative),
        "elaborated_modules": 98, "source_manifest": manifest,
        "final_metrics": coverage_core.metrics(cumulative),
        "final_fsm_metrics": fsm_metrics(cumulative_fsm), "iterations": iterations,
        "validity_gates": {
            "trace_mutation_detected": mutation, "fsm_mutation_detected": state_mutation,
            "deterministic_replay": deterministic,
            "all_rvfi_and_fsm_checks": all(row.get("accepted", False) for row in iterations),
        },
    }
    summary["coverage_valid"] = (mutation and state_mutation and deterministic and
                                 all(row.get("accepted", False) for row in iterations))
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(out / "WEEKLY_REPORT_CN.md", summary)
    print(json.dumps({"coverage_valid": summary["coverage_valid"],
                      "accepted_llm_generations": summary["accepted_llm_generations"],
                      "seed_replays": summary["seed_replays"], "model_calls": model_calls,
                      "final_metrics": summary["final_metrics"],
                      "final_fsm_metrics": summary["final_fsm_metrics"]},
                     ensure_ascii=False), flush=True)
    return 0 if summary["coverage_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
