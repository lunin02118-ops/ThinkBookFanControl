using System;
using System.Globalization;
using System.Text;
using LibreHardwareMonitor.Hardware;

namespace ThinkBookSensorHelper
{
    internal static class Program
    {
        private static string EscapeJson(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }

        private static string FormatFloat(float? v)
        {
            if (!v.HasValue || float.IsNaN(v.Value) || float.IsInfinity(v.Value))
                return "null";
            return v.Value.ToString("0.0", CultureInfo.InvariantCulture);
        }

        public static int Main(string[] args)
        {
            Console.InputEncoding = Encoding.UTF8;
            Console.OutputEncoding = new UTF8Encoding(false);

            var computer = new Computer
            {
                IsCpuEnabled = true,
                IsGpuEnabled = false,
                IsMemoryEnabled = false,
                IsMotherboardEnabled = false,
                IsControllerEnabled = false,
                IsNetworkEnabled = false,
                IsStorageEnabled = false,
                IsBatteryEnabled = false,
                IsPsuEnabled = false
            };

            try
            {
                computer.Open();

                if (args.Length > 0 && args[0] == "--once")
                {
                    // Warm up load counters over 120ms so first read has valid delta
                    foreach (IHardware hw in computer.Hardware) hw.Update();
                    System.Threading.Thread.Sleep(120);
                    Console.WriteLine(ReadCpuJson(computer));
                    return 0;
                }

                string line;
                while ((line = Console.ReadLine()) != null)
                {
                    string cmd = line.Trim().ToUpperInvariant();
                    if (cmd == "EXIT" || cmd == "QUIT")
                        break;
                    if (cmd == "POLL" || cmd.Length == 0)
                    {
                        Console.WriteLine(ReadCpuJson(computer));
                        Console.Out.Flush();
                    }
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("{\"ok\":false,\"error\":\"" + EscapeJson(ex.Message) + "\"}");
                Console.Out.Flush();
                return 1;
            }
            finally
            {
                try { computer.Close(); } catch { }
            }

            return 0;
        }

        private static string ReadCpuJson(Computer computer)
        {
            var sb = new StringBuilder(2048);
            sb.Append("{\"ok\":true,\"sensors\":[");
            bool first = true;
            string cpuName = "";

            foreach (IHardware hw in computer.Hardware)
            {
                if (hw.HardwareType != HardwareType.Cpu)
                    continue;

                cpuName = hw.Name ?? "";
                hw.Update();

                foreach (ISensor s in hw.Sensors)
                {
                    if (s.SensorType != SensorType.Temperature &&
                        s.SensorType != SensorType.Load &&
                        s.SensorType != SensorType.Clock &&
                        s.SensorType != SensorType.Power)
                    {
                        continue;
                    }
                    if (s.Name != null && s.Name.IndexOf("Distance to TjMax", StringComparison.OrdinalIgnoreCase) >= 0)
                        continue;

                    if (!first) sb.Append(',');
                    first = false;

                    sb.Append("{\"t\":\"")
                      .Append(s.SensorType.ToString())
                      .Append("\",\"n\":\"")
                      .Append(EscapeJson(s.Name))
                      .Append("\",\"i\":")
                      .Append(s.Index)
                      .Append(",\"v\":")
                      .Append(FormatFloat(s.Value))
                      .Append('}');
                }
            }

            sb.Append("],\"cpu\":\"")
              .Append(EscapeJson(cpuName))
              .Append("\"}");
            return sb.ToString();
        }
    }
}
