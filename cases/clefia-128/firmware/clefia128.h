#ifndef CLEFIA128_H
#define CLEFIA128_H
#include <stdint.h>
typedef struct { uint32_t wk[4]; uint32_t rk[36]; } clefia128_ctx;
void clefia128_set_key(clefia128_ctx *ctx, const uint8_t key[16]);
void clefia128_encrypt(const clefia128_ctx *ctx, const uint8_t in[16], uint8_t out[16]);
int clefia128_selftest(void);
#endif
