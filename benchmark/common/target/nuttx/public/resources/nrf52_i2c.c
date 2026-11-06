/****************************************************************************
 * arch/arm/src/nrf52/nrf52_i2c.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.  The
 * ASF licenses this file to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance with the
 * License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
 * License for the specific language governing permissions and limitations
 * under the License.
 *
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <string.h>
#include <nuttx/clock.h>
#include <nuttx/semaphore.h>

#ifdef CONFIG_I2C_POLLED
#  error Bounded TWIM requires interrupt mode
#endif

#include <nuttx/debug.h>
#include <nuttx/irq.h>
#include <nuttx/arch.h>
#include <nuttx/mutex.h>
#include <nuttx/kmalloc.h>
#include <arch/board/board.h>

#include "arm_internal.h"
#include "nrf52_gpio.h"
#include "nrf52_i2c.h"

#include "hardware/nrf52_twi.h"
#include "hardware/nrf52_utils.h"

/****************************************************************************
 * Private Types
 ****************************************************************************/

/* I2C Device Private Data */

struct nrf52_i2c_priv_s
{
  const struct i2c_ops_s *ops;      /* Standard I2C operations */
  uint32_t                base;     /* TWI base address */
  uint32_t                scl_pin;  /* SCL pin configuration */
  uint32_t                sda_pin;  /* SDA pin configuration */
  int                     refs;     /* Reference count */
  volatile int            status;   /* I2C transfer status */
  volatile bool           active;
  volatile bool           stopped;
  bool                    quarantined;
  uint32_t                dma_tx[64];
  uint32_t                dma_rx[64];
#ifndef CONFIG_I2C_POLLED
  uint32_t                irq;      /* TWI interrupt */
#endif
  uint8_t                 msgc;     /* Message count */
  struct i2c_msg_s       *msgv;     /* Message list */
  uint8_t                *ptr;      /* Current message buffer */
#ifdef CONFIG_NRF52_I2C_MASTER_COPY_BUF_SIZE
  /* Static buffer used for continued messages */

