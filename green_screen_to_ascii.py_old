#!/usr/bin/env python3
"""
Green Screen to ASCII Video Converter

Converts a video with green screen background to ASCII art video.
Pipeline: chromakey → black background → frame extraction → jp2a conversion → ASCII rendering → video assembly

Features:
- Automatic green color detection
- Progress bars for all stages
- Configurable chromakey parameters
"""

import argparse
import subprocess
import tempfile
import shutil
import json
import sys
import struct
from pathlib import Path
from tqdm import tqdm

def run_command(cmd, check=True, quiet_progress=False):
    """Run a shell command and return the result."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False
    )
    if check and result.returncode != 0:
        print(f"\nSTDERR: {result.stderr}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result

def sample_green_color(input_path, sample_frames=10):
    """Sample multiple frames and find dominant non-black/non-white color in center area."""
    print(f"  Sampling {sample_frames} frames for green color detection...")

    try:
        # Extract sample frames from middle section of video
        probe_cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_entries', 'format=duration',
            str(input_path)
        ]
        info = json.loads(run_command(probe_cmd).stdout)
        duration = float(info['format']['duration'])

        # Sample frames at regular intervals from the middle 50%
        start_time = duration * 0.25
        interval = (duration * 0.5) / sample_frames

        rgb_values = []

        with tqdm(total=sample_frames, desc="  Sampling colors", leave=False) as pbar:
            for i in range(sample_frames):
                sample_time = start_time + (interval * i)

                # Extract a single frame and dump pixel data from center region
                cmd = [
                    'ffmpeg', '-y',
                    '-ss', str(sample_time),
                    '-i', str(input_path),
                    '-frames:v', '1',
                    '-vf', f"crop='iw/3:{int}(ih/3)':{int}(iw/3):(ih/3)",  # Center 1/3 crop
                    '-f', 'rawvideo',
                    '-pix_fmt', 'rgb24',
                    '-'
                ]

                result = subprocess.run(cmd, capture_output=True)
                if result.returncode != 0:
                    continue

                # Parse raw RGB data
                data = result.stdout
                num_pixels = len(data) // 3

                # Collect samples, skipping very dark/bright areas (likely subject/shadows)
                for j in range(0, min(num_pixels, 1000), 10):  # Sample every 10th pixel
                    r = data[j]
                    g = data[j + 1]
                    b = data[j + 2]

                    # Skip near-black, near-white, and blue-ish areas
                    brightness = (r + g + b) / 3
                    if brightness < 30 or brightness > 220:
                        continue

                    # Check if this is "green-dominant"
                    if g > r and g > b and g - r > 30 and g - b > 30:
                        rgb_values.append((r, g, b))

                pbar.update(1)

        if not rgb_values:
            print("  Warning: Could not detect strong green. Using default chroma-key green.")
            return '0x00FF00', False

        # Calculate average color
        avg_r = int(sum(c[0] for c in rgb_values) / len(rgb_values))
        avg_g = int(sum(c[1] for c in rgb_values) / len(rgb_values))
        avg_b = int(sum(c[2] for c in rgb_values) / len(rgb_values))

        hex_color = f'0x{avg_r:02X}{avg_g:02X}{avg_b:02X}'
        print(f"  Detected green: RGB({avg_r},{avg_g},{avg_b}) → {hex_color}")
        return hex_color, True

    except Exception as e:
        print(f"  Error detecting green color: {e}")
        print("  Falling back to default chroma-key green (0x00FF00)")
        return '0x00FF00', False

def probe_video(filepath):
    """Extract video info using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_streams', str(filepath)
    ]
    result = run_command(cmd)
    info = json.loads(result.stdout)

    # Find the video stream
    video_stream = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
    audio_stream = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)

    if not video_stream:
        raise ValueError("No video stream found in input file")

    width = int(video_stream.get('width', 1280))
    height = int(video_stream.get('height', 720))
    fps_raw = video_stream.get('r_frame_rate', '30/1')
    # Parse frame rate (e.g., "30/1" → 30.0)
    num, denom = fps_raw.split('/')
    fps = float(num) / float(denom)

    has_audio = audio_stream is not None

    return {
        'width': width,
        'height': height,
        'fps': fps,
        'has_audio': has_audio
    }

