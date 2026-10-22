`timescale 1ns/1ps
// Direct AXI-Lite RTL regression, independent of the HLS UVM polling agent.
module test_clearance_scan_axi;
parameter MAX_CASES=1;
reg clk=0, resetn=0;
always #5 clk=~clk;
reg awvalid=0,wvalid=0,arvalid=0,rready=0,bready=0;
reg [13:0] awaddr=0,araddr=0;
reg [31:0] wdata=0;
wire awready,wready,arready,rvalid,bvalid,interrupt;
wire [31:0] rdata;
wire [1:0] rresp,bresp;
puzzle_clearance_scan dut(
 .ap_clk(clk),.ap_rst_n(resetn),
 .s_axi_control_AWVALID(awvalid),.s_axi_control_AWREADY(awready),.s_axi_control_AWADDR(awaddr),
 .s_axi_control_WVALID(wvalid),.s_axi_control_WREADY(wready),.s_axi_control_WDATA(wdata),.s_axi_control_WSTRB(4'hf),
 .s_axi_control_ARVALID(arvalid),.s_axi_control_ARREADY(arready),.s_axi_control_ARADDR(araddr),
 .s_axi_control_RVALID(rvalid),.s_axi_control_RREADY(rready),.s_axi_control_RDATA(rdata),.s_axi_control_RRESP(rresp),
 .s_axi_control_BVALID(bvalid),.s_axi_control_BREADY(bready),.s_axi_control_BRESP(bresp),.interrupt(interrupt));

task automatic write_reg(input [13:0] address,input [31:0] value);
 begin
  @(negedge clk);awaddr=address;awvalid=1;
  do @(posedge clk); while(!awready);
  @(negedge clk);awvalid=0;wdata=value;wvalid=1;
  do @(posedge clk); while(!wready);
  @(negedge clk);wvalid=0;
  // Exercise response backpressure, too.
  repeat(3) @(negedge clk);
  bready=1;
  do @(posedge clk); while(!bvalid);
  if(bresp!==0)$fatal(1,"AXI write response");
  @(negedge clk);bready=0;
 end
endtask
task automatic read_reg(input [13:0] address,output [31:0] value);
 begin
  @(negedge clk);araddr=address;arvalid=1;
  do @(posedge clk); while(!arready);
  @(negedge clk);arvalid=0;
  repeat(2) @(negedge clk);
  rready=1;
  do @(posedge clk); while(!rvalid);
  value=rdata;
  if(rresp!==0)$fatal(1,"AXI read response");
  @(negedge clk);rready=0;
 end
endtask
task automatic run_scan(input [31:0] request,input [31:0] expected_status);
 reg [31:0] value;
 integer polls;
 begin
  write_reg('h10,request);write_reg(0,1);
  value=0;polls=0;
  while(!(value&2) && polls<1000)begin
   repeat(1000) @(posedge clk);
   read_reg(0,value);polls=polls+1;
  end
  if(!(value&2))$fatal(1,"scan timeout");
  read_reg('h28,value);if(value!=='h43440101)$fatal(1,"ABI");
  read_reg('h20,value);if(value!==request)$fatal(1,"completion ID");
  read_reg('h30,value);if(value!==expected_status)$fatal(1,"status");
 end
endtask
integer file_handle,cases,c,i,rc;
reg [31:0] value,low,high;
initial begin
 file_handle=$fopen("axi_vectors.txt","r");
 if(!file_handle)$fatal(1,"vectors missing");
 rc=$fscanf(file_handle,"%d",cases);
 repeat(10) @(negedge clk);resetn=1;
 for(c=0;c<cases && c<MAX_CASES;c=c+1)begin
  for(i=0;i<76;i=i+1)begin
   rc=$fscanf(file_handle,"%h",value);if(rc!=1)$fatal(1,"input EOF");
   write_reg('h1000+4*i,value);
  end
  run_scan(c+1,0);
  for(i=0;i<401;i=i+1)begin
   rc=$fscanf(file_handle,"%h %h",low,high);if(rc!=2)$fatal(1,"output EOF");
   read_reg('h2000+4*i,value);
   // Positive IEEE-754 values preserve unsigned integer ordering.
   if((^value===1'bx) || value<low || value>high)
    $fatal(1,"distance case=%0d sample=%0d got=%h interval=%h,%h",c,i,value,low,high);
  end
  $display("RTL_SCAN_PASS %0d",c);
 end
 write_reg('h1000,'h40000000);run_scan(100,1); // n=2
 write_reg('h1000,'h40800000);write_reg('h1030,'h7fc00000);run_scan(101,1); // NaN
 run_scan(0,1);
 $display("AXI_RTL_PASS %0d scans and 3 invalid requests",c);
 $finish;
end
initial begin #100000000; $fatal(1,"global timeout"); end
endmodule
