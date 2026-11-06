APPLICATION = source_lsm303dlhc
BOARD ?= nrf52840dk
ifndef RIOTBASE
$(error Supply the frozen RIOTBASE through cross_os_benchmark.py)
endif
include $(CURDIR)/../configuration.mk
CFLAGS += -include $(CURDIR)/../configuration.h
include $(RIOTBASE)/Makefile.include
