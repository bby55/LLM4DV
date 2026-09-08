#include "Vstride_detector.h"
#include "Vstride_detector___024root.h"
#include "verilated.h"
#include "verilated_cov.h"

#include <cstdint>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>

namespace {

void eval_cycle(Vstride_detector& dut, VerilatedContext& context) {
    dut.clk_i = 0;
    dut.eval();
    context.timeInc(5);
    dut.clk_i = 1;
    dut.eval();
    context.timeInc(5);
}

}  // namespace

double sc_time_stamp() { return 0.0; }

int main(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "usage: stride_detector_cov_sim <stimulus.txt> <result.csv> <coverage.dat>\n";
        return 2;
    }

    VerilatedContext context;
    context.commandArgs(argc, argv);
    auto dut = std::make_unique<Vstride_detector>(&context);

    dut->valid_i = 0;
    dut->value_i = 0;
    dut->rst_ni = 0;
    for (int cycle = 0; cycle < 3; ++cycle) {
        eval_cycle(*dut, context);
    }
    dut->rst_ni = 1;

    std::ifstream stimulus(argv[1]);
    std::ofstream result(argv[2]);
    if (!stimulus || !result) {
        std::cerr << "cannot open stimulus or result file\n";
        return 3;
    }

    result << "cycle,valid,value,stride_1,stride_1_valid,stride_2,stride_2_valid,stride_2_state\n";
    unsigned valid = 0;
    std::uint64_t value = 0;
    std::uint64_t cycle = 0;
    while (stimulus >> valid >> value) {
        dut->valid_i = valid != 0;
        dut->value_i = static_cast<std::uint32_t>(value);
        eval_cycle(*dut, context);
        result << cycle++ << ',' << valid << ','
               << static_cast<std::uint32_t>(value) << ','
               << static_cast<unsigned>(dut->stride_1_o) << ','
               << static_cast<unsigned>(dut->stride_1_valid_o) << ','
               << static_cast<unsigned>(dut->stride_2_o) << ','
               << static_cast<unsigned>(dut->stride_2_valid_o) << ','
               << static_cast<unsigned>(dut->rootp->stride_detector__DOT__stride_2_state_q) << '\n';
    }

    dut->valid_i = 0;
    dut->rst_ni = 0;
    eval_cycle(*dut, context);
    dut->final();
    VerilatedCov::write(argv[3]);
    return 0;
}
