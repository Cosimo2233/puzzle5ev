`timescale 1ns/1ps
module test_hdmi_scanout;
    reg clk=0, resetn=0;
    always #5 clk=~clk;
    reg [31:0] data=32'h00123456;
    reg valid=1, user=1, last=0;
    wire ready, de, hs, vs;
    wire [23:0] rgb;
    hdmi_scanout #(.H_ACTIVE(4),.H_FRONT(1),.H_SYNC(1),.H_BACK(2),
      .V_ACTIVE(3),.V_FRONT(1),.V_SYNC(1),.V_BACK(1)) dut(
      .pixel_clk(clk),.resetn(resetn),.s_axis_tdata(data),.s_axis_tvalid(valid),
      .s_axis_tready(ready),.s_axis_tuser(user),.s_axis_tlast(last),
      .rgb(rgb),.de(de),.hsync(hs),.vsync(vs));
    integer index=0, cycle=0, visible=0, frames=0, black=0;
    integer x=0, y=0;
    reg held=0;
    reg [31:0] held_data;
    reg held_user, held_last;
    reg expect_de, expect_hs, expect_vs;
    reg [23:0] expect_rgb;
    always @(posedge clk) begin
      if (resetn) begin
        expect_de=(x<4 && y<3);
        expect_hs=(x==5); expect_vs=(y==4);
        expect_rgb=dut.valid_pixel ? 24'h125634 : 0;
        if (valid && ready) index=(index+1)%12;
        if (dut.valid_pixel) visible=visible+1;
        else if (expect_de) black=black+1;
        if (x==0 && y==0) frames=frames+1;
        #1;
        if (de!==expect_de || hs!==expect_hs || vs!==expect_vs || rgb!==expect_rgb)
          $fatal(1,"raster/color mismatch at x=%0d y=%0d",x,y);
        if (x==7) begin x=0; y=(y+1)%6; end else x=x+1;
        cycle=cycle+1;
      end
    end
    always @(negedge clk) begin
      if (resetn) begin
        // Drop one beat in the third frame, then restore an aligned stream.
        valid=!(cycle==99);
        user=(index==0); last=(index%4==3);
        if (valid && !ready && held && (data!==held_data || user!==held_user || last!==held_last))
          $fatal(1,"test source changed a held beat");
        held=valid && !ready;
        held_data=data; held_user=user; held_last=last;
      end
    end
    initial begin
      repeat(3) @(negedge clk);
      resetn=1;
      repeat(240) @(negedge clk);
      if (frames<4 || visible<36 || black==0)
        $fatal(1,"missing complete frames or underrun/recovery coverage");
      $display("PASS: raster, RGB order, frame alignment, blanking and underrun recovery (%0d pixels)",visible);
      $finish;
    end
endmodule