  uint8_t                 copy_buf[CONFIG_NRF52_I2C_MASTER_COPY_BUF_SIZE];
#endif
  uint32_t                freq;      /* Current I2C frequency */
  int                     dcnt;      /* Current message length */
  uint16_t                flags;     /* Current message flags */
  uint16_t                addr;      /* Current I2C address */
  mutex_t                 lock;      /* Mutual exclusion mutex */
#ifndef CONFIG_I2C_POLLED
  sem_t                   sem_isr;   /* Interrupt wait semaphore */
#endif
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static inline void nrf52_i2c_putreg(struct nrf52_i2c_priv_s *priv,
                                    uint32_t offset,
                                    uint32_t value);
static inline uint32_t nrf52_i2c_getreg(struct nrf52_i2c_priv_s *priv,
                                        uint32_t offset);
static int nrf52_i2c_transfer(struct i2c_master_s *dev,
                              struct i2c_msg_s *msgs,
                              int count);
#ifdef CONFIG_I2C_RESET
static int nrf52_i2c_reset(struct i2c_master_s *dev);
#endif
#ifndef CONFIG_I2C_POLLED
static int nrf52_i2c_isr(int irq, void *context, void *arg);
#endif
static int nrf52_i2c_deinit(struct nrf52_i2c_priv_s *priv);
static int nrf52_i2c_init(struct nrf52_i2c_priv_s *priv);

/****************************************************************************
 * Private Data
 ****************************************************************************/

/* I2C operations */

static const struct i2c_ops_s g_nrf52_i2c_ops =
{
  .transfer = nrf52_i2c_transfer
#ifdef CONFIG_I2C_RESET
  , .reset  = nrf52_i2c_reset
#endif
};

/* I2C0 (TWI0) device */

#ifdef CONFIG_NRF52_I2C0_MASTER

static struct nrf52_i2c_priv_s g_nrf52_i2c0_priv =
{
  .ops     = &g_nrf52_i2c_ops,
  .base    = NRF52_TWIM0_BASE,
  .scl_pin = BOARD_I2C0_SCL_PIN,
  .sda_pin = BOARD_I2C0_SDA_PIN,
  .refs    = 0,
  .lock    = NXMUTEX_INITIALIZER,
#ifndef CONFIG_I2C_POLLED
  .sem_isr = SEM_INITIALIZER(0),
  .irq     = NRF52_IRQ_SPI_TWI_0,
#endif
  .msgc    = 0,
  .msgv    = NULL,
  .ptr     = NULL,
  .freq    = 0,
  .dcnt    = 0,
  .flags   = 0,
  .addr    = 0,
};
#endif

/* I2C1 (TWI1) device */

#ifdef CONFIG_NRF52_I2C1_MASTER
static struct nrf52_i2c_priv_s g_nrf52_i2c1_priv =
{
  .ops     = &g_nrf52_i2c_ops,
  .base    = NRF52_TWIM1_BASE,
  .scl_pin = BOARD_I2C1_SCL_PIN,
  .sda_pin = BOARD_I2C1_SDA_PIN,
  .refs    = 0,
  .lock    = NXMUTEX_INITIALIZER,
#ifndef CONFIG_I2C_POLLED
  .sem_isr = SEM_INITIALIZER(0),
  .irq     = NRF52_IRQ_SPI_TWI_1,
#endif
  .msgc    = 0,
  .msgv    = NULL,
  .ptr     = NULL,
  .freq    = 0,
  .dcnt    = 0,
  .flags   = 0,
  .addr    = 0,
};
#endif

/****************************************************************************
 * Private Functions
 ****************************************************************************/

/****************************************************************************
 * Name: nrf52_i2c_putreg
 *
 * Description:
 *   Put a 32-bit register value by offset
 *
 ****************************************************************************/

static inline void nrf52_i2c_putreg(struct nrf52_i2c_priv_s *priv,
                                    uint32_t offset,
                                    uint32_t value)
{
  putreg32(value, priv->base + offset);
}

/****************************************************************************
 * Name: nrf52_i2c_getreg
 *
 * Description:
 *   Get a 32-bit register value by offset
 *
 ****************************************************************************/

static inline uint32_t nrf52_i2c_getreg(struct nrf52_i2c_priv_s *priv,
                                        uint32_t offset)
{
  return getreg32(priv->base + offset);
}

/****************************************************************************
 * Name: nrf52_i2c_transfer
 *
 * Description:
 *   Generic I2C transfer function
 *
 ****************************************************************************/

/* Generic bounded transport. No sensor address, register or conversion data.
 * DMA uses controller-owned storage, retained until STOPPED. A missing STOPPED
 * quarantines the controller: later callers fail until hardware is quiescent.
 */
#define NRF52_I2C_CAPACITY 256
#define NRF52_I2C_TRANSFER_MS 50
#define NRF52_I2C_ABORT_MS 10

static void nrf52_i2c_clear(struct nrf52_i2c_priv_s *priv)
{
  nrf52_i2c_putreg(priv, NRF52_TWIM_EVENTS_STOPPED_OFFSET, 0);
  nrf52_i2c_putreg(priv, NRF52_TWIM_EVENTS_ERROR_OFFSET, 0);
  nrf52_i2c_putreg(priv, NRF52_TWIM_EVENTS_LASTTX_OFFSET, 0);
  nrf52_i2c_putreg(priv, NRF52_TWIM_EVENTS_LASTRX_OFFSET, 0);
  nrf52_i2c_putreg(priv, NRF52_TWIM_EVENTS_SUSPENDED_OFFSET, 0);
  nrf52_i2c_putreg(priv, NRF52_TWIM_ERRORSRC_OFFSET, 7);
  nxsem_reset(&priv->sem_isr, 0);
}

static void nrf52_i2c_abort(struct nrf52_i2c_priv_s *priv)
{
  irqstate_t flags = enter_critical_section();
  nrf52_i2c_putreg(priv, NRF52_TWIM_INTEN_OFFSET, 0);
  priv->active = false;
  if (nrf52_i2c_getreg(priv, NRF52_TWIM_EVENTS_STOPPED_OFFSET))
    {
      priv->stopped = true;
    }

  if (!priv->stopped)
    {
      nrf52_i2c_putreg(priv, NRF52_TWIM_SHORTS_OFFSET, 0);
      nrf52_i2c_putreg(priv, NRF52_TWIM_TASKS_RESUME_OFFSET, 1);
      nrf52_i2c_putreg(priv, NRF52_TWIM_TASKS_STOP_OFFSET, 1);
    }
  leave_critical_section(flags);

  uint32_t start = (uint32_t)clock_systime_ticks();
  while (!priv->stopped &&
         (uint32_t)((uint32_t)clock_systime_ticks() - start) <
         MSEC2TICK(NRF52_I2C_ABORT_MS))
    {
      if (nrf52_i2c_getreg(priv, NRF52_TWIM_EVENTS_STOPPED_OFFSET))
        {
          priv->stopped = true;
          break;
        }
      up_udelay(10);
    }

  /* Do not disable or reuse a DMA buffer without STOPPED. A physically stuck
   * bus cannot be declared released merely because a software timer expired.
   * These buffers have static lifetime; no caller stack/heap remains exposed.
   */
  priv->quarantined = !priv->stopped;
  if (priv->stopped)
    {
      nrf52_i2c_clear(priv);
    }
}

/* Group adjacent continuation messages. Support native STOP-separated groups,
 * NOSTART scatter/gather, and a repeated-start write group followed by a read
 * group. Other repeated-start shapes are rejected before touching hardware.
 */
static int nrf52_i2c_group(struct i2c_msg_s *msgs, int count, int first,
                           int *end, unsigned *length)
{
  int i = first;
  unsigned total = 0;
  do
    {
      if (msgs[i].addr != msgs[first].addr ||
          msgs[i].frequency != msgs[first].frequency ||
          ((msgs[i].flags ^ msgs[first].flags) & I2C_M_READ))
        {
          return -ENOTSUP;
        }
      total += msgs[i].length;
      if (total > NRF52_I2C_CAPACITY) return -EMSGSIZE;
      i++;
    }
  while (i < count && (msgs[i].flags & I2C_M_NOSTART));
  *end = i;
  *length = total;
  return OK;
}

static int nrf52_i2c_transfer(struct i2c_master_s *dev,
                              struct i2c_msg_s *msgs, int count)
{
  struct nrf52_i2c_priv_s *priv = (struct nrf52_i2c_priv_s *)dev;
  unsigned txlength, rxlength, length;
  int first, end, next, ret;
  bool read, paired;
  uint32_t start, elapsed, frequency, shortcuts;
  uint8_t *tx;
  uint8_t *rx;

