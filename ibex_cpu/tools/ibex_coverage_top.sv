// Verification-only top exposing Ibex RVFI signals to the C++ harness.
module ibex_coverage_top import ibex_pkg::*; (
  input  logic        clk_i,
  input  logic        rst_ni,

  output logic        instr_req_o,
  input  logic        instr_gnt_i,
  input  logic        instr_rvalid_i,
  output logic [31:0] instr_addr_o,
  input  logic [31:0] instr_rdata_i,

  output logic        data_req_o,
  input  logic        data_gnt_i,
  input  logic        data_rvalid_i,
  output logic        data_we_o,
  output logic [3:0]  data_be_o,
  output logic [31:0] data_addr_o,
  output logic [31:0] data_wdata_o,
  input  logic [31:0] data_rdata_i,

  input  logic        irq_software_i,
  input  logic        irq_timer_i,
  input  logic        irq_external_i,
  input  logic [14:0] irq_fast_i,
  input  logic        irq_nm_i,
  input  logic        debug_req_i,

  output logic        rvfi_valid_o,
  output logic [63:0] rvfi_order_o,
  output logic [31:0] rvfi_insn_o,
  output logic        rvfi_trap_o,
  output logic        rvfi_halt_o,
  output logic        rvfi_intr_o,
  output logic [4:0]  rvfi_rs1_addr_o,
  output logic [4:0]  rvfi_rs2_addr_o,
  output logic [31:0] rvfi_rs1_rdata_o,
  output logic [31:0] rvfi_rs2_rdata_o,
  output logic [4:0]  rvfi_rd_addr_o,
  output logic [31:0] rvfi_rd_wdata_o,
  output logic [31:0] rvfi_pc_rdata_o,
  output logic [31:0] rvfi_pc_wdata_o,
  output logic [31:0] rvfi_mem_addr_o,
  output logic [3:0]  rvfi_mem_rmask_o,
  output logic [3:0]  rvfi_mem_wmask_o,
  output logic [31:0] rvfi_mem_rdata_o,
  output logic [31:0] rvfi_mem_wdata_o,

  output logic [3:0]  fsm_ctrl_state_o,
  output logic [3:0]  fsm_ctrl_next_o,
  output logic        fsm_id_state_o,
  output logic [2:0]  fsm_lsu_state_o,
  output logic [2:0]  fsm_md_state_o,
  output logic [1:0]  fsm_mult_state_o
);
  ibex_top_tracing #(
    .PMPEnable       (1'b0),
    .MHPMCounterNum  (0),
    .RV32E           (1'b0),
    .RV32M           (RV32MFast),
    .RV32B           (RV32BNone),
    .RegFile         (RegFileFF),
    .BranchTargetALU (1'b0),
    .WritebackStage  (1'b0),
    .ICache          (1'b0),
    .ICacheECC       (1'b0),
    .BranchPredictor (1'b0),
    .DbgTriggerEn    (1'b0),
    .SecureIbex      (1'b0),
    .ICacheScramble  (1'b0),
    .DmHaltAddr      (32'h0010_0000),
    .DmExceptionAddr (32'h0010_0000)
  ) dut (
    .clk_i,
    .rst_ni,
    .test_en_i              (1'b0),
    .scan_rst_ni            (1'b1),
    .ram_cfg_i              ('0),
    .hart_id_i              (32'b0),
    .boot_addr_i            (32'h0010_0000),
    .instr_req_o,
    .instr_gnt_i,
    .instr_rvalid_i,
    .instr_addr_o,
    .instr_rdata_i,
    .instr_rdata_intg_i     ('0),
    .instr_err_i            (1'b0),
    .data_req_o,
    .data_gnt_i,
    .data_rvalid_i,
    .data_we_o,
    .data_be_o,
    .data_addr_o,
    .data_wdata_o,
    .data_wdata_intg_o      (),
    .data_rdata_i,
    .data_rdata_intg_i      ('0),
    .data_err_i             (1'b0),
    .irq_software_i,
    .irq_timer_i,
    .irq_external_i,
    .irq_fast_i,
    .irq_nm_i,
    .scramble_key_valid_i   (1'b0),
    .scramble_key_i         ('0),
    .scramble_nonce_i       ('0),
    .scramble_req_o         (),
    .debug_req_i,
    .crash_dump_o           (),
    .double_fault_seen_o    (),
    .fetch_enable_i         (IbexMuBiOn),
    .alert_minor_o          (),
    .alert_major_internal_o (),
    .alert_major_bus_o      (),
    .core_sleep_o           ()
  );

  assign rvfi_valid_o     = dut.rvfi_valid;
  assign rvfi_order_o     = dut.rvfi_order;
  assign rvfi_insn_o      = dut.rvfi_insn;
  assign rvfi_trap_o      = dut.rvfi_trap;
  assign rvfi_halt_o      = dut.rvfi_halt;
  assign rvfi_intr_o      = dut.rvfi_intr;
  assign rvfi_rs1_addr_o  = dut.rvfi_rs1_addr;
  assign rvfi_rs2_addr_o  = dut.rvfi_rs2_addr;
  assign rvfi_rs1_rdata_o = dut.rvfi_rs1_rdata;
  assign rvfi_rs2_rdata_o = dut.rvfi_rs2_rdata;
  assign rvfi_rd_addr_o   = dut.rvfi_rd_addr;
  assign rvfi_rd_wdata_o  = dut.rvfi_rd_wdata;
  assign rvfi_pc_rdata_o  = dut.rvfi_pc_rdata;
  assign rvfi_pc_wdata_o  = dut.rvfi_pc_wdata;
  assign rvfi_mem_addr_o  = dut.rvfi_mem_addr;
  assign rvfi_mem_rmask_o = dut.rvfi_mem_rmask;
  assign rvfi_mem_wmask_o = dut.rvfi_mem_wmask;
  assign rvfi_mem_rdata_o = dut.rvfi_mem_rdata;
  assign rvfi_mem_wdata_o = dut.rvfi_mem_wdata;

  assign fsm_ctrl_state_o =
      dut.u_ibex_top.u_ibex_core.id_stage_i.controller_i.ctrl_fsm_cs;
  assign fsm_ctrl_next_o =
      dut.u_ibex_top.u_ibex_core.id_stage_i.controller_i.ctrl_fsm_ns;
  assign fsm_id_state_o = dut.u_ibex_top.u_ibex_core.id_stage_i.id_fsm_q;
  assign fsm_lsu_state_o = dut.u_ibex_top.u_ibex_core.load_store_unit_i.ls_fsm_cs;
  assign fsm_md_state_o =
      dut.u_ibex_top.u_ibex_core.ex_block_i.gen_multdiv_fast.multdiv_i.md_state_q;
  assign fsm_mult_state_o =
      dut.u_ibex_top.u_ibex_core.ex_block_i.gen_multdiv_fast.multdiv_i.gen_mult_fast.mult_state_q;
endmodule
