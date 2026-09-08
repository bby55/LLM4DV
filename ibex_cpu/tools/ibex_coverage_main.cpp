#include "Vibex_coverage_top.h"
#include "verilated.h"
#include "verilated_cov.h"

#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <deque>
#include <sstream>
#include <string>
#include <vector>

double sc_time_stamp() { return 0.0; }

namespace {
constexpr uint32_t kProgramBase = 0x00100080u;
constexpr uint32_t kDefaultInsn = 0x0000006fu;  // jal x0, 0

uint32_t sign_extend(uint32_t value, unsigned bits) {
  const uint32_t sign = 1u << (bits - 1u);
  return (value ^ sign) - sign;
}

uint32_t jal_imm(uint32_t insn) {
  uint32_t imm = ((insn >> 31) & 1u) << 20;
  imm |= ((insn >> 12) & 0xffu) << 12;
  imm |= ((insn >> 20) & 1u) << 11;
  imm |= ((insn >> 21) & 0x3ffu) << 1;
  return sign_extend(imm, 21);
}

uint32_t branch_imm(uint32_t insn) {
  uint32_t imm = ((insn >> 31) & 1u) << 12;
  imm |= ((insn >> 7) & 1u) << 11;
  imm |= ((insn >> 25) & 0x3fu) << 5;
  imm |= ((insn >> 8) & 0xfu) << 1;
  return sign_extend(imm, 13);
}

bool check_next_pc(const Vibex_coverage_top& top, std::string& error) {
  if (top.rvfi_trap_o || top.rvfi_intr_o) return true;
  const uint32_t insn = top.rvfi_insn_o;
  const uint32_t pc = top.rvfi_pc_rdata_o;
  const uint32_t actual = top.rvfi_pc_wdata_o;
  const uint32_t opcode = insn & 0x7fu;
  uint32_t expected = pc + ((insn & 3u) == 3u ? 4u : 2u);

  if (opcode == 0x6fu) {
    expected = pc + jal_imm(insn);
  } else if (opcode == 0x67u) {
    expected = (top.rvfi_rs1_rdata_o + sign_extend(insn >> 20, 12)) & ~1u;
  } else if (opcode == 0x63u) {
    const uint32_t a = top.rvfi_rs1_rdata_o;
    const uint32_t b = top.rvfi_rs2_rdata_o;
    bool taken = false;
    switch ((insn >> 12) & 7u) {
      case 0: taken = a == b; break;
      case 1: taken = a != b; break;
      case 4: taken = static_cast<int32_t>(a) < static_cast<int32_t>(b); break;
      case 5: taken = static_cast<int32_t>(a) >= static_cast<int32_t>(b); break;
      case 6: taken = a < b; break;
      case 7: taken = a >= b; break;
      default: return true;
    }
    if (taken) expected = pc + branch_imm(insn);
  } else if (opcode == 0x73u || opcode == 0x0fu) {
    return true;
  }

  if (expected != actual) {
    std::ostringstream os;
    os << "PC mismatch at 0x" << std::hex << pc << ": expected 0x" << expected
       << ", got 0x" << actual << ", insn 0x" << insn;
    error = os.str();
    return false;
  }
  return true;
}

bool load_words(const std::string& path, std::map<uint32_t, uint32_t>& mem) {
  std::ifstream input(path);
  if (!input) return false;
  std::string line;
  uint32_t address = kProgramBase;
  while (std::getline(input, line)) {
    const auto hash = line.find('#');
    if (hash != std::string::npos) line.resize(hash);
    std::istringstream is(line);
    std::string token;
    if (!(is >> token)) continue;
    try {
      const uint32_t word = static_cast<uint32_t>(std::stoul(token, nullptr, 0));
      mem[address] = word;
      address += 4;
    } catch (...) {
      std::cerr << "Invalid stimulus word: " << token << "\n";
      return false;
    }
  }
  return !mem.empty();
}

uint32_t read_word(const std::map<uint32_t, uint32_t>& mem, uint32_t address,
                   uint32_t fallback) {
  const auto it = mem.find(address);
  return it == mem.end() ? fallback : it->second;
}

void apply_write(std::map<uint32_t, uint32_t>& mem, uint32_t address,
                 uint32_t value, uint8_t be) {
  uint32_t old = read_word(mem, address, 0u);
  for (unsigned byte = 0; byte < 4; ++byte) {
    if (be & (1u << byte)) {
      const uint32_t mask = 0xffu << (8u * byte);
      old = (old & ~mask) | (value & mask);
    }
  }
  mem[address] = old;
}
}  // namespace