  if (!dev || !msgs || count < 1 || count > 255) return -EINVAL;
  tx = (uint8_t *)priv->dma_tx;
  rx = (uint8_t *)priv->dma_rx;
  for (int i = 0; i < count; i++)
    {
      if (msgs[i].length < 0 || msgs[i].addr > 127 ||
          (msgs[i].length && !msgs[i].buffer)) return -EINVAL;
      if (msgs[i].flags & ~(I2C_M_READ | I2C_M_NOSTOP | I2C_M_NOSTART))
        return -ENOTSUP;
      if (msgs[i].frequency != 100000 && msgs[i].frequency != 250000 &&
          msgs[i].frequency != 400000) return -EINVAL;
    }
  if (msgs[0].flags & I2C_M_NOSTART) return -ENOTSUP;

  /* Validate every group before starting the first transfer. */
  for (first = 0; first < count; first = end)
    {
      ret = nrf52_i2c_group(msgs, count, first, &end, &length);
      if (ret < 0) return ret;
      if (msgs[end - 1].flags & I2C_M_NOSTOP)
        {
          if ((msgs[first].flags & I2C_M_READ) || end == count ||
              !(msgs[end].flags & I2C_M_READ) ||
              msgs[end].addr != msgs[first].addr ||
              msgs[end].frequency != msgs[first].frequency) return -ENOTSUP;
          ret = nrf52_i2c_group(msgs, count, end, &next, &length);
          if (ret < 0) return ret;
          if (msgs[next - 1].flags & I2C_M_NOSTOP) return -ENOTSUP;
          end = next;
        }
    }

  ret = nxmutex_timedlock(&priv->lock, 10);
  if (ret < 0) return ret;
  if (priv->quarantined)
    {
      if (!nrf52_i2c_getreg(priv, NRF52_TWIM_EVENTS_STOPPED_OFFSET))
        {
          ret = -EBUSY;
          goto out;
        }
      priv->stopped = true;
      priv->quarantined = false;
      nrf52_i2c_clear(priv);
    }

