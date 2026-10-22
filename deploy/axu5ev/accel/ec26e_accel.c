// SPDX-License-Identifier: GPL-2.0
// Fixed AXU5EV vision DMA. Buffers remain allocated for the device lifetime;
// no module_exit/unbind because a failed DMA must never access freed memory.
#include <linux/dma-mapping.h>
#include <linux/clk.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/sched/signal.h>
#include <linux/uaccess.h>

#define PIXELS (1280 * 720)
#define TX_SIZE (PIXELS * 4)
#define RX_SIZE PIXELS
struct ec26e_frame { u32 width, height, ratio, blue, brightness, id; };
#define EC26E_SUBMIT _IOW('E', 1, struct ec26e_frame)

struct ec26e_accel {
	struct miscdevice misc;
	struct device *dev;
	void __iomem *dma, *core;
	void *tx, *rx;
	dma_addr_t tx_addr, rx_addr;
	struct mutex lock;
	atomic_t opened;
	bool failed;
};

static void ec26e_disable_clock(void *clock)
{
	clk_disable_unprepare(clock);
}

static int ec26e_reset(struct ec26e_accel *a)
{
	u32 v;
	writel(4, a->dma);
	return readl_poll_timeout(a->dma, v, !(v & 4), 100, 1000000);
}

static int ec26e_open(struct inode *inode, struct file *file)
{
	struct ec26e_accel *a = container_of(file->private_data, struct ec26e_accel, misc);
	if (atomic_cmpxchg(&a->opened, 0, 1))
		return -EBUSY;
	if (a->failed) {
		atomic_set(&a->opened, 0);
		return -EIO;
	}
	file->private_data = a;
	return 0;
}

static int ec26e_release(struct inode *inode, struct file *file)
{
	struct ec26e_accel *a = file->private_data;
	atomic_set(&a->opened, 0);
	return 0;
}

static int ec26e_mmap(struct file *file, struct vm_area_struct *vma)
{
	struct ec26e_accel *a = file->private_data;
	unsigned long selector = vma->vm_pgoff;
	size_t size = selector ? RX_SIZE : TX_SIZE;
	if (selector > 1 || vma->vm_end - vma->vm_start != PAGE_ALIGN(size))
		return -EINVAL;
	vma->vm_pgoff = 0;
	return dma_mmap_coherent(a->dev, vma, selector ? a->rx : a->tx,
				selector ? a->rx_addr : a->tx_addr, size);
}

static long ec26e_ioctl(struct file *file, unsigned int command, unsigned long arg)
{
	struct ec26e_accel *a = file->private_data;
	struct ec26e_frame p;
	u32 count, status, rx_status;
	bool done = false;
	int error = 0;
	unsigned long deadline;
	if (command != EC26E_SUBMIT)
		return -ENOTTY;
	if (copy_from_user(&p, (void __user *)arg, sizeof(p)))
		return -EFAULT;
	if (!p.width || p.width > 1280 || !p.height || p.height > 720 ||
	    !p.id || p.id == 0xffffffff || p.blue > 255 || p.brightness > 255 || p.ratio > 65535)
		return -EINVAL;
	if (mutex_lock_interruptible(&a->lock))
		return -ERESTARTSYS;
	if (a->failed) { error = -EIO; goto out; }
	if (!(readl(a->core) & 4)) { error = -EBUSY; goto fault; }
	count = p.width * p.height;
	writel(p.width, a->core + 0x10);
	writel(p.height, a->core + 0x18);
	writel(p.ratio, a->core + 0x20);
	writel(p.blue, a->core + 0x28);
	writel(p.brightness, a->core + 0x30);
	writel(p.id, a->core + 0x38);
	dma_wmb();
	writel(0x7000, a->dma + 0x04);
	writel(0x7000, a->dma + 0x34);
	writel(1, a->dma + 0x30);
	writel(lower_32_bits(a->rx_addr), a->dma + 0x48);
	writel(count, a->dma + 0x58);
	writel(1, a->core);
	writel(1, a->dma);
	writel(lower_32_bits(a->tx_addr), a->dma + 0x18);
	writel(count * 4, a->dma + 0x28);
	deadline = jiffies + msecs_to_jiffies(5000);
	for (;;) {
		done |= !!(readl(a->core) & 2);
		status = readl(a->dma + 0x04);
		rx_status = readl(a->dma + 0x34);
		if ((status | rx_status) & 0x70) { error = -EIO; goto fault; }
		if (done && (status & 2) && (rx_status & 2)) break;
		if (time_after_eq(jiffies, deadline)) { error = -ETIMEDOUT; goto fault; }
		if (signal_pending(current)) { error = -EINTR; goto fault; }
		usleep_range(500, 1000);
	}
	if (readl(a->core + 0x40) != p.id) { error = -EPROTO; goto fault; }
	dma_rmb();
	goto out;
fault:
	a->failed = true;
	if (ec26e_reset(a))
		dev_err(a->dev, "DMA reset failed; buffers retained until reboot\n");
out:
	mutex_unlock(&a->lock);
	return error;
}