int main(int argc, char** argv) {
  if (argc < 5) {
    std::cerr << "usage: ibex_cov STIMULUS TRACE COVERAGE MAX_CYCLES [FSM_TRACE] [SCENARIO]\n";
    return 2;
  }
  const std::string stimulus_path = argv[1];
  const std::string trace_path = argv[2];
  const std::string coverage_path = argv[3];
  const uint64_t max_cycles = std::stoull(argv[4]);
  const std::string fsm_trace_path = argc > 5 ? argv[5] : trace_path + ".fsm.jsonl";
  const std::string scenario = argc > 6 ? argv[6] : "normal";

  std::map<uint32_t, uint32_t> imem;
  std::map<uint32_t, uint32_t> dmem;
  if (!load_words(stimulus_path, imem)) {
    std::cerr << "Unable to load stimulus " << stimulus_path << "\n";
    return 2;
  }
  if (scenario == "debug" || scenario == "mixed") {
    const auto program = imem;
    for (const auto& item : program) {
      imem[0x00100000u + (item.first - kProgramBase)] = item.second;
    }
  }

  VerilatedContext context;
  context.commandArgs(argc, argv);
  context.randReset(0);
  Vibex_coverage_top top{&context};
  std::ofstream trace(trace_path);
  std::ofstream fsm_trace(fsm_trace_path);
  if (!trace || !fsm_trace) return 2;

  top.clk_i = 0;
  // Start deasserted so the first loop iteration creates a real reset edge.
  top.rst_ni = 1;
  top.instr_gnt_i = 0;
  top.instr_rvalid_i = 0;
  top.instr_rdata_i = 0;
  top.data_gnt_i = 0;
  top.data_rvalid_i = 0;
  top.data_rdata_i = 0;
  top.irq_software_i = 0;
  top.irq_timer_i = 0;
  top.irq_external_i = 0;
  top.irq_fast_i = 0;
  top.irq_nm_i = 0;
  top.debug_req_i = 0;
  top.eval();

  struct Response {
    uint64_t due;
    uint32_t address;
  };
  std::deque<Response> instr_responses;
  std::deque<Response> data_responses;
  uint64_t retired = 0;
  uint64_t traps = 0;
  uint64_t semantic_checks = 0;
  uint64_t expected_order = 0;
  bool have_order = false;
  bool healthy = true;
  std::string failure;

  for (uint64_t cycle = 0; cycle < max_cycles; ++cycle) {
    const bool slow_data = scenario == "data_stall" || scenario == "mixed";
    const unsigned data_latency = scenario == "mixed"
                                      ? static_cast<unsigned>(3u - (cycle % 3u))
                                      : (slow_data ? 3u : 1u);
    top.rst_ni = cycle >= 4;
    // The targeted variants are reactive environment stimuli. They use only
    // the observed RTL controller state and are recorded in the FSM trace;
    // coverage is still emitted exclusively by Verilator at runtime.
    const bool first_fetch_irq = scenario == "irq_first_fetch" &&
                                 top.fsm_ctrl_state_o == 4;
    top.irq_nm_i = (scenario == "irq" && cycle >= 120 && cycle < 124) || first_fetch_irq;
    top.debug_req_i = scenario == "debug" && cycle >= 140 && cycle < 144;
    top.instr_rvalid_i = !instr_responses.empty() && instr_responses.front().due <= cycle;
    top.instr_rdata_i = top.instr_rvalid_i
                            ? read_word(imem, instr_responses.front().address, kDefaultInsn)
                            : 0u;
    top.data_rvalid_i = !data_responses.empty() && data_responses.front().due <= cycle;
    top.data_rdata_i = top.data_rvalid_i
                           ? read_word(dmem, data_responses.front().address, 0u)
                           : 0u;
    top.clk_i = 0;
    top.eval();

    // Request debug only when the unmodified RTL has already selected the
    // DECODE->FLUSH next state. The registered priority bit then makes the
    // following real FLUSH cycle take FLUSH->DBG_TAKEN_IF.
    const bool debug_flush = scenario == "debug_flush" &&
                             top.fsm_ctrl_state_o == 5 && top.fsm_ctrl_next_o == 6;
    if (debug_flush) {
      top.debug_req_i = 1;
      top.eval();
    }

    const bool accept_instr = top.instr_req_o;
    const uint32_t accept_instr_addr = top.instr_addr_o;
    const bool grant_data = top.data_req_o && (!slow_data || (cycle % 3u != 0u));
    const bool accept_data = grant_data;
    const uint32_t accept_data_addr = top.data_addr_o;
    const bool accept_data_write = accept_data && top.data_we_o;
    const uint32_t accept_data_value = top.data_wdata_o;
    const uint8_t accept_data_be = top.data_be_o;
    top.instr_gnt_i = accept_instr;
    top.data_gnt_i = grant_data;
    top.eval();

    top.clk_i = 1;
    top.eval();
    context.timeInc(1);

    if (top.rst_ni && top.rvfi_valid_o) {
      ++retired;
      if (top.rvfi_trap_o) ++traps;
      if (have_order && top.rvfi_order_o != expected_order) {
        healthy = false;
        std::ostringstream os;
        os << "RVFI order mismatch: expected " << expected_order << ", got "
           << top.rvfi_order_o;
        failure = os.str();
      }
      expected_order = top.rvfi_order_o + 1;
      have_order = true;
      if (top.rvfi_rd_addr_o == 0 && top.rvfi_rd_wdata_o != 0) {
        healthy = false;
        failure = "RVFI x0 write data was non-zero";
      }
      std::string pc_error;
      if (!check_next_pc(top, pc_error)) {
        healthy = false;
        failure = pc_error;
      } else {
        ++semantic_checks;
      }
      trace << "{\"cycle\":" << cycle << ",\"order\":" << top.rvfi_order_o
            << ",\"pc\":\"0x" << std::hex << std::setw(8) << std::setfill('0')
            << top.rvfi_pc_rdata_o << "\",\"next_pc\":\"0x" << std::setw(8)
            << top.rvfi_pc_wdata_o << "\",\"insn\":\"0x" << std::setw(8)
            << top.rvfi_insn_o << "\",\"rd\":" << std::dec
            << static_cast<unsigned>(top.rvfi_rd_addr_o) << ",\"rd_wdata\":\"0x"
            << std::hex << std::setw(8) << top.rvfi_rd_wdata_o << "\",\"trap\":"
            << std::dec << static_cast<unsigned>(top.rvfi_trap_o)
            << ",\"intr\":" << static_cast<unsigned>(top.rvfi_intr_o) << "}\n";
    }

    if (top.rst_ni) {
      fsm_trace << "{\"cycle\":" << cycle
                << ",\"controller\":" << static_cast<unsigned>(top.fsm_ctrl_state_o)
                << ",\"id_ex\":" << static_cast<unsigned>(top.fsm_id_state_o)
                << ",\"load_store\":" << static_cast<unsigned>(top.fsm_lsu_state_o)
                << ",\"multdiv\":" << static_cast<unsigned>(top.fsm_md_state_o)
                << ",\"multiplier\":" << static_cast<unsigned>(top.fsm_mult_state_o)
                << "}\n";
    }

    if (accept_data_write) {
      apply_write(dmem, accept_data_addr, accept_data_value, accept_data_be);
    }
    if (top.instr_rvalid_i) instr_responses.pop_front();
    if (top.data_rvalid_i) data_responses.pop_front();
    if (accept_instr) instr_responses.push_back({cycle + 1u, accept_instr_addr});
    if (accept_data) data_responses.push_back({cycle + data_latency, accept_data_addr});

    top.clk_i = 0;
    top.eval();
    context.timeInc(1);
  }

  top.final();
  VerilatedCov::write(coverage_path.c_str());
  std::cout << "{\"cycles\":" << max_cycles << ",\"retired\":" << retired
            << ",\"traps\":" << traps << ",\"semantic_checks\":"
            << semantic_checks << ",\"healthy\":" << (healthy ? "true" : "false")
            << ",\"failure\":\"" << failure << "\"}\n";
  if (retired == 0) return 3;
  return healthy ? 0 : 4;
}
