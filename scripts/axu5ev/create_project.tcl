# Saved AXU5EV project is authoritative after initial creation.
try {
set root [file normalize [file dirname [info script]]/../..]
set project_dir $root
set project_file $project_dir/puzzle5ev.xpr
set mode [expr {$argc ? [lindex $argv 0] : "project"}]
set part xczu5ev-sfvc784-2-i
source $root/scripts/axu5ev/check_hls_abi.tcl
set repos {}
foreach name {preprocess rigid clearance candidate} {
    set repo $root/build/axu5ev/hls_$name/solution/impl/ip
    if {![file exists $repo/component.xml]} {error "Missing AXU5EV HLS IP: $repo"}
    lappend repos $repo
}
if {[file exists $project_file]} {
    open_project $project_file
    if {[get_property PART [current_project]] ne $part} {error "Unexpected saved project part"}
    set_property ip_repo_paths $repos [current_project]
    update_ip_catalog
    open_bd_design [get_files system.bd]
} else {
    create_project puzzle5ev $project_dir -part $part
    set_property ip_repo_paths $repos [current_project]
    update_ip_catalog
    add_files [list $root/src/rtl/hdmi_scanout.v $root/src/rtl/axu5ev_scanout.v]
    add_files -fileset constrs_1 $root/constraints/axu5ev/hdmi.xdc
    create_bd_design system
    set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0]
    source $root/scripts/axu5ev/ps_preset.tcl
    set_property -dict [list CONFIG.PSU__USE__M_AXI_GP0 {1} CONFIG.PSU__USE__M_AXI_GP1 {0} \
        CONFIG.PSU__USE__M_AXI_GP2 {0} CONFIG.PSU__USE__S_AXI_GP2 {1} \
        CONFIG.PSU__USE__S_AXI_GP3 {0} CONFIG.PSU__GPIO_EMIO__PERIPHERAL__ENABLE {0} \
        CONFIG.PSU__I2C0__PERIPHERAL__ENABLE {0} CONFIG.PSU__FPGA_PL1_ENABLE {0} \
        CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} CONFIG.PSU__USE__IRQ0 {1}] $ps

    set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
    set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_sg_length_width {23} \
        CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {8} \
        CONFIG.c_include_mm2s_dre {1} CONFIG.c_include_s2mm_dre {1} CONFIG.c_addr_width {32}] $dma
    foreach name {puzzle_preprocess puzzle_rigid_fit puzzle_clearance_scan} {
        create_bd_cell -type ip -vlnv xilinx.com:hls:$name:1.0 ${name}_0
    }
    connect_bd_intf_net [get_bd_intf_pins $dma/M_AXIS_MM2S] [get_bd_intf_pins puzzle_preprocess_0/src]
    connect_bd_intf_net [get_bd_intf_pins puzzle_preprocess_0/dst] [get_bd_intf_pins $dma/S_AXIS_S2MM]
    set vdma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma:6.3 hdmi_vdma]
    set_property -dict [list CONFIG.c_include_s2mm {0} CONFIG.c_num_fstores {1} \
        CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_m_axi_mm2s_data_width {64} \
        CONFIG.c_mm2s_linebuffer_depth {4096} CONFIG.c_use_mm2s_fsync {0} \
        CONFIG.c_mm2s_genlock_mode {0} CONFIG.c_addr_width {32}] $vdma
    set iic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_iic:2.1 hdmi_iic]
    make_bd_intf_pins_external [get_bd_intf_pins $iic/IIC]
    set_property name hdmi_out_i2c [get_bd_intf_ports IIC_0]
    foreach intf {axi_dma_0/S_AXI_LITE puzzle_preprocess_0/s_axi_control puzzle_rigid_fit_0/s_axi_control puzzle_clearance_scan_0/s_axi_control hdmi_vdma/S_AXI_LITE hdmi_iic/S_AXI} {
        apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master "/zynq_ultra_ps_e_0/M_AXI_HPM0_FPD" Clk "/zynq_ultra_ps_e_0/pl_clk0 (100 MHz)"} [get_bd_intf_pins $intf]
    }
    set reset_pin [get_bd_pins -of_objects [get_bd_nets -of_objects [get_bd_pins axi_dma_0/axi_resetn]] -filter {DIR == O}]
    set memory [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 dma_memory]
    set_property -dict [list CONFIG.NUM_SI {3} CONFIG.NUM_MI {1}] $memory
    foreach intf {axi_dma_0/M_AXI_MM2S axi_dma_0/M_AXI_S2MM hdmi_vdma/M_AXI_MM2S} idx {00 01 02} {
        connect_bd_intf_net [get_bd_intf_pins $intf] [get_bd_intf_pins $memory/S${idx}_AXI]
    }
    connect_bd_intf_net [get_bd_intf_pins $memory/M00_AXI] [get_bd_intf_pins $ps/S_AXI_HP0_FPD]
    foreach pin {dma_memory/aclk zynq_ultra_ps_e_0/saxihp0_fpd_aclk axi_dma_0/m_axi_mm2s_aclk axi_dma_0/m_axi_s2mm_aclk hdmi_vdma/m_axi_mm2s_aclk} {
        connect_bd_net [get_bd_pins $ps/pl_clk0] [get_bd_pins $pin]
    }
    connect_bd_net $reset_pin [get_bd_pins $memory/aresetn]

    set clocks [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 hdmi_clocks]
    set input_mhz [get_property CONFIG.PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ $ps]
    set_property -dict [list CONFIG.PRIM_SOURCE {No_buffer} CONFIG.PRIM_IN_FREQ $input_mhz \
        CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {74.250} CONFIG.RESET_TYPE {ACTIVE_LOW}] $clocks
    connect_bd_net [get_bd_pins $ps/pl_clk0] [get_bd_pins $clocks/clk_in1]
    connect_bd_net $reset_pin [get_bd_pins $clocks/resetn]
    set vr [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 hdmi_reset]
    set_property CONFIG.C_EXT_RESET_HIGH 0 $vr
    connect_bd_net [get_bd_pins $clocks/clk_out1] [get_bd_pins $vr/slowest_sync_clk] [get_bd_pins $vdma/m_axis_mm2s_aclk]
    connect_bd_net [get_bd_pins $clocks/locked] [get_bd_pins $vr/dcm_locked]
    connect_bd_net [get_bd_pins $ps/pl_resetn0] [get_bd_pins $vr/ext_reset_in]
    set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero]
    set_property CONFIG.CONST_VAL 0 $zero
    connect_bd_net [get_bd_pins $zero/dout] [get_bd_pins $vr/mb_debug_sys_rst]
    set scan [create_bd_cell -type module -reference axu5ev_scanout hdmi_scanout_0]
    connect_bd_intf_net [get_bd_intf_pins $vdma/M_AXIS_MM2S] [get_bd_intf_pins $scan/S_AXIS]
    connect_bd_net [get_bd_pins $clocks/clk_out1] [get_bd_pins $scan/pixel_clk]
    connect_bd_net [get_bd_pins $vr/peripheral_aresetn] [get_bd_pins $scan/resetn]
    foreach {port width} {hdmi_out_data 24 hdmi_out_de 1 hdmi_out_hs 1 hdmi_out_vs 1 hdmi_out_clk 1} {
        if {$width == 1} {create_bd_port -dir O $port} else {create_bd_port -dir O -from 23 -to 0 $port}
        connect_bd_net [get_bd_ports $port] [get_bd_pins $scan/$port]
    }
    set irq [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 irq_concat]
    set_property CONFIG.NUM_PORTS 8 $irq
    foreach pin {axi_dma_0/mm2s_introut axi_dma_0/s2mm_introut hdmi_vdma/mm2s_introut hdmi_iic/iic2intc_irpt zero/dout zero/dout zero/dout zero/dout} idx {0 1 2 3 4 5 6 7} {
        connect_bd_net [get_bd_pins $pin] [get_bd_pins $irq/In$idx]
    }
    connect_bd_net [get_bd_pins $irq/dout] [get_bd_pins $ps/pl_ps_irq0]
    assign_bd_address
    foreach {cell addr} {axi_dma_0 0xA0000000 puzzle_preprocess_0 0xA0010000 puzzle_rigid_fit_0 0xA0020000 puzzle_clearance_scan_0 0xA0030000 hdmi_vdma 0xA0040000 hdmi_iic 0xA0050000} {
        set seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces $ps/Data] -filter "NAME =~ *${cell}*Reg"]
        if {[llength $seg] != 1} {error "Ambiguous address segment for $cell: $seg"}
        set_property range 64K $seg
        set_property offset $addr $seg
    }
}
# Independent concave-capable batch distance core. Migrate the saved BD without
# replacing existing PS, DMA, HDMI or accelerator connections/address segments.
if {![llength [get_bd_cells -quiet puzzle_candidate_scan_0]]} {
    create_bd_cell -type ip -vlnv xilinx.com:hls:puzzle_candidate_scan:1.0 puzzle_candidate_scan_0
    apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master "/zynq_ultra_ps_e_0/M_AXI_HPM0_FPD" Clk "/zynq_ultra_ps_e_0/pl_clk0 (100 MHz)"} [get_bd_intf_pins puzzle_candidate_scan_0/s_axi_control]
    assign_bd_address
    set candidate_seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces zynq_ultra_ps_e_0/Data] -filter {NAME =~ *puzzle_candidate_scan_0*Reg}]
    if {[llength $candidate_seg] != 1} {error "Ambiguous candidate scan address"}
    set_property range 64K $candidate_seg
    set_property offset 0xA0060000 $candidate_seg
}
# Preserve cooling when replacing the factory PL; FAN_PWM is active-high.
if {![llength [get_bd_ports -quiet fan_pwm]]} {
    set fan [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 fan_on]
    set_property CONFIG.CONST_VAL 1 $fan
    create_bd_port -dir O fan_pwm
    connect_bd_net [get_bd_ports fan_pwm] [get_bd_pins $fan/dout]
}
# proc_sys_reset's auxiliary reset is ACTIVE_LOW by default. Hold it HIGH
# when unused; tying it to zero leaves the entire HDMI raster in reset.
# Apply this migration to saved BD files as well as new project creation.
set_property CONFIG.C_AUX_RESET_HIGH 0 [get_bd_cells hdmi_reset]
set aux [get_bd_pins hdmi_reset/aux_reset_in]
set high_net [get_bd_nets -of_objects [get_bd_pins fan_on/dout]]
set old_net [get_bd_nets -quiet -of_objects $aux]
if {$old_net ne $high_net} {
    if {[llength $old_net]} {disconnect_bd_net $old_net $aux}
    connect_bd_net [get_bd_pins fan_on/dout] $aux
}
if {[get_property CONFIG.CONST_VAL [get_bd_cells fan_on]] != 1 ||
    [get_property CONFIG.C_AUX_RESET_HIGH [get_bd_cells hdmi_reset]] != 0} {
    error "HDMI auxiliary reset must be inactive"
}
save_bd_design
validate_bd_design
generate_target all [get_files system.bd]
set wrapper [make_wrapper -files [get_files system.bd] -top]
add_files -norecurse $wrapper
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
if {$mode in {all compile}} {
    reset_run synth_1
    launch_runs synth_1 -jobs 4
    wait_on_run synth_1
    if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {error "Synthesis failed"}
    launch_runs impl_1 -to_step write_bitstream -jobs 4
    wait_on_run impl_1
    if {[get_property STATUS [get_runs impl_1]] ne "write_bitstream Complete!"} {error "Implementation failed"}
    open_run impl_1
    set out $root/artifacts/axu5ev
    file mkdir $out
    report_timing_summary -file $out/timing.rpt
    report_utilization -file $out/utilization.rpt
    report_drc -file $out/drc.rpt
    report_methodology -file $out/methodology.rpt
    check_timing -verbose -file $out/check_timing.rpt
    foreach delay {max min} {
        set paths [get_timing_paths -delay_type $delay -max_paths 1 -quiet]
        if {![llength $paths] || [get_property SLACK [lindex $paths 0]] < 0} {error "Timing failed ($delay)"}
    }
    write_hw_platform -include_bit -fixed -force $out/puzzle5ev.xsa
    file copy -force $project_dir/puzzle5ev.runs/impl_1/system_wrapper.bit $out/puzzle5ev.bit
    file copy -force $project_dir/puzzle5ev.gen/sources_1/bd/system/hw_handoff/system.hwh $out/puzzle5ev.hwh
}
close_project
} on error {message options} {
    puts stderr [dict get $options -errorinfo]
    exit 1
}
