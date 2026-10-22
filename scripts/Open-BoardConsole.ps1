param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9@._-]*$')]
    [string]$Bridge = 'radxa@192.168.1.139',
    [ValidatePattern('^/dev/[A-Za-z0-9/_.:-]+$')]
    [string]$Device = '/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_3604098b8d60ec11bc32385f25bfaa52-if00-port0'
)
# Interactive SSH prompts for authentication; no password is stored here.
Write-Host 'FPGA console: 115200 8N1, no flow control. Exit: Ctrl+A then Ctrl+X.'
& ssh -t $Bridge "exec picocom --baud 115200 --flow n --parity n --databits 8 --stopbits 1 --noreset '$Device'"
