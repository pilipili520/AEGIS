#include <stdint.h>
#include "clefia128.h"
#define REG(a) (*(volatile uint32_t *)(a))
#define GPIOA_BSRR REG(0x40020018u)
#define FLASH_ACR REG(0x40023c00u)
#define BARRIER() __asm volatile("dsb\n isb" ::: "memory")
#ifndef NO_TRIGGER
#define NO_TRIGGER 0
#endif
typedef struct {
    uint32_t magic,status,command,sequence,completed,selftest,flash_acr,cycles;
    uint8_t key[16],plaintext[16],ciphertext[16];
} mailbox_t;
__attribute__((section(".mailbox"),used)) volatile mailbox_t mailbox;
void main(void) {
    __asm volatile("cpsid i");
    REG(0xe000e010)=0; /* Stop SysTick during clock initialization. */
    REG(0x40023800)|=1u; while(!(REG(0x40023800)&2u)){} /* HSI ready. */
    REG(0x40023808)=0; while(REG(0x40023808)&12u){} /* HSI 16 MHz, no dividers. */
    REG(0x40023800)&=~(1u<<24); /* PLL off. */
    FLASH_ACR &= ~0x700u; BARRIER(); /* PRFTEN, ICEN, DCEN off. */
    FLASH_ACR |= 0x1800u; FLASH_ACR &= ~0x1800u;
    FLASH_ACR &= ~15u; BARRIER(); /* Zero wait states at 16 MHz. */
#if NO_TRIGGER
    /* Pipeline payloads read SysTick VAL for timing jitter. Run the counter
       without its interrupt; PRIMASK and the cache settings remain unchanged. */
    REG(0xe000e014)=0x00ffffffu;
    REG(0xe000e018)=0;
    REG(0xe000e010)=5u; /* ENABLE | CLKSOURCE, TICKINT=0. */
    BARRIER();
#endif
#if !NO_TRIGGER
    REG(0x40023830)|=1u; (void)REG(0x40023830);
    GPIOA_BSRR=1u<<(6+16);
    REG(0x40020000)=(REG(0x40020000)&~(3u<<12))|(1u<<12);
    REG(0x40020004)&=~(1u<<6); REG(0x40020008)&=~(3u<<12); REG(0x4002000c)&=~(3u<<12);
#endif
    REG(0xe000edfc)|=1u<<24; REG(0xe0001000)|=1; REG(0xe0001004)=0;
    mailbox.magic=0; mailbox.status=0; mailbox.command=0; mailbox.sequence=0; mailbox.completed=0;
    mailbox.selftest=(uint32_t)clefia128_selftest(); mailbox.flash_acr=FLASH_ACR;
    mailbox.cycles=0; mailbox.status=mailbox.selftest?1u:0xeeu; BARRIER(); mailbox.magic=0x434c4631u;
    if(!mailbox.selftest) for(;;) __asm volatile("nop");
    for(;;) {
        if(mailbox.command==1) {
            clefia128_ctx ctx; uint8_t key[16],pt[16],ct[16];
            mailbox.status=2; mailbox.command=0;
            for(int i=0;i<16;i++){ key[i]=mailbox.key[i]; pt[i]=mailbox.plaintext[i]; }
            clefia128_set_key(&ctx,key); /* Excluded from acquisition trigger. */
            for(volatile unsigned d=0;d<16000;d++) __asm volatile("nop");
            uint32_t start=REG(0xe0001004);
#if !NO_TRIGGER
            GPIOA_BSRR=1u<<6; BARRIER();
#endif
            clefia128_encrypt(&ctx,pt,ct);
#if !NO_TRIGGER
            BARRIER(); GPIOA_BSRR=1u<<(6+16); BARRIER();
#endif
            mailbox.cycles=REG(0xe0001004)-start;
            for(int i=0;i<16;i++) mailbox.ciphertext[i]=ct[i];
            mailbox.flash_acr=FLASH_ACR; mailbox.completed=mailbox.sequence; BARRIER(); mailbox.status=1;
        }
    }
}
