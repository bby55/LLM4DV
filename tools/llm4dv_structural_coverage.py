"""Iterative native structural coverage for the LLM4DV stride detector."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
DUT_DIR = ROOT / "stride_detector"
DUT = DUT_DIR / "stride_detector.sv"
DEFAULT_ENV = Path(os.environ.get("LLM4DV_ENV_FILE", str(ROOT / ".env")))
DEFAULT_OUTPUT_ROOT = Path(os.environ.get(
    "LLM4DV_OUTPUT_ROOT", str(ROOT.parent / "outputs")
))
HARNESS = ROOT / "tools" / "stride_detector_cov_main.cpp"
VERILATOR_ROOT = Path(os.environ.get("VERILATOR_ROOT", r"D:\verilator-5.050"))
VERILATOR = VERILATOR_ROOT / "bin" / "verilator.exe"
GXX = Path(os.environ.get("LLM4DV_GXX", r"C:\msys64\ucrt64\bin\g++.exe"))
UCRT_BIN = GXX.parent
MSYS_BIN = Path(r"C:\msys64\usr\bin")
BUILD_TEMP = Path(os.environ.get("LLM4DV_BUILD_TEMP", r"D:\tmp-llm4dv"))
OBJ_DIR = DUT_DIR / "obj_structural_cov"
SIM = OBJ_DIR / "stride_detector_cov_sim.exe"

Stimulus = Tuple[int, int]


def run(command: Sequence[str], cwd: Path, env: Dict[str, str], commands: List[dict]) -> str:
    started = datetime.now().isoformat(timespec="seconds")
    proc = subprocess.run(
        list(map(str, command)), cwd=str(cwd), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    commands.append({
        "started": started,
        "cwd": str(cwd),
        "command": list(map(str, command)),
        "returncode": proc.returncode,
        "output": proc.stdout,
    })
    if proc.returncode:
        raise RuntimeError("command failed (%d): %s\n%s" % (
            proc.returncode, " ".join(map(str, command)), proc.stdout
        ))
    return proc.stdout


def build_simulator(commands: List[dict], rebuild: bool) -> Dict[str, str]:
    required = [VERILATOR, GXX, DUT, HARNESS]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing structural coverage dependencies: %s" % missing)

    BUILD_TEMP.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["VERILATOR_ROOT"] = str(VERILATOR_ROOT)
    env["TEMP"] = str(BUILD_TEMP)
    env["TMP"] = str(BUILD_TEMP)
    env["PATH"] = os.pathsep.join([str(UCRT_BIN), str(MSYS_BIN), str(VERILATOR_ROOT / "bin"), env["PATH"]])

    if rebuild and OBJ_DIR.exists():
        shutil.rmtree(OBJ_DIR)
    if SIM.exists() and not rebuild:
        return env

    drive = "M:"
    subprocess.run(["subst", drive, str(ROOT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mapped_dut = Path(drive + r"\stride_detector")
    try:
        run([
            str(VERILATOR), "--cc", "--coverage", "--coverage-underscore",
            "--coverage-per-instance", "-Wall", "-Wno-fatal",
            "--top-module", "stride_detector", "stride_detector.sv",
            "--Mdir", OBJ_DIR.name,
        ], mapped_dut, env, commands)
    finally:
        subprocess.run(["subst", drive, "/d"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    runtime = [
        VERILATOR_ROOT / "include" / name for name in
        ("verilated.cpp", "verilated_cov.cpp", "verilated_covergroup.cpp", "verilated_threads.cpp")
    ]
    generated = sorted(OBJ_DIR.glob("*.cpp"))
    compile_command = [
        str(GXX), "-std=gnu++17", "-O2", "-I" + str(OBJ_DIR),
        "-I" + str(VERILATOR_ROOT / "include"),
        "-I" + str(VERILATOR_ROOT / "include" / "vltstd"),
        str(HARNESS), *map(str, generated), *map(str, runtime), "-o", str(SIM),
    ]
    run(compile_command, ROOT, env, commands)
    return env


class StrideReference:
    def __init__(self) -> None:
        self.last = 0
        self.stride1 = 0
        self.conf1 = 0
        self.stride2 = [0, 0]
        self.conf2 = [0, 0]
        self.state = 1  # STATE_FIRST_STRIDE

    def step(self, valid: int, value: int) -> Tuple[int, int, int, int, int]:
        value &= 0xFFFFFFFF
        full = (value - self.last) & ((1 << 33) - 1)
        incoming = full & 0x1F
        high = full >> 5
        overflow = high != ((1 << 28) - 1) if incoming & 0x10 else high != 0

        next_stride1, next_conf1 = self.stride1, self.conf1
        next_stride2, next_conf2 = self.stride2[:], self.conf2[:]
        next_state = self.state
        if valid:
            if incoming == self.stride1 and not overflow:
                if self.conf1 < 3:
                    next_conf1 += 1
            elif self.conf1 > 0:
                next_conf1 -= 1
            else:
                next_stride1 = incoming

            index = 0 if self.state == 1 else 1
            if incoming == self.stride2[index] and not overflow:
                if self.conf2[index] < 3:
                    next_conf2[index] += 1
            elif self.conf2[index] > 0:
                next_conf2[index] -= 1
            else:
                next_stride2[index] = incoming
            next_state = 0 if self.state == 1 else 1
            self.last = value

        self.stride1, self.conf1 = next_stride1, next_conf1
        self.stride2, self.conf2 = next_stride2, next_conf2
        self.state = next_state
        stride1_valid = int(self.conf1 == 3 or self.conf2[0] == 3)
        stride1_out = self.stride1 if self.conf1 == 3 else self.stride2[0]
        stride2_valid = int(self.conf2[0] == 3 and self.conf2[1] == 3 and self.conf1 != 3)
        return stride1_out, stride1_valid, self.stride2[1], stride2_valid, self.state


def check_results(stimuli: Sequence[Stimulus], result_path: Path) -> dict:
    model = StrideReference()
    rows = list(csv.DictReader(result_path.open(encoding="utf-8")))
    if len(rows) != len(stimuli):
        return {"pass": False, "error": "row count mismatch", "checked_cycles": len(rows)}
    mismatches = []
    fields = ("stride_1", "stride_1_valid", "stride_2", "stride_2_valid", "stride_2_state")
    observed_states = set()
    observed_transitions = set()
    previous_state = 1
    for index, ((valid, value), row) in enumerate(zip(stimuli, rows)):
        expected = model.step(valid, value)
        actual = tuple(int(row[field]) for field in fields)
        observed_states.add(actual[-1])
        if actual[-1] != previous_state:
            observed_transitions.add("%d->%d" % (previous_state, actual[-1]))
        previous_state = actual[-1]
        if actual != expected:
            mismatches.append({"cycle": index, "expected": expected, "actual": actual})
            if len(mismatches) == 10:
                break
    return {
        "pass": not mismatches, "checked_cycles": len(rows), "mismatches": mismatches,
        "observed_states": sorted(observed_states),
        "observed_transitions": sorted(observed_transitions),
    }


def parse_metadata(raw: str) -> Dict[str, str]:
    attrs = {}
    for item in raw.split("\x01"):
        if not item or "\x02" not in item:
            continue
        key, value = item.split("\x02", 1)
        attrs[key] = value
    return attrs


def parse_coverage(path: Path) -> Dict[str, dict]:
    points = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("C '"):
            continue
        raw, count_text = line[3:].rsplit("' ", 1)
        points[raw] = {"attrs": parse_metadata(raw), "count": int(count_text)}
    if not points:
        raise ValueError("no native coverage points in %s" % path)
    return points


def merge_coverage(all_points: Iterable[Dict[str, dict]]) -> Dict[str, dict]:
    merged: Dict[str, dict] = {}
    for points in all_points:
        for raw, point in points.items():
            if raw not in merged:
                merged[raw] = {"attrs": point["attrs"], "count": 0}
            merged[raw]["count"] += point["count"]
    return merged


def write_coverage(path: Path, points: Dict[str, dict]) -> None:
    lines = ["# SystemC::Coverage-3"]
    lines.extend("C '%s' %d" % (raw, point["count"]) for raw, point in sorted(points.items()))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def metrics(points: Dict[str, dict]) -> Dict[str, dict]:
    grouped = defaultdict(list)
    for point in points.values():
        grouped[point["attrs"].get("t", "unknown")].append(point)
    result = {}
    for coverage_type in ("line", "branch", "expr", "toggle", "fsm_state", "fsm_arc"):
        items = grouped.get(coverage_type, [])
        if not items:
            result[coverage_type] = {"covered": 0, "total": 0, "pct": None}
            continue
        covered = sum(point["count"] >= int(point["attrs"].get("s", "1")) for point in items)
        result[coverage_type] = {
            "covered": covered, "total": len(items), "pct": round(100.0 * covered / len(items), 2)
        }
    return result


def directed_rounds() -> List[Tuple[str, str, List[Stimulus]]]:
    def values(start: int, strides: Sequence[int], count: int) -> List[Stimulus]:
        output = [(1, start & 0xFFFFFFFF)]
        value = start
        for index in range(count):
            value = (value + strides[index % len(strides)]) & 0xFFFFFFFF
            output.append((1, value))
        return output

    rng = random.Random(0x4D4C3444)
    noise = [(1, rng.getrandbits(32)) for _ in range(40)]
    return [
        ("baseline_reset_idle", "reset, valid=0, zero stride and initial confidence paths",
         [(0, 0xBAADDEAD)] * 6 + [(1, 0)] * 10 + [(0, 0xFFFFFFFF)] * 3),
        ("single_stride_boundaries", "positive/negative legal stride and saturated confidence paths",
         values(100, [1], 16) + values(2000, [15], 16) + values(4000, [-1], 16) + values(8000, [-16], 16)),
        ("double_stride_modes", "both FSM states and alternating positive/negative double strides",
         values(100, [2, 5], 40) + values(5000, [-3, -7], 40)),
        ("overflow_and_wrap", "positive/negative overflow predicates and 32-bit wrap boundaries",
         values(0, [16], 20) + values(1000, [-17], 20) +
         values(0x7FFFFFF0, [0x20, -0x40], 24) + values(0, [0x80000000], 12)),
        ("mode_transitions", "noise to single, single to double, and confidence decrement/relearn paths",
         noise + values(100, [3], 24) + values(1000, [2, 6], 40) + values(9000, [-4], 24)),
        ("valid_gaps_and_edges", "valid gating, sign boundaries and high-bit toggle activity",
         [(index % 3 != 0, value & 0xFFFFFFFF) for index, value in enumerate(
             [0, 1, 0xFFFFFFFF, 0x7FFFFFFF, 0x80000000, 15, 16, 17, 0xFFFFFFF0, 0xFFFFFFEF] * 8
         )]),
        ("feedback_closure", "systematic stride cross-product selected after prior uncovered-point feedback",
         sum((values(0x10000 + i * 1000, [a, b], 14)
              for i, (a, b) in enumerate((
                  (-17, -16), (-16, -1), (-1, 0), (0, 1), (1, 15),
                  (15, 16), (16, -17), (-16, 15), (15, -16)
              ))), [])),
    ]


def missed_points(points: Dict[str, dict], limit: int = 30) -> List[dict]:
    missed = []
    for point in points.values():
        threshold = int(point["attrs"].get("s", "1"))
        if point["count"] >= threshold:
            continue
        attrs = point["attrs"]
        missed.append({
            "type": attrs.get("t", "unknown"),
            "line": int(attrs.get("l", "0") or 0),
            "comment": attrs.get("o", ""),
            "count": point["count"],
        })
    return sorted(missed, key=lambda item: (item["type"], item["line"], item["comment"]))[:limit]


def load_env(path: Path) -> Dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$", line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return values


def llm_round(points: Dict[str, dict], model: str, output_dir: Path, index: int) -> Tuple[List[Stimulus], dict]:
    config = load_env(DEFAULT_ENV)
    api_key = config.get("LOCAL_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = config.get("LOCAL_API_BASE") or os.environ.get("OPENAI_BASE_URL")
    if not api_key or not base_url:
        return [], {"status": "skipped", "reason": "no OpenAI-compatible API configuration"}
    content = ""
    try:
        prompt = """You are generating integer stimuli for the LLM4DV stride_detector RTL.