static const struct file_operations ec26e_ops = {
	.owner = THIS_MODULE, .open = ec26e_open, .release = ec26e_release,
	.mmap = ec26e_mmap, .unlocked_ioctl = ec26e_ioctl,
	.llseek = no_llseek,
};

static int ec26e_probe(struct platform_device *pdev)
{
	struct ec26e_accel *a;
	struct clk *clock;
	int error;
	clock = devm_clk_get(&pdev->dev, "axi");
	if (IS_ERR(clock)) return PTR_ERR(clock);
	error = clk_prepare_enable(clock);
	if (error) return error;
	error = devm_add_action_or_reset(&pdev->dev, ec26e_disable_clock, clock);
	if (error) return error;
	error = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (error) return error;
	a = devm_kzalloc(&pdev->dev, sizeof(*a), GFP_KERNEL);
	if (!a) return -ENOMEM;
	a->dev = &pdev->dev;
	a->dma = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(a->dma)) return PTR_ERR(a->dma);
	a->core = devm_platform_ioremap_resource(pdev, 1);
	if (IS_ERR(a->core)) return PTR_ERR(a->core);
	error = ec26e_reset(a);
	if (error) return error;
	a->tx = dma_alloc_coherent(a->dev, TX_SIZE, &a->tx_addr, GFP_KERNEL);
	if (!a->tx) return -ENOMEM;
	a->rx = dma_alloc_coherent(a->dev, RX_SIZE, &a->rx_addr, GFP_KERNEL);
	if (!a->rx) {
		dma_free_coherent(a->dev, TX_SIZE, a->tx, a->tx_addr);
		return -ENOMEM;
	}
	mutex_init(&a->lock);
	atomic_set(&a->opened, 0);
	a->misc.minor = MISC_DYNAMIC_MINOR;
	a->misc.name = "ec26e-vision";
	a->misc.fops = &ec26e_ops;
	a->misc.parent = a->dev;
	a->misc.mode = 0600;
	error = misc_register(&a->misc);
	if (error) {
		dma_free_coherent(a->dev, RX_SIZE, a->rx, a->rx_addr);
		dma_free_coherent(a->dev, TX_SIZE, a->tx, a->tx_addr);
	}
	return error;
}

static const struct of_device_id ec26e_ids[] = {
	{ .compatible = "ec26e,axu5ev-vision" }, { }
};
MODULE_DEVICE_TABLE(of, ec26e_ids);
static struct platform_driver ec26e_driver = {
	.probe = ec26e_probe,
	.driver = { .name = "ec26e-vision", .of_match_table = ec26e_ids,
		.suppress_bind_attrs = true },
};
static int __init ec26e_init(void) { return platform_driver_register(&ec26e_driver); }
module_init(ec26e_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("AXU5EV bounded exclusive vision DMA");
