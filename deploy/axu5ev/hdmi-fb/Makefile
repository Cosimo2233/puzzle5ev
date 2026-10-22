obj-m += ec26e_hdmi_fb.o
KERNEL_SRC ?= /lib/modules/$(shell uname -r)/build
all:
	$(MAKE) -C $(KERNEL_SRC) M=$(CURDIR) modules
clean:
	$(MAKE) -C $(KERNEL_SRC) M=$(CURDIR) clean
