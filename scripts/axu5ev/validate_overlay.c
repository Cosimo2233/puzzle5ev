/* Offline only: check libfdt overlay fixups against the prepared base DT. */
#include <stdio.h>
#include <stdlib.h>
#include <libfdt.h>

static void *read_blob(const char *name, long *size)
{
    FILE *f = fopen(name, "rb");
    void *data;
    if (!f || fseek(f, 0, SEEK_END) || (*size = ftell(f)) < 40 || *size > 16*1024*1024)
        return NULL;
    rewind(f);
    data = malloc(*size);
    if (!data || fread(data, 1, *size, f) != (size_t)*size) { fclose(f); free(data); return NULL; }
    fclose(f);
    if (fdt_check_header(data) || fdt_totalsize(data) > *size) { free(data); return NULL; }
    return data;
}

int main(int argc, char **argv)
{
    long base_size, overlay_size;
    void *base, *overlay, *merged;
    int error;
    FILE *out;
    if (argc != 4) return 2;
    base = read_blob(argv[1], &base_size); overlay = read_blob(argv[2], &overlay_size);
    if (!base || !overlay) return 3;
    merged = malloc(base_size + overlay_size + 65536);
    if (!merged) return 4;
    error = fdt_open_into(base, merged, base_size + overlay_size + 65536);
    if (!error) error = fdt_overlay_apply(merged, overlay);
    if (error) { fprintf(stderr, "%s\n", fdt_strerror(error)); return 5; }
    if (fdt_path_offset(merged, "/amba/vision@a0000000") < 0 ||
        fdt_path_offset(merged, "/amba/framebuffer@a0040000") < 0) return 6;
    fdt_pack(merged);
    out = fopen(argv[3], "wb");
    if (!out || fwrite(merged, 1, fdt_totalsize(merged), out) != fdt_totalsize(merged)) return 7;
    fclose(out); free(base); free(overlay); free(merged);
    puts("PASS: offline overlay application; no live device tree modified");
    return 0;
}
