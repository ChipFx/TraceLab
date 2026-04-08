#!/usr/bin/env python3
"""
generate_test_data.py
Generates a sample CSV file for testing PyScope.
Run: python generate_test_data.py
"""

import numpy as np
import csv

# 10,000 samples at 10 kHz = 1 second of data
N = 10000
SPS = 10000.0
dt = 1.0 / SPS
t = np.arange(N) * dt

# Channels
ch1 = np.sin(2 * np.pi * 50 * t) * 1.0                          # 50 Hz sine
ch2 = np.sin(2 * np.pi * 120 * t) * 0.5 + 0.3                   # 120 Hz + DC offset
ch3 = np.sign(np.sin(2 * np.pi * 10 * t)) * 1.2                  # 10 Hz square
ch4 = np.sin(2 * np.pi * 200 * t) * 0.3 + np.random.randn(N)*0.05  # 200 Hz + noise
ch5 = np.linspace(0, 4095, N).astype(int)                         # ADC ramp (0-4095)
ch6 = (ch1 + ch2) * 0.5                                           # Mixed signal

with open("test_data.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["time", "Ch1_50Hz", "Ch2_120Hz_DC", "Ch3_10Hz_Square",
                     "Ch4_200Hz_Noise", "Ch5_ADC_Ramp", "Ch6_Mixed"])
    for i in range(N):
        writer.writerow([
            f"{t[i]:.8f}",
            f"{ch1[i]:.6f}",
            f"{ch2[i]:.6f}",
            f"{ch3[i]:.6f}",
            f"{ch4[i]:.6f}",
            f"{int(ch5[i])}",
            f"{ch6[i]:.6f}",
        ])

print(f"Generated test_data.csv: {N} samples, {SPS:.0f} Sa/s, {N/SPS:.2f}s duration")
print("  Ch1: 50 Hz sine, ±1V")
print("  Ch2: 120 Hz sine + 0.3V DC offset")
print("  Ch3: 10 Hz square wave ±1.2V")
print("  Ch4: 200 Hz sine + noise")
print("  Ch5: ADC ramp 0-4095 (try scaling to -1.25V..+1.25V)")
print("  Ch6: Mixed (Ch1+Ch2)/2")
print()
print("Load into PyScope with: python main.py")
print("  - Use the time column for time axis, or set SPS=10000")
print("  - For Ch5, enable scaling: 0→4095 maps to -1.25V→+1.25V")
