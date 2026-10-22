// Fixed 1280x720p60 timing. AXI VDMA pixels: XRGB8888 (little endian).
// Underrun/corrupt line => black until a new SOF at the next raster origin.
module hdmi_scanout #(
    parameter H_ACTIVE=1280, H_FRONT=110, H_SYNC=40, H_BACK=220,
    parameter V_ACTIVE=720, V_FRONT=5, V_SYNC=5, V_BACK=20
)(
    (* X_INTERFACE_PARAMETER="ASSOCIATED_BUSIF S_AXIS, ASSOCIATED_RESET resetn" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:clock:1.0 pixel_clk CLK" *) input pixel_clk,
    (* X_INTERFACE_PARAMETER="POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO="xilinx.com:signal:reset:1.0 resetn RST" *) input resetn,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TDATA" *) input [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TVALID" *) input s_axis_tvalid,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TREADY" *) output s_axis_tready,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TUSER" *) input s_axis_tuser,
    (* X_INTERFACE_INFO="xilinx.com:interface:axis:1.0 S_AXIS TLAST" *) input s_axis_tlast,
    output reg [23:0] rgb, // Digilent RGB2DVI bus order: R,B,G
    output reg de, output reg hsync, output reg vsync
);
    localparam H_TOTAL=H_ACTIVE+H_FRONT+H_SYNC+H_BACK;
    localparam V_TOTAL=V_ACTIVE+V_FRONT+V_SYNC+V_BACK;
    reg [12:0] x=0, y=0;
    reg aligned=0;
    wire active=(x<H_ACTIVE && y<V_ACTIVE);
    wire origin=(x==0 && y==0);
    wire start_frame=origin && s_axis_tvalid && s_axis_tuser;
    wire consume_pixel=active && (aligned || start_frame);
    assign s_axis_tready=resetn && (consume_pixel || (!aligned && !s_axis_tuser));
    wire valid_pixel=consume_pixel && s_axis_tvalid &&
        (s_axis_tuser==origin) && (s_axis_tlast==(x==H_ACTIVE-1));
    always @(posedge pixel_clk) begin
        if (!resetn) begin
            x<=0; y<=0; aligned<=0; rgb<=0; de<=0; hsync<=0; vsync<=0;
        end else begin
            if (x==H_TOTAL-1) begin
                x<=0;
                if (y==V_TOTAL-1) y<=0; else y<=y+1;
            end else x<=x+1;
            de<=active;
            hsync<=(x>=H_ACTIVE+H_FRONT && x<H_ACTIVE+H_FRONT+H_SYNC);
            vsync<=(y>=V_ACTIVE+V_FRONT && y<V_ACTIVE+V_FRONT+V_SYNC);
            rgb<=valid_pixel ? {s_axis_tdata[23:16],s_axis_tdata[7:0],s_axis_tdata[15:8]} : 24'b0;
            if (origin) aligned<=start_frame && valid_pixel;
            else if (active && (!s_axis_tvalid || !valid_pixel)) aligned<=0;
            if (x==H_ACTIVE-1 && y==V_ACTIVE-1) aligned<=0;
        end
    end
endmodule
