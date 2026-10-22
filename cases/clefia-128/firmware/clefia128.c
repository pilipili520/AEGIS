/* CLEFIA-128 implementation following RFC 6114, sections 4-6.
 * Deliberately unmasked for controlled side-channel experiments. */
#include "clefia128.h"
#include "tables.h"
static uint32_t load_be(const uint8_t *p) {
    return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];
}
static void store_be(uint8_t *p, uint32_t x) {
    p[0]=(uint8_t)(x>>24); p[1]=(uint8_t)(x>>16); p[2]=(uint8_t)(x>>8); p[3]=(uint8_t)x;
}
static uint8_t x2(uint8_t x) { return (uint8_t)((x<<1) ^ (0x1du * (x>>7))); }
static uint32_t f0(uint32_t x, uint32_t k) {
    uint8_t t[4],d[4],q[4],y[4]; x ^= k;
    t[0]=s0[x>>24]; t[1]=s1[(x>>16)&255]; t[2]=s0[(x>>8)&255]; t[3]=s1[x&255];
    for(int i=0;i<4;i++){ d[i]=x2(t[i]); q[i]=x2(d[i]); }
    y[0]=t[0]^d[1]^q[2]^q[3]^d[3];
    y[1]=d[0]^t[1]^q[2]^d[2]^q[3];
    y[2]=q[0]^q[1]^d[1]^t[2]^d[3];
    y[3]=q[0]^d[0]^q[1]^d[2]^t[3];
    return load_be(y);
}
static uint32_t f1(uint32_t x, uint32_t k) {
    uint8_t t[4],d[4],e[4],y[4]; x ^= k;
    t[0]=s1[x>>24]; t[1]=s0[(x>>16)&255]; t[2]=s1[(x>>8)&255]; t[3]=s0[x&255];
    for(int i=0;i<4;i++){ d[i]=x2(t[i]); e[i]=x2(x2(d[i])); }
    y[0]=t[0]^e[1]^d[2]^e[3]^d[3];
    y[1]=e[0]^t[1]^e[2]^d[2]^d[3];
    y[2]=d[0]^e[1]^d[1]^t[2]^e[3];
    y[3]=e[0]^d[0]^d[1]^e[2]^t[3];
    return load_be(y);
}
static void gfn4(uint32_t x[4], const uint32_t *rk, unsigned rounds) {
    for(unsigned r=0;r<rounds;r++) {
        x[1]^=f0(x[0],rk[2*r]); x[3]^=f1(x[2],rk[2*r+1]);
        if(r+1<rounds){ uint32_t t=x[0]; x[0]=x[1]; x[1]=x[2]; x[2]=x[3]; x[3]=t; }
    }
}
void clefia128_set_key(clefia128_ctx *ctx, const uint8_t key[16]) {
    uint32_t l[4];
    for(int j=0;j<4;j++) l[j]=ctx->wk[j]=load_be(key+4*j);
    gfn4(l,con128,12);
    for(int i=0;i<9;i++) {
        for(int j=0;j<4;j++) ctx->rk[4*i+j]=l[j]^con128[24+4*i+j]^((i&1)?ctx->wk[j]:0);
        uint32_t a=l[0],b=l[1],c=l[2],d=l[3];
        l[0]=(a<<7)|(b>>25); l[1]=(b<<7)|(d&127);
        l[2]=(c>>7)|(a&0xfe000000u); l[3]=(d>>7)|(c<<25);
    }
}
void clefia128_encrypt(const clefia128_ctx *ctx, const uint8_t in[16], uint8_t out[16]) {
    uint32_t x[4];
    for(int i=0;i<4;i++) x[i]=load_be(in+4*i);
    x[1]^=ctx->wk[0]; x[3]^=ctx->wk[1];
    gfn4(x,ctx->rk,18);
    x[1]^=ctx->wk[2]; x[3]^=ctx->wk[3];
    for(int i=0;i<4;i++) store_be(out+4*i,x[i]);
}
int clefia128_selftest(void) {
    const uint8_t key[16]={0xff,0xee,0xdd,0xcc,0xbb,0xaa,0x99,0x88,0x77,0x66,0x55,0x44,0x33,0x22,0x11,0x00};
    const uint8_t pt[16]={0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15};
    const uint8_t expected[16]={0xde,0x2b,0xf2,0xfd,0x9b,0x74,0xaa,0xcd,0xf1,0x29,0x85,0x55,0x45,0x94,0x94,0xfd};
    clefia128_ctx ctx; uint8_t ct[16]; uint32_t diff=0;
    clefia128_set_key(&ctx,key); clefia128_encrypt(&ctx,pt,ct);
    for(int i=0;i<16;i++) diff |= ct[i]^expected[i];
    return diff==0;
}