Input each cycle is valid (0/1) and an unsigned 32-bit value. The detector recognizes
legal signed strides -16..15. Generate exactly 64 cycles aimed at the uncovered native
Verilator structural points below. Return only one complete JSON object:
{\"stimuli\":[[valid,value],...],\"rationale\":\"...\"}. The stimuli array must contain
exactly 64 rows. Use decimal integers, include valid gaps when useful, never claim
coverage, and stop immediately after the closing brace.
Uncovered points: %s""" % json.dumps(missed_points(points), ensure_ascii=False)
        payload = {
            "model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4, "max_tokens": 2500,
        }
        request_path = output_dir / ("llm_request_%02d.json" % index)
        request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
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
                raise RuntimeError("Schannel request failed: %s" % proc.stderr[:500])
            api_response = json.loads(proc.stdout)
            content = api_response["choices"][0]["message"]["content"] or ""
        else:
            from openai import OpenAI
            response = OpenAI(api_key=api_key, base_url=base_url).chat.completions.create(
                model=model, messages=payload["messages"], temperature=0.4
            )
            content = response.choices[0].message.content or ""
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise ValueError("LLM response contains no JSON object")
        parsed = json.loads(match.group(0))
        stimuli = []
        for item in parsed.get("stimuli", []):
            if isinstance(item, list) and len(item) == 2:
                valid, value = int(item[0]), int(item[1])
                stimuli.append((int(bool(valid)), value & 0xFFFFFFFF))
        if not 20 <= len(stimuli) <= 200:
            raise ValueError("LLM returned %d valid stimulus rows" % len(stimuli))
        evidence = {
            "status": "accepted", "model": model, "stimulus_count": len(stimuli),
            "rationale": parsed.get("rationale", ""), "raw_response": content,
        }
        (output_dir / ("llm_response_%02d.json" % index)).write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return stimuli, evidence
    except Exception as exc:
        return [], {"status": "failed", "model": model, "reason": repr(exc), "raw_response": content}


def write_stimulus(path: Path, stimuli: Sequence[Stimulus]) -> None:
    path.write_text("".join("%d %d\n" % item for item in stimuli), encoding="ascii")


def annotate_source(path: Path, points: Dict[str, dict]) -> None:
    by_line = defaultdict(list)
    for point in points.values():
        attrs = point["attrs"]
        if Path(attrs.get("f", "")).name == DUT.name:
            by_line[int(attrs.get("l", "0") or 0)].append(point)
    output = []
    for lineno, source in enumerate(DUT.read_text(encoding="utf-8").splitlines(), 1):
        row = by_line.get(lineno, [])
        if not row:
            output.append("           | %4d | %s" % (lineno, source))
            continue
        covered = sum(item["count"] >= int(item["attrs"].get("s", "1")) for item in row)
        output.append("%3d/%-3d %-3s | %4d | %s" % (
            covered, len(row), "OK" if covered == len(row) else "MISS", lineno, source
        ))
        for item in row:
            attrs = item["attrs"]
            output.append("           |      |   point type=%s count=%d comment=%s" % (
                attrs.get("t", "unknown"), item["count"], attrs.get("o", "")
            ))
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metric_text(metric: dict) -> str:
    if metric["pct"] is None:
        return "N/A"
    return "%d/%d (%.2f%%)" % (metric["covered"], metric["total"], metric["pct"])


def write_report(path: Path, summary: dict) -> None:
    final = summary["final_metrics"]
    observed_fsm = summary["observed_fsm"]
    lines = [
        "# LLM4DV Stride Detector 结构覆盖迭代报告", "",
        "- DUT：`stride_detector/stride_detector.sv`（原始 RTL，无 coverage exclusion）",
        "- 仿真器：Verilator %s" % summary["toolchain"]["verilator_version"],
        "- 真实性判定：**%s**" % ("通过" if summary["coverage_valid"] else "未通过"), "",
        "## 最终结构覆盖率", "",
        "| 指标 | 覆盖结果 | 解释 |", "|---|---:|---|",
        "| 语句/基本块（line） | %s | 每个代码流基本块至少执行一次 |" % metric_text(final["line"]),
        "| 分支（branch） | %s | if/case 的各代码流方向 |" % metric_text(final["branch"]),
        "| 表达式（expr） | %s | 布尔子表达式组合 |" % metric_text(final["expr"]),
        "| 翻转（toggle） | %s | 被插桩信号位 0/1 双向活动 |" % metric_text(final["toggle"]),
        "| 状态覆盖（RTL 采样） | %d/%d (%.2f%%) | 逐周期采样 `stride_2_state_q` 并与参考模型核对 |" % (
            observed_fsm["states"]["covered"], observed_fsm["states"]["total"], observed_fsm["states"]["pct"]),
        "| 状态转移（RTL 采样） | %d/%d (%.2f%%) | 覆盖 FIRST→SECOND 与 SECOND→FIRST |" % (
            observed_fsm["transitions"]["covered"], observed_fsm["transitions"]["total"], observed_fsm["transitions"]["pct"]),
        "| Verilator 原生 FSM 提取 | N/A | 该二值交替寄存器未被保守自动提取器识别 |", "",
        "## 迭代过程", "",
        "| 轮次 | 测试 | 周期 | 新覆盖点 | Line | Branch | Expr | Toggle | FSM state/arc | 判分 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["iterations"]:
        m = row["coverage_after"]
        lines.append("| %d | %s | %d | %d | %s | %s | %s | %s | %s/%s | %s |" % (
            row["iteration"], row["name"], row["cycles"], row["new_covered_points"],
            metric_text(m["line"]), metric_text(m["branch"]), metric_text(m["expr"]),
            metric_text(m["toggle"]), metric_text(m["fsm_state"]), metric_text(m["fsm_arc"]),
            "PASS" if row["scoreboard"]["pass"] else "FAIL",
        ))
    lines.extend([
        "", "## 正确性与真实性", "",
        "- 每轮 RTL 输出均由独立 Python 状态机逐周期核对；失败轮次不会合入 cumulative coverage。",
        "- 覆盖数据库直接由 Verilator 插桩生成，按覆盖点身份合并，不接受 LLM 自报数字。",
        "- 状态覆盖来自 RTL 内部寄存器的真实逐周期采样，不从 LLM 文本或参考模型单方面推断。",
        "- Scoreboard 负向自检：%s；人为篡改一个输出后必须被检测。" % (
            "PASS" if summary["validity_gates"]["scoreboard_mutation_detected"] else "FAIL"
        ),
        "- 原始 RTL SHA-256：`%s`。" % summary["dut_sha256"],
        "- 未应用 coverage exclusion；未命中点仍保留在分母。", "",
        "## 剩余未覆盖点", "",
    ])
    if summary["missed_points"]:
        lines.extend("- `%s` line %s `%s` (count=%s)" % (
            item["type"], item["line"], item["comment"], item["count"]
        ) for item in summary["missed_points"])
    else:
        lines.append("- 无。所有 Verilator 插桩结构覆盖点均已命中。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--llm-model", default="glm-4-flash")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = (args.out_dir or (DEFAULT_OUTPUT_ROOT / ("llm4dv_structural_" + stamp))).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    commands: List[dict] = []
    env = build_simulator(commands, args.rebuild)
    version = run([str(VERILATOR), "--version"], ROOT, env, commands).strip()

    accepted: List[Dict[str, dict]] = []
    iterations = []
    covered_before = 0
    rounds = directed_rounds()
    iteration_index = 0
    llm_insertions = {1, 4, 6} if args.use_llm else set()
    for directed_index, (name, rationale, stimuli) in enumerate(rounds):
        if directed_index in llm_insertions and accepted:
            llm_stimuli, llm_info = llm_round(
                merge_coverage(accepted), args.llm_model, out_dir, iteration_index + 1
            )
            if llm_stimuli:
                rounds_to_run = [("llm_feedback_%d" % (iteration_index + 1), llm_info.get("rationale", ""), llm_stimuli, llm_info)]
            else:
                rounds_to_run = []
                (out_dir / ("llm_response_%02d.json" % (iteration_index + 1))).write_text(
                    json.dumps(llm_info, indent=2, ensure_ascii=False), encoding="utf-8"
                )
        else:
            rounds_to_run = []
        rounds_to_run.append((name, rationale, stimuli, None))

        for round_name, round_rationale, round_stimuli, llm_info in rounds_to_run:
            iteration_index += 1
            case_dir = out_dir / ("iteration_%02d_%s" % (iteration_index, round_name))
            case_dir.mkdir(parents=True, exist_ok=True)
            stimulus_path = case_dir / "stimulus.txt"
            result_path = case_dir / "result.csv"
            coverage_path = case_dir / "coverage.dat"
            write_stimulus(stimulus_path, round_stimuli)
            run([str(SIM), str(stimulus_path), str(result_path), str(coverage_path)], ROOT, env, commands)
            scoreboard = check_results(round_stimuli, result_path)
            case_points = parse_coverage(coverage_path)
            if scoreboard["pass"]:
                accepted.append(case_points)
            cumulative = merge_coverage(accepted)
            current_metrics = metrics(cumulative)
            covered_now = sum(point["count"] > 0 for point in cumulative.values())
            row = {
                "iteration": iteration_index, "name": round_name, "rationale": round_rationale,
                "cycles": len(round_stimuli), "scoreboard": scoreboard,
                "coverage_accepted": scoreboard["pass"],
                "new_covered_points": covered_now - covered_before,
                "coverage_after": current_metrics,
                "llm": llm_info,
                "artifacts": {"stimulus": str(stimulus_path), "result": str(result_path), "coverage": str(coverage_path)},
            }
            iterations.append(row)
            (case_dir / "iteration.json").write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
            covered_before = covered_now
            print("ITER %02d %-28s cycles=%4d new=%3d line=%s branch=%s expr=%s toggle=%s scoreboard=%s" % (
                iteration_index, round_name, len(round_stimuli), row["new_covered_points"],
                metric_text(current_metrics["line"]), metric_text(current_metrics["branch"]),
                metric_text(current_metrics["expr"]), metric_text(current_metrics["toggle"]),
                "PASS" if scoreboard["pass"] else "FAIL",
            ), flush=True)

    final_points = merge_coverage(accepted)
    final_metrics = metrics(final_points)
    merged_path = out_dir / "merged_coverage.dat"
    write_coverage(merged_path, final_points)
    annotate_source(out_dir / "annotated_stride_detector.sv.txt", final_points)

    mutation_detected = False
    if iterations:
        first = Path(iterations[0]["artifacts"]["result"])
        rows = list(csv.DictReader(first.open(encoding="utf-8")))
        if rows:
            rows[0]["stride_1_valid"] = str(1 - int(rows[0]["stride_1_valid"]))
            mutation_file = out_dir / "scoreboard_mutation_result.csv"
            with mutation_file.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            original_stimuli = directed_rounds()[0][2]
            mutation_detected = not check_results(original_stimuli, mutation_file)["pass"]

    all_scoreboards = all(row["scoreboard"]["pass"] for row in iterations)
    observed_states = sorted({state for row in iterations if row["coverage_accepted"]
                              for state in row["scoreboard"].get("observed_states", [])})
    observed_transitions = sorted({transition for row in iterations if row["coverage_accepted"]
                                   for transition in row["scoreboard"].get("observed_transitions", [])})
    observed_fsm_complete = len(observed_states) == 2 and len(observed_transitions) == 2
    summary = {
        "schema": "llm4dv-native-structural-coverage-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "LLM4DV stride_detector original RTL",
        "coverage_valid": all_scoreboards and mutation_detected and bool(final_points) and observed_fsm_complete,
        "dut": str(DUT), "dut_sha256": sha256(DUT),
        "toolchain": {
            "verilator_version": version,
            "verilator_root": str(VERILATOR_ROOT),
            "gxx": str(GXX),
            "gxx_version": run([str(GXX), "--version"], ROOT, env, commands).splitlines()[0],
        },
        "semantics": {
            "line": "Verilator basic-block line coverage (statement/code-flow proxy)",
            "branch": "Verilator branch points inserted by --coverage-line",
            "expr": "Verilator boolean expression combinations",
            "toggle": "Verilator signal-bit toggle coverage",
            "fsm_state": "Verilator experimental native FSM state extraction",
            "fsm_arc": "Verilator experimental native FSM transition extraction",
            "coverage_exclusions_applied": False,
            "failed_iterations_merged": False,
        },
        "validity_gates": {
            "all_rtl_scoreboards_pass": all_scoreboards,
            "scoreboard_mutation_detected": mutation_detected,
            "native_coverage_database_nonempty": bool(final_points),
            "rtl_state_and_transition_coverage_complete": observed_fsm_complete,
        },
        "iterations": iterations,
        "final_metrics": final_metrics,
        "observed_fsm": {
            "states": {"covered": len(observed_states), "total": 2,
                       "pct": round(100.0 * len(observed_states) / 2, 2), "values": observed_states},
            "transitions": {"covered": len(observed_transitions), "total": 2,
                            "pct": round(100.0 * len(observed_transitions) / 2, 2),
                            "values": observed_transitions},
            "source": "cycle-accurate Verilated RTL sampling of stride_2_state_q",
            "native_verilator_fsm_extraction": "N/A",
        },
        "native_point_count": len(final_points),
        "missed_points": missed_points(final_points, limit=100),
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "commands.json").write_text(json.dumps(commands, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(out_dir / "report_zh.md", summary)

    manifest_files = [path for path in out_dir.rglob("*") if path.is_file() and path.name != "evidence_manifest.json"]
    manifest = {
        "root": str(out_dir),
        "files": [{"path": str(path.relative_to(out_dir)), "size": path.stat().st_size, "sha256": sha256(path)}
                  for path in sorted(manifest_files)],
    }
    (out_dir / "evidence_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("FINAL valid=%s report=%s" % (summary["coverage_valid"], out_dir / "report_zh.md"), flush=True)
    return 0 if summary["coverage_valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