def chromakey_to_black(input_path, output_path, key_color, tolerance, edge_blending, width, height, fps):
    """Apply chromakey filter and composite over black background."""
    cmd = [
        'ffmpeg', '-stats', '-y', '-i', str(input_path),
        '-filter_complex',
        f'[0:v]chromakey={key_color}:{tolerance}:{edge_blending}[fg];'
        f'color=c=black:s={width}x{height}:r={fps}[bg];'
        '[bg][fg]overlay=shortest=1,format=yuv420p[v]',
        '-map', '[v]', '-map', '0:a?',
        '-c:a', 'copy',
        '-c:v', 'libx264',
        '-crf', '18',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    run_command(cmd, check=True)
    return output_path

def extract_frames(video_path, frames_dir, target_fps=None, scale_divisor=8):
    """Extract frames from the chromakeyed video with progress bar."""
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Scale down for ASCII rendering (each char = block of pixels)
    scale_filter = f'scale=iw/{scale_divisor}:-1,fps={target_fps}'

    # First, count total frames
    probe_cmd = [
        'ffprobe', '-v', 'quiet', '-count_frames',
        '-select_streams', 'v:0', '-show_entries', 'stream_side_data=nb_read_frames',
        '-of', 'default=nokey=1:noprint_wrappers=1', str(video_path)
    ]
    try:
        total_frames = int(run_command(probe_cmd).stdout.strip())
    except:
        total_frames = None

    cmd = [
        'ffmpeg', '-y', '-i', str(video_path),
        '-vf', scale_filter,
        str(frames_dir / 'frame_%06d.png')
    ]

    # Run ffmpeg with stderr capture for progress
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Try to parse frame count from stderr if ffmpeg reports it
    if total_frames is None:
        # Estimate based on fps and duration
        try:
            dur_cmd = [
                'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', str(video_path)
            ]
            duration = float(run_command(dur_cmd).stdout.strip())
            total_frames = int(duration * target_fps)
        except:
            pass

    print(f"  Extracted frames to {frames_dir}")
    if total_frames:
        print(f"  Total: ~{total_frames} frames")

    return list(frames_dir.glob('frame_*.png'))

def convert_frames_to_ascii(frames, txt_dir, width=160, colorized=False):
    """Convert each PNG frame to ASCII text using jp2a with progress bar."""
    txt_dir.mkdir(parents=True, exist_ok=True)

    ascii_files = []

    jp2a_args = ['--width=160', '--invert']
    if colorized:
        jp2a_args.append('--colors')

    for i, frame in enumerate(tqdm(frames, desc="  Converting to ASCII", unit="frame")):
        txt_file = txt_dir / f'{str(frame.stem)}.txt'

        cmd = ['jp2a'] + jp2a_args + [str(frame)]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"\nWarning: jp2a failed for {frame.name}")
            continue

        with open(txt_file, 'w') as f:
            f.write(result.stdout)
        ascii_files.append(txt_file)

    return ascii_files

