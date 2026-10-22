// SPDX-License-Identifier: GPL-2.0
// Fixed-mode framebuffer for the project's PL VDMA -> scanout -> RGB2DVI.
// One uncached framebuffer; Xorg fbdev writes it while VDMA scans it out.
#include <linux/clk.h>
#include <linux/dma-mapping.h>
#include <linux/fb.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>

#define WIDTH 1280
#define HEIGHT 720
#define STRIDE (WIDTH * 4)
#define SIZE (STRIDE * HEIGHT)
#define CONTROL 0x00
#define STATUS 0x04
#define PARK 0x28
#define VSIZE 0x50
#define HSIZE 0x54
#define FRMDLY_STRIDE 0x58
#define FRAME_ADDR 0x5c

struct ec26e_fb {
	struct fb_info *info;
	void __iomem *regs;
	struct clk *axi, *pixel;
	dma_addr_t address;
	u32 palette[16];
};

static int ec26e_check_var(struct fb_var_screeninfo *var, struct fb_info *info)
{
	if (var->xres != WIDTH || var->yres != HEIGHT ||
	    var->xres_virtual != WIDTH || var->yres_virtual != HEIGHT ||
	    var->bits_per_pixel != 32 || var->xoffset || var->yoffset)
		return -EINVAL;
	*var = info->var;
	return 0;
}

static int ec26e_setcolreg(unsigned int regno, unsigned int red,
		unsigned int green, unsigned int blue, unsigned int transp,
		struct fb_info *info)
{
	struct ec26e_fb *fb = info->par;
	if (regno >= 16)
		return -EINVAL;
	fb->palette[regno] = ((red >> 8) << 16) | ((green >> 8) << 8) | (blue >> 8);
	return 0;
}

static int ec26e_mmap(struct fb_info *info, struct vm_area_struct *vma)
{
	struct ec26e_fb *fb = info->par;
	return dma_mmap_wc(info->device, vma, info->screen_base, fb->address, SIZE);
}

static struct fb_ops ec26e_ops = {
	.owner = THIS_MODULE,
	.fb_check_var = ec26e_check_var,
	.fb_setcolreg = ec26e_setcolreg,
	.fb_read = fb_sys_read,
	.fb_write = fb_sys_write,
	.fb_fillrect = sys_fillrect,
	.fb_copyarea = sys_copyarea,
	.fb_imageblit = sys_imageblit,
	.fb_mmap = ec26e_mmap,
};

static int ec26e_reset(struct ec26e_fb *fb)
{
	u32 value;
	writel(4, fb->regs + CONTROL);
	return readl_poll_timeout(fb->regs + CONTROL, value, !(value & 4), 100, 100000);
}

