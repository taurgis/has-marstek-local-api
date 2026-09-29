// 24Cxx-style I2C EEPROM backed by a file, for the VNSE3-0 config store.
// Two-byte word address (24C64/24C256 style); size set in the .repl.
using System;
using System.IO;
using System.Collections.Generic;
using Antmicro.Renode.Core;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.I2C;

namespace Antmicro.Renode.Peripherals.I2C
{
    public class MarstekI2CEeprom : II2CPeripheral
    {
        public MarstekI2CEeprom(int size = 0x8000, int addressBytes = 2, string backingFile = "")
        {
            this.size = size;
            this.addressBytes = addressBytes;
            this.backingFile = backingFile;
            memory = new byte[size];
            for(var i = 0; i < size; i++) { memory[i] = 0xFF; }
            if(backingFile != "" && File.Exists(backingFile))
            {
                var data = File.ReadAllBytes(backingFile);
                Array.Copy(data, memory, Math.Min(data.Length, size));
            }
        }

        public void Write(byte[] data)
        {
            var i = 0;
            if(!addressed)
            {
                while(i < data.Length && pendingAddress.Count < addressBytes)
                {
                    pendingAddress.Add(data[i++]);
                }
                if(pendingAddress.Count < addressBytes) { return; }
                pointer = 0;
                foreach(var b in pendingAddress) { pointer = (pointer << 8) | b; }
                pointer %= size;
                addressed = true;
            }
            var wrote = false;
            for(; i < data.Length; i++)
            {
                memory[pointer] = data[i];
                pointer = (pointer + 1) % size;
                wrote = true;
            }
            if(wrote) { dirty = true; }
        }

        public byte[] Read(int count = 1)
        {
            var result = new byte[count];
            for(var i = 0; i < count; i++)
            {
                result[i] = memory[pointer];
                pointer = (pointer + 1) % size;
            }
            return result;
        }

        public void FinishTransmission()
        {
            pendingAddress.Clear();
            addressed = false;
            if(dirty && backingFile != "")
            {
                File.WriteAllBytes(backingFile, memory);
            }
            dirty = false;
        }

        public void Reset()
        {
            pendingAddress.Clear();
            addressed = false;
        }

        private readonly int size;
        private readonly int addressBytes;
        private readonly string backingFile;
        private readonly byte[] memory;
        private readonly List<byte> pendingAddress = new List<byte>();
        private bool addressed;
        private bool dirty;
        private int pointer;
    }
}