  start = (uint32_t)clock_systime_ticks();
  for (first = 0; first < count; first = next)
    {
      nrf52_i2c_group(msgs, count, first, &end, &length);
      read = (msgs[first].flags & I2C_M_READ) != 0;
      paired = (msgs[end - 1].flags & I2C_M_NOSTOP) != 0;
      next = end;
      txlength = read ? 0 : length;
      rxlength = read ? length : 0;
      if (paired) nrf52_i2c_group(msgs, count, end, &next, &rxlength);
      if (!read)
        {
          unsigned offset = 0;
          for (int i = first; i < end; i++)
            {
              if (msgs[i].length) memcpy(tx + offset, msgs[i].buffer, msgs[i].length);
              offset += msgs[i].length;
            }
        }

      frequency = msgs[first].frequency == 100000 ? TWIM_FREQUENCY_100KBPS :
                  msgs[first].frequency == 250000 ? TWIM_FREQUENCY_250KBPS :
                                                   TWIM_FREQUENCY_400KBPS;
      elapsed = (uint32_t)((uint32_t)clock_systime_ticks() - start);
      if (elapsed >= MSEC2TICK(NRF52_I2C_TRANSFER_MS))
        {
          ret = -ETIMEDOUT;
          goto out;
        }

      irqstate_t flags = enter_critical_section();
      nrf52_i2c_putreg(priv, NRF52_TWIM_INTEN_OFFSET, 0);
      nrf52_i2c_clear(priv);
      priv->status = OK;
      priv->active = true;
      priv->stopped = false;
      nrf52_i2c_putreg(priv, NRF52_TWIM_FREQUENCY_OFFSET, frequency);
      nrf52_i2c_putreg(priv, NRF52_TWIM_ADDRESS_OFFSET, msgs[first].addr);
      nrf52_i2c_putreg(priv, NRF52_TWIM_TXDPTR_OFFSET, (uint32_t)(uintptr_t)tx);
      nrf52_i2c_putreg(priv, NRF52_TWIM_TXDMAXCNT_OFFSET, txlength);
      nrf52_i2c_putreg(priv, NRF52_TWIM_RXDPTR_OFFSET, (uint32_t)(uintptr_t)rx);
      nrf52_i2c_putreg(priv, NRF52_TWIM_RXDMAXCNT_OFFSET, rxlength);
      shortcuts = paired ? TWIM_SHORTS_LASTTX_STARTRX | TWIM_SHORTS_LASTRX_STOP :
                  read ? TWIM_SHORTS_LASTRX_STOP : TWIM_SHORTS_LASTTX_STOP;
      nrf52_i2c_putreg(priv, NRF52_TWIM_SHORTS_OFFSET, shortcuts);
      nrf52_i2c_putreg(priv, NRF52_TWIM_INTEN_OFFSET, TWIM_INT_STOPPED | TWIM_INT_ERROR);
      nrf52_i2c_putreg(priv, read ? NRF52_TWIM_TASKS_STARTRX_OFFSET :
                                   NRF52_TWIM_TASKS_STARTTX_OFFSET, 1);
      leave_critical_section(flags);

      ret = nxsem_tickwait(&priv->sem_isr,
                           MSEC2TICK(NRF52_I2C_TRANSFER_MS) - elapsed);
      flags = enter_critical_section();
      nrf52_i2c_putreg(priv, NRF52_TWIM_INTEN_OFFSET, 0);
      if (ret == OK) ret = priv->status;
      if (ret == OK && !priv->stopped) ret = -EIO;
      leave_critical_section(flags);
      if (ret < 0)
        {
          nrf52_i2c_abort(priv);
          goto out;
        }
      if ((!read && nrf52_i2c_getreg(priv, NRF52_TWIM_TXDAMOUNT_OFFSET) != txlength) ||
          ((read || paired) && nrf52_i2c_getreg(priv, NRF52_TWIM_RXDAMOUNT_OFFSET) != rxlength))
        {
          ret = -EIO;
          goto out;
        }
      if (read || paired)
        {
          unsigned offset = 0;
          for (int i = read ? first : end; i < next; i++)
            {
              if (msgs[i].length) memcpy(msgs[i].buffer, rx + offset, msgs[i].length);
              offset += msgs[i].length;
            }
        }
    }
out:
  nxmutex_unlock(&priv->lock);
  return ret;
}

/****************************************************************************
 * Name: nrf52_i2c_reset
 *
 * Description:
 *   Perform an I2C bus reset in an attempt to break loose stuck I2C devices.
 *
 * Input Parameters:
 *   dev   - Device-specific state data
 *
 * Returned Value:
 *   Zero (OK) on success; a negated errno value on failure.
 *
 ****************************************************************************/

#ifdef CONFIG_I2C_RESET
static int nrf52_i2c_reset(struct i2c_master_s *dev)
{
#error not implemented
}
#endif

/****************************************************************************
 * Name: nrf52_i2c_isr
 *
 * Description:
 *   Common I2C interrupt service routine
 *
 ****************************************************************************/

#ifndef CONFIG_I2C_POLLED
static int nrf52_i2c_isr(int irq, void *context, void *arg)
{
  struct nrf52_i2c_priv_s *priv = arg;
  bool error = nrf52_i2c_getreg(priv, NRF52_TWIM_EVENTS_ERROR_OFFSET) != 0;
  bool stopped = nrf52_i2c_getreg(priv, NRF52_TWIM_EVENTS_STOPPED_OFFSET) != 0;
  if (error)
    {
      uint32_t source = nrf52_i2c_getreg(priv, NRF52_TWIM_ERRORSRC_OFFSET);
      nrf52_i2c_putreg(priv, NRF52_TWIM_EVENTS_ERROR_OFFSET, 0);
      nrf52_i2c_putreg(priv, NRF52_TWIM_ERRORSRC_OFFSET, 7);
      if (priv->active)
        {
          priv->status = source & TWIM_ERRORSRC_OVERRUN ? -EIO : -ENXIO;
          if (!stopped)
            {
              nrf52_i2c_putreg(priv, NRF52_TWIM_SHORTS_OFFSET, 0);
              nrf52_i2c_putreg(priv, NRF52_TWIM_TASKS_RESUME_OFFSET, 1);
              nrf52_i2c_putreg(priv, NRF52_TWIM_TASKS_STOP_OFFSET, 1);
            }
        }
    }
  if (stopped)
    {
      nrf52_i2c_putreg(priv, NRF52_TWIM_EVENTS_STOPPED_OFFSET, 0);
      if (priv->active)
        {
          priv->stopped = true;
          priv->active = false;
          nxsem_post(&priv->sem_isr);
        }
    }
  return OK;
}
#endif

/****************************************************************************
 * Name: nrf52_i2c_init
 *
 * Description:
 *   Setup the I2C hardware, ready for operation with defaults
 *
 ****************************************************************************/

static int nrf52_i2c_init(struct nrf52_i2c_priv_s *priv)
{
  uint32_t regval = 0;
  int pin         = 0;
  int port        = 0;

  /* Disable TWI interface */

  nrf52_i2c_putreg(priv, NRF52_TWIM_ENABLE_OFFSET, TWIM_ENABLE_DIS);

  /* Configure SCL and SDA pins */

  nrf52_gpio_config(priv->scl_pin);
  nrf52_gpio_config(priv->sda_pin);

  /* Select SCL pin */

  pin  = GPIO_PIN_DECODE(priv->scl_pin);
  port = GPIO_PORT_DECODE(priv->scl_pin);

  regval = (pin << TWIM_PSELSCL_PIN_SHIFT);
  regval |= (port << TWIM_PSELSCL_PORT_SHIFT);
  nrf52_i2c_putreg(priv, NRF52_TWIM_PSELSCL_OFFSET, regval);

  /* Select SDA pin */

  pin  = GPIO_PIN_DECODE(priv->sda_pin);
  port = GPIO_PORT_DECODE(priv->sda_pin);

  regval = (pin << TWIM_PSELSDA_PIN_SHIFT);
  regval |= (port << TWIM_PSELSDA_PORT_SHIFT);
  nrf52_i2c_putreg(priv, NRF52_TWIM_PSELSDA_OFFSET, regval);

  /* Enable TWI interface */

  nrf52_i2c_putreg(priv, NRF52_TWIM_ENABLE_OFFSET, TWIM_ENABLE_EN);

#ifndef CONFIG_I2C_POLLED
  /* Enable I2C interrupts */

  regval = (TWIM_INT_STOPPED | TWIM_INT_ERROR);
  nrf52_i2c_putreg(priv, NRF52_TWIM_INTEN_OFFSET, regval);

  /* Attach error and event interrupts to the ISRs */

  irq_attach(priv->irq, nrf52_i2c_isr, priv);
  up_enable_irq(priv->irq);
#endif

  return OK;
}

/****************************************************************************
 * Name: nrf52_i2c_deinit
 *
 * Description:
 *   Shutdown the I2C hardware
 *
 ****************************************************************************/

static int nrf52_i2c_deinit(struct nrf52_i2c_priv_s *priv)
{
  /* Disable TWI interface */

  nrf52_i2c_putreg(priv, NRF52_TWIM_ENABLE_OFFSET, TWIM_ENABLE_DIS);

  /* Unconfigure GPIO pins */

  nrf52_gpio_unconfig(priv->scl_pin);
  nrf52_gpio_unconfig(priv->sda_pin);

  /* Deatach TWI from GPIO */

  nrf52_i2c_putreg(priv, NRF52_TWIM_PSELSCL_OFFSET, TWIM_PSELSCL_RESET);
  nrf52_i2c_putreg(priv, NRF52_TWIM_PSELSDA_OFFSET, TWIM_PSELSDA_RESET);

  /* Disable and detach interrupts */

#ifndef CONFIG_I2C_POLLED
  up_disable_irq(priv->irq);
  irq_detach(priv->irq);
#endif

  return OK;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

/****************************************************************************
 * Name: nrf52_i2cbus_initialize
 *
 * Description:
 *   Initialize one I2C bus
 *
 ****************************************************************************/

struct i2c_master_s *nrf52_i2cbus_initialize(int port)
{
  struct nrf52_i2c_priv_s *priv = NULL;

  i2cinfo("I2C INITIALIZE port=%d\n", port);

  /* Get interface */

  switch (port)
    {
#ifdef CONFIG_NRF52_I2C0_MASTER
      case 0:
        {
          priv = (struct nrf52_i2c_priv_s *)&g_nrf52_i2c0_priv;
          break;
        }
#endif

#ifdef CONFIG_NRF52_I2C1_MASTER
      case 1:
        {
          priv = (struct nrf52_i2c_priv_s *)&g_nrf52_i2c1_priv;
          break;
        }
#endif

      default:
        {
          return NULL;
        }
    }

  /* Initialize private data for the first time, increment reference count,
   * power-up hardware and configure GPIOs.
   */

  nxmutex_lock(&priv->lock);
  if (priv->refs++ == 0)
    {
      /* Initialize I2C */

      nrf52_i2c_init(priv);
    }

  nxmutex_unlock(&priv->lock);
  return (struct i2c_master_s *)priv;
}

/****************************************************************************
 * Name: nrf52_i2cbus_uninitialize
 *
 * Description:
 *   Uninitialize an I2C bus
 *
 ****************************************************************************/

int nrf52_i2cbus_uninitialize(struct i2c_master_s *dev)
{
  struct nrf52_i2c_priv_s *priv = (struct nrf52_i2c_priv_s *)dev;

  DEBUGASSERT(dev);

  /* Decrement reference count and check for underflow */

  int ret = nxmutex_timedlock(&priv->lock, 10);
  if (ret < 0) return ret;
  if (priv->refs == 0)
    {
      nxmutex_unlock(&priv->lock);
      return ERROR;
    }
  if (priv->quarantined &&
      !nrf52_i2c_getreg(priv, NRF52_TWIM_EVENTS_STOPPED_OFFSET))
    {
      nxmutex_unlock(&priv->lock);
      return -EBUSY;
    }
  priv->quarantined = false;
  if (--priv->refs)
    {
      nxmutex_unlock(&priv->lock);
      return OK;
    }

  /* Disable power and other HW resource (GPIO's) */

  nrf52_i2c_deinit(priv);
  nxmutex_unlock(&priv->lock);

  return OK;
}