static int ec26e_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct fb_info *info;
	struct ec26e_fb *fb;
	int error;

	error = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (error)
		return error;
	info = framebuffer_alloc(sizeof(*fb), dev);
	if (!info)
		return -ENOMEM;
	fb = info->par;
	fb->info = info;
	fb->regs = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(fb->regs)) {
		error = PTR_ERR(fb->regs);
		goto release;
	}
	fb->axi = devm_clk_get(dev, "axi");
	fb->pixel = devm_clk_get(dev, "pixel");
	if (IS_ERR(fb->axi) || IS_ERR(fb->pixel)) {
		error = IS_ERR(fb->axi) ? PTR_ERR(fb->axi) : PTR_ERR(fb->pixel);
		goto release;
	}
	error = clk_prepare_enable(fb->axi);
	if (error)
		goto release;
	error = clk_prepare_enable(fb->pixel);
	if (error)
		goto disable_axi;
	error = ec26e_reset(fb);
	if (error)
		goto disable_pixel;
	info->screen_base = dma_alloc_wc(dev, SIZE, &fb->address, GFP_KERNEL);
	if (!info->screen_base) {
		error = -ENOMEM;
		goto disable_pixel;
	}
	memset(info->screen_base, 0, SIZE);
	info->screen_size = SIZE;
	strscpy(info->fix.id, "ec26e-hdmi", sizeof(info->fix.id));
	info->fix.smem_start = fb->address;
	info->fix.smem_len = SIZE;
	info->fix.type = FB_TYPE_PACKED_PIXELS;
	info->fix.visual = FB_VISUAL_TRUECOLOR;
	info->fix.line_length = STRIDE;
	info->fix.accel = FB_ACCEL_NONE;
	info->var.xres = info->var.xres_virtual = WIDTH;
	info->var.yres = info->var.yres_virtual = HEIGHT;
	info->var.bits_per_pixel = 32;
	info->var.red.offset = 16; info->var.red.length = 8;
	info->var.green.offset = 8; info->var.green.length = 8;
	info->var.blue.offset = 0; info->var.blue.length = 8;
	info->var.pixclock = 13468;
	info->var.left_margin = 220; info->var.right_margin = 110;
	info->var.upper_margin = 20; info->var.lower_margin = 5;
	info->var.hsync_len = 40; info->var.vsync_len = 5;
	info->var.sync = FB_SYNC_HOR_HIGH_ACT | FB_SYNC_VERT_HIGH_ACT;
	info->var.vmode = FB_VMODE_NONINTERLACED;
	info->fbops = &ec26e_ops;
	info->pseudo_palette = fb->palette;
	info->flags = FBINFO_DEFAULT;
	error = fb_alloc_cmap(&info->cmap, 16, 0);
	if (error)
		goto free_dma;
	// Circular mode, one store, internal/free-running frame synchronization.
	writel(0xffffffff, fb->regs + STATUS);
	writel(0, fb->regs + PARK);
	writel(3, fb->regs + CONTROL);
	writel(lower_32_bits(fb->address), fb->regs + FRAME_ADDR);
	writel(STRIDE, fb->regs + FRMDLY_STRIDE);
	writel(STRIDE, fb->regs + HSIZE);
	writel(HEIGHT, fb->regs + VSIZE); // Writing VSIZE commits/starts the transfer.
	error = register_framebuffer(info);
	if (error) {
		// Never free memory if the engine cannot be stopped.
		if (ec26e_reset(fb)) {
			dev_err(dev, "VDMA reset failed; framebuffer retained until reboot\n");
			goto dealloc_cmap;
		}
		goto dealloc_free;
	}
	platform_set_drvdata(pdev, info);
	dev_info(dev, "fb%d: PL HDMI 1280x720 XRGB8888, VDMA at %pad\n", info->node, &fb->address);
	return 0;

dealloc_free:
	fb_dealloc_cmap(&info->cmap);
free_dma:
	dma_free_wc(dev, SIZE, info->screen_base, fb->address);
	goto disable_pixel;
dealloc_cmap:
	fb_dealloc_cmap(&info->cmap);
disable_pixel:
	clk_disable_unprepare(fb->pixel);
disable_axi:
	clk_disable_unprepare(fb->axi);
release:
	framebuffer_release(info);
	return error;
}

// The fixed PL design has no runtime hotplug/unload path. Build into the board
// kernel or load once at boot; retain its framebuffer for the device lifetime.
static const struct of_device_id ec26e_matches[] = {
	{ .compatible = "ec26e,hdmi-fb" }, { }
};
MODULE_DEVICE_TABLE(of, ec26e_matches);
static struct platform_driver ec26e_driver = {
	.probe = ec26e_probe,
	.driver = { .name = "ec26e-hdmi-fb", .of_match_table = ec26e_matches,
		    .suppress_bind_attrs = true },
};
// No module_exit: users cannot unload/reconfigure a live scanout framebuffer.
static int __init ec26e_init(void) { return platform_driver_register(&ec26e_driver); }
module_init(ec26e_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("EC26E fixed-mode PL HDMI framebuffer");
