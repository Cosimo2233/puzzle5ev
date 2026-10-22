obj-m += ec26e_accel.o
KERNEL_SRC ?= /lib/modules/$(shell uname -r)/build
all:
	$(MAKE) -C $(KERNEL_SRC) M=$(CURDIR) modules
