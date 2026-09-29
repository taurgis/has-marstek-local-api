// STM32G4 USART/LPUART (RM0440 sections 37 and 38) with the FIFO mode that
// Renode's STM32F7_USART lacks. The HMG-50 firmware sets CR1.FIFOEN and takes
// its receive interrupt from CR3.RXFTIE (RX FIFO threshold) only, so without
// FIFO support no byte ever reaches it.
//
// Modelled: CR1/CR2/CR3/BRR/RQR/ISR/ICR/RDR/TDR/PRESC, an 8-entry RX FIFO in
// FIFO mode (one entry otherwise), RXFNE/RXFT/RXFF, instant transmission
// (TXFNF, TXFE, TXFT and TC stay set), and the interrupt enables that go with
// them. Not modelled: DMA requests, parity/framing errors, auto baud, RS485
// driver-enable timing (DEM is accepted and ignored), wakeup.
using System;
using Antmicro.Renode.Core;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.Bus;

namespace Antmicro.Renode.Peripherals.UART
{
    public class STM32G4_USART : UARTBase, IDoubleWordPeripheral, IKnownSize
    {
        public STM32G4_USART(IMachine machine, uint frequency = 170000000, bool lowPower = false) : base(machine)
        {
            this.frequency = frequency;
            this.lowPower = lowPower;
            IRQ = new GPIO();
            Reset();
        }

        public override void Reset()
        {
            base.Reset();
            cr1 = cr2 = cr3 = 0;
            brr = 0;
            presc = 0;
            overrun = false;
            idle = false;
            IRQ.Unset();
        }

        public uint ReadDoubleWord(long offset)
        {
            switch((Registers)offset)
            {
            case Registers.CR1: return cr1;
            case Registers.CR2: return cr2;
            case Registers.CR3: return cr3;
            case Registers.BRR: return brr;
            case Registers.PRESC: return presc;
            case Registers.ISR: return Isr();
            case Registers.RDR:
                uint value = 0;
                if(TryGetCharacter(out var c))
                {
                    value = c;
                }
                Update();
                return value;
            case Registers.TDR: return 0;
            default:
                return 0;
            }
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            switch((Registers)offset)
            {
            case Registers.CR1:
                cr1 = value;
                break;
            case Registers.CR2:
                cr2 = value;
                break;
            case Registers.CR3:
                cr3 = value;
                break;
            case Registers.BRR:
                brr = value & (lowPower ? 0xFFFFFu : 0xFFFFu);
                break;
            case Registers.PRESC:
                presc = value & 0xF;
                break;
            case Registers.RQR:
                if((value & (1u << 3)) != 0) // RXFRQ: flush the receive FIFO
                {
                    ClearBuffer();
                }
                break;
            case Registers.ICR:
                if((value & (1u << 3)) != 0) { overrun = false; }
                if((value & (1u << 4)) != 0) { idle = false; }
                break;
            case Registers.TDR:
                if(Enabled && (cr1 & Cr1Te) != 0)
                {
                    TransmitCharacter((byte)value);
                }
                break;
            case Registers.GTPR:
            case Registers.RTOR:
                break;
            default:
                this.Log(LogLevel.Noisy, "Unhandled write to 0x{0:X}: 0x{1:X}", offset, value);
                break;
            }
            Update();
        }

        public long Size => 0x400;

        public GPIO IRQ { get; }

        public override Bits StopBits
        {
            get
            {
                switch((cr2 >> 12) & 3)
                {
                case 1: return Bits.Half;
                case 2: return Bits.Two;
                case 3: return Bits.OneAndAHalf;
                default: return Bits.One;
                }
            }
        }

        public override Parity ParityBit => (cr1 & (1u << 10)) == 0 ? Parity.None : ((cr1 & (1u << 9)) == 0 ? Parity.Even : Parity.Odd);

        public override uint BaudRate
        {
            get
            {
                if(brr == 0)
                {
                    return 0;
                }
                var clock = (ulong)frequency / PrescalerDivider();
                return (uint)(lowPower ? clock * 256 / brr : clock / brr);
            }
        }

