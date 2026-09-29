// CAN node that bridges a Renode CANHub to one TCP client, so an external
// process (can_peers.py) can play the BMS and inverter on the firmware's bus.
//
// Wire format, one frame per line in both directions:
//   "<id hex> <data hex>\n"   e.g. "1801aa01 f4130000fa00e803"
// An id written with eight hex digits is a 29-bit extended frame (the Control
// firmware's bus); one with three or fewer is an 11-bit standard frame (the
// HMG-50 BMS bus, e.g. "355 32006400").
using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Antmicro.Renode.Core;
using Antmicro.Renode.Core.CAN;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.Network;

namespace Antmicro.Renode.Peripherals.CAN
{
    public class MarstekCanBridge : ICAN, IDisposable
    {
        public MarstekCanBridge(IMachine machine, int port = 3457)
        {
            listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            var thread = new Thread(AcceptLoop) { IsBackground = true, Name = "MarstekCanBridge" };
            thread.Start();
        }

        public event Action<CANMessageFrame> FrameSent;

        public void OnFrameReceived(CANMessageFrame message)
        {
            var writer = this.writer;
            if(writer == null)
            {
                return;
            }
            var line = string.Format(message.ExtendedFormat ? "{0:x8} {1}\n" : "{0:x3} {1}\n", message.Id, ToHex(message.Data));
            try
            {
                lock(writerLock)
                {
                    writer.Write(line);
                    writer.Flush();
                }
            }
            catch(IOException)
            {
                this.writer = null;
            }
        }

        public void Reset()
        {
        }

        // "mach clear" disposes peripherals; free the port for the next boot.
        public void Dispose()
        {
            listener.Stop();
            writer = null;
        }

        private void AcceptLoop()
        {
            while(true)
            {
                TcpClient client;
                try
                {
                    client = listener.AcceptTcpClient();
                }
                catch(SocketException)
                {
                    return;
                }
                client.NoDelay = true;
                var stream = client.GetStream();
                writer = new StreamWriter(stream, Encoding.ASCII);
                this.Log(LogLevel.Info, "CAN peer connected");
                var reader = new StreamReader(stream, Encoding.ASCII);
                try
                {
                    string line;
                    while((line = reader.ReadLine()) != null)
                    {
                        Inject(line.Trim());
                    }
                }
                catch(IOException)
                {
                }
                writer = null;
                client.Close();
                this.Log(LogLevel.Info, "CAN peer disconnected");
            }
        }

        private void Inject(string line)
        {
            var parts = line.Split(' ');
            if(parts.Length < 1 || parts[0].Length == 0)
            {
                return;
            }
            try
            {
                var id = Convert.ToUInt32(parts[0], 16);
                var data = parts.Length > 1 ? FromHex(parts[1]) : new byte[0];
                FrameSent?.Invoke(new CANMessageFrame(id, data, extendedFormat: parts[0].Length > 3));
            }
            catch(FormatException)
            {
                this.Log(LogLevel.Warning, "Bad CAN line: {0}", line);
            }
        }

        private static string ToHex(byte[] data)
        {
            var sb = new StringBuilder(data.Length * 2);
            foreach(var b in data)
            {
                sb.Append(b.ToString("x2"));
            }
            return sb.ToString();
        }

        private static byte[] FromHex(string hex)
        {
            var data = new byte[hex.Length / 2];
            for(var i = 0; i < data.Length; i++)
            {
                data[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
            }
            return data;
        }

        private readonly TcpListener listener;
        private readonly object writerLock = new object();
        private volatile StreamWriter writer;
    }
}
