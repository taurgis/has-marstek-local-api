// CAN node that bridges a Renode CANHub to one TCP client, so an external
// process (can_peers.py) can play the BMS and inverter on the firmware's bus.
//
// Wire format, one frame per line in both directions:
//   "<id hex> <data hex>\n"   e.g. "1801aa01 f4130000fa00e803"
// Every frame is a 29-bit extended frame; that is all this bus carries.
//
// Frames from the peer are queued and put on the bus one per millisecond of
// virtual time. can_peers.py sends a burst of about ten frames each second;
// injected straight from the socket thread they would all land inside one
// emulation quantum, before the firmware's ISR could drain the 3-deep RX FIFO,
// and the BMS frames that overflow it read as a missing battery (SoC 0).
using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Antmicro.Renode.Core;
using Antmicro.Renode.Core.CAN;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.Network;
using Antmicro.Renode.Peripherals.Timers;

namespace Antmicro.Renode.Peripherals.CAN
{
    public class MarstekCanBridge : ICAN, IDisposable
    {
        public MarstekCanBridge(IMachine machine, int port = 3457)
        {
            pacer = new LimitTimer(machine.ClockSource, 1000, this, "pacer", limit: 1,
                enabled: true, eventEnabled: true, autoUpdate: true);
            pacer.LimitReached += DeliverOne;
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
            var line = string.Format("{0:x8} {1}\n", message.Id, ToHex(message.Data));
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

        private void DeliverOne()
        {
            if(pending.TryDequeue(out var frame))
            {
                FrameSent?.Invoke(frame);
            }
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
                pending.Enqueue(new CANMessageFrame(id, data, extendedFormat: true));
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

        private readonly ConcurrentQueue<CANMessageFrame> pending = new ConcurrentQueue<CANMessageFrame>();
        private readonly LimitTimer pacer;
        private readonly TcpListener listener;
        private readonly object writerLock = new object();
        private volatile StreamWriter writer;
    }
}
