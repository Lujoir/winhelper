# -*- coding: utf-8 -*-
"""LHM 加载链路验证（tools/test_lhm.py）：非 admin 下也应优雅返回（不崩）"""
import ctypes
import os
import sys

sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main():
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    print("IsAdmin:", is_admin)
    import clr
    libs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "libs")
    clr.AddReference(os.path.join(libs, "HidSharp.dll"))
    clr.AddReference(os.path.join(libs, "LibreHardwareMonitorLib.dll"))
    print("AddReference OK")
    from LibreHardwareMonitor import Hardware
    comp = Hardware.Computer()
    comp.IsCpuEnabled = True
    comp.IsGpuEnabled = True
    comp.Open()
    print("Computer.Open OK")
    try:
        temps = []
        for hw in comp.Hardware:
            print("HW:", hw.Name, hw.HardwareType)
            try:
                hw.Update()
            except Exception as e:
                print("  update failed:", e)
                continue
            for s in hw.Sensors:
                if int(s.SensorType) == 2 and s.Value is not None:  # Temperature
                    temps.append((hw.Name, str(s.Name), round(float(s.Value), 1)))
            for sub in hw.SubHardware:
                try:
                    sub.Update()
                except Exception:
                    pass
                for s in sub.Sensors:
                    if int(s.SensorType) == 2 and s.Value is not None:
                        temps.append((str(sub.Name), str(s.Name), round(float(s.Value), 1)))
        print("temps:", temps if temps else "(无读数——非admin下属预期)")
    finally:
        comp.Close()
        print("Computer.Close OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
