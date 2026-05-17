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
    """Five traces that together demonstrate every Maths operation clearly.

    Trace design
    ------------
    Sine_1Hz   — 1 Hz sine, +/-1 V.
                 Primary signal — good for abs(), add, subtract, multiply,
                 divide, integ(), diff(), and complex chains.

    Sine_3Hz   — 3 Hz sine, +/-0.35 V.
                 Harmonic component.  When added to Sine_1Hz the compound
                 waveform is immediately recognisable.

    Cos_3Hz    — 3 Hz cosine, +/-0.35 V  (alias C).
                 Same frequency as Sine_3Hz but 90 degrees out of phase.
                 arcsin(B / 0.35) recovers instantaneous phase;
                 B**2 + C**2 = 0.1225 (flat line — sin^2+cos^2 identity).

    Envelope   — 0.5 Hz slow positive oscillation, range 0.2 ... 1.0 V  (alias D).
                 Never reaches zero so it is always safe as a divisor.
                 Multiplying gives amplitude modulation; dividing normalises.

    Gate_2Hz   — 2 Hz square wave, +/-1  (alias E).
                 Double the frequency of Sine_1Hz.

    Maths ID assignments A-E are embedded in the file as #trace_meta= headers
    so they load automatically in TraceLab.

    Suggested expressions (open Analysis -> Maths... after import):
    abs(A)              full-wave rectify Sine_1Hz
    A + B               compound 1 Hz + 3 Hz waveform
    A - B               same, 3 Hz component subtracted
    A * B               intermodulation / ring-mod of the two sines
    A * D               amplitude modulation: Sine_1Hz x Envelope
    A / D               normalised Sine_1Hz
    A * E               Sine_1Hz chopped at 2 Hz
    integ(A * D)        integral of AM signal
    diff(A)             rate of change of Sine_1Hz (leads by 90 deg)
    (A + B) / D         compound waveform normalised by Envelope
    arcsin(B / 0.35)    instantaneous phase of Sine_3Hz (radians)
    B**2 + C**2         sin^2 + cos^2 = 0.1225 (flat line)
    """
    N   = 10_000
    SPS = 5_000.0          # 5 kSa/s -> 2 s of data
    t   = np.arange(N) / SPS

    # A: 1 Hz sine +/-1 V
    sine_1hz = np.sin(2 * np.pi * 1.0 * t)

    # B: 3 Hz sine +/-0.35 V  (clear harmonic, won't overpower A)
    sine_3hz = np.sin(2 * np.pi * 3.0 * t) * 0.35

    # C: 3 Hz cosine +/-0.35 V  (same amplitude as B, 90 deg out of phase)
    cos_3hz  = np.cos(2 * np.pi * 3.0 * t) * 0.35

    # D: slow positive envelope 0.5 Hz, range 0.2 ... 1.0 V
    #    (sin+1)/2 maps to 0..1; scale to 0.2..1.0 so it never reaches zero
    envelope = 0.2 + 0.8 * (np.sin(2 * np.pi * 0.5 * t) + 1.0) / 2.0

    # E: 2 Hz square wave +/-1  (double frequency of Sine_1Hz)
    gate_2hz = np.sign(np.sin(2 * np.pi * 2.0 * t)).astype(float)
    gate_2hz[gate_2hz == 0.0] = 1.0   # snap zero-crossings to +1

    path = "maths_demo.csv"
    with open(path, "w", newline="") as f:
        # TraceLab native metadata header: maths IDs are pre-assigned so the
        # channel panel shows A-E badges as soon as the file is imported.
        f.write(f"#samplerate={SPS:.0f}\n")
        f.write('#trace_meta={"Sine_1Hz","maths_id=A"}\n')
        f.write('#trace_meta={"Sine_3Hz","maths_id=B"}\n')
        f.write('#trace_meta={"Cos_3Hz","maths_id=C"}\n')
        f.write('#trace_meta={"Envelope","maths_id=D"}\n')
        f.write('#trace_meta={"Gate_2Hz","maths_id=E"}\n')
        writer = csv.writer(f)
        writer.writerow(["time", "Sine_1Hz", "Sine_3Hz", "Cos_3Hz",
                         "Envelope", "Gate_2Hz"])
        for i in range(N):
            writer.writerow([
                f"{t[i]:.8f}",
                f"{sine_1hz[i]:.6f}",
                f"{sine_3hz[i]:.6f}",
                f"{cos_3hz[i]:.6f}",
                f"{envelope[i]:.6f}",
                f"{gate_2hz[i]:.6f}",
            ])

    print(f"Generated {path}: {N} samples, {SPS:.0f} Sa/s, {N/SPS:.2f}s")
    print()
    print("  A  Sine_1Hz  - 1 Hz sine, +/-1 V")
    print("  B  Sine_3Hz  - 3 Hz sine, +/-0.35 V")
    print("  C  Cos_3Hz   - 3 Hz cosine, +/-0.35 V")
    print("  D  Envelope  - 0.5 Hz slow positive wave, 0.2...1.0 V")
    print("  E  Gate_2Hz  - 2 Hz square wave, +/-1")
    print()
    print("Identifiers A-E are embedded in the file. Import into TraceLab and")
    print("open Analysis -> Maths... -- identifiers are already set up.")
    print()
    print("sin/cos/arcsin/arccos all use RADIANS. To convert degrees: multiply by pi/180.")
    print()
    print("Suggested expressions:")
    print("  abs(A)              full-wave rectify Sine_1Hz")
    print("  A + B               compound 1 Hz + 3 Hz waveform")
    print("  A - B               same, 3 Hz component subtracted")
    print("  A * B               intermodulation / ring-mod")
    print("  A * D               amplitude modulation (Sine_1Hz x Envelope)")
    print("  A / D               normalised Sine_1Hz")
    print("  A * E               Sine_1Hz chopped at 2 Hz")
    print("  integ(A * D)        integral of AM signal")
    print("  diff(A)             rate of change of Sine_1Hz")
    print("  (A + B) / D         compound waveform normalised by Envelope")
    print("  arcsin(B / 0.35)    instantaneous phase of Sine_3Hz (radians)")
    print("  B**2 + C**2         sin^2 + cos^2 = 0.1225 (flat line)")


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