def render_ascii_to_images(ascii_files, img_dir, font_size=12):
    """Render ASCII text files as images with progress bar."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Error: Pillow (PIL) is required. Install with: pip install Pillow")
        sys.exit(1)

    img_dir.mkdir(parents=True, exist_ok=True)

    # Configure font - use monospaced font
    try:
        # Try common monospace fonts
        font_paths = [
            '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
            '/usr/share/fonts/TTF/DejaVuSansMono.ttf',
            '/usr/share/fonts/liberation/LiberationMono-Regular.ttf',
            'C:/Windows/Fonts/consola.ttf',  # Windows
            '/System/Library/Fonts/Monaco.dfont',  # macOS
            'monospace'
        ]
        font = None
        for fp in font_paths:
            try:
                font = ImageFont.truetype(fp, font_size)
                break
            except (OSError, IOError):
                continue
        if font is None:
            font = ImageFont.load_default()
            print("Using default font (may not be monospace)")
    except Exception as e:
        font = ImageFont.load_default()
        print(f"Could not load custom font: {e}. Using default.")

    rendered_images = []
    char_width = 8  # Approximate pixel width per character (adjust for your font)
    char_height = 16  # Approximate pixel height per line

    for i, ascii_file in enumerate(tqdm(ascii_files, desc="  Rendering images", unit="frame")):
        with open(ascii_file, 'r') as f:
            text = f.read()

        lines = text.strip().split('\n')
        if not lines:
            continue

        max_line_len = max(len(line) for line in lines)
        img_width = max_line_len * char_width
        img_height = len(lines) * char_height

        # Create black image
        img = Image.new('RGB', (img_width, img_height), color='black')
        draw = ImageDraw.Draw(img)

        # Draw ASCII text in bright green for contrast
        for y, line in enumerate(lines):
            draw.text((0, y * char_height), line, fill=(100, 255, 100), font=font)

        out_path = img_dir / f'rendered_{str(ascii_file.stem).zfill(6)}.png'
        img.save(out_path)
        rendered_images.append(out_path)

    return rendered_images

def assemble_video(rendered_images, output_path, fps, source_video):
    """Assemble rendered frames into final video with progress."""
    print("  Assembling video...")

    cmd = [
        'ffmpeg', '-stats', '-y',
        '-framerate', str(int(fps)),
        '-pattern_type', 'glob',
        '-i', str(rendered_images[0].parent / 'rendered_*.png'),
        '-c:v', 'libx264',
        '-crf', '18',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]

    # Try to preserve audio from original if available
    try:
        probe = json.loads(run_command([
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', source_video
        ]).stdout)
        has_audio = any(s.get('codec_type') == 'audio' for s in probe.get('streams', []))
        if has_audio:
            # Get duration from source and trim audio accordingly
            duration = float(probe['format'].get('duration', 0))
            cmd.insert(-3, '-i')
            cmd.insert(-2, str(source_video))
            cmd.insert(-5, '-shortest')
    except Exception as e:
        print(f"  Note: Could not detect/preserve audio: {e}")

    run_command(cmd, check=True)
    return output_path

def cleanup_temp(temp_dir):
    """Remove temporary directory."""
    shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    parser = argparse.ArgumentParser(
        description='Convert green-screen video to ASCII art video',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s input.mp4 output.mp4
  %(prog)s video.mp4 ascii.mp4 --detect-green
  %(prog)s clip.mov final.mp4 --key-color 0x00B140 --tolerance 0.3
        """
    )

    parser.add_argument('input', type=str, help='Input video file path')
    parser.add_argument('output', type=str, help='Output ASCII video file path')
    parser.add_argument('--key-color', type=str, default=None,
                        help='Key color in hex format (default: auto-detect)')
    parser.add_argument('--detect-green', action='store_true',
                        help='Force green color detection even if --key-color is provided')
    parser.add_argument('--tolerance', type=float, default=0.25,
                        help='Chromakey tolerance (default: 0.25)')
    parser.add_argument('--edge-blending', type=float, default=0.05,
                        help='Chromakey edge blending (default: 0.05)')
    parser.add_argument('--fps', type=float, default=None,
                        help='Override output FPS (default: extract from input)')
    parser.add_argument('--scale-divisor', type=int, default=8,
                        help='Pixel-to-char scaling factor (default: 8)')
    parser.add_argument('--ascii-width', type=int, default=160,
                        help='ASCII line width in chars (default: 160)')
    parser.add_argument('--colorized', action='store_true',
                        help='Output colored ASCII instead of monochrome')
    parser.add_argument('--keep-temp', action='store_true',
                        help='Keep temporary files after processing')
    parser.add_argument('--silent-progress', action='store_true',
                        help='Suppress progress bars (useful for logging)')

    args = parser.parse_args()

    # Validate inputs
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file '{args.input}' not found")
        sys.exit(1)

    if input_path.suffix.lower() not in ['.mp4', '.mov', '.avi', '.mkv', '.webm']:
        print(f"Warning: Uncommon video format detected. May fail.")

    # Disable progress bars if silent
    tqdm_disabled = args.silent_progress

    # Create temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix='ascii_video_'))

    try:
        print("\n" + "="*60)
        print("GREEN SCREEN TO ASCII VIDEO CONVERTER")
        print("="*60 + "\n")

        # Stage 1: Detect green color or use provided/default
        print("[1/6] Green color selection...")

        if args.detect_green or args.key_color is None:
            detected_color, was_detected = sample_green_color(input_path)
            key_color = detected_color
        else:
            key_color = args.key_color
            was_detected = False

        print(f"  Using chromakey color: {key_color}")
        if was_detected:
            print("  (Auto-detected from input video)")
        print()

        # Stage 2: Probe input video
        print("[2/6] Probing input video...")
        video_info = probe_video(input_path)
        width, height, fps, has_audio = (
            video_info['width'], video_info['height'],
            video_info['fps'], video_info['has_audio']
        )
        fps = args.fps if args.fps else fps
        print(f"  Resolution: {width}x{height}")
        print(f"  Frame rate: {fps:.2f} fps")
        print(f"  Audio: {'Yes' if has_audio else 'No'}")
        print()

        # Stage 3: Chromakey to black
        print("[3/6] Applying chromakey and compositing over black...")
        chroma_output = temp_dir / 'chroma_keyed.mp4'
        with tqdm(total=1, desc="  Processing video", disable=tqdm_disabled) as pbar:
            chromakey_to_black(input_path, chroma_output, key_color,
                               args.tolerance, args.edge_blending, width, height, fps)
            pbar.update(1)
        print(f"  Output: {chroma_output}")
        print()

        # Stage 4: Extract frames
        print("[4/6] Extracting frames...")
        frames_dir = temp_dir / 'frames'
        frames = extract_frames(chroma_output, frames_dir, fps, args.scale_divisor)
        print(f"  Extracted {len(frames)} frames to {frames_dir}")
        print()

        # Stage 5: Convert to ASCII text
        print("[5/6] Converting frames to ASCII...")
        txt_dir = temp_dir / 'txt_frames'
        ascii_files = convert_frames_to_ascii(frames, txt_dir, args.ascii_width, args.colorized)
        print(f"  Created {len(ascii_files)} ASCII files in {txt_dir}")
        print()

        # Stage 6: Render ASCII to images and assemble video
        print("[6/6] Rendering ASCII to images and assembling video...")
        img_dir = temp_dir / 'images'
        rendered_images = render_ascii_to_images(ascii_files, img_dir)
        assemble_video(rendered_images, output_path, fps, input_path)
        print(f"  Final output: {output_path}")
        print()

        print("="*60)
        print("CONVERSION COMPLETE!")
        print("="*60)
        print(f"\nUsed chromakey color: {key_color}" + (" (auto-detected)" if was_detected else ""))
        print(f"Parameters: tolerance={args.tolerance}, edge_blending={args.edge_blending}")
        if not args.keep_temp:
            print(f"Temporary files cleaned up from: {temp_dir}")
        print()

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        cleanup_temp(temp_dir)
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        if not args.keep_temp:
            print(f"Cleaning up temporary files...")
        cleanup_temp(temp_dir)
        sys.exit(1)

if __name__ == '__main__':
    main()
