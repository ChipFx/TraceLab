#!/usr/bin/env python3
"""
generate_test_data.py
Generates sample CSV files for testing TraceLab.

Modes
-----
(no args)   test_data.csv    — general-purpose multi-channel demo
--maths     maths_demo.csv   — four traces designed to demonstrate every
                               Maths operation step by step
--segments  (coming later)   — multi-segment / trigger-group demo

Usage
-----
  python generate_test_data.py              # general demo
  python generate_test_data.py --maths      # maths demo
"""

import argparse
import csv
import numpy as np


# ── Mode: general demo ─────────────────────────────────────────────────────────

def generate_general():
    """Original multi-channel test data."""
    N   = 10_000
    SPS = 10_000.0
    t   = np.arange(N) / SPS

    ch1 = np.sin(2 * np.pi * 50  * t) * 1.0
    ch2 = np.sin(2 * np.pi * 120 * t) * 0.5 + 0.3
    ch3 = np.sign(np.sin(2 * np.pi * 10 * t)) * 1.2
    ch4 = np.sin(2 * np.pi * 200 * t) * 0.3 + np.random.randn(N) * 0.05
    ch5 = np.linspace(0, 4095, N).astype(int)
    ch6 = (ch1 + ch2) * 0.5

    path = "test_data.csv"
    with open(path, "w", newline="") as f:
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

    print(f"Generated {path}: {N} samples, {SPS:.0f} Sa/s, {N/SPS:.2f}s")
    print("  Ch1: 50 Hz sine, +/-1 V")
    print("  Ch2: 120 Hz sine + 0.3 V DC offset")
    print("  Ch3: 10 Hz square wave +/-1.2 V")
    print("  Ch4: 200 Hz sine + noise")
    print("  Ch5: ADC ramp 0-4095 (try scaling to -1.25 V ... +1.25 V)")
    print("  Ch6: Mixed (Ch1+Ch2)/2")
    print()
    print("Load with: python main.py")


# ── Mode: maths demo ───────────────────────────────────────────────────────────

def generate_maths():
    """Four traces that together demonstrate every Maths operation clearly.

    Trace design
    ------------
    Sine_1Hz   — 1 Hz sine, ±1 V.
                 Used as primary input for abs(), add, subtract, multiply,
                 divide, and complex chains.

    Sine_3Hz   — 3 Hz sine, ±0.35 V.
                 A harmonic — when added to Sine_1Hz the compound waveform
                 is instantly recognisable as a sum-of-frequencies shape.

    Envelope   — 0.5 Hz slow positive oscillation, range 0.2 … 1.0 V.
                 Never reaches zero, so it is always safe as a divisor.
                 Multiplying by it produces visible amplitude modulation;
                 dividing by it normalises amplitude back out.

    Gate_1Hz   — 1 Hz square wave, ±1.
                 Multiplying any signal by the gate half-wave-rectifies it
                 (passes positive half-cycles, inverts negative ones) which
                 is one of the most visual results in the maths set.

    Suggested operations to try in ApplyMaths
    ------------------------------------------
    abs(A)              full-wave rectify Sine_1Hz
    A + B               compound 1 Hz + 3 Hz waveform
    A - B               same but 3 Hz component is subtracted (phase flip)
    A * B               intermodulation / ring-mod of the two sines
    A * C               amplitude modulation: Sine_1Hz × Envelope
    A / C               normalised Sine_1Hz (amplitude lifted by 1/Envelope)
    A * D               half-wave polarity switch (gate flips every half cycle)
    (A + B) / C         compound waveform normalised by the envelope
    abs(A * D)          equivalent to abs(A) — two ways to full-wave rectify
    """
    N   = 10_000
    SPS = 5_000.0          # 5 kSa/s → 2 s of data
    t   = np.arange(N) / SPS

    # A: 1 Hz sine ±1 V
    sine_1hz = np.sin(2 * np.pi * 1.0 * t)

    # B: 3 Hz sine ±0.35 V  (clear harmonic, won't overpower A)
    sine_3hz = np.sin(2 * np.pi * 3.0 * t) * 0.35

    # C: slow positive envelope 0.5 Hz, range 0.2 … 1.0 V
    #    sin goes −1 … 1, so (sin + 1) / 2 → 0 … 1; scale to 0.2 … 1.0
    envelope = 0.2 + 0.8 * (np.sin(2 * np.pi * 0.5 * t) + 1.0) / 2.0

    # D: 1 Hz square wave ±1 (same period as Sine_1Hz — the relationship is obvious)
    gate_1hz = np.sign(np.sin(2 * np.pi * 1.0 * t)).astype(float)
    # np.sign returns 0 at zero-crossings; snap those to +1 for a clean square
    gate_1hz[gate_1hz == 0.0] = 1.0

    path = "maths_demo.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "Sine_1Hz", "Sine_3Hz", "Envelope", "Gate_1Hz"])
        for i in range(N):
            writer.writerow([
                f"{t[i]:.8f}",
                f"{sine_1hz[i]:.6f}",
                f"{sine_3hz[i]:.6f}",
                f"{envelope[i]:.6f}",
                f"{gate_1hz[i]:.6f}",
            ])

    print(f"Generated {path}: {N} samples, {SPS:.0f} Sa/s, {N/SPS:.2f}s")
    print()
    print("  Sine_1Hz  - 1 Hz sine, +/-1 V                         (alias A)")
    print("  Sine_3Hz  - 3 Hz sine, +/-0.35 V                      (alias B)")
    print("  Envelope  - 0.5 Hz slow positive wave, 0.2 ... 1.0 V  (alias C)")
    print("  Gate_1Hz  - 1 Hz square wave, +/-1                     (alias D)")
    print()
    print("Open in TraceLab, then Analysis -> Maths... and try:")
    print("  abs(A)          full-wave rectify Sine_1Hz")
    print("  A + B           compound waveform (1 Hz + 3 Hz)")
    print("  A - B           same, 3 Hz component phase-flipped")
    print("  A * B           intermodulation / ring-mod")
    print("  A * C           amplitude modulation (Envelope modulates Sine_1Hz)")
    print("  A / C           normalised Sine_1Hz (Envelope divides it back out)")
    print("  A * D           half-wave polarity switch via square gate")
    print("  (A + B) / C     compound waveform normalised by Envelope")
    print("  abs(A * D)      full-wave rectify via gate (same result as abs(A))")


# ── Mode stubs (future) ────────────────────────────────────────────────────────

def generate_segments():
    raise NotImplementedError(
        "--segments mode is not implemented yet.\n"
        "It will generate a multi-segment CSV with trigger-group annotations.")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate test CSV files for TraceLab.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--maths",
        action="store_true",
        help="Generate maths_demo.csv — traces designed to demo every Maths operation")
    group.add_argument(
        "--segments",
        action="store_true",
        help="(Coming soon) Generate a multi-segment / trigger-group demo CSV")
    args = parser.parse_args()

    if args.maths:
        generate_maths()
    elif args.segments:
        generate_segments()
    else:
        generate_general()


if __name__ == "__main__":
    main()