        protected override bool IsReceiveEnabled => Enabled && (cr1 & Cr1Re) != 0;

        // Renode's receive queue is unbounded, so it never overruns; the
        // firmware drains it byte by byte from the RXFT interrupt.
        protected override void CharWritten()
        {
            idle = true;
            Update();
        }

        protected override void QueueEmptied()
        {
            Update();
        }

        private bool Enabled => (cr1 & 1) != 0;

        private bool FifoMode => (cr1 & (1u << 29)) != 0;

        private int RxThreshold()
        {
            // RXFTCFG: 1/8, 1/4, 1/2, 3/4, 7/8 or all of the 8-entry FIFO.
            var cfg = (int)((cr3 >> 25) & 7);
            var levels = new[] { 1, 2, 4, 6, 7, 8 };
            return cfg < levels.Length ? levels[cfg] : 8;
        }

        private uint Isr()
        {
            var count = Count;
            var isr = (1u << 6) | (1u << 7) | (1u << 23) | (1u << 27); // TC, TXE/TXFNF, TXFE, TXFT
            if(count > 0) { isr |= 1u << 5; } // RXNE/RXFNE
            if(FifoMode && count >= RxThreshold()) { isr |= 1u << 26; } // RXFT
            if(FifoMode && count >= 8) { isr |= 1u << 24; } // RXFF
            if(overrun) { isr |= 1u << 3; }
            if(idle && count == 0) { isr |= 1u << 4; }
            if((cr1 & Cr1Te) != 0) { isr |= 1u << 21; } // TEACK
            if((cr1 & Cr1Re) != 0) { isr |= 1u << 22; } // REACK
            return isr;
        }

        private void Update()
        {
            var isr = Isr();
            var irq = false;
            irq |= (isr & (1u << 5)) != 0 && (cr1 & (1u << 5)) != 0;           // RXNEIE/RXFNEIE
            irq |= (isr & (1u << 26)) != 0 && (cr3 & (1u << 28)) != 0;          // RXFTIE
            irq |= (isr & (1u << 24)) != 0 && (cr1 & (1u << 31)) != 0;          // RXFFIE
            irq |= (isr & (1u << 7)) != 0 && (cr1 & (1u << 7)) != 0;            // TXEIE/TXFNFIE
            irq |= (isr & (1u << 6)) != 0 && (cr1 & (1u << 6)) != 0;            // TCIE
            irq |= (isr & (1u << 23)) != 0 && (cr1 & (1u << 30)) != 0;          // TXFEIE
            irq |= (isr & (1u << 27)) != 0 && (cr3 & (1u << 23)) != 0;          // TXFTIE
            irq |= (isr & (1u << 4)) != 0 && (cr1 & (1u << 4)) != 0;            // IDLEIE
            irq |= (isr & (1u << 3)) != 0 && ((cr1 & (1u << 5)) != 0 || (cr3 & 1) != 0); // ORE
            IRQ.Set(Enabled && irq);
        }

        private uint PrescalerDivider()
        {
            var table = new uint[] { 1, 2, 4, 6, 8, 10, 12, 16, 32, 64, 128, 256 };
            return presc < table.Length ? table[presc] : 256;
        }

        private const uint Cr1Re = 1u << 2;
        private const uint Cr1Te = 1u << 3;

        private readonly uint frequency;
        private readonly bool lowPower;
        private uint cr1, cr2, cr3, brr, presc;
        private bool overrun;
        private bool idle;

        private enum Registers : long
        {
            CR1 = 0x00,
            CR2 = 0x04,
            CR3 = 0x08,
            BRR = 0x0C,
            GTPR = 0x10,
            RTOR = 0x14,
            RQR = 0x18,
            ISR = 0x1C,
            ICR = 0x20,
            RDR = 0x24,
            TDR = 0x28,
            PRESC = 0x2C,
        }
    }
}
