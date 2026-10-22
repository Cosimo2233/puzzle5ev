// ADV7511 24-bit RGB output. Existing scanout uses Digilent's R,B,G ordering.
module axu5ev_scanout(
    (* X_INTERFACE_PARAMETER="ASSOCIATED_BUSIF S_AXIS, ASSOCIATED_RESET resetn" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 pixel_clk CLK" *) input pixel_clk,
    (* X_INTERFACE_PARAMETER="POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 resetn RST" *) input resetn,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TDATA" *) input [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input s_axis_tvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output s_axis_tready,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TUSER" *) input s_axis_tuser,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input s_axis_tlast,
    output [23:0] hdmi_out_data,
    output hdmi_out_de, hdmi_out_hs, hdmi_out_vs, hdmi_out_clk
);
    wire [23:0] rbg;
    hdmi_scanout scan(
        .pixel_clk(pixel_clk), .resetn(resetn),
        .s_axis_tdata(s_axis_tdata), .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready), .s_axis_tuser(s_axis_tuser),
        .s_axis_tlast(s_axis_tlast), .rgb(rbg),
        .de(hdmi_out_de), .hsync(hdmi_out_hs), .vsync(hdmi_out_vs)
    );
    assign hdmi_out_data = {rbg[23:16], rbg[7:0], rbg[15:8]};
    // Forward an inverted pixel clock: ADV7511 samples half a cycle after
    // scanout updates RGB/DE/sync. Do not route a fabric inverter as a clock.
    ODDRE1 #(.SIM_DEVICE("ULTRASCALE_PLUS")) clock_out(
        .C(pixel_clk), .D1(1'b0), .D2(1'b1), .SR(1'b0), .Q(hdmi_out_clk)
    );
endmodule
