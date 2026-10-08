#!/usr/bin/env python3
"""
asciify_cli — command-line front-end for the asciify core module.

Responsibilities (and *only* these):
  * define and parse the command-line interface
  * validate and normalize options (colors, alpha, container rules)
  * resolve and validate --reuse directories
  * translate arguments into an asciify.ConversionConfig and dispatch

All actual video processing lives in asciify.run_conversion().
"""

import argparse
import json
import sys
from pathlib import Path

import asciify
from asciify import DEFAULT, count_dir, load_state, report_state

VIDEO_SUFFIXES = ['.mp4', '.mov', '.avi', '.mkv', '.webm']


def build_parser():
    parser = argparse.ArgumentParser(
        description='Convert green-screen video to ASCII art video',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s input.mp4 output.mp4
  %(prog)s input.mp4 out.mp4 --key-color 0x00FF00 --ascii-width 480
  %(prog)s input.mp4 out.mp4 --no-key --leave-black-empty
  %(prog)s input.mp4 out.mp4 --keep-temp
  %(prog)s -i
  %(prog)s input.mp4 out2.mp4 --reuse ascii --render-font-size 96
  %(prog)s input.mp4 out3.mp4 --reuse final
        """
    )

    parser.add_argument('input', type=str, nargs='?',
                        help='Input video file path')
    parser.add_argument('output', type=str, nargs='?',
                        help='Output ASCII video file path')
    parser.add_argument('--key-color', type=str, default=None,
                        help=f'Key color in hex format (default: {DEFAULT["KEY-COLOR"]})')
    parser.add_argument('--detect-green', action='store_true',
                        help='Force green color detection even if --key-color provided')
    parser.add_argument('--tolerance', type=float, default=DEFAULT['TOLERANCE'],
                        help=f'Chromakey tolerance (default: {DEFAULT["TOLERANCE"]})')
    parser.add_argument('--edge-blending', type=float, default=DEFAULT['EDGE-BLEEDING'],
                        help=f'Chromakey edge blending (default: {DEFAULT["EDGE-BLEEDING"]})')
    parser.add_argument('--fps', type=float, default=None,
                        help='Override output FPS (default: from input)')
    parser.add_argument('--scale-divisor', type=int, default=DEFAULT['SCALE-DIVISOR'],
                        help='Default ascii width = source width / this '
                             f'when --ascii-width not given (default: {DEFAULT["SCALE-DIVISOR"]})')
    parser.add_argument('--ascii-width', type=int, default=DEFAULT['ASCII-WIDTH'],
                        help=f'ASCII grid width in characters (default: {DEFAULT["ASCII-WIDTH"]})')
    parser.add_argument('--ramp', type=str, default=None,
                        help="'jp2a' or a custom ramp string "
                             '(default: full density ramp)')
    parser.add_argument('--colorized', action='store_true',
                        help='Render characters in their source colors '
                             'instead of monochrome green')
    parser.add_argument('--leave-black-empty', action='store_true',
                        help='Leave black areas empty (no ASCII characters)')
    parser.add_argument('--black-threshold', type=int, default=DEFAULT['BLACK-THRESHOLD'],
                        help=f'Brightness threshold for "black" (default: {DEFAULT["BLACK-THRESHOLD"]})')
    parser.add_argument('--hist-stretch', action='store_true',
                        help='Global histogram stretch before character mapping')
    parser.add_argument('--no-key', action='store_true',
                        help='Skip chromakey stage entirely')
    parser.add_argument('--sample-region', type=str, default=DEFAULT['SAMPLE-REGION'],
                        help='Key color sampling region: corners, center, '
                             f'full, or WxH+X+Y (default: {DEFAULT["SAMPLE-REGION"]})')
    parser.add_argument('--render-font-size', type=int, default=DEFAULT['RASTERIZATION-QUALITY'],
                        help=f'Font rasterization quality (default: {DEFAULT["RASTERIZATION-QUALITY"]})')
    parser.add_argument('--target-resolution', type=str, default=None,
                        help='Target WIDTHxHEIGHT, e.g. 1920x1080 '
                             '(default: source dimensions)')
    parser.add_argument('--jobs', type=int, default=None, metavar='N',
                        help='Parallel worker processes (default: cores - 1)')
    parser.add_argument('--keep-temp', action='store_true',
                        help='Keep temporary files; record run in state file')
    parser.add_argument('--silent-progress', action='store_true',
                        help='Suppress progress bars')
    parser.add_argument('--reuse', choices=['extracted', 'ascii', 'final'],
                        default=None,
                        help='Reuse artifacts: "extracted" skips keying+'
                             'extraction, "ascii" also skips conversion, '
                             '"final" reuses rendered frames entirely')
    parser.add_argument('--reuse-dir', type=str, default=None, metavar='PATH',
                        help='Explicit temp dir to reuse (default: last '
                             '--keep-temp run, or $ASCII_VIDEO_TEMP)')
    parser.add_argument('--report', action='store_true',
                        help='Print recorded state of the last run and exit')
    parser.add_argument('--state-file', action='store_true',
                        help='Print the raw state JSON and exit')
    parser.add_argument('--background-color', type=str, default=DEFAULT['BACKGROUND-COLOR'],
                        help=f'Background color in CSS hex format (default: {DEFAULT["BACKGROUND-COLOR"]})')
    parser.add_argument('--background-alpha', type=float, default=DEFAULT['BACKGROUND-ALPHA'],
                        help=f'Background opacity 0-1 (0=transparent, 1=opaque; default: {DEFAULT["BACKGROUND-ALPHA"]})')
    parser.add_argument('--vpx-preset', type=str, default=DEFAULT['VPX-PRESET'],
                        choices=['ultrafast', 'veryfast', 'faster', 'fast',
                                 'medium', 'slow'],
                        help=f'VP9 encoding speed for alpha output (default: {DEFAULT["VPX-PRESET"]})')
    parser.add_argument('--font-color', type=str, default=None,
                        help='Character color in CSS hex format '
                             f'(default: {DEFAULT["FONT-COLOR"]}, the classic terminal green). '
                             'Incompatible with --colorized')

    return parser


# --------------------------------------------------------------------------
# Normalization helpers (CLI-side validation)
# --------------------------------------------------------------------------

def _color_as_rgb_checked(hex_str, label):
    """Parse a CSS-style hex color, exiting with a message on failure.

    Mirrors asciify._color_as_rgb but owns the CLI error contract:
    bad input -> stderr message + exit(1).
    """
    try:
        return asciify._color_as_rgb(hex_str)
    except (ValueError, IndexError):
        print(f"Error: Invalid {label} '{hex_str}'. "
              f"Use CSS hex format (e.g., '#RRGGBB')")
        sys.exit(1)


def _parse_target_resolution(spec):
    """Parse 'WIDTHxHEIGHT' -> (width, height); None passes through."""
    if spec is None:
        return None
    try:
        tw, th = spec.split('x')
        return (int(tw), int(th))
    except ValueError:
        print(f"Error: Invalid target resolution '{spec}'. "
              f"Expected WIDTHxHEIGHT, e.g. 1920x1080")
        sys.exit(1)


def _apply_webm_rename_rule(output_path, bg_alpha):
    """Alpha output requires WebM; rename (and dodge collisions).

    Returns the possibly-adjusted output path. Pure side-effect-free
    decision logic; printing the boxed notice is this function's job.
    """
    if bg_alpha >= 1.0:
        return output_path
    if output_path.suffix.lower() == '.webm':
        return output_path

    original_name = str(output_path)
    output_path = output_path.with_suffix('.webm')
    print("! " + "-" * 58 + " !")
    print(f"! {'NOTE: alpha output requires WebM'.center(56)} !")
    print("! " + "-" * 58 + " !")
    print(f"!  Output filename changed:")
    print(f"!    {original_name}")
    print(f"!    -> {output_path}")
    print("! " + "-" * 58 + " !")
    print(f"!  Reason: --background-alpha {bg_alpha:g} produces a")
    print(f"!  transparent video, which requires the VP9/WebM")
    print(f"!  container. Use --background-alpha 1 to force MP4.")
    print("! " + "-" * 58 + " !\n")
    if output_path.exists():
        alt = output_path.with_name(
            output_path.stem + '_new' + output_path.suffix)
        print(f"!  WARNING: {output_path} already exists;")
        print(f"!  writing to {alt} instead\n")
        output_path = alt
    return output_path


def report_reuse_semantics_cli(reuse_level, argv):
    """Explain which options are dead and which still work at this reuse
    level, highlighting anything the user actually passed.

    CLI-specific because it inspects the raw argument list; moved here
    from the core module during the CLI/core split.
    """
    skipped = asciify.REUSE_SKIPPED_STAGES[reuse_level]
    ignored = sorted({f for s in skipped
                      for f in asciify.STAGE_FLAG_MAP.get(s, [])})

    print(f"  Stages skipped: {', '.join(skipped)}")
    if ignored:
        print(f"  No effect at this level: {', '.join(ignored)}")

    # Did the user actually pass any of the dead flags?
    passed = []
    for arg in argv:
        flag_name = arg.split('=')[0]
        if flag_name in ignored:
            passed.append(flag_name)

    for f in sorted(set(passed)):
        print(f"    !! {f} was passed but is IGNORED (--reuse {reuse_level})")

    if reuse_level == 'final':
        print("  Still honored: --fps, --target-resolution "
              "(frames rescaled at assembly), audio mapping")
        print("  Note: background color/alpha, font, and masking are "
              "baked into the reused frames")
    elif reuse_level == 'ascii':
        print("  Still honored: font, colorized, black-masking, and "
              "background options (rendering re-runs from reused text)")
        print("  Note: character grid comes from the reused text files")
    else:  # extracted
        print("  Still honored: conversion and rendering options "
              "(--ascii-width, --ramp, --hist-stretch, fonts, backgrounds)")


# --------------------------------------------------------------------------
# Argument -> config translation
# --------------------------------------------------------------------------

def build_config(args, input_path, output_path):
    """Translate validated argparse.Namespace into a ConversionConfig.

    Order matters and mirrors the original main():
      1. background color parse + alpha clamp
      2. WebM rename rule (depends on alpha)
      3. --font-color / --colorized mutual exclusion
      4. font color parse
    """
    background_rgb = _color_as_rgb_checked(args.background_color,
                                           'background color')
    bg_alpha = max(0.0, min(1.0, args.background_alpha))
    output_path = _apply_webm_rename_rule(output_path, bg_alpha)

    if args.font_color and args.colorized:
        print("Error: --font-color and --colorized are mutually exclusive.")
        print("       --colorized samples each character's color from the "
              "source frame,")
        print("       leaving no role for a uniform --font-color.")
        sys.exit(1)

    font_color_rgb = _color_as_rgb_checked(
        args.font_color or DEFAULT['FONT-COLOR'], 'font color')

    return asciify.ConversionConfig(
        input=input_path,
        output=output_path,
        no_key=args.no_key,
        key_color=args.key_color,
        detect_green=args.detect_green,
        tolerance=args.tolerance,
        edge_blending=args.edge_blending,
        sample_region=args.sample_region,
        ascii_width=args.ascii_width,
        ramp=args.ramp,
        hist_stretch=args.hist_stretch,
        fps_override=args.fps,
        render_font_size=args.render_font_size,
        colorized=args.colorized,
        font_color=args.font_color,
        font_color_rgb=font_color_rgb,
        leave_black_empty=args.leave_black_empty,
        black_threshold=args.black_threshold,
        background_color=args.background_color,
        background_rgb=background_rgb,
        background_alpha=bg_alpha,
        target_resolution=_parse_target_resolution(args.target_resolution),
        jobs=args.jobs,
        vpx_preset=args.vpx_preset,
        keep_temp=args.keep_temp,
        silent_progress=args.silent_progress,
        reuse_level=args.reuse,
        reuse_dir=None,          # filled in by resolve_reuse
    )


def resolve_reuse(args, state):
    """Resolve and validate the reuse directory; None if not reusing."""
    if not args.reuse:
        return None

    reuse_dir = asciify.resolve_reuse_dir(args.reuse_dir, state)
    if reuse_dir is None:
        print("Error: no reusable temp directory found. Run once with "
              "--keep-temp first, or set ASCII_VIDEO_TEMP / --reuse-dir.")
        sys.exit(1)
    asciify.validate_reuse(reuse_dir, args.reuse)

    frames_dir = reuse_dir / 'frames'
    txt_dir = reuse_dir / 'txt_frames'
    img_dir = reuse_dir / 'images'
    print(f"  Reusing artifacts from: {reuse_dir}")
    print(f"  Level: {args.reuse}  (frames={count_dir(frames_dir)}, "
          f"ascii={count_dir(txt_dir)}, rendered={count_dir(img_dir)})")
    report_reuse_semantics_cli(args.reuse, sys.argv[1:])
    return reuse_dir


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # ---- Standalone state modes (exit before touching the pipeline) ----
    if args.report:
        report_state(load_state())
        return 0
    if args.state_file:
        print(json.dumps(load_state(), indent=2))
        return 0

    if not args.input or not args.output:
        parser.error("input and output are required "
                     "(unless using --report / --state-file)")

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file '{args.input}' not found")
        return 1
    if input_path.suffix.lower() not in VIDEO_SUFFIXES:
        print("Warning: Uncommon video format detected. May fail.")

    state = load_state()

    # ---- Build the normalized config (validates along the way) ----
    cfg = build_config(args, input_path, output_path)

    # ---- Reuse resolution (needs the state; config carries the dir) ----
    cfg.reuse_dir = resolve_reuse(args, state)

    try:
        asciify.run_conversion(cfg)
    except KeyboardInterrupt:
        return 1
    except Exception as e:
        print(f"\nERROR: {e}")
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
