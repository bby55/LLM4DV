`timescale 1ns/1ps

module stride_detector_iverilog_tb;
  logic clk_i = 1'b0;
  logic rst_ni = 1'b0;
  logic [31:0] value_i = '0;
  logic valid_i = 1'b0;
  logic [4:0] stride_1_o;
  logic stride_1_valid_o;
  logic [4:0] stride_2_o;
  logic stride_2_valid_o;

  stride_detector dut (.*);
  always #5 clk_i = ~clk_i;

  string stimulus_path;
  string result_path;
  integer stimulus_fd;
  integer result_fd;
  integer valid_value;
  integer scan_result;
  integer cycle = 0;
  reg [31:0] input_value;

  initial begin
    if (!$value$plusargs("STIMULUS=%s", stimulus_path)) $fatal(1, "missing STIMULUS plusarg");
    if (!$value$plusargs("RESULT=%s", result_path)) $fatal(1, "missing RESULT plusarg");
    stimulus_fd = $fopen(stimulus_path, "r");
    result_fd = $fopen(result_path, "w");
    if (!stimulus_fd || !result_fd) $fatal(1, "cannot open replay files");
    $fwrite(result_fd, "cycle,valid,value,stride_1,stride_1_valid,stride_2,stride_2_valid,stride_2_state\n");

    repeat (3) @(posedge clk_i);
    #1 rst_ni = 1'b1;
    while (!$feof(stimulus_fd)) begin
      scan_result = $fscanf(stimulus_fd, "%d %d\n", valid_value, input_value);
      if (scan_result == 2) begin
        valid_i = valid_value != 0;
        value_i = input_value;
        @(posedge clk_i);
        #1;
        $fwrite(result_fd, "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",
                cycle, valid_i, $unsigned(value_i), stride_1_o, stride_1_valid_o,
                stride_2_o, stride_2_valid_o, dut.stride_2_state_q);
        cycle = cycle + 1;
      end
    end
    $fclose(stimulus_fd);
    $fclose(result_fd);
    $finish;
  end
endmodule
